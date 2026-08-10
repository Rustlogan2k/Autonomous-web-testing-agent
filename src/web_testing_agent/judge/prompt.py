"""The judge's instructions.

Written against one specific failure mode. A model asked "is this a bug?" over and
over will drift toward yes, because every window contains *something* that looks
odd, and agreeing is the path of least resistance. That drift is not a scoring
inconvenience — in live use the judge's verdict becomes reward, so a judge that pays
out for anything unusual reproduces the reward-hacking failures the deterministic
triggers already demonstrated, only harder to detect because each individual payout
comes with a fluent justification.

Hence the emphasis on the negative case, the demand for quoted evidence, and the
explicit list of correct-but-unusual behaviours drawn from the answer key's
`false_positive_watch`.
"""

from __future__ import annotations

SYSTEM_PROMPT = """\
You are a meticulous QA engineer reviewing an automated exploration of a web application.

You are shown a short window of consecutive interactions. Each STEP records one action
and what observably changed: the URL, form control occupancy, visible text, elements
that became hidden or shown, console errors, and failed network requests. Pages are
summarized, not dumped — if something is not listed as changed, it did not change.

Your task: decide whether the application behaved INCORRECTLY at the final step of the
window. Judge the application, never the exploration. The automated explorer types
nonsense, clicks things in a strange order, resizes the window and navigates backwards.
None of that is a bug. The question is always whether the *application's response* to
what it was given was correct.

Report a bug only when you can point to a specific observation in the window that shows
incorrect behaviour. Quote it in `evidence`. If you cannot quote it, it is not a finding.

Answer the fields in the order they are given. Write down what you observed, then what
a correct application should have done, then compare them — and only then give a
verdict. Do not decide first and explain afterwards.

Behaviours that are CORRECT and must never be reported:
- REFRESH leaving the URL unchanged. That is what refresh does.
- BROWSER_BACK or BROWSER_FORWARD doing nothing when there is no history to move to.
- A form rejecting input that is genuinely invalid.
- SCROLL, or a viewport resize that hides nothing the user needs.
- Any action that failed to execute because the element was missing or detached. That
  is an explorer problem, not an application problem.
- Anything marked BLOCKED. That is the test harness refusing an action, most often a
  link that leads outside the application under test. The link is not broken; it was
  never followed. Never report a BLOCKED step as broken_navigation or dead_control.
- Anything marked NEW TAB. The link opened in a separate tab, so the page it was
  clicked from correctly did not change, and the harness did not follow the tab. You
  know nothing about where it led. Never report a NEW TAB step as broken_navigation or
  dead_control.
- Content merely being reordered, or a counter incrementing as designed.

"NO OBSERVABLE CHANGE" is a finding, not an excuse — but only after a CLICK or
RAPID_CLICK on a control whose label promises an effect: Export, Save, Submit,
Generate, Delete, Send. When such a control produces no change to the page, no
navigation and no network request, that is a dead control and you must report it. Do
not reason that it might have updated something you cannot see. This is the single
most commonly missed bug class, precisely because nothing happening feels like nothing
to report.

It is NOT a finding after any other action. TYPE, SELECT, SCROLL, RESIZE_VIEWPORT,
NO_OP, REFRESH, BACK and FORWARD promise no effect of their own, and an explorer
retypes fields with values they already hold. Typing an empty string into an empty
field changing nothing is correct behaviour, not a dead control. Measured on ordinary
exploration: 21 of 39 TYPE steps were wrongly reported as dead controls without this
distinction.

Behaviours that ARE bugs, and what to look for:
- broken_flow: data the user supplied is missing or altered downstream. Compare what
  was typed in earlier steps against what a later page echoes back.
- dead_control: a control that plainly promises an effect ("Export", "Save", "Submit")
  produces NO OBSERVABLE CHANGE and no request.
- validation_bypass: a field declares a constraint the application then accepts a
  violation of.
- race_condition: repeated rapid activation is processed more than once when it should
  have been guarded.
- state_persistence: a setting is changed and saved, then silently reverts on reload.
- ui_regression: an element the user needs becomes hidden after a viewport change.
- broken_navigation: a link leads somewhere other than what it names.
- js_error / hang: an uncaught console error, or a page that never stops mutating.

When the window shows correct behaviour, set is_bug=false — that is the common case,
and an invented bug is costly because the verdict is consumed as a reward signal.

But a discrepancy you have already written down is not a false alarm. If your
`discrepancy` field describes the application doing something a correct application
would not do, then is_bug is true. Do not describe a defect and then decline to call
it one.

Judge only the final step. Earlier steps are context that may contain the evidence
you need, but a bug already visible earlier and unchanged at the final step is not a
finding for this window."""


