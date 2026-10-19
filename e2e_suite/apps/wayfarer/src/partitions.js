/*
 * Where Wayfarer keeps its data: a MongoDB server, one database per signed-in browser.
 *
 * MONGODB_URL names the app's home database. The home holds three small collections:
 *   registry     ws-000017 -> { partition, user_id, touched }   one row per browser session
 *   share_links  token -> { sid, trip_id }                        what a public share link opens
 *   counters     the session counter
 * A browser's own database, "<home>_s000017", is built at its first successful sign-in from
 * the seed (collections.js gives each collection its validator and indexes). Signing out
 * keeps it; signing in again from the same browser picks it up. When more than
 * WTA_SESSION_CAP databases are live, the one used longest ago is dropped, together with the
 * share links into it.
 *
 * Each browser session has a lane: its requests run one after another, never interleaved, so
 * a double click is deterministic and a database is dropped only once its lane is idle.
 */
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");
const { COLLECTIONS } = require("./collections.js");

// The suite's pinned driver, which every build carries in vendor/ (nothing is installed).
const { MongoClient } = createRequire(__filename)("../vendor/node/node_modules/mongodb");

const SID = /^ws-(\d{6})$/;
const CONNECT_PATIENCE_MS = 30000;
const CONNECT_ATTEMPT_MS = 3000;

/** The seed: seed/<collection>.json holds that collection's documents exactly as stored. */
function readSeed(folder) {
  const documents = {};
  for (const { name } of COLLECTIONS) {
    documents[name] = JSON.parse(fs.readFileSync(path.join(folder, `${name}.json`), "utf8"));
  }
  return documents;
}

const isSid = (value) => SID.test(String(value || ""));

class Partitions {
  constructor(client, home, seed, cap) {
    this.client = client;
    this.home = home;
    this.seed = seed;
    this.cap = cap;
    const homeDb = client.db(home);
    this.registry = homeDb.collection("registry");
    this.links = homeDb.collection("share_links");
    this.homeCounters = homeDb.collection("counters");
    this.lanes = new Map();
    this.clock = 0; // bumped by every request: the least-recently-used order
  }

  /** Connect, waiting for the server for up to 30 s, and start with an empty home. */
  static async connect(url, { seed, cap }) {
    const home = /^mongodb(?:\+srv)?:\/\/[^/]+\/([a-z][a-z0-9_]{0,40})(?:\?|$)/.exec(url);
    if (!home) {
      throw new Error("MONGODB_URL must end in the name of the app's home database");
    }
    for (let attempt = 1; ; attempt += 1) {
      const client = new MongoClient(url, { serverSelectionTimeoutMS: CONNECT_ATTEMPT_MS });
      try {
        await client.connect();
        const partitions = new Partitions(client, home[1], seed, cap);
        await partitions.startEmpty();
        return partitions;
      } catch (error) {
        await client.close().catch(() => undefined);
        if (attempt * CONNECT_ATTEMPT_MS >= CONNECT_PATIENCE_MS) { throw error; }
      }
    }
  }

  async startEmpty() {
    const mine = new RegExp(`^${this.home}_s\\d{6}$`);
    const { databases } = await this.client.db("admin").admin().listDatabases({ nameOnly: true });
    for (const database of databases) {
      if (mine.test(database.name)) { await this.client.db(database.name).dropDatabase(); }
    }
    await this.client.db(this.home).dropDatabase();
    await this.registry.createIndex({ touched: 1 }, { name: "least_recently_used" });
    await this.links.createIndex({ sid: 1 }, { name: "links_by_session" });
  }

  async healthy() {
    try {
      await this.client.db(this.home).command({ ping: 1 });
      return true;
    } catch {
      return false;
    }
  }

  /** Queue `job` behind every earlier job of session `sid`; resolves with its result. */
  lane(sid, job) {
    const before = this.lanes.get(sid) || Promise.resolve();
    const result = before.then(job);
    const settled = result.catch(() => undefined);
    this.lanes.set(sid, settled);
    settled.then(() => { if (this.lanes.get(sid) === settled) { this.lanes.delete(sid); } });
    return result;
  }

  /** The session behind cookie `sid`, marked as just used; null if it has no database. */
  async find(sid) {
    if (!isSid(sid)) { return null; }
    return this.registry.findOneAndUpdate({ _id: sid }, { $set: { touched: ++this.clock } },
      { returnDocument: "after" });
  }

  database(row) {
    return this.client.db(row.partition);
  }

  /** Sign `userId` in on browser session `sid`: its own database again, or a new one. */
  async signIn(sid, userId) {
    if (isSid(sid)) {
      const again = await this.registry.findOneAndUpdate({ _id: sid },
        { $set: { user_id: userId, touched: ++this.clock } });
      if (again) { return { sid, created: false }; }
    }
    const { value: number } = await this.homeCounters.findOneAndUpdate(
      { _id: "sessions" }, { $inc: { value: 1 } }, { upsert: true, returnDocument: "after" });
    const digits = String(number).padStart(6, "0");
    const row = { _id: `ws-${digits}`, partition: `${this.home}_s${digits}`, user_id: userId, touched: ++this.clock };
    await this.build(row.partition);
    await this.registry.insertOne(row);
    await this.makeRoom(row._id);
    return { sid: row._id, created: true };
  }

  signOut(sid) {
    return this.registry.updateOne({ _id: sid }, { $set: { user_id: null } });
  }

  async build(partition) {
    const database = this.client.db(partition);
    try {
      for (const { name, validator, indexes } of COLLECTIONS) {
        await database.createCollection(name, {
          validator: { $jsonSchema: validator }, validationLevel: "strict", validationAction: "error",
        });
        if (indexes.length) { await database.collection(name).createIndexes(indexes); }
        const rows = this.seed[name];
        if (rows.length) { await database.collection(name).insertMany(structuredClone(rows)); }
      }
    } catch (error) {
      await database.dropDatabase().catch(() => undefined);
      throw error;
    }
  }

  /** Drop the least recently used databases until no more than `cap` are live. */
  async makeRoom(keep) {
    while ((await this.registry.countDocuments({})) > this.cap) {
      const oldest = await this.registry.findOneAndDelete({ _id: { $ne: keep } }, { sort: { touched: 1 } });
      if (!oldest) { return; }
      await this.links.deleteMany({ sid: oldest._id });
      await this.lane(oldest._id, () => this.client.db(oldest.partition).dropDatabase());
    }
  }

  /** Record that `token` opens trip `tripId` of session `sid` (a public share link). */
  publish(token, sid, tripId) {
    return this.links.updateOne({ _id: token }, { $set: { sid, trip_id: tripId } }, { upsert: true });
  }

  /** Withdraw a share link (its trip was deleted). */
  unpublish(token) {
    return this.links.deleteOne({ _id: token });
  }

  /** What a share link opens: { sid, trip_id, partition }, or null if it opens nothing. */
  async resolve(token) {
    const link = await this.links.findOne({ _id: token });
    const owner = link && await this.registry.findOne({ _id: link.sid });
    return owner ? { sid: link.sid, trip_id: link.trip_id, partition: owner.partition } : null;
  }
}

module.exports = { Partitions, readSeed, isSid };
