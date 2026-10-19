"""Loading and checking an application's `app.json`.

The manifest carries everything the harness needs: how to start the app, its workflow
graph, workflows, state-dependent actions, decoys, regions and seeded faults. The fault
section uses the primary oracle's own `FaultSpec` fields so the existing grammar verifies
those faults unchanged (`BenchmarkApp.from_dict`, imported read-only); faults that need the
suite's extension grammar are kept apart and verified by `ext_oracle`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from web_testing_agent.envs.input_values import generate_input_value
from web_testing_agent.envs.types import InputValueCategory
from web_testing_agent.oracle.faults import AssertionKind, BenchmarkApp, FaultClass

#: The suite this harness admits. `WTA_SUITE_ROOT` points it at another suite laid out the same
#: way (the v2 validation set in `validation_suite/`), so one harness admits both.
SUITE_ROOT = (Path(os.environ["WTA_SUITE_ROOT"]).resolve() if os.environ.get("WTA_SUITE_ROOT")
              else Path(__file__).resolve().parents[1])
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
#: Suite v2 database engines and the session isolation each may declare (plan §2, D1).
ISOLATION = {"postgres": {"schema-per-session"},
             "mongodb": {"database-per-session", "tenant-field"}}
#: The data-model probes each engine's manifest may declare, and the kinds it must cover:
#: PostgreSQL refuses them with SQLSTATE 23503 / 23514 / 23505, MongoDB with 121 / 121 / 11000.
PROBE_KINDS = {"postgres": {"foreign_key", "check", "unique"},
               "mongodb": {"required", "enum", "duplicate", "validation"}}
REQUIRED_PROBE_KINDS = {"postgres": {"foreign_key", "check", "unique"},
                        "mongodb": {"required", "enum", "duplicate"}}
MIN_PROBES = 3
MIN_MONGO_REFERENCES = 4
#: Every HTML input type the tester may type into: the credentials must equal none of the
#: values it generates for any of them (decision D4), or exploration could sign in again.
TYPABLE_INPUT_TYPES = ("text", "email", "number", "tel", "url", "password", "date", "search",
                       "textarea", "datetime-local", "month", "time", "week", "color", "range")
#: Plan §8 (the enlarged development apps): the interaction patterns an app may claim, and
#: the minimum breadth it must declare in its `breadth` block and exercise at admission.
PATTERNS = {"pagination", "sort-filter", "search", "modal", "tabs", "inline-edit", "wizard",
            "reorder", "bulk-action", "undo", "client-routing"}
CRUD = ("create", "read", "update", "delete")
MIN_VIEWS = 12
MIN_CRUD_ENTITIES = 3
MIN_PATTERNS = 5


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
    def database(self) -> str:
        """The database engine the app stores its data in: "postgres" or "mongodb" (suite
        v2), or "" (the v1 apps' embedded SQLite or JSON files, which need nothing from the
        harness)."""
        return str((self.data.get("database") or {}).get("engine", ""))

    @property
    def database_spec(self) -> dict:
        """The manifest's `database` block (engine, isolation, entities, probes, ...)."""
        return dict(self.data.get("database") or {})

    @property
    def login(self) -> dict:
        """The manifest's `login` block; empty for an app without a sign-in."""
        return dict(self.data.get("login") or {})

    @property
    def login_steps(self) -> list[dict]:
        """The sign-in macro every browser session runs first (suite v2), in the replay
        schema; a TYPE step carries its literal text as `params.value`. Empty: no login."""
        return [dict(step) for step in self.login.get("steps", [])]

    @property
    def macro_user(self) -> dict:
        """The seeded user the sign-in macro signs in as (`macro: true`)."""
        return next((dict(u) for u in self.login.get("users", []) if u.get("macro")), {})

    def user(self, username: str) -> dict:
        return next((dict(u) for u in self.login.get("users", []) if u.get("username") == username), {})

    def login_steps_as(self, username: str) -> list[dict]:
        """The sign-in macro with the macro user's name replaced by `username`: a role check
        signs in as another seeded user exactly the way the tester signs in."""
        macro = self.macro_user.get("username")
        return [dict(step, params={**step["params"], "value": username})
                if (step.get("params") or {}).get("value") == macro else dict(step)
                for step in self.login_steps]

    @property
    def language(self) -> str:
        """`python` or `node`: the runtime the app's start command names."""
        return str((self.data.get("start") or [""])[0])

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
    if "login" in d:
        problems += validate_login(d.get("login") or {}, d.get("regions", {}))
    if "database" in d:
        problems += validate_database(app)
    if "breadth" in d:
        problems += [f"breadth: {p}" for p in breadth_problems(app, structural_only=True)]
    for name, selector in d.get("regions", {}).items():
        if not (selector.startswith("#") or selector.startswith('[id="')):
            problems.append(f"region {name}: use an #id selector, got {selector!r}")
    return problems


