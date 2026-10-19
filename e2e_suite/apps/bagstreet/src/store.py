"""Where Bagstreet keeps things: MongoDB, a database of its own for each signed-in browser.

MONGODB_URL names the app's home database. The home holds `visits` (one row per browser
session: its database, who is signed in, the one-time message for the next page, when it was
last used) and the visit counter. A browser's first successful sign-in creates its database,
`<home>_s000017`, from the seed (validators.py gives each collection its `$jsonSchema` and
indexes); signing out keeps it, and signing in again from the same browser carries on with it.
Beyond WTA_SESSION_CAP databases, the one used longest ago is dropped.

Requests are served one at a time (server.py holds one lock around each), so a session's reads
and writes never interleave and a database is never dropped under a request.
"""

from __future__ import annotations

import copy
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vendor" / "python"))
import pymongo  # noqa: E402 - the suite's vendored driver, made importable just above
from pymongo.errors import PyMongoError  # noqa: E402

from validators import COLLECTIONS  # noqa: E402

SID = re.compile(r"^bs-(\d{6})$")
WAIT_FOR_SERVER_S = 30


def read_seed(folder: Path) -> dict[str, list[dict]]:
    return {c["name"]: json.loads((folder / f"{c['name']}.json").read_text(encoding="utf-8")) for c in COLLECTIONS}


class Visits:
    """The registry of browser sessions and their databases."""

    def __init__(self, url: str, seed: dict, cap: int) -> None:
        self.client = pymongo.MongoClient(url, serverSelectionTimeoutMS=3000)
        self.home = self.client.get_default_database().name
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,40}", self.home):
            raise ValueError("MONGODB_URL must end in the name of the app's home database")
        self.seed, self.cap = seed, cap
        self.rows = self.client[self.home]["visits"]
        self.counter = self.client[self.home]["counters"]
        self.uses = 0
        self._wait_for_server()
        self._start_empty()

    def _wait_for_server(self) -> None:
        for attempt in range(WAIT_FOR_SERVER_S):
            try:
                self.client.admin.command("ping")
                return
            except PyMongoError:
                if attempt == WAIT_FOR_SERVER_S - 1:
                    raise
                time.sleep(1)  # the server may still be starting

    def _start_empty(self) -> None:
        own = re.compile(rf"^{self.home}_s\d{{6}}$")
        for name in self.client.list_database_names():
            if name == self.home or own.match(name):
                self.client.drop_database(name)
        self.rows.create_index([("used", pymongo.ASCENDING)], name="least_recently_used")

    def healthy(self) -> bool:
        try:
            self.client.admin.command("ping")
            return True
        except PyMongoError:
            return False

    def find(self, sid: str) -> dict | None:
        """The visit behind cookie `sid`, marked as just used; None if it has no database."""
        if not SID.match(sid or ""):
            return None
        self.uses += 1
        return self.rows.find_one_and_update({"_id": sid}, {"$set": {"used": self.uses}},
                                             return_document=pymongo.ReturnDocument.AFTER)

    def database(self, visit: dict):
        return self.client[visit["partition"]]

    def sign_in(self, sid: str, user_id: str) -> tuple[str, bool]:
        """Sign `user_id` in on browser session `sid`; returns (sid, whether it is new)."""
        self.uses += 1
        if SID.match(sid or "") and self.rows.find_one_and_update(
                {"_id": sid}, {"$set": {"user_id": user_id, "used": self.uses}}):
            return sid, False
        number = self.counter.find_one_and_update({"_id": "visits"}, {"$inc": {"value": 1}}, upsert=True,
                                                  return_document=pymongo.ReturnDocument.AFTER)["value"]
        visit = {"_id": f"bs-{number:06d}", "partition": f"{self.home}_s{number:06d}", "user_id": user_id,
                 "used": self.uses, "notice": "", "error": ""}
        self._create(visit["partition"])
        self.rows.insert_one(visit)
        self._evict_beyond_cap(visit["_id"])
        return visit["_id"], True

    def _create(self, partition: str) -> None:
        database = self.client[partition]
        try:
            for spec in COLLECTIONS:
                database.create_collection(spec["name"], validator={"$jsonSchema": spec["validator"]},
                                           validationLevel="strict", validationAction="error")
                for index in spec["indexes"]:
                    database[spec["name"]].create_index(index["keys"], name=index["name"],
                                                        unique=index.get("unique", False))
                if self.seed[spec["name"]]:
                    database[spec["name"]].insert_many(copy.deepcopy(self.seed[spec["name"]]))
        except PyMongoError:
            self.client.drop_database(partition)
            raise

    def _evict_beyond_cap(self, keep: str) -> None:
        while self.rows.count_documents({}) > self.cap:
            oldest = self.rows.find_one_and_delete({"_id": {"$ne": keep}}, sort=[("used", pymongo.ASCENDING)])
            if oldest is None:
                return
            self.client.drop_database(oldest["partition"])

    def sign_out(self, sid: str) -> None:
        self.rows.update_one({"_id": sid}, {"$set": {"user_id": None}})

    def say(self, sid: str, *, notice: str = "", error: str = "") -> None:
        """Leave a one-time message (a notice, or an error) for the next page."""
        self.rows.update_one({"_id": sid}, {"$set": {"notice": notice, "error": error}})

    def heard(self, visit: dict, kind: str) -> str:
        """Take the one-time message of `kind` ("notice" or "error"), clearing it."""
        text = visit.get(kind) or ""
        if text:
            self.rows.update_one({"_id": visit["_id"]}, {"$set": {kind: ""}})
            visit[kind] = ""
        return text
