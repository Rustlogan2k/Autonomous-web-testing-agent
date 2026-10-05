"""The ctxbench generator: twelve variants of one loan-request task for the contextual
diagnostic (`docs/methodology/2026-10-04-contextual-diagnostic-preregistration.md`).

    python -m web_testing_agent.ctxbench.generate            # writes tests/fixtures/ctxbench/
    python -m web_testing_agent.ctxbench.generate --check    # verifies the tree is current

Every variant is the same application: `assets/core.js` is byte-identical everywhere and the
two seeded faults are the same patches everywhere. What varies is `assets/config.js`
(vocabulary profile, workflow structure, decoy flag) and the HTML shells. The output uses
the primary benchmark's fixture format (`clean/`, `buggy/`, `build.py`, `faults.json`), so
the primary validator admits it unchanged when pointed at this directory
(`WTA_BENCHMARK_ROOT=tests/fixtures/ctxbench`). The primary suite never reads it.

Deterministic: no randomness is needed, and none is used; "seeded" in the pre-registration
is satisfied trivially (seed 0, recorded in every manifest).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent / "templates"
REPO_ROOT = Path(__file__).resolve().parents[3]
OUT_ROOT = REPO_ROOT / "tests" / "fixtures" / "ctxbench"
SEED = 0

# -- vocabulary ----------------------------------------------------------------------------

#: Labels of controls the tester acts on (buttons, links, fields). The token-overlap rule of
#: the pre-registration is checked over exactly these keys (`act.decoy` excepted: it is the
#: training label on purpose).
CONTROL_KEYS = (
    "act.begin", "act.add", "act.remove", "act.advance", "act.advance_alt", "act.save",
    "act.edit", "act.commit", "act.to_review", "act.new", "act.home", "act.close",
    "field.quantity", "field.purpose", "field.return_date", "field.name", "field.email",
    "title.items", "title.configure", "title.contact", "title.review", "title.help",
)
TRAINING_DECOY_LABEL = "Proceed"

PROFILES: dict[str, dict[str, str]] = {
    "V0": {
        "act.begin": "Start a request", "act.add": "Add", "act.remove": "Remove",
        "act.advance": "Proceed", "act.advance_alt": "Move along", "act.save": "Save",
        "act.edit": "Edit", "act.commit": "Send request", "act.to_review": "Review request",
        "act.new": "New request", "act.home": "Back home", "act.close": "Close",
        "field.quantity": "Quantity", "field.purpose": "Purpose", "field.return_date": "Return date",
        "field.name": "Your name", "field.email": "Email address",
        "title.items": "Items", "title.configure": "Details of loan", "title.contact": "Contact",
        "title.review": "Review", "title.help": "Help",
    },
    "V1": {
        "act.begin": "Open an application", "act.add": "Pick", "act.remove": "Unpick",
        "act.advance": "Carry on", "act.advance_alt": "Carry on", "act.save": "Store",
        "act.edit": "Change", "act.commit": "Place application", "act.to_review": "Check application",
        "act.new": "Another application", "act.home": "Main page", "act.close": "Shut",
        "field.quantity": "How many", "field.purpose": "Reason", "field.return_date": "Due back",
        "field.name": "Full name", "field.email": "E-mail",
        "title.items": "Equipment", "title.configure": "Loan settings", "title.contact": "Who you are",
        "title.review": "Check", "title.help": "Guidance",
    },
    "VS": {  # common synonyms, no token shared with V0/V1
        "act.begin": "Initiate booking", "act.add": "Select", "act.remove": "Deselect",
        "act.advance": "Go forward", "act.advance_alt": "Go forward", "act.save": "Keep",
        "act.edit": "Modify", "act.commit": "Confirm booking", "act.to_review": "Inspect booking",
        "act.new": "Fresh booking", "act.home": "Landing", "act.close": "Dismiss",
        "field.quantity": "Count", "field.purpose": "Intention", "field.return_date": "Hand-in day",
        "field.name": "Surname and forename", "field.email": "Mailbox",
        "title.items": "Gear", "title.configure": "Booking options", "title.contact": "Correspondent",
        "title.review": "Summary", "title.help": "Assistance",
    },
    "VU1": {  # meaningful English, no token shared with V0/V1
        "act.begin": "Commence lodgement", "act.add": "Enlist", "act.remove": "Withdraw",
        "act.advance": "Onward", "act.advance_alt": "Onward", "act.save": "Retain",
        "act.edit": "Amend", "act.commit": "Lodge", "act.to_review": "Peruse",
        "act.new": "Recommence", "act.home": "Lobby", "act.close": "Conceal",
        "field.quantity": "Tally", "field.purpose": "Rationale", "field.return_date": "Deadline",
        "field.name": "Moniker", "field.email": "Inbox",
        "title.items": "Inventory", "title.configure": "Particulars", "title.contact": "Correspondence",
        "title.review": "Overview", "title.help": "Advice",
    },
    "VU2": {  # pronounceable non-words
        "act.begin": "Plimova", "act.add": "Brask", "act.remove": "Dresk",
        "act.advance": "Fenlow", "act.advance_alt": "Fenlow", "act.save": "Torvin",
        "act.edit": "Quillet", "act.commit": "Vadrim", "act.to_review": "Snorbel",
        "act.new": "Plimoven", "act.home": "Grindle", "act.close": "Mopt",
        "field.quantity": "Zarn", "field.purpose": "Klett", "field.return_date": "Hobwin",
        "field.name": "Trelly", "field.email": "Fizzon",
        "title.items": "Wubbet", "title.configure": "Glonk", "title.contact": "Pradda",
        "title.review": "Skeevo", "title.help": "Yarrom",
    },
}
#: Page text that is not a control label, shared by every profile.
COMMON_TEXT = {
    "act.decoy": TRAINING_DECOY_LABEL,
    "brand": "Northbank Makerspace", "title.home": "Equipment loans",
    "label.intro": "Borrow equipment from the makerspace for up to six months.",
    "label.chosen": "Chosen", "label.item": "Item", "label.deposit": "Deposit",
    "label.contact": "Contact", "label.done": "Done", "label.todo": "To do",
    "label.help": "Loans are free; a refundable deposit is charged per item.",
    "title.done": "Request received", "title.hub": "Your request",
    "purpose.study": "Study", "purpose.project": "Project", "purpose.event": "Event",
    "msg.one_item": "One item per request: remove the current item first.",
    "msg.date_invalid": "Choose a return date between tomorrow and the end of the year.",
    "msg.name_needed": "Tell us your name.", "msg.email_invalid": "That email address is not valid.",
    "msg.item_needed": "Choose an item first.", "msg.incomplete": "Finish every section first.",
    "msg.draft_kept": "Your progress is kept on this device.",
}
_TOKEN = re.compile(r"[a-z]+")


def tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def training_tokens() -> set[str]:
    return {tok for profile in ("V0", "V1") for key in CONTROL_KEYS for tok in tokens(PROFILES[profile][key])}


def overlap_report() -> dict:
    """Token overlap of each held-out profile's control labels with the training labels."""
    train = training_tokens()
    return {name: sorted({tok for key in CONTROL_KEYS for tok in tokens(PROFILES[name][key])} & train)
            for name in ("VS", "VU1", "VU2")}