def suite_v2_contract(app: AppManifest) -> list[str]:
    """What keeps an app from being a suite v2 app at all: every app stores its data in a
    database server and sits behind a sign-in (plan §1b)."""
    problems = []
    if app.database not in ISOLATION:
        problems.append("declares no database block with engine postgres or mongodb")
    if not app.login_steps:
        problems.append("declares no login block with a sign-in macro")
    return problems


def breadth_problems(app: AppManifest, *, structural_only: bool = False) -> list[str]:
    """Problems with an app's `breadth` block (plan §8). Structural problems always; with
    `structural_only=False` also the size rules (12 views, 3 full-CRUD entities, 5 patterns).

    ```json
    "breadth": {
      "views": [{"name": "patient directory", "path": "/patients"},
                {"name": "my day", "path": "/my/day", "as": "amara.okafor"},
                {"name": "sign-in page", "path": "/login", "as": null},
                {"name": "slot table", "workflow": "<workflow name>", "where": "/book/\\d+/slots"}],
      "entities": {"patient": {"create": "<workflow>", "read": "...", "update": "...", "delete": "..."}},
      "patterns": {"pagination": "<workflow>", "tabs": "<workflow>", ...}
    }
    ```
    A path view is opened directly by the browser check as the macro user (or `as` a listed
    user, or anonymously for `null`); a workflow view is satisfied when that workflow passes
    and visits a URL matching `where`. Entities and patterns name the workflows that prove them.
    """
    spec = app.data.get("breadth") or {}
    workflows = {w.get("name") for w in app.data.get("workflows", [])}
    users = {u.get("username") for u in app.login.get("users", [])}
    problems: list[str] = []
    views = spec.get("views") if isinstance(spec.get("views"), list) else []
    names = [v.get("name") for v in views if isinstance(v, dict)]
    if len(set(names)) != len(names):
        problems.append("view names repeat")
    for view in views:
        if not isinstance(view, dict) or not view.get("name"):
            problems.append(f"view {view!r} has no name")
        elif "path" in view:
            if not _is_path(view["path"]):
                problems.append(f"view {view['name']!r}: path must be a same-origin path")
            if view.get("as") is not None and view.get("as") not in users:
                problems.append(f"view {view['name']!r}: `as` {view.get('as')!r} is not a listed user")
        elif view.get("workflow") not in workflows or not isinstance(view.get("where"), str):
            problems.append(f"view {view['name']!r} needs a path, or a workflow and a `where` regex")
    entities = spec.get("entities") if isinstance(spec.get("entities"), dict) else {}
    patterns = spec.get("patterns") if isinstance(spec.get("patterns"), dict) else {}
    for entity, operations in entities.items():
        for operation, workflow in (operations or {}).items():
            if operation not in CRUD or workflow not in workflows:
                problems.append(f"entity {entity!r}: {operation} -> {workflow!r} is not a declared workflow")
    for pattern, workflow in patterns.items():
        if pattern not in PATTERNS:
            problems.append(f"pattern {pattern!r} is not one of {sorted(PATTERNS)}")
        elif workflow not in workflows:
            problems.append(f"pattern {pattern!r}: {workflow!r} is not a declared workflow")
    if structural_only:
        return problems
    if len(views) < MIN_VIEWS:
        problems.append(f"{len(views)} views declared, at least {MIN_VIEWS} required")
    full = [e for e, ops in entities.items() if set(CRUD) <= set(ops or {})]
    if len(full) < MIN_CRUD_ENTITIES:
        problems.append(f"{len(full)} entities with create, read, update and delete, at least "
                        f"{MIN_CRUD_ENTITIES} required")
    if len(set(patterns) & PATTERNS) < MIN_PATTERNS:
        problems.append(f"{len(set(patterns) & PATTERNS)} interaction patterns, at least {MIN_PATTERNS} required")
    return problems


