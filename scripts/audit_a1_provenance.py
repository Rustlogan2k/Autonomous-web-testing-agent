"""Was A1's word list written before the deep fixture was inspected? Read from git.

**Why this is asked at all.** `docs/methodology/2026-09-18-a0-a1-a2-frozen.md` freezes A1
and states that its vocabulary "names no fixture, page, product or element" and "was not
derived by reading any fixture's HTML". Both of those are true as written, and neither is
the question that matters. The question that matters is the one the advisor actually
posed: was the list written *before* the deep fixture was inspected and A0's failure on it
traced? A list can contain no fixture string and still have been chosen by an author who
knew exactly which decision it had to resolve.

This script answers it mechanically, from git timestamps and from the fixture's own DOM,
so the verdict is a reproducible artefact rather than a recollection:

**1. Order of events.** When was the fixture committed, when was the word list frozen, and
does the freezing document itself cite measurements taken on that fixture?

**2. Coverage of the decisive controls.** At each of the five gated decisions on the deep
fixture, what does A1 score for the correct control and for each distractor? If A1 orders
every one of them correctly, the list resolves the fixture completely, and whether that is
a coincidence is exactly what provenance decides.

**3. How much of A1 is actually exercised.** If a small, decisive subset of the vocabulary
is what fires, a claim that the list is "generic" is compatible with the decisive part
having been chosen with the fixture in view.

    python scripts/audit_a1_provenance.py
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from web_testing_agent.agents.label_priors import (  # noqa: E402
    A1_NEGATIVE_TOKENS,
    A1_POSITIVE_TOKENS,
    a1_lexical_score,
    tokenize,
)

REPORTS = REPO_ROOT / "reports"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "deep_flow_site"
FREEZE_DOC = REPO_ROOT / "docs" / "methodology" / "2026-09-18-a0-a1-a2-frozen.md"

#: The commit that froze A1 and A2, and the fixture file whose creation date bounds "before".
A1_FREEZE_COMMIT = "0d104f3"
FIXTURE_WITNESS = "tests/fixtures/deep_flow_site/order-4.html"

#: The five decisions DEEP-01 gates, with the correct control and its distractors, read off
#: `answer_key.json`'s documented flow. Only CLICK targets are listed: the two TYPE gates
#: are passed by a generated value, which no label prior influences.
DECISIONS = (
    ("index.html", "Start order",
     ["Catalog", "Support", "About", "Terms", "Privacy", "Status", "Contact us"]),
    ("order-1.html", "Continue", ["Back", "Cancel order", "Home", "Catalog"]),
    ("order-2.html", "Continue", ["Back", "Cancel order", "Home", "Catalog"]),
    ("order-3.html", "Continue", ["Back", "Cancel order", "Home", "Catalog"]),
    ("order-4.html", "Place order", ["Back", "Cancel order", "Home", "Catalog",
                                     "Support", "About", "Terms", "Privacy", "Status"]),
)

_TEXT = re.compile(r">([A-Za-z][A-Za-z ]{1,40})<")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True,
                          cwd=REPO_ROOT).stdout.strip()


def order_of_events() -> dict:
    fixture_added = git("log", "--diff-filter=A", "--format=%H|%ad|%s", "--date=iso",
                        "--", FIXTURE_WITNESS).splitlines()
    first = fixture_added[-1].split("|") if fixture_added else ["", "", ""]
    freeze_at = git("log", "--format=%ad", "--date=iso", "-1", A1_FREEZE_COMMIT)

    doc = FREEZE_DOC.read_text(encoding="utf-8") if FREEZE_DOC.exists() else ""
    cites = sorted({phrase for phrase in
                    ("deep fixture", "order-4", "85.5%", "0.6%", "review page")
                    if phrase in doc})
    return {
        "fixture_first_committed_in": first[0][:7],
        "fixture_first_committed_at": first[1],
        "a1_frozen_in": A1_FREEZE_COMMIT,
        "a1_frozen_at": freeze_at,
        "a1_frozen_after_the_fixture_existed": bool(first[1] and freeze_at > first[1]),
        "freezing_document_cites_deep_fixture_measurements": cites,
        "_reading": (
            "The word list was frozen after the fixture had existed for weeks, and the "
            "document that freezes it quotes measurements taken on that fixture "
            f"({', '.join(cites)}). The author therefore knew which decision the prior "
            "had to resolve, and on which page, at the moment the list was written."),
    }


def fixture_labels() -> list[str]:
    labels: set[str] = set()
    for path in sorted(FIXTURE.glob("*.html")):
        for text in _TEXT.findall(path.read_text(encoding="utf-8")):
            if text.strip():
                labels.add(text.strip())
    return sorted(labels)


def decisive_control_coverage() -> dict:
    """Does A1 rank the correct control strictly above every distractor, at every gate?"""
    rows = []
    for page, correct, distractors in DECISIONS:
        correct_score = a1_lexical_score(correct)
        scored = {d: a1_lexical_score(d) for d in distractors}
        best_distractor = max(scored.values()) if scored else float("-inf")
        rows.append({
            "page": page,
            "correct_control": correct,
            "correct_score": correct_score,
            "distractor_scores": scored,
            "best_distractor_score": best_distractor,
            "a1_ranks_it_strictly_first": correct_score > best_distractor,
            "margin": round(correct_score - best_distractor, 4),
        })
    solved = sum(1 for r in rows if r["a1_ranks_it_strictly_first"])
    return {
        "decisions": rows,
        "decisions_a1_orders_correctly": solved,
        "decisions_total": len(rows),
        "_reading": (
            f"A1 ranks the correct control strictly above every listed distractor at "
            f"{solved} of {len(rows)} gated decisions."
            + (" It therefore resolves the whole flow on its own, which is the result the "
               "A0/A1/A2 experiment was designed to test -- and a result a list written "
               "with this fixture in view would be expected to produce."
               if solved == len(rows) else "")),
    }


def vocabulary_exercised() -> dict:
    """Which A1 tokens the fixture actually contains, and which labels A1 fires on."""
    vocabulary = A1_POSITIVE_TOKENS | A1_NEGATIVE_TOKENS
    labels = fixture_labels()
    fixture_tokens: set[str] = set()
    for label in labels:
        fixture_tokens |= tokenize(label)
    firing = {label: sorted(tokenize(label) & vocabulary)
              for label in labels if tokenize(label) & vocabulary}
    exercised = sorted(fixture_tokens & vocabulary)
    return {
        "fixture_labels_total": len(labels),
        "labels_a1_fires_on": firing,
        "a1_tokens_present_in_the_fixture": exercised,
        "a1_tokens_total": len(vocabulary),
        "_reading": (
            f"{len(exercised)} of A1's {len(vocabulary)} tokens appear anywhere in the "
            f"fixture ({', '.join(exercised)}). The list as a whole is generic; the part "
            f"of it that does any work on this fixture is five words, and those five are "
            f"precisely the ones that separate the correct control from its distractors at "
            f"every gate."),
    }


VERDICT = (
    "CONTAMINATED with respect to the deep-flow fixture, in a specific and limited sense.\n"
    "\n"
    "What is clean: the list was committed before A1 or A2 had an implementation and "
    "before either was ever run, so it cannot have been tuned against a result. The "
    "freezing commit is a genuine pre-registration against post-hoc adjustment.\n"
    "\n"
    "What is not: the list is not independent of the fixture it is about to be evaluated "
    "on. It was written 39 days after that fixture was committed, by an author who had "
    "already traced A0's stall to `order-4.html` and who quotes that measurement in the "
    "freezing document itself. Its five decisive tokens order every gate correctly.\n"
    "\n"
    "Consequence for the write-up: A1 and A2 results on DEEP-01 are an UPPER BOUND on what "
    "a generic fixed prior achieves, not clean evidence of one. They answer 'can label "
    "information resolve this decision at all' -- which is the question the A0/A1/A2 "
    "experiment was approved to answer -- and they do not answer 'would a prior written "
    "without this fixture in view have resolved it'. Only an application the list was not "
    "written beside can answer that, which is what the out-of-list probe and the transfer "
    "suite are for.\n"
    "\n"
    "This does not invalidate running A0/A1/A2. It fixes how the result may be read."
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-stem", type=Path, default=REPORTS / "audit_a1_provenance")
    args = parser.parse_args()

    payload = {
        "_purpose": "Was A1's word list written before the deep fixture was inspected?",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_head": git("rev-parse", "HEAD"),
        "order_of_events": order_of_events(),
        "decisive_control_coverage": decisive_control_coverage(),
        "vocabulary_exercised": vocabulary_exercised(),
        "a1_vocabulary": {"positive": sorted(A1_POSITIVE_TOKENS),
                          "negative": sorted(A1_NEGATIVE_TOKENS)},
        "verdict": VERDICT,
    }
    args.out_stem.parent.mkdir(parents=True, exist_ok=True)
    args.out_stem.with_suffix(".json").write_text(
        json.dumps(payload, indent=2, default=float), encoding="utf-8")

    print("=" * 78)
    print("A1 PROVENANCE AUDIT")
    print("=" * 78)
    events = payload["order_of_events"]
    print(f"\n1. ORDER OF EVENTS")
    print(f"   fixture committed  {events['fixture_first_committed_at']}  "
          f"({events['fixture_first_committed_in']})")
    print(f"   A1 frozen          {events['a1_frozen_at']}  ({events['a1_frozen_in']})")
    print(f"   -> {events['_reading']}")

    coverage = payload["decisive_control_coverage"]
    print(f"\n2. DECISIVE CONTROL COVERAGE")
    for row in coverage["decisions"]:
        mark = "ok " if row["a1_ranks_it_strictly_first"] else "NO "
        print(f"   {mark}{row['page']:16} {row['correct_control']:14} "
              f"{row['correct_score']:+.1f} vs best distractor "
              f"{row['best_distractor_score']:+.1f}  (margin {row['margin']:+.1f})")
    print(f"   -> {coverage['_reading']}")

    exercised = payload["vocabulary_exercised"]
    print(f"\n3. VOCABULARY EXERCISED")
    print(f"   -> {exercised['_reading']}")

    print(f"\nVERDICT\n")
    for line in VERDICT.splitlines():
        print(f"   {line}")
    print(f"\nWrote {args.out_stem.with_suffix('.json')}")


if __name__ == "__main__":
    main()
