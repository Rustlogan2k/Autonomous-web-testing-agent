"""The suite's MongoDB server: one local container, one home database per running variant.

The MongoDB apps of suite v2 store their data here. Like `postgres.py`, the harness runs one
server for all of them (`mongo:7.0`, pinned by digest, published on 127.0.0.1 only) and gives
every running variant its own *home* database, named after the app and the variant's port,
which the app reads from the path of `MONGODB_URL`. Each signed-in browser session gets its
own *partition* database, `<home>_s` + six digits (CONVENTIONS.md §3, plan decision D1).
A variant's storage is its home plus its partitions; `drop_home` removes all of them.

The server is a **standalone** mongod (decision D2): requests are serialised per session and
every aggregate is one document, so no transaction is needed, and a standalone avoids the
replica-set host and `directConnection` traps. It is a disposable fixture with a size-capped
tmpfs data directory, a small WiredTiger cache and no diagnostic data collection. The root
credentials are fixed local test credentials on a loopback-only port, not secrets.

The harness talks to it with the suite's vendored, pure-Python pymongo
(`e2e_suite/vendor/python`); nothing is installed into the tester's environment. The engine
interface matches `postgres.py` (see its docstring).
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import sys
import time
from pathlib import Path

VENDOR = Path(__file__).resolve().parents[1] / "vendor"
if str(VENDOR / "python") not in sys.path:
    sys.path.insert(0, str(VENDOR / "python"))

import pymongo  # noqa: E402 - the vendored driver, made importable just above
from bson import ObjectId  # noqa: E402
from pymongo.errors import PyMongoError  # noqa: E402

ENGINE = "mongodb"
IMAGE = "mongo:7.0@sha256:1f995ad6fdb93244a1addab1b58f934a0bc2f5643c38e02f5e9d7f0c7d227a7b"
CONTAINER = os.environ.get("WTA_MONGO_CONTAINER", "wta-mongo")
PORT = int(os.environ.get("WTA_MONGO_PORT", "57017"))
#: The port inside the container: what a container on the `wta-net` fallback network uses.
INTERNAL_PORT = 27017
USER = "wta"
PASSWORD = "wta-local-test"  # a fixed fixture credential on a loopback-only port
READY_TIMEOUT_S = 90.0
#: MongoDB database names: no `/\. "$*<>:|?`, under 64 bytes; ours are lower-case words.
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
#: A session partition database: `<home>_s` + six digits (CONVENTIONS.md §3).
_PARTITION_SUFFIX = re.compile(r"_s[0-9]{6}$")
#: The server error code each kind of data-model probe must be refused with: a document
#: that fails its collection's validator (121), or a duplicate key (11000).
PROBE_CODES = {"required": 121, "enum": 121, "validation": 121, "duplicate": 11000}
SERVER_ARGS = ("--wiredTigerCacheSizeGB", "0.75",
               "--setParameter", "diagnosticDataCollectionEnabled=false")
_QUERY = "authSource=admin&directConnection=true&retryWrites=false"


def url_for(home: str, *, host: str = "127.0.0.1", port: int = PORT) -> str:
    """The connection string an app receives as `MONGODB_URL`; its path names the home."""
    return f"mongodb://{USER}:{PASSWORD}@{host}:{port}/{home}?{_QUERY}"


def container_url(home: str, route: str) -> str:
    """The URL an app container uses: `host` reaches the published port through
    host.docker.internal; `network` reaches the server by name on the `wta-net` network."""
    if route == "host":
        return url_for(home, host="host.docker.internal")
    return url_for(home, host=CONTAINER, port=INTERNAL_PORT)


def _docker(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout,
                          check=False)


def _client(timeout_ms: int = 5000) -> pymongo.MongoClient:
    return pymongo.MongoClient(url_for("admin"), serverSelectionTimeoutMS=timeout_ms,
                               connectTimeoutMS=timeout_ms, socketTimeoutMS=30000,
                               uuidRepresentation="standard")


@contextlib.contextmanager
def client(timeout_ms: int = 5000):
    connection = _client(timeout_ms)
    try:
        yield connection
    finally:
        connection.close()


def ready() -> bool:
    try:
        with client(timeout_ms=1500) as connection:
            connection.admin.command("ping")
        return True
    except PyMongoError:
        return False


def run_arguments() -> list[str]:
    """The `docker run` arguments that create the server container (standalone, with auth:
    the image enables `--auth` when it is given a root user)."""
    return ["run", "-d", "--name", CONTAINER, "-p", f"127.0.0.1:{PORT}:{INTERNAL_PORT}",
            "-e", f"MONGO_INITDB_ROOT_USERNAME={USER}", "-e", f"MONGO_INITDB_ROOT_PASSWORD={PASSWORD}",
            "--tmpfs", "/data/db:rw,size=3g", "--tmpfs", "/data/configdb:rw,size=64m",
            IMAGE, *SERVER_ARGS]


def ensure_server() -> None:
    """Start the container if it is not running; return once it accepts authenticated
    commands. Safe to call from several worker processes at once (see `postgres.py`)."""
    if ready():
        return
    state = _docker("inspect", "-f", "{{.State.Running}}", CONTAINER)
    if state.returncode == 0 and state.stdout.strip() != "true":
        _docker("start", CONTAINER)
    elif state.returncode != 0:
        _docker(*run_arguments(), timeout=300)
    deadline = time.monotonic() + READY_TIMEOUT_S
    while time.monotonic() < deadline:
        if ready():
            return
        time.sleep(0.5)
    logs = _docker("logs", "--tail", "20", CONTAINER).stdout
    raise RuntimeError(f"MongoDB container {CONTAINER} not ready on port {PORT}:\n{logs}")


def _safe(name: str) -> str:
    if not _NAME.match(name):
        raise ValueError(f"not a safe home database name: {name!r}")
    return name


def home_name(app_id: str, port: int | str) -> str:
    """One home database per running variant: the app and the variant's (unique) port."""
    return f"e2e_{re.sub(r'[^a-z0-9]', '_', app_id.lower())}_{port}"


