"""Structural, workflow, vocabulary and implementation diversity across the suite.

Diversity is measured, not asserted: from each manifest's declared architecture, workflow
graph and features, from the vocabulary files, and from the source itself (how much code
two apps share), so "eight different applications" is a checked property.
"""

from __future__ import annotations

import itertools
import re

from .manifest import AppManifest

WORKFLOW_KINDS = ("linear-wizard", "hub-and-spoke", "branching", "modal-heavy", "revisit",
                  "state-machine", "nested-form", "data-grid")
FEATURES = ("modal", "tabs", "nested-form", "conditional-section", "table", "filter", "sort",
            "bulk-operation", "cross-page-state", "client-state", "server-state", "spa",
            "server-rendered", "fragment-update", "dynamic-validation", "enable-disable",
            "multi-role", "reload-persistence")
_TOKEN = re.compile(r"[a-z]+")
#: What counts as implementation: code, markup, styles and (suite v2) SQL. Authentication and
#: database plumbing are written per app, so they fall under the same 15% shared-line rule.
SHINGLED_SUFFIXES = {".py", ".js", ".mjs", ".html", ".css", ".sql"}


def _shingles(app: AppManifest) -> set[str]:
    """Normalised non-trivial source lines, the unit of 'shared implementation'."""
    lines = set()
    for path in (app.root / "src").rglob("*"):
        if path.is_file() and path.suffix in SHINGLED_SUFFIXES:
            for line in path.read_text(encoding="utf-8").splitlines():
                norm = " ".join(line.split())
                if len(norm) >= 25 and not norm.startswith(("#", "//", "/*", "*", "--")):
                    lines.add(norm)
    return lines


def _labels(app: AppManifest, profile: str) -> set[str]:
    table = app.vocab(profile)
    return {w for k, v in table.items() if k.startswith("act.") for w in _TOKEN.findall(str(v).lower())}


def analyse(apps: list[AppManifest]) -> dict:
    rows = {}
    for app in apps:
        d = app.data
        graph = d.get("workflow_graph", {})
        rows[app.app_id] = {
            "architecture": d.get("architecture"), "stack": d.get("stack"),
            "persistence": d.get("persistence"),
            "workflow_kinds": sorted(graph.get("kind", [])),
            "views": len(graph.get("nodes", [])), "transitions": len(graph.get("edges", [])),
            "features": sorted(d.get("features", [])),
            "faults": len(d.get("faults", [])),
            "fault_subtypes": sorted({f.get("subtype") for f in d.get("faults", [])}),
            "longest_witness": max((len(f.get("trigger_trace", [])) for f in d.get("faults", [])),
                                   default=0),
            "dockerfile": app.has_dockerfile,
        }
    problems = []
    triples = {}
    for app_id, row in rows.items():
        key = (row["architecture"], row["stack"], row["persistence"])
        triples.setdefault(key, []).append(app_id)
    for key, ids in triples.items():
        if len(ids) > 1:
            problems.append(f"{ids} share architecture/stack/persistence {key}")
    uncovered_kinds = [k for k in WORKFLOW_KINDS if not any(k in r["workflow_kinds"] for r in rows.values())]
    uncovered_features = [f for f in FEATURES if not any(f in r["features"] for r in rows.values())]
    if uncovered_kinds:
        problems.append(f"workflow kinds no app exercises: {uncovered_kinds}")
    if uncovered_features:
        problems.append(f"features no app exercises: {uncovered_features}")

    shingles = {a.app_id: _shingles(a) for a in apps}
    shared = {}
    for a, b in itertools.combinations(sorted(shingles), 2):
        union = shingles[a] | shingles[b]
        jaccard = len(shingles[a] & shingles[b]) / len(union) if union else 0.0
        shared[f"{a}~{b}"] = round(jaccard, 3)
        if jaccard > 0.15:
            problems.append(f"{a} and {b} share {jaccard:.0%} of their source lines")

    vocab_overlap = {}
    labels = {a.app_id: _labels(a, "default") for a in apps}
    for a, b in itertools.combinations(sorted(labels), 2):
        union = labels[a] | labels[b]
        vocab_overlap[f"{a}~{b}"] = round(len(labels[a] & labels[b]) / len(union), 3) if union else 0.0
    profile_overlap = {a.app_id: round(len(_labels(a, "default") & _labels(a, "alt"))
                                       / max(1, len(_labels(a, "default") | _labels(a, "alt"))), 3)
                       for a in apps}
    return {
        "apps": rows,
        "workflow_kind_matrix": {k: [i for i, r in rows.items() if k in r["workflow_kinds"]]
                                 for k in WORKFLOW_KINDS},
        "feature_matrix": {f: [i for i, r in rows.items() if f in r["features"]] for f in FEATURES},
        "source_line_jaccard": shared,
        "max_source_line_jaccard": max(shared.values(), default=0.0),
        "action_vocab_jaccard_between_apps": vocab_overlap,
        "action_vocab_jaccard_default_vs_alt": profile_overlap,
        "problems": problems,
        "passed": not problems,
    }
