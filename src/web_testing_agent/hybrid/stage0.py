"""Stage 0 gate of the hybrid plan: are the LLM's page and control labels good enough to
build the tester's state from? And does the frozen progress score track true progress?

Measured on the ctxbench probe states (development data; plan §2), whose ground truth is
known by construction: every page is one of the workflow's views, every control has a
fixed DOM id with a fixed function, and the app's own counter gives true progress. Each
probe state is replayed on the clean build so the labeller sees the page exactly as the
tester would (rendered text included), then labelled and scored.

Fixed before the first run (this file is committed first):
- ground truth: `GT_PAGE`, `GT_ROLE`, `GT_FIELD` below (sets of acceptable answers where a
  view or control honestly fits more than one word); the decoy control has no true role and
  is reported separately;
- the gate: LLM labels are used if, on the four meaningful vocabularies (V0, V1, VS, VU1),
  page-kind accuracy >= 0.85, clickable-role accuracy >= 0.80 and cross-vocabulary
  agreement >= 0.80; otherwise the tester falls back to deterministic labels. VU2
  (pseudo-words) is reported, not gated: there, only structure can identify a control.

    python -m web_testing_agent.hybrid.stage0 --workers 3
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import numpy as np

from ..benchmark.suite import load_app, make_env, serve_app
from ..ctxbench import probes as probe_sets
from ..ctxbench.generate import OUT_ROOT
from ..ctxbench.runner import HELD_OUT, TRAINING
from ..experiments.transfer import REPO_ROOT, git_state
from .labels import SemanticLabeller, control_identity

GATE = {"page_kind_accuracy": 0.85, "role_accuracy": 0.80, "cross_vocabulary_agreement": 0.80}
MEANINGFUL = ("v0", "v1", "vs", "vu1")
OUT = REPO_ROOT / "reports" / "v2_stage0"

GT_PAGE: dict[str, set[str]] = {
    "home": {"home"},
    "hub": {"list", "home"},
    "items": {"list", "form"},
    "configure": {"form"},
    "contact": {"form"},
    "review": {"review"},
    "done": {"outcome"},
    "help": {"info"},
}
#: DOM id (or id prefix ending in "-") -> acceptable roles. `None`: no true role (the decoy).
GT_ROLE: dict[str, set[str] | None] = {
    "start-request": {"open", "advance"},
    "add-": {"add"},
    "remove-": {"remove"},
    "advance-items": {"advance"},
    "save-config": {"save", "advance"},
    "save-contact": {"save", "advance"},
    "edit-config": {"edit"},
    "edit-contact": {"edit"},
    "commit": {"commit"},
    "go-review": {"advance", "navigate", "open"},
    "task-": {"open", "navigate"},
    "tab-": {"navigate", "open"},
    "help-link": {"help"},
    "close-dialog": {"back"},
    "new-request": {"open"},
    "decoy-advance": None,
}
GT_FIELD: dict[str, set[str]] = {
    "quantity": {"number", "choice"},
    "purpose": {"choice"},
    "return-date": {"date"},
    "contact-name": {"name"},
    "contact-email": {"email"},
}
CONTEXT = "Northbank Makerspace loans"


def view_of(url: str) -> str:
    parsed = urlparse(url)
    if parsed.fragment:
        return parsed.fragment
    name = Path(parsed.path).name.removesuffix(".html")
    return "home" if name in ("", "index") else name


def truth_for(dom_id: str, table: dict) -> tuple[bool, set[str] | None]:
    """(known, acceptable answers) for a DOM id, by exact id or by prefix."""
    if dom_id in table:
        return True, table[dom_id]
    for key, value in table.items():
        if key.endswith("-") and dom_id.startswith(key):
            return True, value
    return False, None


def label_variant(app_id: str) -> dict:
    """Replay, label and score every probe state of one variant."""
    from ..reward.progress import ProgressScorer

    app = load_app(app_id, OUT_ROOT)
    probes = probe_sets.load(app_id)
    labeller, scorer = SemanticLabeller(), ProgressScorer()
    rows = []
    with serve_app(app, ("clean",)) as urls:
        env = make_env(urls["clean"], probe_sets.SETTINGS)
        try:
            for state in probes["states"]:
                _, _, info = probe_sets._replay(env, state["prefix"])  # noqa: SLF001
                page = info.get("page") or {}
                specs = list(info.get("action_specs") or [])
                labels = labeller.label(
                    page.get("url", ""),
                    page.get("html", ""),
                    specs,
                    context=CONTEXT,
                    visible_text=page.get("visible_text"),
                )
                judgment = scorer.potential(
                    page.get("url", ""),
                    page.get("html", ""),
                    context=CONTEXT,
                    visible_text=page.get("visible_text"),
                )
                controls = {}
                for spec in specs:
                    identity = control_identity(spec)
                    dom = identity[5:-2] if identity.startswith('[id="') else ""
                    if dom and labels is not None and dom not in controls:
                        controls[dom] = labels.roles.get(identity) or labels.meanings.get(identity)
                rows.append(
                    {
                        "probe_id": state["probe_id"],
                        "trace": state["trace"],
                        "prefix_length": state["prefix_length"],
                        "view": view_of(page.get("url", "")),
                        "progress": state["progress"],
                        "kind": labels.kind if labels else None,
                        "controls": controls,
                        "phi": judgment.value if judgment else None,
                        "label_source": labels.source if labels else None,
                        "deterministic": list(labels.deterministic) if labels else [],
                    }
                )
        finally:
            env.close()
    return {
        "app_id": app_id,
        "rows": rows,
        "labeller": labeller.stats.to_dict(),
        "scorer": scorer.stats.to_dict(),
    }


def _vocab(app_id: str) -> str:
    return app_id.split("_")[1]


def _concordance(phi: list[float], truth: list[int]) -> float | None:
    """P(phi orders a pair of states the way true progress does), ties in phi count half."""
    good = total = 0.0
    for i in range(len(phi)):
        for j in range(len(phi)):
            if truth[i] < truth[j]:
                total += 1
                good += 1.0 if phi[i] < phi[j] else 0.5 if phi[i] == phi[j] else 0.0
    return good / total if total else None


def score(results: list[dict]) -> dict:
    per_variant: dict[str, dict] = {}
    pooled: dict[str, list] = {"kind": [], "role": [], "field": []}
    decoys: dict[str, int] = {}
    for result in results:
        app_id, vocab = result["app_id"], _vocab(result["app_id"])
        kind_hits = role_hits = field_hits = 0
        kind_n = role_n = field_n = 0
        for row in result["rows"]:
            if row["kind"] is not None and row["view"] in GT_PAGE:
                kind_n += 1
                kind_hits += row["kind"] in GT_PAGE[row["view"]]
            for dom, answer in row["controls"].items():
                if answer is None:
                    continue
                known, ok = truth_for(dom, GT_ROLE)
                if known and ok is None:
                    decoys[answer] = decoys.get(answer, 0) + 1
                elif known:
                    role_n += 1
                    role_hits += answer in ok
                else:
                    known, ok = truth_for(dom, GT_FIELD)
                    if known:
                        field_n += 1
                        field_hits += answer in ok
        phis = [r["phi"] for r in result["rows"] if r["phi"] is not None]
        truth = [r["progress"] for r in result["rows"] if r["phi"] is not None]
        per_variant[app_id] = {
            "states": len(result["rows"]),
            "page_kind_accuracy": kind_hits / kind_n if kind_n else None,
            "role_accuracy": role_hits / role_n if role_n else None,
            "field_accuracy": field_hits / field_n if field_n else None,
            "phi_progress_concordance": _concordance(phis, truth),
            "labeller": result["labeller"],
            "scorer": result["scorer"],
        }
        if vocab in MEANINGFUL:
            pooled["kind"].append((kind_hits, kind_n))
            pooled["role"].append((role_hits, role_n))
            pooled["field"].append((field_hits, field_n))

    def rate(pairs):
        hits, n = sum(p[0] for p in pairs), sum(p[1] for p in pairs)
        return hits / n if n else None

    agreement = cross_vocabulary_agreement(results)
    meaningful = {
        "page_kind_accuracy": rate(pooled["kind"]),
        "role_accuracy": rate(pooled["role"]),
        "field_accuracy": rate(pooled["field"]),
        "cross_vocabulary_agreement": agreement["meaningful"],
    }
    passed = all(meaningful[k] is not None and meaningful[k] >= v for k, v in GATE.items())
    return {
        "gate": GATE,
        "meaningful_vocabularies": meaningful,
        "gate_passed": passed,
        "decision": "llm labels" if passed else "deterministic labels",
        "cross_vocabulary": agreement,
        "decoy_labelled_as": decoys,
        "per_variant": per_variant,
    }


def cross_vocabulary_agreement(results: list[dict]) -> dict:
    """Same state, same controls, different words: do the labels agree with V0's?

    Over the S1/D0 variants, which share one structure; states are aligned by the walk and
    prefix that reached them, controls by DOM id.
    """
    by_variant = {
        r["app_id"]: {(row["trace"], row["prefix_length"]): row for row in r["rows"]}
        for r in results
        if r["app_id"].endswith("_s1_d0")
    }
    reference = by_variant.get("ctx_v0_s1_d0")
    out: dict = {}
    if reference is None:
        return {"meaningful": None}
    for app_id, rows in sorted(by_variant.items()):
        if app_id == "ctx_v0_s1_d0":
            continue
        same = total = 0
        for key, ref in reference.items():
            row = rows.get(key)
            if row is None or ref["kind"] is None or row["kind"] is None:
                continue
            total += 1
            same += row["kind"] == ref["kind"]
            for dom, answer in ref["controls"].items():
                if dom in row["controls"] and answer is not None:
                    total += 1
                    same += row["controls"][dom] == answer
        out[app_id] = same / total if total else None
    values = [v for k, v in out.items() if _vocab(k) in MEANINGFUL and v is not None]
    out["meaningful"] = float(np.mean(values)) if values else None
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--out", type=Path, default=OUT)
    arguments = parser.parse_args(argv)
    variants = TRAINING + HELD_OUT
    results = []
    with ProcessPoolExecutor(max_workers=arguments.workers) as pool:
        futures = {pool.submit(label_variant, v): v for v in variants}
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(
                f"== {result['app_id']}: {len(result['rows'])} states, "
                f"labeller {result['labeller']}",
                flush=True,
            )
    results.sort(key=lambda r: r["app_id"])
    payload = {"git": git_state(), "summary": score(results), "results": results}
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.with_suffix(".json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    summary = payload["summary"]
    print(
        json.dumps(
            {
                k: summary[k]
                for k in (
                    "meaningful_vocabularies",
                    "gate_passed",
                    "decision",
                    "cross_vocabulary",
                    "decoy_labelled_as",
                )
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