def _belongs(database: str, home: str) -> bool:
    return database == home or (database.startswith(home + "_s")
                                and bool(_PARTITION_SUFFIX.search(database))
                                and len(database) == len(home) + 8)


def list_homes(prefix: str) -> list[str]:
    """Every database (homes and partitions) whose name starts with `prefix`."""
    with client() as connection:
        return sorted(n for n in connection.list_database_names() if n.startswith(prefix))


def home_exists(name: str) -> bool:
    """Whether anything of home `name` is left: the home itself or any of its partitions."""
    return any(_belongs(n, _safe(name)) for n in list_homes(name))


def drop_home(name: str) -> None:
    """Drop home `name` and every partition database of it."""
    _safe(name)
    with contextlib.suppress(PyMongoError), client() as connection:
        for database in connection.list_database_names():
            if _belongs(database, name):
                connection.drop_database(database)


def create_home(name: str) -> str:
    """Make sure home `name` starts empty (MongoDB creates databases on first write); return
    the URL the app receives."""
    _safe(name)
    ensure_server()
    drop_home(name)
    return url_for(name)


def drop_prefixed(prefix: str) -> list[str]:
    """Drop every database whose name starts with `prefix` (`dbctl reset`); return them."""
    if not re.fullmatch(r"[a-z][a-z0-9_]*", prefix):
        raise ValueError(f"not a safe prefix: {prefix!r}")
    dropped = list_homes(prefix)
    with client() as connection:
        for database in dropped:
            connection.drop_database(database)
    return dropped


def partitions(name: str) -> list[str]:
    """The session partition databases of home `name`, oldest first."""
    return [n for n in list_homes(_safe(name) + "_s") if _belongs(n, name) and n != name]


# -- the data model of one partition (CONVENTIONS.md §9) ----------------------------------------


