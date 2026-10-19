"""Session storage: one MongoDB database per signed-in browser.

The database named in MONGODB_URL is this server's *home*. It holds the session registry
(`sessions`: which `wta_sid` cookie owns which database, who is signed in there, and a use
counter for eviction) and a `counters` collection. On start the home and every session
database left over from an earlier run are dropped.

A browser's first successful sign-in creates `<home>_s000123`: every collection is created
with its validator (`schema.py`, strict, errors refused) and its indexes, then filled from the
seed held in memory. Signing out clears who is signed in but keeps the database, so signing in
again in the same browser continues where it left off. Beyond WTA_SESSION_CAP databases the
least recently used is dropped. Requests never interleave: one client, one lock.
"""

from __future__ import annotations

import contextlib
import copy
import re
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vendor" / "python"))
import pymongo  # noqa: E402 - the suite's pinned pure-Python driver, shipped in every build
from pymongo.errors import PyMongoError  # noqa: E402

import schema  # noqa: E402

_SID = re.compile(r"hl\d{6}")


class SessionStore:
    def __init__(self, url: str, cap: int, seed: dict[str, list[dict]]) -> None:
        self.client = pymongo.MongoClient(url, serverSelectionTimeoutMS=3000, connectTimeoutMS=3000,
                                          appname="helpline")
        self.home = self.client.get_default_database().name
        self.cap = max(1, cap)
        self.seed = seed
        self._lock = threading.Lock()

    @property
    def registry(self):
        return self.client[self.home]["sessions"]

    def _tick(self, name: str) -> int:
        found = self.client[self.home]["counters"].find_one_and_update(
            {"_id": name}, {"$inc": {"n": 1}}, upsert=True, return_document=pymongo.ReturnDocument.AFTER)
        return found["n"]

    def wait(self, seconds: float = 30.0) -> None:
        """Return once the server answers, retrying while it starts."""
        deadline = time.monotonic() + seconds
        while True:
            try:
                self.client.admin.command("ping")
                return
            except PyMongoError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)

    def alive(self) -> bool:
        try:
            self.client.admin.command("ping")
            return True
        except PyMongoError:
            return False

    def reset(self) -> None:
        """Drop the home and every session database of an earlier run."""
        with self._lock:
            for name in self.client.list_database_names():
                if name == self.home or re.fullmatch(re.escape(self.home) + r"_s\d{6}", name):
                    self.client.drop_database(name)
            self.registry.create_index([("touched", 1)], name="touched")

    def _build(self, name: str) -> None:
        db = self.client[name]
        for collection, validator in schema.VALIDATORS.items():
            db.create_collection(collection, validator={"$jsonSchema": validator},
                                 validationLevel="strict", validationAction="error")
            for keys, options in schema.INDEXES.get(collection, []):
                db[collection].create_index(keys, **options)
            documents = self.seed.get(collection)
            if documents:
                db[collection].insert_many(copy.deepcopy(documents), ordered=True)

    def sign_in(self, sid: str | None, agent_id: str) -> str:
        """Sign `agent_id` in on this browser's database (made on its first sign-in)."""
        with self._lock:
            if sid and _SID.fullmatch(sid):
                found = self.registry.find_one_and_update(
                    {"_id": sid}, {"$set": {"agent_id": agent_id, "touched": self._tick("touches")}})
                if found is not None:
                    return sid
            number = self._tick("sessions")
            sid, name = f"hl{number:06d}", f"{self.home}_s{number:06d}"
            try:
                self._build(name)
            except BaseException:
                self.client.drop_database(name)
                raise
            self.registry.insert_one({"_id": sid, "partition": name, "agent_id": agent_id,
                                      "touched": self._tick("touches")})
            while self.registry.count_documents({}) > self.cap:
                oldest = self.registry.find_one_and_delete({"_id": {"$ne": sid}}, sort=[("touched", 1)])
                if oldest is None:
                    break
                self.client.drop_database(oldest["partition"])
            return sid

    def sign_out(self, sid: str | None) -> None:
        if sid and _SID.fullmatch(sid):
            with self._lock:
                self.registry.update_one({"_id": sid}, {"$set": {"agent_id": None}})

    @contextlib.contextmanager
    def open(self, sid: str | None):
        """(database or None, sid or None, agent id or None) for one request, under the lock."""
        with self._lock:
            found = None
            if sid and _SID.fullmatch(sid):
                found = self.registry.find_one_and_update({"_id": sid}, {"$set": {"touched": self._tick("touches")}})
            if found is None:
                yield None, None, None
            else:
                yield self.client[found["partition"]], sid, found["agent_id"]
