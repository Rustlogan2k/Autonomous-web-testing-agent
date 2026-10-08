"""Unit tests of the suite v2 harness additions: manifest login and database blocks, name
sanitisers, the vendored drivers, the static checks and the data-model contract.
None of these needs a database server or a browser."""

from __future__ import annotations

import copy
import json

import pytest

from e2e_suite.harness import db_checks, manifest, mongo, postgres, static_checks, variants
from e2e_suite.harness.manifest import AppManifest, validate_database, validate_login

REGIONS = {"signed_in_as": "#signed-in-as", "login_error": "#login-error",
           "access_notice": "#access-notice"}
PASSWORD = "Fixture-pass-26"
LOGIN = {
    "path": "/login", "protected": ["/", "/rota"], "public": ["/help"],
    "users": [{"username": "sam.okoye", "name": "Sam Okoye", "roles": ["coordinator"], "macro": True},
              {"username": "ana.silva", "name": "Ana Silva", "roles": ["volunteer"]}],
    "password_value": PASSWORD,
    "steps": [{"type": "TYPE", "id": "login-username", "params": {"value": "sam.okoye"}},
              {"type": "TYPE", "id": "login-password", "params": {"value": PASSWORD}},
              {"type": "CLICK", "id": "login-submit"}],
    "form": {"action": "/login", "username_field": "username", "password_field": "password"},
    "signed_in_region": "signed_in_as", "error_region": "login_error",
    "sign_out": [{"type": "CLICK", "id": "nav-account"}, {"type": "CLICK", "id": "sign-out"}],
    "restricted": [{"user": "ana.silva", "path": "/volunteers/ben", "region": "access_notice"}],
}


def _login(**changes) -> dict:
    block = copy.deepcopy(LOGIN)
    block.update(changes)
    return block


# -- the login block -------------------------------------------------------------------------------


def test_a_well_formed_login_block_passes():
    assert validate_login(LOGIN, REGIONS) == []


@pytest.mark.parametrize("value", ["CorrectHorseBattery9!", "Test User Input", "42", "tester@example.com",
                                   "a", "  test user input  "])
def test_credentials_equal_to_a_tester_value_are_rejected(value):
    as_password = _login(password_value=value)
    as_password["steps"][1]["params"]["value"] = value
    assert any("equals a value the tester types" in p for p in validate_login(as_password, REGIONS))
    as_username = _login()
    as_username["users"][1]["username"] = value
    assert any("equals a value the tester types" in p for p in validate_login(as_username, REGIONS))


def test_the_tester_values_cover_every_generated_value():
    values = manifest.tester_values()
    assert {"test user input", "correcthorsebattery9!", "999999999", "2026-06-15", ""} <= values


def test_exactly_one_macro_user():
    block = _login()
    block["users"][1]["macro"] = True
    assert any("exactly one user" in p for p in validate_login(block, REGIONS))


def test_the_macro_is_two_types_then_a_click_and_types_both_credentials():
    longer = _login(steps=LOGIN["steps"] + [{"type": "CLICK", "id": "nav-stock"}])
    assert any("3-step macro" in p for p in validate_login(longer, REGIONS))
    wrong_user = _login()
    wrong_user["steps"][0]["params"]["value"] = "ana.silva"
    assert any("macro user's username" in p for p in validate_login(wrong_user, REGIONS))
    no_password = _login()
    no_password["steps"][1]["params"]["value"] = "Other-pass-1"
    assert any("TYPE the password" in p for p in validate_login(no_password, REGIONS))


def test_sign_out_is_two_clicks_and_regions_are_declared():
    one_click = _login(sign_out=[{"type": "CLICK", "id": "sign-out"}])
    assert any("two CLICKs" in p for p in validate_login(one_click, REGIONS))
    assert any("not a declared region" in p
               for p in validate_login(_login(error_region="nowhere"), REGIONS))
    bad_restriction = _login(restricted=[{"user": "zed", "path": "/x", "region": "access_notice"}])
    assert any("login.restricted" in p for p in validate_login(bad_restriction, REGIONS))
    assert validate_login(_login(restricted=[]), REGIONS) == []