# -- variants ----------------------------------------------------------------------------------

VARIANTS: list[dict] = [
    {"vocab": "V0", "structure": "S1", "decoy": False, "role": "training"},
    {"vocab": "V0", "structure": "S2", "decoy": False, "role": "training"},
    {"vocab": "V1", "structure": "S1", "decoy": False, "role": "training"},
    {"vocab": "V1", "structure": "S2", "decoy": False, "role": "training"},
    {"vocab": "VS", "structure": "S1", "decoy": False, "role": "held-out: vocabulary"},
    {"vocab": "VU1", "structure": "S1", "decoy": False, "role": "held-out: vocabulary"},
    {"vocab": "VU2", "structure": "S1", "decoy": False, "role": "held-out: vocabulary"},
    {"vocab": "V0", "structure": "S3", "decoy": False, "role": "held-out: structure"},
    {"vocab": "V0", "structure": "S4", "decoy": False, "role": "held-out: structure"},
    {"vocab": "V0", "structure": "S5", "decoy": False, "role": "held-out: structure"},
    {"vocab": "V0", "structure": "S1", "decoy": True, "role": "held-out: decoy"},
    {"vocab": "VU1", "structure": "S5", "decoy": True, "role": "held-out: combined"},
]
STRUCTURE_NAMES = {"S1": "linear multi-page wizard", "S2": "hub-and-spoke task list",
                   "S3": "single-page tabs", "S4": "modal-dialog flow",
                   "S5": "reordered stages (contact before configuration)"}


def variant_id(v: dict) -> str:
    return f"ctx_{v['vocab'].lower()}_{v['structure'].lower()}_d{int(v['decoy'])}"


# -- faults (identical in every variant) ------------------------------------------------------

