"""Database and session checks of a suite v2 app, over plain HTTP: no browser needed.

* **Sign-in over HTTP.** The manifest's `login.form` names the form the sign-in page posts;
  every app accepts that form POST and answers 303 with the `wta_sid` cookie (CONVENTIONS.md
  §4). `HttpSession` is one cookie jar, i.e. one browser context, and never follows a
  redirect by itself, so every hop is observable.
* **Seed state.** The sha of a freshly signed-in partition (`/__wta/state`): the browser
  checks require the sign-in macro to land on exactly this state, and the Docker check
  requires the container to.
* **Data-model contract** (plan §4, CONVENTIONS.md §9), counted in one freshly seeded
  partition with the engine module's `describe`, `check_references` and `run_probes`.
* **Session cost and cap.** Sign-ins against a variant whose `WTA_SESSION_CAP` is small, so
  that most of them also evict a partition, which is the steady state of a long run.
"""

from __future__ import annotations

import http.cookiejar
import json
import math
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit

from .dbctl import engine
from .launcher import Server
from .manifest import AppManifest

SESSION_COOKIE = "wta_sid"
#: plan §4: a new session (credential check + partition + eviction) within 300 ms at p95;
#: above 150 ms is reported as a warning.
SESSION_COST_P95_MS = 300.0
SESSION_COST_WARN_MS = 150.0
CAP_UNDER_TEST = 4
SIGN_INS_UNDER_TEST = 20

PG_CONTRACT = {"tables": 8, "foreign_keys": 6, "foreign_keys_indexed_ratio": 0.8, "checks": 5,
               "enumerations": 2, "unique": 2, "secondary_indexes": 3, "not_null_ratio": 0.6,
               "seed_rows": 40, "non_empty_tables": 5, "probes": 3}
MONGO_CONTRACT = {"collections": 4, "required_fields": 2, "additional_properties_false": 2,
                  "enums": 3, "one_of": 1, "with_arrays_of_objects": 2, "depth": 3,
                  "unique_indexes": 2, "secondary_indexes": 3, "compound_indexes": 1,
                  "references": 3, "volume": 60, "non_empty_collections": 4, "probes": 3}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003 - urllib's signature
        return None


@dataclass
class Reply:
    status: int
    url: str
    location: str
    body: bytes
    ms: float

    @property
    def path(self) -> str:
        return urlsplit(self.url).path

    def json(self):
        return json.loads(self.body.decode("utf-8") or "null")


class HttpSession:
    """One cookie jar against one variant: what a fresh browser context is, minus the page."""

    def __init__(self, base_url: str) -> None:
        self.base = base_url.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar),
                                                  _NoRedirect())

    def send(self, method: str, path: str, form: dict | None = None) -> Reply:
        url = urljoin(self.base + "/", path)
        data = urlencode(form).encode("utf-8") if form is not None else None
        request = urllib.request.Request(url, data=data, method=method, headers={
            "Accept": "text/html,application/json",
            **({"Content-Type": "application/x-www-form-urlencoded"} if data is not None else {})})
        started = time.perf_counter()
        try:
            with self.opener.open(request, timeout=20) as response:
                status, headers, body = response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:  # a 3xx (redirects are not followed) or 4xx/5xx
            status, headers, body = error.code, error.headers, error.read()
        elapsed = (time.perf_counter() - started) * 1000
        return Reply(status, url, headers.get("Location", "") or "", body, round(elapsed, 1))

    def follow(self, path: str, hops: int = 8) -> list[Reply]:
        """GET `path` and every same-origin redirect after it; the last reply is where it lands."""
        replies = [self.send("GET", path)]
        while replies[-1].status in (301, 302, 303, 307, 308) and len(replies) <= hops:
            target = urljoin(replies[-1].url, replies[-1].location)
            if urlsplit(target).netloc != urlsplit(self.base).netloc:
                break
            replies.append(self.send("GET", target))
        return replies

    def state(self) -> dict:
        return self.send("GET", "/__wta/state").json() or {}

    @property
    def sid(self) -> str | None:
        return next((c.value for c in self.jar if c.name == SESSION_COOKIE), None)


def sign_in(base_url: str, app: AppManifest, username: str | None = None,
            password: str | None = None) -> tuple[HttpSession, Reply]:
    """Post the sign-in form in a fresh cookie jar (the macro user and fixture password unless
    told otherwise); return the session and the form's reply."""
    login = app.login
    form = login["form"]
    session = HttpSession(base_url)
    reply = session.send("POST", form["action"], {
        form["username_field"]: username or app.macro_user["username"],
        form["password_field"]: login["password_value"] if password is None else password})
    return session, reply


