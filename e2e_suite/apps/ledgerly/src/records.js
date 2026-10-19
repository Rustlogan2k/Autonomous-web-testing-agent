// Reading and writing one session's schema. Every function takes the request's client, which
// db.js has already put inside the session schema and a transaction.

import { committedClaims, fold } from "./claims.js";

export const MERCHANTS_PER_PAGE = 8;
const MERCHANT_ORDER = {
  name: `name COLLATE "C", id`,
  "name-desc": `name COLLATE "C" DESC, id`,
  city: `city COLLATE "C", name COLLATE "C", id`,
};

/** Every claim as it now stands (the fold of its events over its line revisions). */
export async function loadClaims(client) {
  const claims = (await client.query("SELECT id, owner_id, cost_centre FROM claims ORDER BY id")).rows;
  const lines = (await client.query(
    "SELECT claim_id, revision, line_no, description, category, currency, amount_minor FROM claim_lines "
    + "ORDER BY claim_id, revision, line_no")).rows;
  const events = (await client.query(
    "SELECT claim_id, seq, kind, actor_id, acting_for, revision, at, note FROM claim_events ORDER BY claim_id, seq")).rows;
  return fold({ claims, lines, events });
}

/** Take the next number of a sequence kept in `counters` (never handed out twice). */
export async function takeNumber(client, name) {
  const { rows } = await client.query("UPDATE counters SET value = value + 1 WHERE name = $1 RETURNING value - 1 AS n", [name]);
  return rows[0].n;
}

export async function peekNumber(client, name) {
  return (await client.query("SELECT value FROM counters WHERE name = $1", [name])).rows[0].value;
}

/**
 * Record the events a command produced. `actor` clicked; an event's `by` is the person it
 * counts as, so `acting_for` is set when they differ. Lines start a new revision.
 */
export async function record(client, events, { actor, at, costCentreOf }) {
  for (const event of events) {
    if (event.type === "claim_drafted") {
      await client.query("INSERT INTO claims (id, owner_id, cost_centre) VALUES ($1, $2, $3)",
                         [event.claim, event.by, costCentreOf(event.by)]);
    }
    const { rows } = await client.query(
      "SELECT coalesce(max(seq), 0) AS seq, coalesce(max(revision), 0) AS revision FROM claim_events WHERE claim_id = $1",
      [event.claim]);
    let revision = Math.max(rows[0].revision, 1);
    if (event.lines) {
      revision = rows[0].revision + 1;
      for (const [index, line] of event.lines.entries()) {
        await client.query(
          "INSERT INTO claim_lines (claim_id, revision, line_no, description, category, currency, amount_minor) "
          + "VALUES ($1, $2, $3, $4, $5, $6, $7)",
          [event.claim, revision, index + 1, line.description, line.category, line.currency, line.minor]);
      }
    }
    await client.query(
      "INSERT INTO claim_events (claim_id, seq, kind, actor_id, acting_for, revision, at, note) "
      + "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
      [event.claim, rows[0].seq + 1, event.type, actor, event.by === actor ? null : event.by, revision, at, event.note ?? ""]);
  }
}

/** A never-lodged draft goes entirely (its lines and events cascade). */
export function discardClaim(client, id) {
  return client.query("DELETE FROM claims WHERE id = $1", [id]);
}

// -- merchants ------------------------------------------------------------------------------

/** The list request in the URL: search text, category, order and page (all optional). */
export function merchantQuery(params, categories) {
  const page = Number(params.get("page"));
  const sort = params.get("sort") ?? "name";
  return {
    q: String(params.get("q") ?? "").trim().slice(0, 80),
    category: categories.includes(params.get("category")) ? params.get("category") : "",
    sort: Object.hasOwn(MERCHANT_ORDER, sort) ? sort : "name",
    page: Number.isInteger(page) && page >= 1 && page <= 9999 ? page : 1,
  };
}

