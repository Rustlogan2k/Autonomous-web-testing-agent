"""A fresh validation set for the page labeller (Stage 0, option A re-test).

The Stage 0 gate failed on the ctxbench probes, and the labeller was then revised on what
those errors showed. Re-testing on the same pages would reward fitting them, so the revised
labeller is gated on pages it has never seen: the six benchmark applications (development
data), reached by replaying every reachable prefix of each application's benign walks and
fault witnesses on its clean build. Distinct pages are kept by their value-free content.

Order, fixed by this module: `collect` writes the page snapshots; the ground truth is then
written by hand into `ground_truth.json` and committed; only after that is `evaluate` run.

    python -m web_testing_agent.hybrid.label_validation collect
    python -m web_testing_agent.hybrid.label_validation evaluate
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys

from ..benchmark.suite import discover, make_env, serve_app
from ..ctxbench import probes as probe_sets
from ..evaluation.scripted import ScriptedPolicy
from ..experiments.transfer import REPO_ROOT, git_state
from .abstraction import path_template
from .labels import control_identity, describe_controls, without_entered_values

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "v2_label_validation"
PAGES = FIXTURE / "pages.json.gz"
GROUND_TRUTH = FIXTURE / "ground_truth.json"
REPORT = REPO_ROOT / "reports" / "v2_label_validation.json"
MAX_PAGES_PER_APP = 12


def _walks(app) -> list[list[dict]]:
    manifest = app.manifest
    walks = [list(t) for t in (getattr(manifest, "benign_traces", None) or [])]
    walks += [list(f.trigger_trace) for f in manifest.faults if f.trigger_trace]
    return walks


def collect() -> dict:
    pages: list[dict] = []
    for app in discover():
        seen: set[str] = set()
        kept: list[dict] = []
        with serve_app(app, ("clean",)) as urls:
            env = make_env(urls["clean"], probe_sets.SETTINGS)
            try:
                for walk in _walks(app):
                    _, info = env.reset()
                    for number in range(len(walk) + 1):
                        page = info.get("page") or {}
                        content = path_template(page.get("url", "")) + without_entered_values(
                            page.get("html", "")
                        )
                        key = hashlib.sha256(content.encode("utf-8", "replace")).hexdigest()[:16]
                        if key not in seen:
                            seen.add(key)
                            specs = list(info.get("action_specs") or [])
                            kept.append(
                                {
                                    "page_id": f"{app.app_id}:{key}",
                                    "app_id": app.app_id,
                                    "context": app.manifest.name,
                                    "url": page.get("url", ""),
                                    "path": path_template(page.get("url", "")),
                                    "html": page.get("html", ""),
                                    "visible_text": page.get("visible_text", ""),
                                    "action_specs": [probe_sets.spec_to_dict(s) for s in specs],
                                    "controls": [
                                        {"identity": c.identity, "kind": c.kind, "line": c.line}
                                        for c in describe_controls(specs, page.get("html", ""))
                                    ],
                                }
                            )
                        if number == len(walk):
                            break
                        index = ScriptedPolicy._match(walk[number], info.get("action_specs") or [])  # noqa: SLF001
                        if index is None:
                            break  # the walk leaves what the clean build allows
                        _, _, terminated, truncated, info = env.step(index)
                        if terminated or truncated:
                            break
            finally:
                env.close()
        # Prefer coverage of distinct locations when an application offers many pages.
        kept.sort(key=lambda p: (p["path"], p["page_id"]))
        by_path: dict[str, list[dict]] = {}
        for page in kept:
            by_path.setdefault(page["path"], []).append(page)
        chosen: list[dict] = []
        while len(chosen) < MAX_PAGES_PER_APP and any(by_path.values()):
            for path in sorted(by_path):
                if by_path[path] and len(chosen) < MAX_PAGES_PER_APP:
                    chosen.append(by_path[path].pop(0))
        pages += chosen
        print(f"== {app.app_id}: {len(kept)} distinct pages, {len(chosen)} kept", flush=True)
    payload = {"git": git_state(), "pages": pages}
    FIXTURE.mkdir(parents=True, exist_ok=True)
    with gzip.GzipFile(PAGES, "wb", mtime=0) as handle:
        handle.write(json.dumps(payload, sort_keys=True).encode("utf-8"))
    return payload


def load_pages() -> list[dict]:
    with gzip.open(PAGES, "rb") as handle:
        return json.loads(handle.read().decode("utf-8"))["pages"]


def evaluate() -> dict:
    """Label every validation page with the current labeller and score it against the truth."""
    from .labels import SemanticLabeller

    truth = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    labeller = SemanticLabeller()
    kind_hits = kind_n = role_hits = role_n = field_hits = field_n = 0
    rows = []
    for page in load_pages():
        expected = truth["pages"][page["page_id"]]
        specs = [probe_sets.spec_from_dict(s) for s in page["action_specs"]]
        labels = labeller.label(
            page["url"],
            page["html"],
            specs,
            context=page["context"],
            visible_text=page["visible_text"],
        )
        if labels is None:
            rows.append({"page_id": page["page_id"], "error": "no labels"})
            continue
        kind_n += 1
        kind_ok = labels.kind in expected["kind"]
        kind_hits += kind_ok
        wrong: dict = {}
        scored: set[str] = set()  # a click and a rapid click share one control and one label
        for spec in specs:
            identity = control_identity(spec)
            want = expected["controls"].get(identity)
            if want is None or identity in scored:
                continue
            scored.add(identity)
            got = labels.roles.get(identity) or labels.meanings.get(identity)
            if identity in labels.roles:
                role_n += 1
                role_hits += got in want
            else:
                field_n += 1
                field_hits += got in want
            if got not in want:
                wrong[identity] = {"got": got, "want": sorted(want)}
        rows.append(
            {
                "page_id": page["page_id"],
                "kind": labels.kind,
                "kind_ok": kind_ok,
                "wrong_controls": wrong,
            }
        )
    return {
        "page_kind_accuracy": kind_hits / kind_n if kind_n else None,
        "role_accuracy": role_hits / role_n if role_n else None,
        "field_accuracy": field_hits / field_n if field_n else None,
        "pages": kind_n,
        "controls_scored": role_n,
        "fields_scored": field_n,
        "labeller": labeller.stats.to_dict(),
        "label_config": labeller.config(),
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("collect", "evaluate"))
    arguments = parser.parse_args(argv)
    if arguments.command == "collect":
        collect()
        return 0
    result = {"git": git_state(), **evaluate()}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "page_kind_accuracy",
                    "role_accuracy",
                    "field_accuracy",
                    "pages",
                    "controls_scored",
                )
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
