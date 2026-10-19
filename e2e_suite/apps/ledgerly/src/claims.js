// Ledgerly's domain: a claim's state is never stored, only derived. Its events (claim_events)
// are folded in order on every request; its lines are the revision the latest line-carrying
// event names. Commands validate against the folded state and answer with the events to
// record (and the lines they carry), or with the reason they were refused.

import { CURRENCIES, parseMinor, parseRates, roundToPence, sterlingHundredths } from "./static/money.js";

export const NOTE_LIMIT = 500;
export const DESCRIPTION_LIMIT = 120;

/** Where a claim stands after each kind of event. An event that is not listed leaves the
 *  status as it was (saving changes to a draft or to a sent-back claim, for instance). */
const STATUS_AFTER = {
  claim_drafted: "draft",
  lodged: "awaiting",
  approved: "approved",
  sent_back: "sent_back",
  escalated: "with_finance",
  rejected: "rejected",
  resubmitted: "awaiting", // back into the manager's queue
};

const CARRIES_LINES = new Set(["claim_drafted", "draft_saved", "lodged", "resubmitted"]);

/** The reference data every request uses, read once from the seed: people, rates, limits. */
export function config(seed) {
  const people = Object.fromEntries(seed.users.map((user) => [user.id, {
    name: user.name, role: user.role, costCentre: user.cost_centre,
  }]));
  const manager = seed.users.find((user) => user.role === "manager");
  return {
    people,
    delegations: seed.delegations.map((d) => [d.delegate_id, d.principal_id]),
    // Rates are numeric(8,4) in the database but have two decimals: "0.8600" -> "0.86".
    rates: parseRates(Object.fromEntries(seed.currencies.map((c) => [c.code, Number(c.rate).toFixed(2)]))),
    categories: seed.categories.slice().sort((a, b) => a.position - b.position).map((c) => c.code),
    managerLimit: manager.approval_limit_pence,
    lineLimit: 1000000,
    maxLines: 6,
  };
}

// -- folding the events ---------------------------------------------------------------------

/** Claims as they stand, from the rows of claims, claim_lines and claim_events. */
export function fold(rows) {
  const linesOf = new Map();
  for (const line of rows.lines) {
    const key = `${line.claim_id}:${line.revision}`;
    if (!linesOf.has(key)) linesOf.set(key, []);
    linesOf.get(key).push(line);
  }
  const claims = rows.claims.map((row) => ({
    id: row.id, owner: row.owner_id, costCentre: row.cost_centre, status: "draft",
    approved_by: "", approved_via: "", revision: 0, seq: 0, lines: [], history: [],
  }));
  const byId = new Map(claims.map((claim) => [claim.id, claim]));
  const ordered = rows.events.slice().sort((a, b) => a.claim_id.localeCompare(b.claim_id) || a.seq - b.seq);
  for (const event of ordered) {
    const claim = byId.get(event.claim_id);
    const by = event.acting_for ?? event.actor_id;
    const via = event.acting_for ? event.actor_id : "";
    if (CARRIES_LINES.has(event.kind)) {
      claim.lines = (linesOf.get(`${claim.id}:${event.revision}`) ?? [])
        .sort((a, b) => a.line_no - b.line_no)
        .map((line) => ({ description: line.description, category: line.category, currency: line.currency,
                          minor: line.amount_minor }));
    }
    claim.revision = Math.max(claim.revision, event.revision);
    claim.seq = event.seq;
    if (event.kind === "approved") {
      claim.approved_by = by;
      claim.approved_via = via;
    }
    claim.history.push({ type: event.kind, by, via, at: event.at, note: event.note });
    const next = STATUS_AFTER[event.kind];
    if (next) claim.status = next;
  }
  return claims.sort((a, b) => a.id.localeCompare(b.id));
}

export function findClaim(claims, id) {
  return claims.find((claim) => claim.id === id) ?? null;
}

// -- money on a claim -----------------------------------------------------------------------

/** Minor units of one currency on a claim, unconverted. */
export function currencySubtotal(lines, currency) {
  return lines.filter((line) => line.currency === currency).reduce((sum, line) => sum + line.minor, 0);
}

/** The claim's value in pence, every line converted at the configured rate. */
export function claimTotalPence(lines, rates) {
  return roundToPence(lines.reduce((sum, line) => sum + sterlingHundredths(line.minor, line.currency, rates), 0));
}

/** The editor's summary for rows as typed (not yet validated): per-currency sums and the
 *  sterling total, computed exactly as the editor in the browser computes them. */
export function summarise(rows, rates) {
  const byCurrency = Object.fromEntries(CURRENCIES.map((code) => [code, 0]));
  let hundredths = 0;
  for (const row of rows) {
    const currency = CURRENCIES.includes(row.currency) ? row.currency : "GBP";
    const minor = parseMinor(row.amount) ?? 0;
    byCurrency[currency] += minor;
    hundredths += sterlingHundredths(minor, currency, rates);
  }
  return { ...byCurrency, total: roundToPence(hundredths) };
}

// -- lines from the editor ------------------------------------------------------------------

const ROW_FIELD = /^(description|category|amount|currency)_(\d{1,2})$/;

/** The editor's rows as posted, in line order, exactly as typed. */
export function readRows(form) {
  const numbers = new Set();
  for (const key of form.keys()) {
    const match = ROW_FIELD.exec(key);
    if (match) numbers.add(Number(match[2]));
  }
  return [...numbers].sort((a, b) => a - b).map((n) => ({
    n,
    description: form.get(`description_${n}`) ?? "",
    category: form.get(`category_${n}`) ?? "",
    amount: form.get(`amount_${n}`) ?? "",
    currency: form.get(`currency_${n}`) ?? "",
  }));
}

