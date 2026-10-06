// Ledgerly's domain: a claim's state is never stored, only derived. A session's event log
// (store.js) is folded over the seed on every request; commands validate against the folded
// state and answer with the events to append, or with the reason they were refused.

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

/** Every event that belongs to a claim's history. */
const CLAIM_EVENTS = new Set(["claim_drafted", "draft_saved", "lodged", "approved", "sent_back", "escalated",
                              "rejected", "resubmitted"]);
const CARRIES_LINES = new Set(["claim_drafted", "draft_saved", "lodged", "resubmitted"]);

export function config(seed) {
  return {
    people: seed.people,
    claimant: seed.claimant,
    rates: parseRates(seed.rates_to_gbp),
    categories: seed.categories,
    managerLimit: parseMinor(seed.manager_limit),
    lineLimit: parseMinor(seed.line_limit),
    maxLines: seed.max_lines,
  };
}

// -- folding the log ------------------------------------------------------------------------

export function fold(seed, events) {
  const state = {
    viewer: seed.claimant,
    claims: structuredClone(seed.claims),
    flash: null,
    nextNumber: seed.first_claim_number,
  };
  for (const claim of state.claims) {
    state.nextNumber = Math.max(state.nextNumber, Number(claim.id.slice(3)) + 1);
  }
  for (const event of events) apply(state, event);
  return state;
}

function apply(state, event) {
  if (event.type === "viewer_switched") {
    state.viewer = event.viewer;
    return;
  }
  if (event.type === "flash") {
    state.flash = { path: event.path, notice: event.notice ?? "", error: event.error ?? "",
                    errors: event.errors ?? [], rows: event.rows ?? null };
    return;
  }
  if (event.type === "flash_cleared") {
    state.flash = null;
    return;
  }
  if (!CLAIM_EVENTS.has(event.type)) return; // session_opened, and anything from a newer build
  let claim = findClaim(state, event.claim);
  if (!claim) {
    claim = { id: event.claim, owner: event.by, status: "draft", approved_by: "", lines: [], history: [] };
    state.claims.push(claim);
    state.nextNumber = Math.max(state.nextNumber, Number(event.claim.slice(3)) + 1);
  }
  if (CARRIES_LINES.has(event.type)) claim.lines = event.lines;
  if (event.type === "approved") claim.approved_by = event.by;
  claim.history.push({ type: event.type, by: event.by, at: event.at, note: event.note ?? "" });
  const next = STATUS_AFTER[event.type];
  if (next) claim.status = next;
}

export function findClaim(state, id) {
  return state.claims.find((claim) => claim.id === id) ?? null;
}

/** What `/__wta/state` reports: the claims, not who is looking at them or what is on screen. */
export function committed(state) {
  return { claims: [...state.claims].sort((a, b) => a.id.localeCompare(b.id)) };
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

// -- commands -------------------------------------------------------------------------------

export function roleOf(cfg, person) {
  return cfg.people[person]?.role ?? "";
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

/** Save, lodge or resubmit from the editor. `claim` is null for a new claim. */
export function editCommand(state, cfg, claim, action, rows, at) {
  const by = state.viewer;
  if (roleOf(cfg, by) !== "employee") return { refused: "msg.only_claimant" };
  if (claim && (claim.owner !== by || !editable(claim))) return { refused: "msg.not_editable" };
  if (!editorActions(claim).includes(action)) return { refused: "msg.not_editable" };
  const { lines, errors } = validateRows(rows, cfg);
  if (errors.length) return { invalid: errors };
  const id = claim ? claim.id : `CL-${state.nextNumber}`;
  const events = [];
  if (!claim) events.push({ type: "claim_drafted", claim: id, by, at, lines });
  if (action === "save" && claim) events.push({ type: "draft_saved", claim: id, by, at, lines });
  if (action === "lodge") events.push({ type: "lodged", claim: id, by, at, lines });
  if (action === "resubmit") events.push({ type: "resubmitted", claim: id, by, at, lines });
  return { events, claim: id, notice: `msg.done_${action}` };
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

/** Approve, send back, escalate or reject. */
export function decideCommand(state, cfg, claim, action, rawNote, at) {
  const by = state.viewer;
  const rule = DECISIONS[roleOf(cfg, by)];
  if (!rule || !rule.actions.includes(action)) return { refused: "msg.not_your_decision" };
  if (claim.status !== rule.from) return { refused: "msg.not_your_turn" };
  const note = String(rawNote ?? "").trim();
  if (note.length > NOTE_LIMIT) return { refused: "msg.note_long" };
  if (NOTE_REQUIRED.has(action) && !note) return { refused: "msg.note_needed" };
  if (action === "approve" && roleOf(cfg, by) === "manager" && !withinManagerLimit(claim, cfg)) {
    return { refused: "msg.over_limit" };
  }
  return { events: [{ type: DECISION_EVENT[action], claim: claim.id, by, at, note }],
           notice: `msg.done_${action}` };
}