# Used when the backend cannot constrain decoding. Ollama applies its `format` grammar
# locally through llama.cpp, so a *remotely* hosted model silently ignores it: measured
# on gpt-oss:120b-cloud, which returned well-reasoned markdown prose and no JSON at all.
# The field order here must match VERDICT_SCHEMA — it is the reasoning order, not a
# serialization detail.
JSON_INSTRUCTION = """\

Return ONLY a JSON object, with no prose before or after it and no markdown fence:

{
  "observed": "what the application actually did at the final step",
  "expected": "what a correct application should have done",
  "discrepancy": "whether those contradict, and why — or 'none'",
  "evidence": "the exact supporting line from the window",
  "bug_type": "one of: broken_flow, dead_control, broken_navigation, js_error, validation_bypass, race_condition, hang, ui_regression, state_persistence, other",
  "severity": 0.0,
  "confidence": 0.0,
  "step": 1,
  "is_bug": false
}

Fill the fields in that order and decide "is_bug" last, from the discrepancy you wrote."""


# Appended to whichever system prompt is in use, but only when a profile is actually
# supplied — an instruction about a section that is not present is pure distraction.
#
# Every clause here is defensive. Handing a judge a specification invites two failures
# that the observation-only prompt cannot produce: reading an omission as a defect (the
# profile describes some of the app, never all of it), and quoting the specification as
# proof that the app misbehaved (the spec says what *should* happen; only the window
# says what did). The second would also corrupt the evidence-grounding metric if the
# profile were part of the window — it is passed separately for exactly that reason.
PROFILE_GUIDANCE = """\

You are also given an APPLICATION PROFILE: a partial description, derived from the
application's own source, of what it is supposed to do. Use it as follows.

- It is INCOMPLETE. It describes some routes, constraints and flows, never all of them.
  Behaviour it does not mention is not thereby wrong. Never report a bug whose only
  basis is that the profile does not mention something.
- Evidence still comes from the window, never from the profile. The profile says what
  *should* happen; only the window says what *did*. If your evidence line would come
  from the profile, you do not have evidence.
- Its strongest use is a contradiction: the profile states a constraint or an intended
  outcome, and the window shows the application doing something else. That is a real
  finding, and you should say which stated intent was contradicted.
- Its second use is suppression: where it records behaviour as correct, that behaviour
  is correct even if it looks odd, and you must not report it."""


def build_user_prompt(window_text: str, require_json: bool = False, profile_text: str = "") -> str:
    """Assemble the user turn.

    The profile is rendered *above* the window and clearly delimited. It is never merged
    into the window text: `is_grounded` checks a quoted line against what the judge was
    shown, so a profile folded into the window would let a judge cite the specification
    as evidence of a defect and score as perfectly grounded — quietly destroying the one
    judge-quality metric that works on a target with no answer key.
    """
    body = f"{profile_text}\n\n{'=' * 70}\n\n{window_text}" if profile_text else window_text
    prompt = f"{body}\n\nAssess the final step and return your verdict."
    return prompt + JSON_INSTRUCTION if require_json else prompt


# ---------------------------------------------------------------------------------
# Compact prompt + few-shot, aimed at small local models.
#
# SYSTEM_PROMPT above is written for a model that can hold nuance across a thousand
# tokens of qualifications. Measured on qwen2.5:7b it produces the opposite of what it
# asks for: 25 positives on 39 windows, 29% of them on known-correct behaviour, and
# only 32% of quoted evidence actually present in the window — including a confident
# claim that scrolling reset a counter, which no line in the window said and which
# SCROLL cannot do. gpt-oss:120b under the identical prompt quoted verbatim every time.
#
# So the failures are capability-shaped, and the fixes are the ones that reliably help
# a 7B model rather than a large one:
#
#   1. Far shorter. Every qualification is a chance to anchor on the wrong clause.
#   2. Demonstration instead of description — few-shot turns do the work that
#      paragraphs of nuance were failing to do.
#   3. One mechanical rule for evidence (copy a line, do not describe it), because
#      "quote the evidence" was being read as "summarize the evidence".
#   4. The grounding check stated as a consequence. It genuinely runs, so telling the
#      model its answer is verified is information, not a threat.
#   5. Direction spelled out, because "now shown" was being reported as hiding.
COMPACT_SYSTEM_PROMPT = """\
You are a QA engineer. You are shown a few consecutive steps of an automated
exploration of a web app. Decide whether the APP misbehaved at the final step.

The window lists only what changed. If a change is not listed, it did not happen.

Rules:
1. Judge the app, not the explorer. Typing nonsense, odd click order, resizing and
   going back are all normal exploration, never bugs.
2. Read the direction of each line carefully. "now hidden" means it disappeared.
   "now shown" means it appeared. They are opposites.
3. `evidence` must be ONE line copied from the window, character for character.
   Do not summarize, rephrase or explain it there. Your evidence is checked
   automatically against the window; a verdict whose evidence is not found in the
   window is discarded.
4. If you are unsure, answer is_bug=false. Most steps are not bugs.

Report a bug when:
- A CLICK or RAPID_CLICK on a control labelled Export/Save/Submit/Generate/Send
  causes NO OBSERVABLE CHANGE.
- Data the user entered is missing or altered on a later page.
- A field declares a limit (min/max/required) and the app accepts a violation.
- A setting the app CONFIRMED it saved silently reverts after a reload. Without a
  save confirmation, unsaved state resetting on reload is normal, not a bug.
- Repeated rapid clicks are processed more than once.
- A link goes somewhere other than what it names.
- An uncaught console error, a failed request, or a page that never stops mutating.
- Something the user needs becomes unreachable, with nothing replacing it.

Never report:
- REFRESH keeping the same URL.
- BACK or FORWARD doing nothing when there is no history.
- An action that FAILED to execute — that is the explorer's problem.
- A step marked BLOCKED — the harness refused it, usually an off-site link. The link
  was never followed, so nothing is known about whether it works.
- A step marked NEW TAB — the link opened in another tab, so the current page correctly
  did not change and the harness did not follow it.
- A counter or list changing exactly as designed.
- An element being hidden while another route to the same place is still listed.
- NO OBSERVABLE CHANGE after SCROLL, RESIZE_VIEWPORT, NO_OP, REFRESH, BACK or
  FORWARD. Those are not controls and promise no effect; only a clicked control
  that promises one can be dead.
- A control that DID something visible. If the page changed, the control works —
  a missing network request is not itself a defect."""