def signed_in(reply: Reply, session: HttpSession, app: AppManifest) -> bool:
    """A sign-in succeeded: 303 away from the sign-in page, with the session cookie set."""
    target = urlsplit(urljoin(reply.url, reply.location)).path
    return reply.status == 303 and bool(session.sid) and target != app.login["path"]


def percentile(values: list[float], share: float) -> float:
    """Nearest-rank percentile (the value at rank ceil(share * n))."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)] if ordered else 0.0


# -- the data-model contract -------------------------------------------------------------------


def _at_least(failures: list[str], label: str, measured, minimum) -> None:
    if measured < minimum:
        failures.append(f"{label}: {measured} < {minimum}")


def _declared(failures: list[str], kind: str, found: list[str], spec: dict) -> None:
    declared = set(spec.get("entities", [])) | set(spec.get("auxiliary", []))
    undeclared = sorted(set(found) - declared)
    missing = sorted(set(spec.get("entities", [])) - set(found))
    if undeclared:
        failures.append(f"{kind} neither entities nor auxiliary in the manifest: {undeclared}")
    if missing:
        failures.append(f"declared entities missing from the partition: {missing}")


def _probe_failures(failures: list[str], probes: list[dict], kinds: set[str]) -> None:
    passed = [p for p in probes if p["passed"]]
    _at_least(failures, "probes refused as expected", len(passed), 3)
    for probe in probes:
        if not probe["passed"]:
            failures.append(f"probe {probe['name']!r}: expected {probe['expected']}, got {probe['got']}")
    uncovered = kinds - {p["kind"] for p in passed}
    if uncovered:
        failures.append(f"no passing probe of kind {sorted(uncovered)}")


def evaluate_postgres(spec: dict, d: dict, probes: list[dict]) -> list[str]:
    """Every way partition description `d` misses the PostgreSQL contract (plan §4)."""
    c, failures = PG_CONTRACT, []
    _declared(failures, "tables", d["tables"], spec)
    _at_least(failures, "tables", d["table_count"], c["tables"])
    _at_least(failures, "foreign keys", d["foreign_key_count"], c["foreign_keys"])
    if d["foreign_key_count"]:
        ratio = d["foreign_keys_indexed"] / d["foreign_key_count"]
        _at_least(failures, "share of foreign keys backed by an index", round(ratio, 3),
                  c["foreign_keys_indexed_ratio"])
    _at_least(failures, "CHECK constraints", d["check_count"], c["checks"])
    _at_least(failures, "enumerations", len(d["enumerations"]), c["enumerations"])
    _at_least(failures, "UNIQUE besides primary keys", len(d["unique_indexes"]), c["unique"])
    _at_least(failures, "non-unique secondary indexes", len(d["secondary_indexes"]),
              c["secondary_indexes"])
    _at_least(failures, "NOT NULL share of columns", d["not_null_ratio"], c["not_null_ratio"])
    for key in ("user_triggers", "rules", "event_triggers", "volatile_defaults"):
        if d[key]:
            failures.append(f"{key.replace('_', ' ')} must be none: {d[key][:5]}")
    _at_least(failures, "seed rows", d["seed_rows"], c["seed_rows"])
    _at_least(failures, "non-empty tables", d["non_empty_tables"], c["non_empty_tables"])
    _probe_failures(failures, probes, {"foreign_key", "check", "unique"})
    return failures


def evaluate_mongodb(spec: dict, d: dict, references: list[dict], probes: list[dict]) -> list[str]:
    """Every way partition description `d` misses the MongoDB contract (plan §4)."""
    c, failures = MONGO_CONTRACT, []
    _declared(failures, "collections", d["collections"], spec)
    _at_least(failures, "collections", d["collection_count"], c["collections"])
    weak = sorted(name for name, info in d["per_collection"].items()
                  if not (info["validator"] and info["validation_level"] == "strict"
                          and info["validation_action"] == "error"
                          and info["required"] >= c["required_fields"]))
    if weak:
        failures.append(f"collections without a strict, erroring $jsonSchema requiring >= "
                        f"{c['required_fields']} fields: {weak}")
    for key in ("additional_properties_false", "enums", "one_of", "with_arrays_of_objects",
                "depth", "unique_indexes", "secondary_indexes", "compound_indexes", "volume",
                "non_empty_collections"):
        _at_least(failures, key.replace("_", " "), d[key], c[key])
    if d["object_ids"]:
        failures.append(f"ObjectIds in the seed: {d['object_ids']} (ids are counter-derived)")
    exercised = [r for r in references if r["values"] > 0]
    _at_least(failures, "declared references with seeded values", len(exercised), c["references"])
    for reference in references:
        if reference["dangling"]:
            failures.append(f"reference {reference['from']} -> {reference['to']}: "
                            f"{reference['dangling']} dangling {reference['examples']}")
    _probe_failures(failures, probes, {"required", "enum", "duplicate"})
    return failures


# -- the checks ----------------------------------------------------------------------------------


def check_database(app: AppManifest, directory: Path) -> dict:
    """Seed state, data-model contract, session cost and session cap, on one clean variant
    started with `WTA_SESSION_CAP=4`. Sections are reported separately."""
    spec = app.database_spec
    store = engine(app.database)
    out: dict = {}
    with Server(app, directory, session_cap=CAP_UNDER_TEST) as server:
        # The first sign-in creates the partition the contract is counted in.
        first, reply = sign_in(server.url, app)
        landed = first.follow(app.data.get("entry", "/"))[-1]
        state = first.state()
        out["sign_in"] = {
            "passed": signed_in(reply, first, app) and landed.status == 200
            and landed.path != app.login["path"] and state.get("user") == app.macro_user["username"],
            "status": reply.status, "location": reply.location, "cookie": bool(first.sid),
            "entry_status": landed.status, "entry_path": landed.path, "user": state.get("user")}
        out["seed_sha"] = state.get("sha")
        partition = (store.partitions(server.home) or [None])[0]
        if partition is None:
            out["data_model"] = {"passed": False, "failures": ["no partition after a sign-in"]}
        else:
            description = store.describe(server.home, partition, spec.get("entities"))
            if app.database == "postgres":
                probes = store.run_probes(server.home, partition, spec.get("probes", []))
                references: list[dict] = []
                failures = evaluate_postgres(spec, description, probes)
            else:
                references = store.check_references(server.home, partition, spec.get("references", []))
                probes = store.run_probes(server.home, partition, spec.get("probes", []))
                failures = evaluate_mongodb(spec, description, references, probes)
            out["data_model"] = {"passed": not failures, "failures": failures,
                                 "description": description, "probes": probes,
                                 "references": references}
        out["sessions"] = _sessions_under_cap(server, app, store, first)
    return out


def _sessions_under_cap(server: Server, app: AppManifest, store, first: HttpSession) -> dict:
    """`SIGN_INS_UNDER_TEST` sign-ins, each in a fresh cookie jar: their cost, and the number
    of live partitions after each against the cap. The first session is evicted on the way,
    and must then be simply signed out (redirected to the sign-in page), never an error."""
    costs, live, refused = [], [], 0
    seed_sha = first.state().get("sha")
    shas = set()
    for _ in range(SIGN_INS_UNDER_TEST):
        session, reply = sign_in(server.url, app)
        if not signed_in(reply, session, app):
            refused += 1
        costs.append(reply.ms)
        live.append(len(store.partitions(server.home)))
        shas.add(session.state().get("sha"))
    evicted_state = first.state()
    evicted_landing = first.follow(app.data.get("entry", "/"))
    p95 = percentile(costs, 0.95)
    cost = {"passed": refused == 0 and p95 <= SESSION_COST_P95_MS,
            "warn": p95 > SESSION_COST_WARN_MS, "samples": len(costs),
            "p50_ms": percentile(costs, 0.5), "p95_ms": p95, "max_ms": max(costs),
            "limit_ms": SESSION_COST_P95_MS, "refused": refused,
            "regime": f"WTA_SESSION_CAP={CAP_UNDER_TEST}: every sign-in after the cap also evicts"}
    cap = {"passed": max(live) <= CAP_UNDER_TEST and evicted_state.get("session") is None
           and evicted_landing[-1].status == 200
           and evicted_landing[-1].path == app.login["path"]
           and all(r.status < 400 for r in evicted_landing) and shas == {seed_sha},
           "cap": CAP_UNDER_TEST, "max_live_partitions": max(live), "live_after_each": live,
           "evicted_session_state": evicted_state.get("session"),
           "evicted_session_lands_on": evicted_landing[-1].path,
           "every_new_session_starts_from_the_seed": shas == {seed_sha}}
    return {"cost": cost, "cap": cap}
