"""The Application Profile — what the source code says the app is supposed to do.

This is the data structure §3.0b's Repo Profiler produces and §3.4's reward model
consumes. It exists separately from the profiler itself because the two answer
different questions, and only one of them is on the critical path:

* *Can source-level intent improve judgment?* — needs a profile and a judge that reads
  one. Testable today against a hand-authored profile.
* *Can a profile be extracted automatically from an arbitrary repo?* — needs tree-sitter
  extractors per framework, and is weeks of work whose value depends entirely on the
  first question's answer.

Building the consumer first means the extractor is written against a schema that has
already been shown to help, rather than the other way round.

**Provenance is a required field and it is not decoration.** A hand-authored profile and
an extracted one are the same shape and produce identical prompts, so nothing downstream
can tell them apart — which makes it trivially easy to report a number from a profile a
human wrote as though a profiler had produced it. `provenance` is mandatory, is carried
into every scoring report, and `manual` is rendered nowhere near the model so it cannot
influence a verdict either.

Two properties matter for how this reaches the judge:

**The profile is a partial description.** Nothing here is a complete specification, and
a judge told "here is what the app does" will read omissions as defects. Every renderer
says so explicitly, and the prompt guidance repeats it.

**The profile is never part of the window.** `is_grounded` checks that a judge's quoted
evidence occurs in the text it was shown; if the profile were concatenated into the
window, a judge could quote the *specification* as evidence that the app misbehaved and
score as perfectly grounded. That would silently destroy the only judge-quality metric
that transfers to a target with no answer key. Hence `Judge.judge(window_text,
profile_text)` takes two arguments, and only the first is ever the grounding haystack.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# The slice handed to the judge is capped. The measured lesson from window trimming
# (§5, 2026-08-08) is that additions displace attention rather than accumulate: cutting
# tautological content improved every metric on both models at once. A profile is new
# content competing with the observation for the same attention, so it is budgeted from
# the start rather than allowed to grow until something regresses.
PROFILE_CHARS_MAX = 1400

# Per-entry cap. A rule or behaviour longer than this is almost always carrying its
# rationale as well as its content, and the rationale is not what the judge needs.
_ENTRY_CHARS_MAX = 260

# How the budget is divided between sections, by what each contributes to a verdict.
# Constraints supply the "the code defines X" half of a finding and behaviours suppress
# the false positives that make a reward signal dangerous, so they take most of it.
# Routes take least: the window already shows the URL, title and headings, so a route
# entry mostly restates what the judge can see, adding only the page's stated purpose.
_W_RULES, _W_BEHAVIOURS, _W_FLOWS, _W_ROUTES = 0.35, 0.30, 0.20, 0.15

# Rendered when a route in the window has no entry at all, so the judge is told the
# difference between "the profile permits this" and "the profile is silent".
_UNPROFILED = "(this route is not described in the profile — judge it from the window alone)"


def _shorten(text: str, limit: int) -> str:
    """Trim to `limit`, saying so. An unmarked cut reads as the statement ending there,
    which turns a truncated rule into a different — and wrong — rule."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " …[trimmed]"


def _render_block(title: str, entries: tuple, budget: int) -> tuple[str, int]:
    """One titled section, filled entry by entry until its share of the budget is spent.

    Dropping whole entries and counting them beats trimming each one to fit: a rule cut
    mid-clause is a rule that says something the source never said, and the judge has no
    way to tell a truncated constraint from a lax one.
    """
    lines = ["", f"{title}:"]
    used = len(title) + 3
    kept = 0
    for entry in entries:
        rendered = f"  - {_shorten(entry.render(), _ENTRY_CHARS_MAX)}"
        # The first entry is always taken, even when it overruns this section's share.
        # A section is a category of grounding, and dropping one entirely because its
        # opening entry was 6 characters too long is a far worse outcome than borrowing
        # from the next section: measured on Gitea, a strict share dropped *both* the
        # declared constraints and the known-correct behaviours from the signup window
        # while keeping the flow and route lists, which is the opposite of the ordering
        # this budget exists to enforce.
        if kept and used + len(rendered) + 1 > budget:
            break
        lines.append(rendered)
        used += len(rendered) + 1
        kept += 1
    if not kept:
        return "", 0
    dropped = len(entries) - kept
    if dropped:
        note = f"  … [{dropped} further entr{'y' if dropped == 1 else 'ies'} omitted for length]"
        lines.append(note)
        used += len(note) + 1
    return "\n".join(lines), used


def _segments(path: str) -> list[str]:
    return [segment for segment in path.split("?", 1)[0].split("/") if segment]


def path_matches(pattern: str, path: str) -> bool:
    """Whether a concrete URL path is an instance of a profile route pattern.

    `{name}` matches exactly one segment, so `/{owner}/{repo}/issues` matches
    `/tester/notes/issues` but not `/tester/notes`. A trailing `/*` matches any remaining
    segments, which is how a whole subtree (Swagger's generated pages, an asset root)
    gets one entry instead of dozens.
    """
    want, got = _segments(pattern), _segments(path)
    if want and want[-1] == "*":
        want = want[:-1]
        if len(got) < len(want):
            return False
        got = got[: len(want)]
    if len(want) != len(got):
        return False
    return all(
        expected.startswith("{") and expected.endswith("}") or expected == actual
        for expected, actual in zip(want, got)
    )