def tester_values() -> set[str]:
    """Every value the tester's action registry can type (`envs/input_values.py`),
    normalised the way the credential comparison normalises (stripped, case-folded)."""
    return {generate_input_value(kind, category).strip().casefold()
            for kind in TYPABLE_INPUT_TYPES for category in InputValueCategory}


def _is_path(value) -> bool:
    return isinstance(value, str) and value.startswith("/") and not value.startswith("//")


def validate_login(login: dict, regions: dict) -> list[str]:  # noqa: PLR0912 - one rule per line
    """Problems with a `login` block (CONVENTIONS.md §4, §5); empty means well formed."""
    problems: list[str] = []
    path = login.get("path")
    if not _is_path(path):
        problems.append(f"login.path must be a same-origin path, got {path!r}")
    protected, public = login.get("protected"), login.get("public", [])
    if not isinstance(protected, list) or not protected or not all(map(_is_path, protected)):
        problems.append("login.protected must list at least one path")
        protected = []
    if not isinstance(public, list) or not all(map(_is_path, public)):
        problems.append("login.public must be a list of paths")
        public = []
    if path in protected or path in public:
        problems.append("login.path cannot itself be listed as protected or public")
    if set(protected) & set(public):
        problems.append(f"paths both protected and public: {sorted(set(protected) & set(public))}")

    users = login.get("users")
    if not isinstance(users, list) or not users:
        problems.append("login.users lists no seeded users")
        users = []
    names = [u.get("username") for u in users if isinstance(u, dict)]
    for user in users:
        if not isinstance(user, dict) or not all(isinstance(user.get(k), str) and user[k].strip()
                                                 for k in ("username", "name")):
            problems.append(f"login.users: {user!r} needs a username and a name")
        elif not isinstance(user.get("roles"), list) or not user["roles"]:
            problems.append(f"login.users: {user['username']} declares no roles")
    if len(set(names)) != len(names):
        problems.append("login.users: usernames repeat")
    macros = [u for u in users if isinstance(u, dict) and u.get("macro") is True]
    if len(macros) != 1:
        problems.append(f"login.users: exactly one user must be `macro: true`, found {len(macros)}")
    macro = macros[0].get("username") if len(macros) == 1 else None

    password = login.get("password_value")
    if not isinstance(password, str) or not password:
        problems.append("login.password_value is missing")
        password = None
    generated = tester_values()
    for credential in [password, *names]:
        if isinstance(credential, str) and credential.strip().casefold() in generated:
            problems.append(f"login: {credential!r} equals a value the tester types "
                            "(credentials must be unguessable by exploration, decision D4)")

    steps = login.get("steps")
    _check_trace(steps, "login.steps", problems)
    if isinstance(steps, list):
        typed = [(s.get("params") or {}).get("value") for s in steps
                 if isinstance(s, dict) and s.get("type") == "TYPE"]
        if len(steps) != 3 or len(typed) != 2 or not isinstance(steps[-1], dict) \
                or steps[-1].get("type") != "CLICK":
            problems.append("login.steps must be the 3-step macro: two TYPEs, then the CLICK "
                            "that submits")
        if password is not None and typed.count(password) != 1:
            problems.append("login.steps must TYPE the password (params.value) exactly once")
        if macro is not None and typed.count(macro) != 1:
            problems.append("login.steps must TYPE the macro user's username exactly once")
        if any(isinstance(s, dict) and s.get("type") == "TYPE" and not s.get("id") for s in steps):
            problems.append("login.steps: every TYPE step names its field by id")

    form = login.get("form")
    if not isinstance(form, dict) or not _is_path(form.get("action")) or not all(
            isinstance(form.get(k), str) and form[k] for k in ("username_field", "password_field")):
        problems.append("login.form needs action (a path), username_field and password_field")
    for key in ("signed_in_region", "error_region"):
        if login.get(key) not in regions:
            problems.append(f"login.{key} {login.get(key)!r} is not a declared region")
    sign_out = login.get("sign_out")
    _check_trace(sign_out, "login.sign_out", problems)
    if not isinstance(sign_out, list) or len(sign_out) != 2 or any(
            not isinstance(s, dict) or s.get("type") != "CLICK" for s in sign_out):
        problems.append("login.sign_out must be two CLICKs: the account control, then Sign out "
                        "(decision D3)")
    restricted = login.get("restricted")
    if not isinstance(restricted, list):
        problems.append("login.restricted must be a list (empty when the app has no roles)")
        restricted = []
    for entry in restricted:
        if not isinstance(entry, dict) or entry.get("user") not in names \
                or not _is_path(entry.get("path")) or entry.get("region") not in regions:
            problems.append(f"login.restricted: {entry!r} needs a listed user, a path and a "
                            "declared region")
    return problems


