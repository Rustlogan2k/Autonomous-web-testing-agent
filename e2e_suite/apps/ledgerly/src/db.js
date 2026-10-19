// Ledgerly's database: PostgreSQL, one schema per signed-in browser session.
//
// DATABASE_URL names the app's home database. At start the server rebuilds two schemas there:
//   app_meta  the session registry (cookie -> schema, signed-in user, who they act as, the
//             one-time message for the next page, last use) and its sequences
//   tmpl      src/schema.sql filled from seed/*.json
// and drops every session schema left from an earlier run. The first successful sign-in of a
// browser creates its own schema, s_000017, in ONE simple-protocol round trip:
//   BEGIN; CREATE SCHEMA s_000017; SET LOCAL search_path TO s_000017; <schema.sql>;
//   INSERT INTO <table> SELECT * FROM tmpl.<table>; ...; INSERT INTO app_meta.sessions ...; COMMIT
// Beyond WTA_SESSION_CAP schemas the least recently used one is dropped. Every request of a
// session runs alone in that session's lane, inside BEGIN; SET LOCAL search_path ...; COMMIT.

import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

// The suite's pinned driver: every build carries it in vendor/, nothing is installed.
const require = createRequire(import.meta.url);
const pg = require("../vendor/node/node_modules/pg");

// node-pg turns int8 and numeric into strings and dates into Dates in the process's zone.
// Counts become numbers; numeric, date and timestamp stay the text the database sent.
pg.types.setTypeParser(20, (text) => Number(text));
pg.types.setTypeParser(1700, (text) => text);
pg.types.setTypeParser(1082, (text) => text);
pg.types.setTypeParser(1114, (text) => text);

/** Tables in the order their rows can be inserted (references first). */
export const TABLES = ["cost_centres", "users", "delegations", "currencies", "categories", "expense_policies",
                       "merchants", "claims", "claim_lines", "claim_events", "counters"];
const SID = /^ldg-(\d{6})$/;
const WAIT_ATTEMPTS = 30;

export function readSeed(directory) {
  return Object.fromEntries(TABLES.map((table) =>
    [table, JSON.parse(fs.readFileSync(path.join(directory, `${table}.json`), "utf8"))]));
}

export const isSid = (value) => SID.test(String(value ?? ""));
const sleep = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

export class Ledger {
  constructor(pool, schemaSql, cap) {
    this.pool = pool;
    this.schemaSql = schemaSql;
    this.cap = cap;
    this.lanes = new Map();
    this.copySql = TABLES.map((table) => `INSERT INTO ${table} SELECT * FROM tmpl.${table};`).join("\n");
  }

  /** Connect (waiting up to about 30 s for the server), then rebuild the home. */
  static async open(url, { schemaSql, seed, cap }) {
    const pool = new pg.Pool({ connectionString: url, max: 10 });
    for (let attempt = 1; ; attempt += 1) {
      try {
        await pool.query("SELECT 1");
        break;
      } catch (error) {
        if (attempt >= WAIT_ATTEMPTS) throw error;
        await sleep(1000);
      }
    }
    const ledger = new Ledger(pool, schemaSql, cap);
    await ledger.rebuild(seed);
    return ledger;
  }

  async rebuild(seed) {
    const client = await this.pool.connect();
    try {
      const { rows } = await client.query(
        "SELECT nspname FROM pg_namespace WHERE nspname ~ '^s_[0-9]{6}$' OR nspname IN ('app_meta', 'tmpl')");
      for (const { nspname } of rows) await client.query(`DROP SCHEMA "${nspname}" CASCADE`);
      await client.query(`BEGIN;
CREATE SCHEMA app_meta;
CREATE SEQUENCE app_meta.session_numbers;
CREATE SEQUENCE app_meta.uses;
CREATE TABLE app_meta.sessions (
  sid        text PRIMARY KEY,
  partition  text NOT NULL UNIQUE,
  user_id    text,
  acting_as  text,
  flash      jsonb,
  touched    bigint NOT NULL
);
CREATE INDEX sessions_by_use ON app_meta.sessions (touched);
CREATE SCHEMA tmpl;
SET LOCAL search_path TO tmpl;
${this.schemaSql}
COMMIT;`);
      await client.query("BEGIN");
      for (const table of TABLES) {
        for (const row of seed[table]) {
          const columns = Object.keys(row);
          await client.query(
            `INSERT INTO tmpl.${table} (${columns.join(", ")}) VALUES (${columns.map((_, i) => `$${i + 1}`).join(", ")})`,
            columns.map((column) => row[column]));
        }
      }
      await client.query("COMMIT");
    } finally {
      client.release();
    }
  }