@dataclass(frozen=True, slots=True)
class Route:
    """A page the application serves, and what it is for."""

    path: str
    purpose: str
    auth: str = "anonymous"  # anonymous | authenticated | admin
    source: str = ""

    def render(self) -> str:
        """Judge-facing text. Deliberately excludes `source`.

        Provenance is what makes a *finding* checkable — "the code defines X at
        services/forms/user_form.go, testing observed Y" — and the Bug Report Engine
        (§3.5) needs it. The judge does not: it has to decide whether the observation
        contradicts the rule, and a file path cannot help with that. Measured on the
        Gitea slice, citations were ~40% of the characters in the constraints section
        and pushed three of the five constraints out of the budget entirely. Every
        character here competes with the window for attention, which is the one thing
        this project has measured repeatedly.
        """
        return f"{self.path} — {self.purpose} (access: {self.auth})"

    def cite(self) -> str:
        return self.render() + (f"  [{self.source}]" if self.source else "")


@dataclass(frozen=True, slots=True)
class Rule:
    """A constraint the application's own code declares.

    This is the half of a finding that says "the code defines X"; the window supplies
    "testing observed Y". `source` names where the declaration lives, which is what
    turns a finding into a code-vs-behaviour diff (§3.5) — but it is deliberately absent
    from `render`: see `Route.render`.
    """

    field: str
    route: str
    rule: str
    source: str = ""

    def render(self) -> str:
        return f"{self.field}: {self.rule}"

    def cite(self) -> str:
        """Rule plus provenance, for a bug report rather than for a prompt."""
        return f"{self.field}: {self.rule}" + (f"  [{self.source}]" if self.source else "")


@dataclass(frozen=True, slots=True)
class Flow:
    """A multi-step user journey and the outcome it is supposed to reach."""

    name: str
    routes: list[str]
    steps: list[str]
    outcome: str
    source: str = ""

    def render(self) -> str:
        return f"{self.name}: {' -> '.join(self.steps)} => {self.outcome}"

    def cite(self) -> str:
        return self.render() + (f"  [{self.source}]" if self.source else "")


@dataclass(frozen=True, slots=True)
class Behaviour:
    """Correct-but-surprising behaviour, stated so it is not reported as a defect.

    The judge's expensive failure is the false positive, and the expensive false
    positives are the ones where the app is *deliberately* doing something that looks
    wrong. `route="*"` makes an entry global; anything else scopes it.
    """

    description: str
    route: str = "*"
    source: str = ""

    def render(self) -> str:
        return self.description

    def cite(self) -> str:
        return self.description + (f"  [{self.source}]" if self.source else "")