def validate_database(app: AppManifest) -> list[str]:  # noqa: PLR0912 - one rule per line
    """Problems with a `database` block (CONVENTIONS.md §5, §9); empty means well formed."""
    spec = app.database_spec
    problems: list[str] = []
    engine = spec.get("engine")
    if engine not in ISOLATION:
        return [f"database.engine must be one of {sorted(ISOLATION)}, got {engine!r}"]
    if spec.get("isolation") not in ISOLATION[engine]:
        problems.append(f"database.isolation must be one of {sorted(ISOLATION[engine])} for "
                        f"{engine}, got {spec.get('isolation')!r}")
    schema = spec.get("schema")
    if not isinstance(schema, str) or not (app.root / schema).is_file():
        problems.append(f"database.schema {schema!r} must name the app's schema file "
                        "(src/schema.sql, or the module that defines the validators)")
    entities, auxiliary = spec.get("entities"), spec.get("auxiliary", [])
    if not isinstance(entities, list) or not entities or not all(isinstance(e, str) for e in entities):
        problems.append("database.entities must list the business tables or collections")
        entities = []
    if not isinstance(auxiliary, list) or not all(isinstance(e, str) for e in auxiliary):
        problems.append("database.auxiliary must be a list of names")
        auxiliary = []
    if len(set(entities)) != len(entities) or set(entities) & set(auxiliary):
        problems.append("database.entities repeat a name or overlap database.auxiliary")

    references = spec.get("references", [])
    if not isinstance(references, list):
        problems.append("database.references must be a list")
        references = []
    for reference in references:
        if not isinstance(reference, dict) or not all(
                isinstance(reference.get(k), str) and "." in reference[k] for k in ("from", "to")):
            problems.append(f"database.references: {reference!r} needs `from` and `to` paths "
                            "such as cards.project_id and projects._id")
        elif reference["from"].split(".")[0] not in entities \
                or reference["to"].split(".")[0] not in entities:
            problems.append(f"database.references: {reference!r} names an undeclared collection")
    if engine == "mongodb" and len(references) < MIN_MONGO_REFERENCES:
        problems.append(f"database.references: a MongoDB app declares at least "
                        f"{MIN_MONGO_REFERENCES}, found {len(references)}")

    probes = spec.get("probes")
    if not isinstance(probes, list) or len(probes) < MIN_PROBES:
        problems.append(f"database.probes: at least {MIN_PROBES} required")
        probes = []
    for probe in probes:
        if not isinstance(probe, dict) or probe.get("kind") not in PROBE_KINDS[engine]:
            problems.append(f"database.probes: {probe!r} needs a kind in "
                            f"{sorted(PROBE_KINDS[engine])}")
        elif engine == "postgres" and not (isinstance(probe.get("sql"), str) and probe["sql"].strip()):
            problems.append(f"database.probes: {probe.get('name')!r} has no sql statement")
        elif engine == "mongodb" and (probe.get("collection") not in entities or not (
                isinstance(probe.get("insert"), dict) or isinstance(probe.get("update"), dict))):
            problems.append(f"database.probes: {probe.get('name')!r} needs a declared collection "
                            "and an insert document or an update {filter, update}")
    missing = REQUIRED_PROBE_KINDS[engine] - {p.get("kind") for p in probes if isinstance(p, dict)}
    if missing:
        problems.append(f"database.probes: no probe of kind {sorted(missing)}")

    absent = spec.get("absent_constraints")
    fault_ids = {f.get("fault_id") for f in app.faults}
    if not isinstance(absent, list):
        problems.append("database.absent_constraints must be a list (empty when no fault needs one)")
        absent = []
    for entry in absent:
        if not isinstance(entry, dict) or entry.get("fault_id") not in fault_ids or not all(
                isinstance(entry.get(k), str) and entry[k].strip() for k in ("constraint", "reason")):
            problems.append(f"database.absent_constraints: {entry!r} needs one of this app's "
                            "fault ids, the constraint left out and the reason")
    return problems