/** Rows -> claim lines, or the problems with them (as vocabulary keys, by line position). */
export function validateRows(rows, cfg) {
  const errors = [];
  const lines = [];
  if (rows.length > cfg.maxLines) return { lines, errors: [{ line: 0, key: "msg.too_many_lines" }] };
  const filled = rows.filter((row) => row.description.trim() || row.amount.trim());
  if (!filled.length) return { lines, errors: [{ line: 0, key: "msg.no_lines" }] };
  filled.forEach((row, index) => {
    const line = index + 1;
    const description = row.description.trim();
    const minor = parseMinor(row.amount);
    if (!description) errors.push({ line, key: "msg.line_description" });
    else if (description.length > DESCRIPTION_LIMIT) errors.push({ line, key: "msg.line_description_long" });
    if (!cfg.categories.includes(row.category)) errors.push({ line, key: "msg.line_category" });
    if (!CURRENCIES.includes(row.currency)) errors.push({ line, key: "msg.line_currency" });
    if (minor === null || minor === 0 || minor > cfg.lineLimit) errors.push({ line, key: "msg.line_amount" });
    lines.push({ description, category: row.category, currency: row.currency, minor });
  });
  return { lines: errors.length ? [] : lines, errors };
}

// -- who acts, and as whom ----------------------------------------------------------------

export function roleOf(cfg, person) {
  return cfg.people[person]?.role ?? "";
}

/** The people `user` may act for, in the order they were delegated (empty: only themself). */
export function principalsOf(cfg, user) {
  return cfg.delegations.filter(([delegate]) => delegate === user).map(([, principal]) => principal);
}

/** Who can act on a claim next, or "" when nobody can. */
export function waitingOn(cfg, claim) {
  const byRole = (role) => Object.keys(cfg.people).find((p) => cfg.people[p].role === role) ?? "";
  switch (claim.status) {
    case "draft":
    case "sent_back":
      return claim.owner;
    case "awaiting":
      return byRole("manager");
    case "with_finance":
      return byRole("finance");
    default:
      return "";
  }
}

export function editable(claim) {
  return claim.status === "draft" || claim.status === "sent_back";
}

/** The editor's buttons (other than the template decoy) for a claim in this status. */
export function editorActions(claim) {
  if (!claim || claim.status === "draft") return ["save", "lodge"];
  if (claim.status === "sent_back") return ["save", "resubmit"];
  return [];
}

// -- commands -------------------------------------------------------------------------------

/** Save, lodge or resubmit from the editor, as `by`. `claim` is null for a new claim. */
export function editCommand(cfg, by, claim, nextId, action, rows) {
  if (roleOf(cfg, by) !== "employee") return { refused: "msg.only_claimant" };
  if (claim && (claim.owner !== by || !editable(claim))) return { refused: "msg.not_editable" };
  if (!editorActions(claim).includes(action)) return { refused: "msg.not_editable" };
  const { lines, errors } = validateRows(rows, cfg);
  if (errors.length) return { invalid: errors };
  const id = claim ? claim.id : nextId;
  const events = [];
  if (!claim) events.push({ type: "claim_drafted", claim: id, by, lines });
  if (action === "save" && claim) events.push({ type: "draft_saved", claim: id, by, lines });
  if (action === "lodge") events.push({ type: "lodged", claim: id, by, lines });
  if (action === "resubmit") events.push({ type: "resubmitted", claim: id, by, lines });
  return { events, claim: id, created: !claim, notice: `msg.done_${action}` };
}

/** Whether the manager may approve this claim alone. */
export function withinManagerLimit(claim, cfg) {
  // The limit applies to the claim's whole value in sterling, converted lines included.
  const value = claimTotalPence(claim.lines, cfg.rates);
  return value <= cfg.managerLimit;
}

const DECISIONS = {
  manager: { from: "awaiting", actions: ["approve", "send_back", "escalate"] },
  finance: { from: "with_finance", actions: ["approve", "reject"] },
};
const DECISION_EVENT = { approve: "approved", send_back: "sent_back", escalate: "escalated", reject: "rejected" };
const NOTE_REQUIRED = new Set(["send_back", "reject"]);

/** Approve, send back, escalate or reject, as `by`. */
export function decideCommand(cfg, by, claim, action, rawNote) {
  const rule = DECISIONS[roleOf(cfg, by)];
  if (!rule || !rule.actions.includes(action)) return { refused: "msg.not_your_decision" };
  if (claim.status !== rule.from) return { refused: "msg.not_your_turn" };
  const note = String(rawNote ?? "").trim();
  if (note.length > NOTE_LIMIT) return { refused: "msg.note_long" };
  if (NOTE_REQUIRED.has(action) && !note) return { refused: "msg.note_needed" };
  if (action === "approve" && roleOf(cfg, by) === "manager" && !withinManagerLimit(claim, cfg)) {
    return { refused: "msg.over_limit" };
  }
  return { events: [{ type: DECISION_EVENT[action], claim: claim.id, by, note }],
           notice: `msg.done_${action}` };
}

/** A draft its claimant no longer wants: it was never lodged, so it can go entirely. */
export function discardCommand(cfg, by, claim) {
  if (roleOf(cfg, by) !== "employee" || claim.owner !== by || claim.status !== "draft") {
    return { refused: "msg.not_discardable" };
  }
  return { discard: claim.id, notice: "msg.done_discard" };
}

/** What `/__wta/state` reports about claims: business data only. */
export function committedClaims(claims) {
  return claims.map((claim) => ({
    id: claim.id, owner: claim.owner, cost_centre: claim.costCentre, status: claim.status,
    approved_by: claim.approved_by, approved_via: claim.approved_via, lines: claim.lines,
    history: claim.history.map((entry) => [entry.type, entry.by, entry.via, entry.note]),
  }));
}