def test_protected_paths_are_same_origin_and_the_sign_in_page_is_neither():
    assert any("protected" in p for p in validate_login(_login(protected=["//evil.example/"]), REGIONS))
    assert any("cannot itself" in p for p in validate_login(_login(protected=["/", "/login"]), REGIONS))


def test_the_macro_can_sign_in_as_another_listed_user(tmp_path):
    app = AppManifest(root=tmp_path, data={"app_id": "x", "login": LOGIN})
    steps = app.login_steps_as("ana.silva")
    assert [s.get("params", {}).get("value") for s in steps] == ["ana.silva", PASSWORD, None]
    assert app.login_steps[0]["params"]["value"] == "sam.okoye"  # the manifest is untouched
    assert app.macro_user["name"] == "Sam Okoye"


# -- the database block ----------------------------------------------------------------------------


def _database_app(tmp_path, spec: dict) -> AppManifest:
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "schema.sql").write_text("CREATE TABLE t (id int PRIMARY KEY);\n", encoding="utf-8")
    return AppManifest(root=tmp_path, data={"app_id": "x", "database": spec,
                                            "faults": [{"fault_id": "X-1"}]})


PG = {"engine": "postgres", "isolation": "schema-per-session", "schema": "src/schema.sql",
      "entities": ["items", "journal"], "auxiliary": ["counters"],
      "probes": [{"name": "fk", "kind": "foreign_key", "sql": "INSERT INTO journal VALUES (1)"},
                 {"name": "check", "kind": "check", "sql": "UPDATE items SET stock = -1"},
                 {"name": "unique", "kind": "unique", "sql": "INSERT INTO items VALUES (1)"}],
      "absent_constraints": [{"fault_id": "X-1", "constraint": "a trigger", "reason": "X-1 needs it absent"}]}


def test_a_well_formed_postgres_block_passes(tmp_path):
    assert validate_database(_database_app(tmp_path, PG)) == []


def test_database_blocks_are_checked_engine_by_engine(tmp_path):
    assert validate_database(_database_app(tmp_path, {**PG, "engine": "sqlite"}))
    no_unique = {**PG, "probes": PG["probes"][:2] + [dict(PG["probes"][0], name="fk2")]}
    assert any("no probe of kind ['unique']" in p for p in validate_database(_database_app(tmp_path, no_unique)))
    wrong_isolation = {**PG, "isolation": "database-per-session"}
    assert any("isolation" in p for p in validate_database(_database_app(tmp_path, wrong_isolation)))
    unknown_fault = {**PG, "absent_constraints": [{"fault_id": "Y-9", "constraint": "c", "reason": "r"}]}
    assert any("absent_constraints" in p for p in validate_database(_database_app(tmp_path, unknown_fault)))
    missing_schema = {**PG, "schema": "src/nothing.sql"}
    assert any("database.schema" in p for p in validate_database(_database_app(tmp_path, missing_schema)))


def test_a_mongodb_block_declares_four_references_and_its_own_probe_kinds(tmp_path):
    spec = {"engine": "mongodb", "isolation": "database-per-session", "schema": "src/schema.sql",
            "entities": ["cards", "projects", "users"], "absent_constraints": [],
            "references": [{"from": "cards.project_id", "to": "projects._id"}],
            "probes": [{"name": "r", "kind": "required", "collection": "cards", "insert": {"_id": 9}},
                       {"name": "e", "kind": "enum", "collection": "cards", "insert": {"column": "x"}},
                       {"name": "d", "kind": "duplicate", "collection": "users", "insert": {"_id": "mei"}}]}
    problems = validate_database(_database_app(tmp_path, spec))
    assert problems == ["database.references: a MongoDB app declares at least 4, found 1"]
    spec["references"] = [{"from": f"cards.{f}", "to": "users._id"} for f in ("a", "b", "c")] + \
        [{"from": "cards.project_id", "to": "boards._id"}]
    assert any("undeclared collection" in p for p in validate_database(_database_app(tmp_path, spec)))


def test_suite_v2_contract_needs_both_a_database_and_a_login(tmp_path):
    bare = AppManifest(root=tmp_path, data={"app_id": "x"})
    assert len(manifest.suite_v2_contract(bare)) == 2
    full = AppManifest(root=tmp_path, data={"app_id": "x", "database": {"engine": "postgres"},
                                            "login": LOGIN})
    assert manifest.suite_v2_contract(full) == []


