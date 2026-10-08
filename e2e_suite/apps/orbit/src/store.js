"use strict";
/*
 * Orbit's storage: MongoDB, one database per signed-in browser session.
 *
 * MONGODB_URL names the app's home database (the URL's path). The home holds the session
 * registry, `sessions` {_id: sid, partition, user_id, touched}, and the session counter. A
 * browser session's own database, `<home>_s000017`, is created at its first successful
 * sign-in: for each collection of schema.js, `create` (validator, strict, error), then
 * `createIndexes`, then `insertMany` from the seed held in memory. Anonymous requests create
 * nothing. Signing out clears the registry's user_id and keeps the partition, so signing in
 * again from the same browser resumes the same data. Beyond WTA_SESSION_CAP live partitions
 * the least recently used one is dropped and its browser is signed out; the tester never
 * returns to a closed browser context, so nothing it still uses is dropped.
 *
 * Requests of one session run one at a time (`exclusive`): a request's reads, changes and
 * writes never interleave with another request of the same session, so rapid clicks are
 * deterministic, and an evicted partition is dropped only after its last request finished.
 *
 * `Work` is one request's unit of work: the documents it loaded and, at commit, the ones
 * that changed or are new, written back with one bulkWrite of replaceOne per collection.
 */

const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");
const { COLLECTIONS } = require("./schema.js");

// The suite's pinned driver. Every build carries it as vendor/: nothing is installed.
const { MongoClient } = createRequire(__filename)("../vendor/node/node_modules/mongodb");

const SESSION_ID = /^s\d{6}$/;
const HOME = /^mongodb(?:\+srv)?:\/\/[^/]+\/([A-Za-z0-9_]{1,40})(?:\?|$)/;
const CONNECT_ATTEMPTS = 6;

/** The seed: one JSON file per collection, holding the documents exactly as stored. */
function loadSeed(directory) {
  const read = (name) => JSON.parse(fs.readFileSync(path.join(directory, `${name}.json`), "utf8"));
  const seed = { users: read("users"), workspaces: read("workspaces"), projects: read("projects"),
    cards: read("cards") };
  seed.counters = [{
    _id: "seq", card: Math.max(...seed.cards.map((c) => c._id)),
    position: Math.max(...seed.cards.map((c) => c.position)), event: 0,
  }];
  const missing = COLLECTIONS.map((c) => c.name).filter((name) => !Array.isArray(seed[name]));
  if (missing.length) {
    throw new Error(`seed/ has no documents for ${missing.join(", ")}`);
  }
  return seed;
}

function isSessionId(value) {
  return SESSION_ID.test(String(value || ""));
}

class Store {
  constructor(client, home, seed, cap) {
    this.client = client;
    this.home = home;
    this.seed = seed;
    this.cap = cap;
    this.registry = client.db(home).collection("sessions");
    this.counters = client.db(home).collection("counters");
    this.queues = new Map();
    this.touches = 0; // a request counter: the registry's least recently used order
  }

  /** Connect (retrying while the server comes up) and start from an empty home. */
  static async open(url, { seed, cap }) {
    const match = HOME.exec(url);
    if (!match) {
      throw new Error("MONGODB_URL must name the app's home database as its path");
    }
    let lastError = null;
    for (let attempt = 1; attempt <= CONNECT_ATTEMPTS; attempt += 1) {
      const client = new MongoClient(url, { serverSelectionTimeoutMS: 5000, maxPoolSize: 20 });
      try {
        await client.connect();
        const store = new Store(client, match[1], seed, cap);
        await store.reset();
        return store;
      } catch (error) {
        lastError = error;
        await client.close().catch(() => {});
      }
    }
    throw lastError;
  }

  /** Drop this app's partitions and registry: a restarted server starts from nothing. */
  async reset() {
    const own = new RegExp(`^${this.home}_s\\d{6}$`);
    const { databases } = await this.client.db("admin").admin().listDatabases({ nameOnly: true });
    for (const { name } of databases.filter((d) => own.test(d.name))) {
      await this.client.db(name).dropDatabase();
    }
    await this.client.db(this.home).dropDatabase();
    await this.registry.createIndex({ touched: 1 }, { name: "touched" });
  }

  async ping() {
    try {
      await this.client.db(this.home).command({ ping: 1 });
      return true;
    } catch {
      return false;
    }
  }

  close() {
    return this.client.close();
  }

  /** Run `task` after every earlier task of session `sid` has settled; return its result. */
  exclusive(sid, task) {
    const previous = this.queues.get(sid) || Promise.resolve();
    const run = previous.then(() => task());
    const tail = run.then(() => undefined, () => undefined);
    this.queues.set(sid, tail);
    tail.then(() => {
      if (this.queues.get(sid) === tail) {
        this.queues.delete(sid);
      }
    });
    return run;
  }

  /** The registry row of a live session (marking it used), or null. */
  session(sid) {
    if (!isSessionId(sid)) {
      return Promise.resolve(null);
    }
    return this.registry.findOneAndUpdate({ _id: sid }, { $set: { touched: ++this.touches } },
      { returnDocument: "after" });
  }

  database(partition) {
    return this.client.db(partition);
  }