  async healthy() {
    try {
      await this.pool.query("SELECT 1");
      return true;
    } catch {
      return false;
    }
  }

  close() {
    return this.pool.end();
  }

  /** Run `job` once every earlier job of session `sid` has settled. */
  lane(sid, job) {
    const earlier = this.lanes.get(sid) ?? Promise.resolve();
    const result = earlier.then(job);
    const settled = result.then(() => undefined, () => undefined);
    this.lanes.set(sid, settled);
    settled.then(() => { if (this.lanes.get(sid) === settled) this.lanes.delete(sid); });
    return result;
  }

  /** The registry row of cookie `sid`, marked as just used; null when it has no schema. */
  async session(sid) {
    if (!isSid(sid)) return null;
    const { rows } = await this.pool.query(
      "UPDATE app_meta.sessions SET touched = nextval('app_meta.uses') WHERE sid = $1 RETURNING *", [sid]);
    return rows[0] ?? null;
  }

  /** Sign `userId` in on browser session `sid`: its schema again, or a new one from tmpl. */
  async signIn(sid, userId, actingAs) {
    if (isSid(sid)) {
      const { rows } = await this.pool.query(
        "UPDATE app_meta.sessions SET user_id = $2, acting_as = $3, touched = nextval('app_meta.uses') "
        + "WHERE sid = $1 RETURNING sid", [sid, userId, actingAs]);
      if (rows.length) return { sid, created: false };
    }
    const { rows } = await this.pool.query("SELECT nextval('app_meta.session_numbers') AS n");
    const digits = String(rows[0].n).padStart(6, "0");
    const fresh = { sid: `ldg-${digits}`, partition: `s_${digits}` };
    if (!/^[a-z]+$/.test(userId) || !/^[a-z]+$/.test(actingAs)) throw new Error("unexpected account id");
    // One round trip, without parameters (the simple protocol takes several statements).
    await this.pool.query(`BEGIN;
CREATE SCHEMA ${fresh.partition};
SET LOCAL search_path TO ${fresh.partition};
${this.schemaSql}
${this.copySql}
INSERT INTO app_meta.sessions (sid, partition, user_id, acting_as, touched)
  VALUES ('${fresh.sid}', '${fresh.partition}', '${userId}', '${actingAs}', nextval('app_meta.uses'));
COMMIT;`);
    await this.evictBeyondCap(fresh.sid);
    return { sid: fresh.sid, created: true };
  }

  signOut(sid) {
    return this.pool.query("UPDATE app_meta.sessions SET user_id = NULL WHERE sid = $1", [sid]);
  }

  async evictBeyondCap(keep) {
    for (;;) {
      const { rows } = await this.pool.query(
        "DELETE FROM app_meta.sessions WHERE sid = (SELECT sid FROM app_meta.sessions WHERE sid <> $1 "
        + "AND (SELECT count(*) FROM app_meta.sessions) > $2 ORDER BY touched LIMIT 1) RETURNING sid, partition",
        [keep, this.cap]);
      if (!rows.length) return;
      const [victim] = rows;
      await this.lane(victim.sid, () => this.pool.query(`DROP SCHEMA ${victim.partition} CASCADE`));
    }
  }

  setActingAs(sid, person) {
    return this.pool.query("UPDATE app_meta.sessions SET acting_as = $2 WHERE sid = $1", [sid, person]);
  }

  /** Leave a one-time message for the next page of this session (or clear it with null). */
  setFlash(sid, flash) {
    return this.pool.query("UPDATE app_meta.sessions SET flash = $2 WHERE sid = $1",
                           [sid, flash === null ? null : JSON.stringify(flash)]);
  }

  /** Run `work(client)` in one transaction inside session schema `partition`. */
  async within(partition, work) {
    if (!/^s_\d{6}$/.test(partition)) throw new Error("not a session schema");
    const client = await this.pool.connect();
    try {
      await client.query(`BEGIN; SET LOCAL search_path TO ${partition}`);
      const result = await work(client);
      await client.query("COMMIT");
      return result;
    } catch (error) {
      await client.query("ROLLBACK").catch(() => undefined);
      throw error;
    } finally {
      client.release();
    }
  }
}