# -- names, drivers, builds ------------------------------------------------------------------------


def test_home_and_partition_names_are_sanitised():
    assert postgres.home_name("Stack-Room v2", 5123) == "e2e_stack_room_v2_5123"
    assert mongo.home_name("orbit", "docker") == "e2e_orbit_docker"
    with pytest.raises(ValueError):
        postgres.create_home('x"; DROP DATABASE postgres; --')
    with pytest.raises(ValueError):
        mongo.create_home("e2e/../admin")
    assert mongo._belongs("e2e_orbit_5001_s000017", "e2e_orbit_5001")  # noqa: SLF001
    assert not mongo._belongs("e2e_orbit_50012_s000017", "e2e_orbit_5001")  # noqa: SLF001
    assert not mongo._belongs("e2e_orbit_5001_sx", "e2e_orbit_5001")  # noqa: SLF001


def test_the_vendored_drivers_are_pure_python():
    import bson
    import pg8000
    import pymongo

    assert pymongo.has_c() is False and bson.has_c() is False
    assert "vendor" in pymongo.__file__ and "vendor" in pg8000.__file__
    assert bson.decode(bson.encode({"n": 1, "xs": [{"a": "b"}]})) == {"n": 1, "xs": [{"a": "b"}]}


def test_a_build_carries_only_its_own_language_of_vendored_drivers(tmp_path):
    app = AppManifest(root=tmp_path / "app", data={"app_id": "app", "start": ["python", "src/server.py"],
                                                   "database": {"engine": "postgres"}})
    (tmp_path / "app" / "src").mkdir(parents=True)
    (tmp_path / "app" / "src" / "server.py").write_text("print('x')\n", encoding="utf-8")
    variants._copy_runtime(app, tmp_path / "build")  # noqa: SLF001
    assert sorted(p.name for p in (tmp_path / "build" / "vendor").iterdir()) == ["python"]
    assert (tmp_path / "build" / "vendor" / "python" / "pg8000").is_dir()
    assert not list((tmp_path / "build" / "vendor").rglob("__pycache__"))


def test_the_vendor_tree_matches_its_manifest_and_the_python_drivers_are_pure():
    """`scripts/vendor_drivers.py --check`, offline: every vendored file has the sha256 the
    manifest pins, and pymongo and bson load without their C extensions (plan §4)."""
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[3]
    completed = subprocess.run([sys.executable, str(repo / "scripts" / "vendor_drivers.py"), "--check"],
                               capture_output=True, text=True, timeout=300, cwd=repo, check=False)
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output[-2000:]
    assert "vendor tree matches the manifest" in output
    assert '"pymongo_has_c": false' in output and '"bson_has_c": false' in output


# -- static checks ---------------------------------------------------------------------------------


def _source_app(tmp_path, files: dict[str, str], engine: str = "postgres") -> AppManifest:
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return AppManifest(root=tmp_path, data={"app_id": "x", "database": {"engine": engine}})


def test_the_database_clock_and_server_side_automation_are_flagged(tmp_path):
    app = _source_app(tmp_path, {
        "src/schema.sql": "CREATE TABLE t (at timestamp DEFAULT now());\nCREATE TRIGGER x AFTER UPDATE ON t;\n",
        "src/a.py": 'db.run("INSERT INTO t (at) VALUES (now())")\nstamp = clock.now()\n'
                    'token = secrets.token_hex(8)\n',
        "src/b.js": "await col.updateOne({}, { $currentDate: { at: true } });\nconst id = new ObjectId();\n"})
    found = static_checks.nondeterminism(app)
    assert any("schema.sql:1" in f for f in found) and any("schema.sql:2" in f for f in found)
    assert any("a.py:1" in f for f in found) and any("a.py:3" in f for f in found)
    assert not any("a.py:2" in f for f in found)  # the app's own clock is not the database's
    assert any("b.js:1" in f for f in found) and any("b.js:2" in f for f in found)