def _schema_keywords(schema, keywords: tuple[str, ...]) -> int:
    """How many times any of `keywords` is used anywhere in a `$jsonSchema`."""
    if isinstance(schema, dict):
        return sum(1 for k in schema if k in keywords) + sum(
            _schema_keywords(v, keywords) for v in schema.values())
    if isinstance(schema, list):
        return sum(_schema_keywords(v, keywords) for v in schema)
    return 0


def _document_shape(document: dict) -> tuple[int, int, int]:
    """(embedded elements, deepest document level, ObjectIds) of one stored document.

    The document itself is level 1 and every embedded document one level deeper. Arrays are
    transparent (an object inside `days[]` is level 2, an object inside `days[].stops[]`
    level 3), so "depth >= 3" means a sub-document inside an embedded one, not merely an
    array of objects. Embedded elements are array elements of any type, at any depth.
    """
    elements = object_ids = 0
    deepest = 1
    stack = [(value, 1) for value in document.values()]
    while stack:
        value, level = stack.pop()  # `level`: the document `value` sits in
        if isinstance(value, ObjectId):
            object_ids += 1
        elif isinstance(value, dict):
            deepest = max(deepest, level + 1)
            stack.extend((v, level + 1) for v in value.values())
        elif isinstance(value, list):
            elements += len(value)
            stack.extend((v, level) for v in value)
    return elements, deepest, object_ids


def _arrays_of_objects(document: dict) -> int:
    """Non-empty arrays of embedded documents anywhere in `document`."""
    found = 0
    stack: list = [document]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            stack.extend(value.values())
        elif isinstance(value, list):
            if value and all(isinstance(v, dict) for v in value):
                found += 1
            stack.extend(value)
    return found


def describe(name: str, partition: str | None = None, entities: list[str] | None = None) -> dict:
    """What one session partition of home `name` holds, for the data-model contract:
    validators and their strictness, indexes, documents and their shape, `_id` types."""
    _safe(name)
    if partition is None:
        found = partitions(name)
        if not found:
            return {"partition": None}
        partition = found[0]
    with client() as connection:
        database = connection[partition]
        infos = sorted(database.list_collections(), key=lambda info: info["name"])
        collections = [info["name"] for info in infos if info.get("type", "collection") == "collection"]
        counted, rows = {}, {}
        for info in infos:
            title = info["name"]
            if title not in collections:
                continue
            documents = list(database[title].find({}, sort=[("_id", 1)]))
            rows[title] = len(documents)
            if entities is not None and title not in entities:
                continue
            options = info.get("options") or {}
            schema = (options.get("validator") or {}).get("$jsonSchema")
            indexes = database[title].index_information()
            keyed = {k: v for k, v in indexes.items() if k != "_id_"}
            elements = deepest = object_ids = arrays = 0
            id_types = set()
            for document in documents:
                e, d, o = _document_shape(document)
                elements, deepest, object_ids = elements + e, max(deepest, d), object_ids + o
                arrays += _arrays_of_objects(document)
                id_types.add(type(document.get("_id")).__name__)
            counted[title] = {
                "validator": schema is not None,
                "validation_level": options.get("validationLevel", "strict"),
                "validation_action": options.get("validationAction", "error"),
                "required": len((schema or {}).get("required", [])),
                "additional_properties_false": (schema or {}).get("additionalProperties") is False,
                "enums": _schema_keywords(schema, ("enum",)),
                "one_of": _schema_keywords(schema, ("oneOf", "anyOf")),
                "unique_indexes": sorted(k for k, v in keyed.items() if v.get("unique")),
                "secondary_indexes": sorted(k for k, v in keyed.items() if not v.get("unique")),
                "compound_indexes": sorted(k for k, v in keyed.items() if len(v.get("key", [])) > 1),
                "documents": len(documents), "embedded_elements": elements,
                "arrays_of_objects": arrays, "depth": deepest,
                "object_ids": object_ids, "id_types": sorted(id_types),
            }
    per = counted.values()
    return {
        "partition": partition, "collections": collections, "entities_found": sorted(counted),
        "collection_count": len(counted), "per_collection": counted, "rows": rows,
        "validated_strict": sum(1 for c in per if c["validator"] and c["validation_level"] == "strict"
                                and c["validation_action"] == "error" and c["required"] >= 2),
        "additional_properties_false": sum(1 for c in per if c["additional_properties_false"]),
        "enums": sum(c["enums"] for c in per), "one_of": sum(c["one_of"] for c in per),
        "with_arrays_of_objects": sum(1 for c in per if c["arrays_of_objects"] > 0),
        "depth": max((c["depth"] for c in per), default=0),
        "unique_indexes": sum(len(c["unique_indexes"]) for c in per),
        # Every index but `_id_` is secondary, unique or not (plan §4: ">= 3 secondary
        # indexes, including >= 1 compound", counted beside ">= 2 unique").
        "secondary_indexes": sum(len(c["unique_indexes"]) + len(c["secondary_indexes"]) for c in per),
        "compound_indexes": sum(len(c["compound_indexes"]) for c in per),
        "object_ids": sum(c["object_ids"] for c in per),
        "volume": sum(c["documents"] + c["embedded_elements"] for c in per),
        "non_empty_collections": sum(1 for c in per if c["documents"] > 0),
    }