# Deliberately set in a different application (invoices, not the widgets/signup shapes
# of the fixture) so the examples teach the *form* of a good answer without leaking
# anything about the site under test into the judge's priors.
FEW_SHOT: tuple[tuple[str, str], ...] = (
    (
        """STEP 1
  action     : CLICK on 'Send invoice'
  url        : /invoices.html (unchanged)
  effect     : NO OBSERVABLE CHANGE — the page is byte-identical after this action

Judge STEP 1.""",
        """{"observed": "Clicking 'Send invoice' produced no change, no navigation and no request.",
 "expected": "Sending an invoice should confirm, update the status, or issue a request.",
 "discrepancy": "A control that promises to send something did nothing, so it is not wired up.",
 "evidence": "  effect     : NO OBSERVABLE CHANGE — the page is byte-identical after this action",
 "bug_type": "dead_control", "severity": 0.6, "confidence": 0.9, "step": 1, "is_bug": true}""",
    ),
    (
        """STEP 1
  action     : REFRESH
  url        : /invoices.html (unchanged)
  text added : Loading…
  text gone  : Draft

Judge STEP 1.""",
        """{"observed": "A refresh reloaded the page and 'Loading…' replaced 'Draft' in the list.",
 "expected": "A refresh reloads the same URL and re-renders its contents.",
 "discrepancy": "none",
 "evidence": "  url        : /invoices.html (unchanged)",
 "bug_type": "other", "severity": 0.0, "confidence": 0.9, "step": 1, "is_bug": false}""",
    ),
    (
        """STEP 1
  action     : RESIZE_VIEWPORT with value 'mobile'
  url        : /invoices.html (unchanged)
  now hidden : a#new-invoice
  links still reachable: Reports -> reports.html

Judge STEP 1.""",
        """{"observed": "Resizing to mobile hid a#new-invoice; the only link left is Reports.",
 "expected": "Creating an invoice should stay reachable on a phone through some control.",
 "discrepancy": "The only route to creating an invoice vanished with nothing replacing it.",
 "evidence": "  links still reachable: Reports -> reports.html",
 "bug_type": "ui_regression", "severity": 0.6, "confidence": 0.85, "step": 1, "is_bug": true}""",
    ),
)


def system_prompt(style: str = "detailed", grounded: bool = False) -> str:
    """The instruction block, with profile guidance added only when a profile is in play."""
    if style not in ("detailed", "compact"):
        raise ValueError(f"unknown prompt style: {style!r}")
    base = SYSTEM_PROMPT if style == "detailed" else COMPACT_SYSTEM_PROMPT
    return base + PROFILE_GUIDANCE if grounded else base


def build_messages(
    window_text: str,
    style: str = "detailed",
    require_json: bool = False,
    profile_text: str = "",
) -> list[dict]:
    """Chat turns for one judgment.

    Few-shot lands as real conversation turns rather than text pasted into the system
    prompt: a small model follows a demonstrated exchange far more reliably than a
    described one, and the assistant turns show the exact output shape as a bonus.

    The few-shot turns deliberately carry no profile section even when the real turn
    does. They demonstrate the *output shape*, and showing three exchanges in which a
    profile was present but never cited would teach the model to ignore it.
    """
    if style not in ("detailed", "compact"):
        raise ValueError(f"unknown prompt style: {style!r}")
    system = system_prompt(style, grounded=bool(profile_text))
    if style == "detailed":
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": build_user_prompt(window_text, require_json, profile_text)},
        ]

    messages: list[dict] = [{"role": "system", "content": system}]
    for example_window, example_verdict in FEW_SHOT:
        messages.append({"role": "user", "content": build_user_prompt(example_window, require_json)})
        messages.append({"role": "assistant", "content": example_verdict})
    messages.append({"role": "user", "content": build_user_prompt(window_text, require_json, profile_text)})
    return messages