PATCHES = [
    {"fault_id": "CTX-1", "filename": "assets/core.js",
     "find": "  state.quantity = parseInt(q, 10) || 1;\n  state.deposit = DEPOSIT * state.quantity;\n  save();",
     "replace": "  state.quantity = parseInt(q, 10) || 1;\n  save();",
     "why": "calculation error (stale state): the deposit is computed when an item is added and not recomputed when the quantity changes"},
    {"fault_id": "CTX-2", "filename": "assets/core.js",
     "find": "  if (!EMAIL.test(state.emailDraft)) { return notice(\"msg.email_invalid\"); }",
     "replace": "  if (!state.contactDone && !EMAIL.test(state.emailDraft)) { return notice(\"msg.email_invalid\"); }",
     "why": "constraint bypass: contact details re-saved after an edit from the review skip the email check"},
]


def _step(kind: str, ident: str, **params) -> dict:
    step = {"type": kind, "id": ident}
    if params:
        step["params"] = params
    return step


def traces(structure: str) -> dict[str, list]:
    """The scripted walks for one structure: the two faults' witnesses and benign walks."""
    start = [_step("CLICK", "start-request")]
    items = [_step("CLICK", "add-laser"), _step("CLICK", "advance-items")]
    config_q = [_step("SELECT", "quantity", option="3"),
                _step("TYPE", "return-date", category="valid_typical"), _step("CLICK", "save-config")]
    config = [_step("TYPE", "return-date", category="valid_typical"), _step("CLICK", "save-config")]
    contact = [_step("TYPE", "contact-name", category="valid_typical"),
               _step("TYPE", "contact-email", category="valid_typical"), _step("CLICK", "save-contact")]

    def flow(cfg: list) -> list:
        if structure == "S2":
            return (start + [_step("CLICK", "task-items")] + items + [_step("CLICK", "task-configure")] + cfg
                    + [_step("CLICK", "task-contact")] + contact + [_step("CLICK", "go-review")])
        if structure == "S5":
            return start + items + contact + cfg
        return start + items + cfg + contact

    to_review = flow(config)
    edit_email = [_step("CLICK", "edit-contact"), _step("TYPE", "contact-email", category="type_mismatch"),
                  _step("CLICK", "save-contact"), _step("CLICK", "commit")]
    return {
        "CTX-1": flow(config_q),
        "CTX-2": to_review + edit_email,
        "benign": [
            to_review + [_step("CLICK", "commit")],
            to_review + [_step("CLICK", "edit-config"), _step("SELECT", "purpose", option="project"),
                         _step("CLICK", "save-config"), _step("CLICK", "commit")],
            start + ([_step("CLICK", "task-items")] if structure == "S2" else [])
            + [_step("CLICK", "add-laser"), _step("CLICK", "add-printer"), _step("CLICK", "remove-laser"),
               _step("CLICK", "add-camera"), _step("CLICK", "advance-items")],
        ],
    }


def manifest(v: dict) -> dict:
    vid = variant_id(v)
    walk = traces(v["structure"])
    review_where = r"(review\.html|#review)$"
    done_where = r"(done\.html|#done)$"
    return {
        "app_id": vid,
        "name": "Northbank Makerspace loans",
        "entry": "index.html",
        "_note": "ctxbench variant for the contextual diagnostic; not part of the primary suite.",
        "regions": {"review_deposit": "#review-deposit", "done_contact": "#done-contact"},
        "faults": [
            {"fault_id": "CTX-1", "fault_class": "calculation_error",
             "description": "The deposit is computed when an item is added and not recomputed when the quantity changes, so the review shows the deposit for one item.",
             "trigger_condition": "Add an item, choose a quantity other than one, complete the details and contact stages, and open the review.",
             "expected_behaviour": "The review's deposit equals the quantity times the £12.00 deposit per item.",
             "observable_violation": "The review shows £12.00 for a quantity of 3 (expected £36.00).",
             "assertions": [{"kind": "numeric_equals", "where": review_where, "region": "review_deposit",
                             "expression": "quantity * 12",
                             "describes": "the deposit is £12 per item on loan"}],
             "reproduction": {"replays": 2, "must_all_reproduce": True},
             "observation_horizon": 0, "shortest_path": len(walk["CTX-1"]),
             "trigger_trace": walk["CTX-1"]},
            {"fault_id": "CTX-2", "fault_class": "constraint_bypass",
             "description": "Contact details re-saved after an edit from the review skip the email check, so an invalid address is committed with the request.",
             "trigger_condition": "Complete the request to the review, edit the contact details, enter an invalid email, save, and send the request.",
             "expected_behaviour": "A request is only ever committed with a valid email address.",
             "observable_violation": "The confirmation shows the contact email 'not-an-email'.",
             "assertions": [{"kind": "text_absent", "where": done_where, "region": "done_contact",
                             "expected": "not-an-email",
                             "describes": "a committed request never carries an invalid email address"}],
             "reproduction": {"replays": 2, "must_all_reproduce": True},
             "observation_horizon": 1, "shortest_path": len(walk["CTX-2"]),
             "trigger_trace": walk["CTX-2"]},
        ],
        "benign_traces": walk["benign"],
        "characterization": {"generator_seed": SEED, "vocab": v["vocab"], "structure": v["structure"],
                             "structure_name": STRUCTURE_NAMES[v["structure"]], "decoy": v["decoy"],
                             "role": v["role"]},
    }