  /**
   * Sign `userId` in on the browser session `sid`: resume its partition if it still has one,
   * otherwise create a new session and partition from the seed. Returns {sid, isNew}.
   */
  async signIn(sid, userId) {
    if (isSessionId(sid)) {
      const resumed = await this.registry.findOneAndUpdate({ _id: sid },
        { $set: { user_id: userId, touched: ++this.touches } });
      if (resumed) {
        return { sid, isNew: false };
      }
    }
    const counter = await this.counters.findOneAndUpdate({ _id: "session" }, { $inc: { value: 1 } },
      { upsert: true, returnDocument: "after" });
    const fresh = `s${String(counter.value).padStart(6, "0")}`;
    const partition = `${this.home}_${fresh}`;
    await this.createPartition(partition);
    await this.registry.insertOne({ _id: fresh, partition, user_id: userId, touched: ++this.touches });
    await this.evictBeyondCap(fresh);
    return { sid: fresh, isNew: true };
  }

  signOut(sid) {
    return this.registry.updateOne({ _id: sid }, { $set: { user_id: null } });
  }

  async createPartition(name) {
    const db = this.client.db(name);
    try {
      await Promise.all(COLLECTIONS.map(async ({ name: collection, schema, indexes }) => {
        await db.createCollection(collection, { validator: { $jsonSchema: schema },
          validationLevel: "strict", validationAction: "error" });
        if (indexes.length) {
          await db.collection(collection).createIndexes(indexes);
        }
        const documents = this.seed[collection];
        if (documents.length) {
          await db.collection(collection).insertMany(structuredClone(documents), { ordered: true });
        }
      }));
    } catch (error) {
      await db.dropDatabase().catch(() => {});
      throw error;
    }
  }

  /** Drop least recently used partitions until at most `cap` are live (never `keep`). */
  async evictBeyondCap(keep) {
    while (await this.registry.countDocuments() > this.cap) {
      const victim = await this.registry.findOneAndDelete({ _id: { $ne: keep } }, { sort: { touched: 1 } });
      if (!victim) {
        return;
      }
      // Its row is gone, so no new request of that browser reaches the partition; requests
      // already queued finish first.
      await this.exclusive(victim._id, () => this.client.db(victim.partition).dropDatabase());
    }
  }
}

/** One request's unit of work over a session's database. */
class Work {
  constructor(db) {
    this.db = db;
    this.entries = new Map();
  }

  /** Load documents and remember how they looked, to find the changed ones at commit. */
  async find(collection, filter, options = {}) {
    const documents = await this.db.collection(collection).find(filter, options).toArray();
    for (const document of documents) {
      this.entries.set(`${collection}:${document._id}`,
        { collection, document, before: JSON.stringify(document) });
    }
    return documents;
  }

  /** Take on documents the request created (they are written at commit). */
  adopt(collection, documents) {
    for (const document of documents) {
      const key = `${collection}:${document._id}`;
      if (!this.entries.has(key)) {
        this.entries.set(key, { collection, document, before: null });
      }
    }
  }

  async commit() {
    const writes = new Map();
    for (const { collection, document, before } of this.entries.values()) {
      if (JSON.stringify(document) !== before) {
        const operations = writes.get(collection) || [];
        operations.push({ replaceOne: { filter: { _id: document._id }, replacement: document, upsert: true } });
        writes.set(collection, operations);
      }
    }
    for (const [collection, operations] of writes) {
      await this.db.collection(collection).bulkWrite(operations, { ordered: true });
    }
  }
}

/** A board's scope (board.js): the project, its cards, the counters and who is who. */
async function loadBoard(work, pid) {
  const [project] = await work.find("projects", { _id: pid });
  if (!project) {
    return null;
  }
  const [cards, [seq], users] = await Promise.all([
    work.find("cards", { project_id: pid }, { sort: { column: 1, position: 1 } }),
    work.find("counters", { _id: "seq" }),
    work.db.collection("users").find({}, { projection: { name: 1 } }).toArray(),
  ]);
  return { projects: [project], cards, seq, users: new Map(users.map((u) => [u._id, u.name])) };
}

/** The project a card belongs to, or null for an unknown card. */
async function cardProject(db, n) {
  const card = await db.collection("cards").findOne({ _id: n }, { projection: { project_id: 1 } });
  return card ? card.project_id : null;
}

/** The projects `userId` belongs to, and their card counts by project. */
async function projectsOf(db, userId) {
  const projects = await db.collection("projects").find({ "members.user_id": userId })
    .sort({ position: 1, _id: 1 }).toArray();
  const counts = await db.collection("cards").aggregate([
    { $match: { project_id: { $in: projects.map((p) => p._id) } } },
    { $group: { _id: "$project_id", cards: { $sum: 1 },
      shipped: { $sum: { $cond: [{ $eq: ["$column", "shipped"] }, 1, 0] } } } },
  ]).toArray();
  return { projects, counts: new Map(counts.map((c) => [c._id, c])) };
}

/** Every project and card: what the committed-state digest covers. */
async function everything(db) {
  const [projects, cards] = await Promise.all([
    db.collection("projects").find({}).sort({ _id: 1 }).toArray(),
    db.collection("cards").find({}).sort({ _id: 1 }).toArray(),
  ]);
  return { projects, cards };
}

module.exports = { Store, Work, loadSeed, isSessionId, loadBoard, cardProject, projectsOf, everything };
