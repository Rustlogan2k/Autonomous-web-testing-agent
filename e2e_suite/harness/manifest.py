"""Loading and checking an application's `app.json`.

The manifest carries everything the harness needs: how to start the app, its workflow
graph, workflows, state-dependent actions, decoys, regions and seeded faults. The fault
section uses the primary oracle's own `FaultSpec` fields so the existing grammar verifies
those faults unchanged (`BenchmarkApp.from_dict`, imported read-only); faults that need the
suite's extension grammar are kept apart and verified by `ext_oracle`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from web_testing_agent.oracle.faults import AssertionKind, BenchmarkApp, FaultClass

SUITE_ROOT = Path(__file__).resolve().parents[1]
APPS_ROOT = SUITE_ROOT / "apps"
BUILD_ROOT = SUITE_ROOT / ".build"
ADMISSION_ROOT = SUITE_ROOT / "admission"

TRACE_TYPES = {"CLICK", "TYPE", "SELECT", "REFRESH", "BROWSER_BACK", "BROWSER_FORWARD",
               "RAPID_CLICK", "NO_OP", "SCROLL"}
ORACLES = {"existing", "extension"}
SUBTYPES = {
    "calculation", "stale-state", "missing-validation", "incorrect-navigation",
    "state-transition", "form-submission", "conditional-ui", "persistence", "cross-page",
    "displayed-vs-submitted", "workflow-completion", "wrong-target",
}
#: Extension assertion kinds (`ext_oracle`); everything else must be a primary kind.
EXTENSION_KINDS = {"region_stable_across_reload", "input_absent", "region_arithmetic"}


@dataclass
class Patch:
    fault_id: str
    file: str
    find: str
    replace: str


@dataclass
class AppManifest:
    root: Path
    data: dict
    patches: list[Patch] = field(default_factory=list)

    @property
    def app_id(self) -> str:
        return self.data["app_id"]

    @property
    def faults(self) -> list[dict]:
        return list(self.data.get("faults", []))

    def faults_by_oracle(self, oracle: str) -> list[dict]:
        return [f for f in self.faults if f.get("oracle", "existing") == oracle]

    def oracle_app(self) -> BenchmarkApp:
        """The primary oracle's view of this app: only the faults it can verify."""
        return BenchmarkApp.from_dict({
            "app_id": self.app_id,
            "name": self.data.get("name", self.app_id),
            "entry": self.data.get("entry", "/"),
            "regions": self.data.get("regions", {}),
            "faults": self.faults_by_oracle("existing"),
            "benign_traces": self.data.get("benign_traces", []),
        })

    @property
    def has_dockerfile(self) -> bool:
        return (self.root / "Dockerfile").is_file()

    def vocab(self, profile: str) -> dict:
        return json.loads((self.root / "vocab" / f"{profile}.json").read_text(encoding="utf-8"))


def load(app_id_or_path: str | Path) -> AppManifest:
    root = Path(app_id_or_path)
    if not root.is_dir():
        root = APPS_ROOT / str(app_id_or_path)
    data = json.loads((root / "app.json").read_text(encoding="utf-8"))
    patches = [Patch(f["fault_id"], f["patch"]["file"], f["patch"]["find"], f["patch"]["replace"])
               for f in data.get("faults", []) if f.get("patch")]
    return AppManifest(root=root, data=data, patches=patches)


def discover() -> list[AppManifest]:
    """Every app of this suite, in app_id order. Reads only `e2e_suite/apps/*/app.json`."""
    if not APPS_ROOT.is_dir():
        return []
    return [load(p) for p in sorted(APPS_ROOT.iterdir()) if (p / "app.json").is_file()]


def _check_trace(trace: list, where: str, problems: list[str]) -> None:
    if not isinstance(trace, list):
        problems.append(f"{where}: trace is not a list")
        return
    for index, step in enumerate(trace):
        if not isinstance(step, dict) or step.get("type") not in TRACE_TYPES:
            problems.append(f"{where}[{index}]: bad step {step!r}")
        elif step["type"] in ("CLICK", "TYPE", "SELECT", "RAPID_CLICK") and not (
                step.get("id") or step.get("text") or step.get("selector")):
            problems.append(f"{where}[{index}]: {step['type']} names no control")