# -- rendering ---------------------------------------------------------------------------------

def _shell(view: str, title: str) -> str:
    return f"""<!doctype html>
<html lang="en-GB"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><link rel="stylesheet" href="assets/style.css"></head>
<body data-view="{view}"><div id="app"></div>
<script src="assets/config.js"></script><script src="assets/core.js"></script></body></html>
"""


def render_variant(v: dict, dest: Path) -> None:
    clean = dest / "clean"
    if clean.exists():
        shutil.rmtree(clean)
    (clean / "assets").mkdir(parents=True)
    shutil.copyfile(TEMPLATES / "core.js", clean / "assets" / "core.js")
    shutil.copyfile(TEMPLATES / "style.css", clean / "assets" / "style.css")
    labels = {**COMMON_TEXT, **PROFILES[v["vocab"]]}
    config = {"vocab": v["vocab"], "structure": v["structure"], "decoy": v["decoy"], "labels": labels}
    (clean / "assets" / "config.js").write_text(
        "window.CTX_CONFIG = " + json.dumps(config, indent=2, ensure_ascii=False) + ";\n",
        encoding="utf-8", newline="\n")
    title = "Northbank Makerspace"
    if v["structure"] in ("S1", "S2", "S5"):
        views = ["index", "items", "configure", "contact", "review", "done", "help"]
        if v["structure"] == "S2":
            views.append("hub")
        for view in views:
            (clean / f"{view}.html").write_text(_shell("home" if view == "index" else view, title),
                                                encoding="utf-8", newline="\n")
    else:
        (clean / "index.html").write_text(_shell("home", title), encoding="utf-8", newline="\n")
    build = f'''"""Seeded faults for {variant_id(v)} (generated by web_testing_agent.ctxbench.generate)."""
from web_testing_agent.benchmark.builder import Patch, main

PATCHES = (
{"".join(f"    Patch(fault_id={p['fault_id']!r}, filename={p['filename']!r},{chr(10)}          find={p['find']!r},{chr(10)}          replace={p['replace']!r},{chr(10)}          why={p['why']!r}),{chr(10)}" for p in PATCHES)})

if __name__ == "__main__":
    raise SystemExit(main(__file__, PATCHES))
'''
    (dest / "build.py").write_text(build, encoding="utf-8", newline="\n")
    (dest / "faults.json").write_text(json.dumps(manifest(v), indent=2, ensure_ascii=False) + "\n",
                                      encoding="utf-8", newline="\n")


def generate(out_root: Path = OUT_ROOT) -> list[str]:
    problems = {k: v for k, v in overlap_report().items() if v}
    if problems:
        raise SystemExit(f"held-out profiles share tokens with the training labels: {problems}")
    out_root.mkdir(parents=True, exist_ok=True)
    ids = []
    for v in VARIANTS:
        dest = out_root / variant_id(v)
        dest.mkdir(parents=True, exist_ok=True)
        render_variant(v, dest)
        completed = subprocess.run([sys.executable, str(dest / "build.py")], capture_output=True, text=True,
                                   check=False)
        if completed.returncode != 0:
            raise SystemExit(f"{dest.name}: build failed: {completed.stdout}{completed.stderr}")
        ids.append(dest.name)
    (out_root / "README.md").write_text(
        "# ctxbench\n\nGenerated by `python -m web_testing_agent.ctxbench.generate`; do not edit by hand.\n"
        "Variants of one loan-request task for the contextual diagnostic "
        "(`docs/methodology/2026-10-04-contextual-diagnostic-preregistration.md`). "
        "Not part of the primary benchmark (`tests/fixtures/benchmark/`).\n\n"
        "| variant | role | vocabulary | structure | decoy |\n|---|---|---|---|---|\n"
        + "".join(f"| {variant_id(v)} | {v['role']} | {v['vocab']} | {v['structure']} ({STRUCTURE_NAMES[v['structure']]}) | {v['decoy']} |\n"
                  for v in VARIANTS)
        + f"\nToken overlap of held-out control labels with training labels: {overlap_report()}\n",
        encoding="utf-8", newline="\n")
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=OUT_ROOT)
    args = parser.parse_args()
    ids = generate(args.out)
    print(f"generated {len(ids)} variants into {args.out}: {ids}")
    print(f"token overlap with training labels: {overlap_report()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