def test_a_database_url_must_come_from_the_environment(tmp_path):
    hard = _source_app(tmp_path / "hard", {"src/db.py": 'URL = "postgresql://wta:pw@127.0.0.1:55432/x"\n'})
    assert len(static_checks.database_url_from_environment(hard)) == 2  # the literal, no DATABASE_URL
    read = _source_app(tmp_path / "read", {"src/db.js": "const url = process.env.MONGODB_URL;\n"}, "mongodb")
    assert static_checks.database_url_from_environment(read) == []
    (tmp_path / "read" / "Dockerfile").write_text("FROM node:22-slim\nCOPY src ./src\n", encoding="utf-8")
    assert static_checks.dockerfile_copies_vendor(read)
    (tmp_path / "read" / "Dockerfile").write_text("FROM node:22-slim\nCOPY vendor ./vendor\n", encoding="utf-8")
    assert static_checks.dockerfile_copies_vendor(read) == []


# -- the data-model contract -----------------------------------------------------------------------


def _pg_description(**changes) -> dict:
    description = {
        "tables": ["counters", "a", "b", "c", "d", "e", "f", "g", "h"], "table_count": 8,
        "foreign_key_count": 6, "foreign_keys_indexed": 5, "check_count": 5,
        "enumerations": ["a.x", "b.y"], "unique_indexes": ["a.u", "b.u"],
        "secondary_indexes": ["a.i", "b.i", "c.i"], "not_null_ratio": 0.6,
        "user_triggers": [], "rules": [], "event_triggers": [], "volatile_defaults": [],
        "seed_rows": 40, "non_empty_tables": 5}
    description.update(changes)
    return description


SPEC = {"entities": list("abcdefgh"), "auxiliary": ["counters"]}
PROBES = [{"name": k, "kind": k, "expected": "", "got": "", "passed": True}
          for k in ("foreign_key", "check", "unique")]


def test_the_postgres_contract_holds_exactly_at_its_thresholds():
    assert db_checks.evaluate_postgres(SPEC, _pg_description(), PROBES) == []


@pytest.mark.parametrize("changes, expected", [
    ({"foreign_keys_indexed": 4}, "backed by an index"),
    ({"enumerations": ["a.x"]}, "enumerations"),
    ({"not_null_ratio": 0.59}, "NOT NULL"),
    ({"user_triggers": ["s_000001.items.audit"]}, "user triggers"),
    ({"volatile_defaults": ["s.t.at DEFAULT now()"]}, "volatile defaults"),
    ({"tables": ["counters", "a", "b", "c", "d", "e", "f", "g", "h", "sneaky"]}, "neither entities"),
])
def test_the_postgres_contract_names_each_shortfall(changes, expected):
    assert any(expected in f for f in db_checks.evaluate_postgres(SPEC, _pg_description(**changes), PROBES))


def test_a_probe_the_database_accepts_fails_the_contract():
    accepted = PROBES[:2] + [{"name": "dup", "kind": "unique", "expected": "23505", "got": "accepted",
                              "passed": False}]
    failures = db_checks.evaluate_postgres(SPEC, _pg_description(), accepted)
    assert any("got accepted" in f for f in failures) and any("['unique']" in f for f in failures)


def test_mongo_document_shape_counts_levels_with_arrays_transparent():
    from bson import ObjectId

    trip = {"_id": "t1", "days": [{"day": 1, "stops": [{"id": "s1", "kind": "walk"}]}], "tags": ["a", "b"]}
    assert mongo._document_shape(trip) == (4, 3, 0)  # noqa: SLF001 - 1 day + 1 stop + 2 tags
    card = {"_id": 1, "checklist": [{"n": 1, "text": "x"}], "owner": ObjectId()}
    assert mongo._document_shape(card) == (1, 2, 1)  # noqa: SLF001
    assert mongo._arrays_of_objects(trip) == 2  # noqa: SLF001
    assert mongo._path_values({"members": [{"user_id": "a"}, {"user_id": None}, {}]},  # noqa: SLF001
                              ["members[]", "user_id"]) == ["a"]


def test_percentile_is_nearest_rank():
    samples = list(range(1, 21))
    assert db_checks.percentile(samples, 0.95) == 19 and db_checks.percentile(samples, 0.5) == 10
    assert db_checks.percentile([], 0.95) == 0.0


def test_every_suite_v2_manifest_in_the_suite_is_well_formed():
    for app in manifest.discover():
        if app.database:
            assert manifest.validate_manifest(app) == [], app.app_id
            json.dumps(app.data)  # serialisable for the admission report