@dataclass(frozen=True, slots=True)
class ApplicationProfile:
    """Structured intent for one application under test."""

    name: str
    app_type: str
    summary: str
    auth: str
    provenance: str  # "manual" | "profiler" — required; see the module docstring
    routes: tuple[Route, ...] = ()
    rules: tuple[Rule, ...] = ()
    flows: tuple[Flow, ...] = ()
    behaviours: tuple[Behaviour, ...] = ()
    notes: str = ""

    # -- loading ---------------------------------------------------------------

    @classmethod
    def from_dict(cls, payload: dict) -> "ApplicationProfile":
        missing = [key for key in ("name", "app_type", "summary", "auth", "provenance") if not payload.get(key)]
        if missing:
            raise ValueError(f"Application profile is missing required field(s): {', '.join(missing)}")
        if payload["provenance"] not in ("manual", "profiler"):
            raise ValueError(
                f"provenance must be 'manual' or 'profiler', got {payload['provenance']!r}. "
                "A hand-authored profile and an extracted one produce identical prompts, "
                "so the distinction has to be recorded at the source."
            )
        return cls(
            name=payload["name"],
            app_type=payload["app_type"],
            summary=payload["summary"],
            auth=payload["auth"],
            provenance=payload["provenance"],
            routes=tuple(Route(**entry) for entry in payload.get("routes", [])),
            rules=tuple(Rule(**entry) for entry in payload.get("rules", [])),
            flows=tuple(Flow(**entry) for entry in payload.get("flows", [])),
            behaviours=tuple(Behaviour(**entry) for entry in payload.get("behaviours", [])),
            notes=payload.get("notes", ""),
        )

    @classmethod
    def load(cls, path: str | Path) -> "ApplicationProfile":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    # -- slicing ---------------------------------------------------------------

    def slice_for(self, paths: Iterable[str]) -> "ApplicationProfile":
        """Keep only what bears on the routes this window actually touched.

        §3.0b specifies "the relevant slice of the Application Profile", not the whole
        thing, and the reason is the same one that made lean windows beat verbose ones:
        a real application's profile is far larger than any single window's context, and
        text that cannot bear on the step under judgment still competes for attention.
        """
        wanted = [p for p in paths if p]
        routes = tuple(r for r in self.routes if any(path_matches(r.path, p) for p in wanted))
        rules = tuple(r for r in self.rules if any(path_matches(r.route, p) for p in wanted))
        flows = tuple(
            f for f in self.flows
            if any(path_matches(route, p) for route in f.routes for p in wanted)
        )
        behaviours = tuple(
            b for b in self.behaviours
            if b.route == "*" or any(path_matches(b.route, p) for p in wanted)
        )
        return ApplicationProfile(
            name=self.name,
            app_type=self.app_type,
            summary=self.summary,
            auth=self.auth,
            provenance=self.provenance,
            routes=routes,
            rules=rules,
            flows=flows,
            behaviours=behaviours,
            notes=self.notes,
        )

    def covers(self, path: str) -> bool:
        return any(path_matches(route.path, path) for route in self.routes)

    # -- rendering -------------------------------------------------------------

    def render(self, paths: Iterable[str] = (), budget: int = PROFILE_CHARS_MAX) -> str:
        """The profile section handed to the judge, sliced to `paths` and budgeted.

        Returns an empty string when the slice carries nothing, so a window about a
        route the profile has never heard of is judged exactly as it was before — the
        A/B then measures grounding where grounding exists, rather than measuring the
        cost of a header.

        The budget is spent **per section, in priority order**, not by truncating the
        tail of the assembled text. Tail truncation ranks content by where it happens to
        sit in the template: on Gitea it cut every declared constraint while keeping a
        three-line application summary that is identical in all 109 windows. Constraints
        and known-correct behaviour are the two things the profile exists to add — one
        supplies the "the code defines X" half of a finding, the other suppresses false
        positives — so they are filled first and the route list absorbs the shortfall.
        """
        wanted = [p for p in paths if p]
        sliced = self.slice_for(wanted) if wanted else self
        if not (sliced.routes or sliced.rules or sliced.flows or sliced.behaviours):
            return ""

        # One line, not a preamble. The rules for using a profile (it is incomplete, it
        # is never the evidence, use it for contradiction and suppression) live in
        # `judge.prompt.PROFILE_GUIDANCE`, which is stated once in the system turn and
        # is cacheable. Repeating them here cost ~450 characters in *every* window —
        # more than the entire constraints section — to say something the model had
        # already been told in the same request.
        header = [
            "APPLICATION PROFILE — partial; absence from it is not a defect.",
            f"application: {sliced.name} — {sliced.app_type}",
            f"access     : {_shorten(sliced.auth, 240)}",
        ]
        unprofiled = [p for p in wanted if not self.covers(p)]
        if unprofiled:
            header.append(f"note       : {', '.join(sorted(set(unprofiled))[:4])} {_UNPROFILED}")

        # Ordered by what the profile is for. A constraint the app violated is a finding
        # nothing else in the pipeline can produce; a behaviour recorded as correct
        # prevents the failure mode that actually costs this project (false positives in
        # a reward signal). Routes are context and go last.
        sections = [
            (title, entries, weight)
            for title, entries, weight in (
                ("declared constraints (the app's own rules)", sliced.rules, _W_RULES),
                ("known-correct behaviour — never report these as bugs", sliced.behaviours, _W_BEHAVIOURS),
                ("intended flows", sliced.flows, _W_FLOWS),
                ("routes in this window", sliced.routes, _W_ROUTES),
            )
            if entries
        ]

        # Each section gets a weighted share of what is left, and whatever it does not
        # use flows to the next. Two failures this avoids, both measured on Gitea:
        #
        #   strict priority — the five signup constraints consumed the whole budget and
        #   starved the known-correct behaviours out of 101 of 109 windows, which is
        #   exactly backwards, since those behaviours are what suppress false positives.
        #
        #   equal shares — with four sections every one got a single entry, including
        #   the route list, whose entries mostly restate the URL and title the window
        #   already shows. Weighting spends the tail on the sections that add something.
        remaining = budget - len("\n".join(header))
        blocks: list[str] = []
        for position, (title, entries, weight) in enumerate(sections):
            weights_left = sum(w for _, _, w in sections[position:])
            share = remaining if position == len(sections) - 1 else int(remaining * weight / weights_left)
            block, used = _render_block(title, entries, share)
            if block:
                blocks.append(block)
                remaining -= used
        return "\n".join(header) + "\n" + "\n".join(blocks)


def window_paths(window) -> list[str]:  # noqa: ANN001 - judge.window.JudgeWindow, imported lazily by callers
    """Every route a window touches, in order, deduplicated.

    Both sides of every transition, because a flow's intent usually lives on the page it
    started from: a signup rule is declared on `/user/sign_up` and violated on whatever
    the submit redirects to, and slicing on the final page alone would drop the rule
    exactly when it is needed.
    """
    seen: dict[str, None] = {}
    for record in window.records:
        for side in (record.before, record.after):
            path = (side.get("path") or "").split("?", 1)[0]
            if path:
                seen.setdefault(path, None)
    return list(seen)