def _path_values(document, parts: list[str]) -> list:
    """The values at a reference path such as `members[].user_id` (`[]` = each element)."""
    if not parts:
        return [document]
    if not isinstance(document, dict):
        return []
    head, rest = parts[0], parts[1:]
    each = head.endswith("[]")
    value = document.get(head[:-2] if each else head)
    if value is None:
        return []
    if each:
        if not isinstance(value, list):
            return []
        return [v for item in value for v in _path_values(item, rest)]
    return _path_values(value, rest)


def _split_reference(path: str) -> tuple[str, list[str]]:
    collection, _, field = path.partition(".")
    return collection, field.split(".") if field else ["_id"]


def check_references(name: str, partition: str, references: list[dict]) -> list[dict]:
    """For each declared reference (`{"from": "cards.project_id", "to": "projects._id"}`),
    how many values the seed holds at `from` and which of them name nothing at `to`.
    A null or missing value is an absent (optional) reference, not a dangling one."""
    _safe(name)
    out = []
    with client() as connection:
        database = connection[partition]
        for reference in references:
            source, source_path = _split_reference(reference["from"])
            target, target_path = _split_reference(reference["to"])
            targets = {repr(v) for d in database[target].find({}) for v in _path_values(d, target_path)}
            values = [v for d in database[source].find({}) for v in _path_values(d, source_path)]
            dangling = [v for v in values if repr(v) not in targets]
            out.append({"from": reference["from"], "to": reference["to"], "values": len(values),
                        "dangling": len(dangling), "examples": [repr(v) for v in dangling[:5]],
                        "passed": not dangling})
    return out


def run_probes(name: str, partition: str, probes: list[dict]) -> list[dict]:
    """Try each probe write in `partition` and report the server error code it was refused
    with. A write the server accepts fails its probe (and is removed again)."""
    _safe(name)
    out = []
    with client() as connection:
        database = connection[partition]
        for probe in probes:
            expected = int(probe.get("code") or PROBE_CODES.get(probe.get("kind"), 0))
            collection = database[probe["collection"]]
            try:
                if "insert" in probe:
                    inserted = collection.insert_one(dict(probe["insert"])).inserted_id
                    collection.delete_one({"_id": inserted})
                else:
                    collection.update_one(probe["update"]["filter"], probe["update"]["update"])
                got: object = "accepted"
            except PyMongoError as exc:
                got = getattr(exc, "code", None) or type(exc).__name__
            out.append({"name": probe.get("name", probe["collection"]), "kind": probe.get("kind"),
                        "expected": expected, "got": got, "passed": got == expected})
    return out