def validate_manifest(app: AppManifest) -> list[str]:
    """Structural problems; empty means well formed. Never raises."""
    problems: list[str] = []
    d = app.data
    for key in ("app_id", "name", "domain", "architecture", "stack", "persistence", "start",
                "workflow_graph", "features", "regions", "workflows", "faults",
                "benign_traces", "decoys", "state_dependent"):
        if key not in d:
            problems.append(f"missing key {key!r}")
    if d.get("app_id") != app.root.name:
        problems.append(f"app_id {d.get('app_id')!r} does not match directory {app.root.name!r}")
    if (app.root / "faults.json").exists():
        problems.append("contains faults.json, a name the primary benchmark discovers")
    for profile in ("default", "alt"):
        if not (app.root / "vocab" / f"{profile}.json").is_file():
            problems.append(f"missing vocab/{profile}.json")
    if bool(d.get("dockerfile", True)) != app.has_dockerfile:
        problems.append(f"manifest says dockerfile={d.get('dockerfile', True)} but the file "
                        f"{'exists' if app.has_dockerfile else 'is missing'}")
    for w in d.get("workflows", []):
        _check_trace(w.get("trace"), f"workflow {w.get('name')}", problems)
        if not w.get("expect"):
            problems.append(f"workflow {w.get('name')}: declares no expectation")
    for s in d.get("state_dependent", []):
        for side in ("useless", "useful"):
            part = s.get(side) or {}
            _check_trace(part.get("trace", []) + [part.get("action") or {}],
                         f"state_dependent {s.get('name')}.{side}", problems)
    for decoy in d.get("decoys", []):
        _check_trace(decoy.get("trace", []) + [decoy.get("decoy") or {}],
                     f"decoy {decoy.get('name')}", problems)
    for trace_index, trace in enumerate(d.get("benign_traces", [])):
        _check_trace(trace, f"benign_traces[{trace_index}]", problems)
    seen: set[str] = set()
    for f in d.get("faults", []):
        fid = f.get("fault_id", "?")
        if fid in seen:
            problems.append(f"duplicate fault id {fid}")
        seen.add(fid)
        try:
            FaultClass(f.get("fault_class"))
        except ValueError:
            problems.append(f"{fid}: unknown fault_class {f.get('fault_class')!r}")
        if f.get("subtype") not in SUBTYPES:
            problems.append(f"{fid}: unknown subtype {f.get('subtype')!r}")
        oracle = f.get("oracle", "existing")
        if oracle not in ORACLES:
            problems.append(f"{fid}: oracle must be existing or extension")
        for a in f.get("assertions", []):
            kind = a.get("kind")
            if oracle == "existing":
                try:
                    AssertionKind(kind)
                except ValueError:
                    problems.append(f"{fid}: {kind!r} is not a primary assertion kind")
            elif kind not in EXTENSION_KINDS and kind not in {k.value for k in AssertionKind}:
                problems.append(f"{fid}: unknown assertion kind {kind!r}")
            named = [a.get("region")] + list((a.get("operands") or {}).values()) +                 [(a.get("guard") or {}).get("region")]
            for region in filter(None, named):
                if region not in d.get("regions", {}):
                    problems.append(f"{fid}: region {region!r} not defined")
        if not f.get("patch"):
            problems.append(f"{fid}: no patch")
        if not f.get("trigger_trace"):
            problems.append(f"{fid}: no trigger_trace")
        _check_trace(f.get("trigger_trace", []), f"{fid}.trigger_trace", problems)
    for name, selector in d.get("regions", {}).items():
        if not (selector.startswith("#") or selector.startswith('[id="')):
            problems.append(f"region {name}: use an #id selector, got {selector!r}")
    return problems