export async function listMerchants(client, query) {
  // Search text is data, not a pattern: LIKE's wildcards and escape character are escaped.
  const pattern = `%${query.q.replace(/[\\%_]/g, (ch) => `\\${ch}`)}%`;
  const where = "WHERE ($1 = '' OR name ILIKE $2 ESCAPE '\\' OR city ILIKE $2 ESCAPE '\\') AND ($3 = '' OR category = $3)";
  const params = [query.q, pattern, query.category];
  const total = (await client.query(`SELECT count(*) AS n FROM merchants ${where}`, params)).rows[0].n;
  const pages = Math.max(1, Math.ceil(total / MERCHANTS_PER_PAGE));
  const page = Math.min(query.page, pages);
  const rows = (await client.query(
    `SELECT id, name, city, category, preferred FROM merchants ${where} ORDER BY ${MERCHANT_ORDER[query.sort]} `
    + `LIMIT ${MERCHANTS_PER_PAGE} OFFSET $4`, [...params, (page - 1) * MERCHANTS_PER_PAGE])).rows;
  return { rows, total, pages, page };
}

export async function findMerchant(client, id) {
  return (await client.query("SELECT id, name, city, category, preferred FROM merchants WHERE id = $1", [id])).rows[0] ?? null;
}

/** A merchant form's values, and what is wrong with them (vocabulary keys). */
export function merchantValues(form, categories) {
  const values = {
    name: String(form.get("name") ?? "").trim(), city: String(form.get("city") ?? "").trim(),
    category: String(form.get("category") ?? ""), preferred: form.get("preferred") === "yes",
  };
  const problems = [];
  if (!values.name || values.name.length > 80) problems.push("msg.merchant_name");
  if (!values.city || values.city.length > 60) problems.push("msg.merchant_city");
  if (!categories.includes(values.category)) problems.push("msg.line_category");
  return { values, problems };
}

/** Insert or update; null on success, or the problem (a name and city already listed). */
export async function saveMerchant(client, id, values, isNew) {
  const clash = await client.query("SELECT 1 FROM merchants WHERE name = $1 AND city = $2 AND id <> $3",
                                   [values.name, values.city, id]);
  if (clash.rows.length) return "msg.merchant_exists";
  if (isNew) {
    await client.query("INSERT INTO merchants (id, name, city, category, preferred) VALUES ($1, $2, $3, $4, $5)",
                       [id, values.name, values.city, values.category, values.preferred]);
  } else {
    await client.query("UPDATE merchants SET name = $2, city = $3, category = $4, preferred = $5 WHERE id = $1",
                       [id, values.name, values.city, values.category, values.preferred]);
  }
  return null;
}

export function deleteMerchant(client, id) {
  return client.query("DELETE FROM merchants WHERE id = $1", [id]);
}

// -- cost centres ---------------------------------------------------------------------------

export async function listCentres(client) {
  return (await client.query("SELECT code, name, budget_pence FROM cost_centres ORDER BY code")).rows;
}

const CENTRE_NAME_LIMIT = 60;

/** Why `name` cannot name cost centre `code` (a vocabulary key), or "" when it can. */
export async function centreNameProblem(client, name, code) {
  if (!name) return "msg.centre_name_needed";
  if (name.length > CENTRE_NAME_LIMIT) return "msg.centre_name_long";
  const taken = await client.query("SELECT 1 FROM cost_centres WHERE name = $1 AND code <> $2", [name, code]);
  return taken.rows.length ? "msg.centre_name_taken" : "";
}

export async function addCentre(client, name, budgetPence) {
  const number = await takeNumber(client, "cost_centre");
  const code = `CC-${String(number).padStart(2, "0")}`;
  await client.query("INSERT INTO cost_centres (code, name, budget_pence) VALUES ($1, $2, $3)", [code, name, budgetPence]);
  return code;
}

export function renameCentre(client, code, name) {
  return client.query("UPDATE cost_centres SET name = $2 WHERE code = $1", [code, name]);
}

/** Delete a cost centre nobody belongs to and no claim is charged to; false when in use. */
export async function deleteCentre(client, code) {
  const used = await client.query(
    "SELECT (SELECT count(*) FROM users WHERE cost_centre = $1) + (SELECT count(*) FROM claims WHERE cost_centre = $1) AS n",
    [code]);
  if (used.rows[0].n > 0) return false;
  await client.query("DELETE FROM cost_centres WHERE code = $1", [code]);
  return true;
}

// -- the committed-state digest -------------------------------------------------------------

export async function committed(client, claims) {
  return {
    claims: committedClaims(claims),
    merchants: (await client.query("SELECT id, name, city, category, preferred FROM merchants ORDER BY id")).rows,
    cost_centres: (await client.query("SELECT code, name, budget_pence FROM cost_centres ORDER BY code")).rows,
  };
}
