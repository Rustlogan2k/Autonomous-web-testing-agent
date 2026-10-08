"use strict";
/*
 * Orbit's domain: projects, columns with work-in-progress (WIP) limits, and cards with
 * checklists and dependencies. Every function here works on one request's board scope,
 * loaded by the server from the session's database (store.js):
 *
 *   { projects: [project], cards: [the project's cards], seq: {card, position, event},
 *     users: Map(id -> name), actor: <signed-in user id>, clock }
 *
 * The functions change those documents in memory; the server writes the changed ones back
 * when the route persists (server.js, ROUTES).
 *
 * Rules of the board:
 *   - A column with a WIP limit never holds more cards than the limit, whichever way a
 *     card arrives (Pull in, or Move to from the card), and an owner can never set a limit
 *     below what the column already holds.
 *   - A card ships only from Doing or Review, only with its checklist complete, and only
 *     once every card it is blocked by has shipped. Shipped is final.
 *   - A card is "blocked" while any card it depends on has not shipped.
 */

const { TITLE_LIMIT, COMMENT_LIMIT, WIP_MAX } = require("./schema.js");

const FINAL = "shipped";
const BACKLOG = "backlog";
const DOING = "doing";
const REVIEW = "review";
const WORK_COLUMNS = [DOING, REVIEW];
const ROLE_RANK = { viewer: 1, member: 2, owner: 3 };
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const ISO = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?/;

/** A malformed request (unknown card, bad field): the UI never sends one. */
class RequestError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

/**
 * The app's clock: its fixed "now" (WTA_FIXED_NOW) plus one minute per recorded event.
 * `iso(n)` is the stored time of the n-th event; `show(iso)` is how a stored time is shown.
 */
function makeClock(now) {
  const m = ISO.exec(String(now));
  if (!m) {
    throw new Error(`WTA_FIXED_NOW is not an ISO date-time: ${now}`);
  }
  const base = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4]), Number(m[5]),
    Number(m[6] || 0));
  const pad = (value) => String(value).padStart(2, "0");
  return {
    iso(minutesAfter) {
      const at = new Date(base + minutesAfter * 60000);
      return `${at.getUTCFullYear()}-${pad(at.getUTCMonth() + 1)}-${pad(at.getUTCDate())}T` +
        `${pad(at.getUTCHours())}:${pad(at.getUTCMinutes())}:00`;
    },
    show(iso) {
      const p = ISO.exec(iso);
      return p ? `${Number(p[3])} ${MONTHS[Number(p[2]) - 1]} ${p[4]}:${p[5]}` : "";
    },
  };
}

// -- lookups ----------------------------------------------------------------------------------

function requireProject(state, pid) {
  const project = state.projects.find((p) => p._id === pid);
  if (!project) {
    throw new RequestError(404, "No such project.");
  }
  return project;
}

function requireCard(state, n) {
  const card = state.cards.find((c) => c._id === n);
  if (!card) {
    throw new RequestError(404, "No such card.");
  }
  return card;
}

/** The user's role on a project ("owner", "member", "viewer"), or null if not a member. */
function roleOf(project, userId) {
  const membership = project.members.find((m) => m.user_id === userId);
  return membership ? membership.role : null;
}

/** Whether `role` is at least `needed` (viewer < member < owner). */
function allows(role, needed) {
  return Boolean(role) && ROLE_RANK[role] >= ROLE_RANK[needed];
}

function columnOf(project, key) {
  return project.columns.find((c) => c.key === key) || null;
}

function cardsIn(state, pid, columnKey) {
  return state.cards.filter((c) => c.project_id === pid && c.column === columnKey)
    .sort((a, b) => a.position - b.position);
}

function openBlockers(state, card) {
  return card.blocked_by.map((n) => state.cards.find((c) => c._id === n))
    .filter((blocker) => blocker && blocker.column !== FINAL);
}

function ticked(card) {
  return card.checklist.filter((item) => item.done).length;
}

function checklistDone(card) {
  return card.checklist.every((item) => item.done);
}

function hasRoom(state, project, column) {
  return column.wip === null || cardsIn(state, project._id, column.key).length < column.wip;
}

// -- changes ------------------------------------------------------------------------------------

const done = (message) => ({ ok: true, message });
const refused = (message) => ({ ok: false, message });

/** Record what happened to `card` in its history, as the signed-in user, at the next event. */
function log(state, card, kind, detail) {
  state.seq.event += 1;
  const last = card.activity[card.activity.length - 1];
  card.activity.push({ kind, n: last ? last.n + 1 : 1, at: state.clock.iso(state.seq.event),
    by: state.actor, detail });
}

function relocate(state, card, columnKey) {
  state.seq.position += 1;
  card.column = columnKey;
  card.position = state.seq.position; // arrives at the bottom of its new column
}

function pullIn(state, pid, t) {
  const project = requireProject(state, pid);
  const doing = columnOf(project, DOING);
  if (!hasRoom(state, project, doing)) {
    const count = cardsIn(state, pid, DOING).length;
    return refused(t("msg.wip_full", { column: doing.name, count, wip: doing.wip }));
  }
  const next = cardsIn(state, pid, BACKLOG).find((card) => openBlockers(state, card).length === 0);
  if (!next) {
    return refused(t("msg.nothing_to_pull"));
  }
  relocate(state, next, DOING);
  log(state, next, "moved", { from: BACKLOG, to: DOING, via: "pull-in" });
  return done(t("msg.pulled", { n: next._id, title: next.title, column: doing.name }));
}

function moveCard(state, card, to, t) {
  const project = requireProject(state, card.project_id);
  const target = columnOf(project, String(to));
  if (!target || target.key === FINAL) {
    throw new RequestError(400, "Choose a column to move the card to.");
  }
  if (card.column === FINAL) {
    return refused(t("msg.card_final"));
  }
  if (card.column === target.key) {
    return refused(t("msg.already_there", { n: card._id, column: target.name }));
  }
  const inTarget = cardsIn(state, project._id, target.key);
  if (target.wip !== null && inTarget.length >= target.wip) {
    return refused(t("msg.wip_full", { column: target.name, count: inTarget.length, wip: target.wip }));
  }
  const from = card.column;
  relocate(state, card, target.key);
  log(state, card, "moved", { from, to: target.key, via: "move" });
  return done(t("msg.moved", { n: card._id, column: target.name }));
}

/** Why `card` cannot ship right now, or null when it can. */
function shipRefusal(state, card, t) {
  if (card.column === FINAL) {
    return t("msg.card_final");
  }
  if (!WORK_COLUMNS.includes(card.column)) {
    return t("msg.ship_from");
  }
  if (!checklistDone(card)) {
    return t("msg.checklist_open", { done: ticked(card), total: card.checklist.length });
  }
  const blockers = openBlockers(state, card);
  if (blockers.length) {
    return t("msg.blocked", { n: blockers[0]._id, title: blockers[0].title });
  }
  return null;
}

function ship(state, card) {
  const from = card.column;
  relocate(state, card, FINAL);
  log(state, card, "shipped", { from });
}

function shipCard(state, card, t) {
  const refusal = shipRefusal(state, card, t);
  if (refusal) {
    return refused(refusal);
  }
  ship(state, card);
  return done(t("msg.shipped", { n: card._id, title: card.title }));
}

/** Review's bulk action: ship every card in Review that could ship on its own. */
function shipReady(state, pid, t) {
  requireProject(state, pid);
  const ready = cardsIn(state, pid, REVIEW).filter((card) => shipRefusal(state, card, t) === null);
  if (!ready.length) {
    return refused(t("msg.none_ready"));
  }
  for (const card of ready) {
    ship(state, card);
  }
  return done(t("msg.shipped_many", { count: ready.length, list: ready.map((c) => `#${c._id}`).join(", ") }));
}

function parkCard(state, card, t) {
  const project = requireProject(state, card.project_id);
  if (card.column === FINAL) {
    return refused(t("msg.card_final"));
  }
  const backlog = columnOf(project, BACKLOG);
  if (card.column === BACKLOG) {
    return refused(t("msg.already_there", { n: card._id, column: backlog.name }));
  }
  const from = card.column;
  relocate(state, card, BACKLOG);
  log(state, card, "parked", { from });
  return done(t("msg.parked", { n: card._id, column: backlog.name }));
}

function setChecklistItem(state, card, itemNumber, value, t) {
  const item = card.checklist.find((entry) => entry.n === itemNumber);
  if (!item) {
    throw new RequestError(404, "No such checklist item.");
  }
  if (typeof value !== "boolean") {
    throw new RequestError(400, "Say whether the item is done.");
  }
  if (card.column === FINAL) {
    return refused(t("msg.card_final"));
  }
  if (item.done !== value) {
    item.done = value;
    log(state, card, "checked", { item: item.n, done: value });
  }
  return done("");
}

function addComment(state, card, body, t) {
  if (typeof body !== "string") {
    throw new RequestError(400, "A comment needs a body.");
  }
  if (!body.trim()) {
    return refused(t("msg.comment_empty"));
  }
  if (body.length > COMMENT_LIMIT) {
    return refused(t("msg.comment_long", { limit: COMMENT_LIMIT }));
  }
  const last = card.comments[card.comments.length - 1];
  const n = last ? last.n + 1 : 1;
  log(state, card, "comment", { comment: n });
  card.comments.push({ n, author_id: state.actor, body, at: card.activity[card.activity.length - 1].at });
  return done(t("msg.commented"));
}

function addCard(state, pid, title, t) {
  const project = requireProject(state, pid);
  if (typeof title !== "string") {
    throw new RequestError(400, "A card needs a title.");
  }
  if (!title.trim()) {
    return refused(t("msg.title_empty"));
  }
  if (title.length > TITLE_LIMIT) {
    return refused(t("msg.title_long", { limit: TITLE_LIMIT }));
  }
  state.seq.card += 1;
  state.seq.position += 1;
  const card = {
    _id: state.seq.card, project_id: project._id, column: BACKLOG, position: state.seq.position,
    title, description: "", checklist: [], blocked_by: [], labels: [], assignee_ids: [],
    comments: [], activity: [],
  };
  state.cards.push(card);
  log(state, card, "created", { column: BACKLOG });
  return done(t("msg.added", { n: card._id, column: columnOf(project, BACKLOG).name }));
}

/** An owner's board settings: new WIP limits for the limited columns, all or nothing. */
function setLimits(state, pid, limits, t) {
  const project = requireProject(state, pid);
  if (!limits || typeof limits !== "object" || Array.isArray(limits)) {
    throw new RequestError(400, "Send the limits by column.");
  }
  const changes = [];
  for (const column of project.columns.filter((c) => c.wip !== null && c.key in limits)) {
    const text = String(limits[column.key]).trim();
    const wip = /^\d{1,3}$/.test(text) ? Number(text) : NaN;
    if (!(wip >= 1 && wip <= WIP_MAX)) {
      return refused(t("msg.wip_invalid", { column: column.name, max: WIP_MAX }));
    }
    const holding = cardsIn(state, pid, column.key).length;
    if (wip < holding) {
      return refused(t("msg.wip_below_load", { column: column.name, count: holding }));
    }
    changes.push([column, wip]);
  }
  if (!changes.length) {
    throw new RequestError(400, "Send a limit for at least one column.");
  }
  for (const [column, wip] of changes) {
    column.wip = wip;
  }
  return done(t("msg.settings_saved"));
}

// -- views --------------------------------------------------------------------------------------

/** The projects hub: the user's projects with their card counts (`counts` by project id). */
function projectsView(projects, counts, userId) {
  return projects.map((p) => ({
    id: p._id, name: p.name, summary: p.summary, role: roleOf(p, userId),
    cards: (counts.get(p._id) || {}).cards || 0, shipped: (counts.get(p._id) || {}).shipped || 0,
  }));
}

function activityText(project, card, entry, t) {
  const name = (key) => (columnOf(project, key) || { name: key }).name;
  const d = entry.detail;
  switch (entry.kind) {
    case "created":
      return t("activity.created", { column: name(d.column) });
    case "moved":
      return d.via === "pull-in" ? t("activity.pulled", { to: name(d.to) })
        : t("activity.moved", { from: name(d.from), to: name(d.to) });
    case "shipped":
      return t("activity.shipped");
    case "parked":
      return t("activity.parked", { to: name(BACKLOG) });
    case "checked": {
      const item = card.checklist.find((i) => i.n === d.item);
      return t(d.done ? "activity.ticked" : "activity.unticked", { item: item ? item.text : `#${d.item}` });
    }
    default:
      return t("activity.commented");
  }
}

function cardView(state, project, card, t) {
  const blockers = card.blocked_by.map((n) => state.cards.find((c) => c._id === n)).filter(Boolean);
  const final = card.column === FINAL;
  const labelName = (key) => (project.labels.find((l) => l.key === key) || { name: key }).name;
  return {
    n: card._id,
    title: card.title,
    description: card.description,
    column: card.column,
    columnName: columnOf(project, card.column).name,
    final,
    checklist: card.checklist.map((item) => ({ id: item.n, text: item.text, done: item.done })),
    done: ticked(card),
    total: card.checklist.length,
    blocked: openBlockers(state, card).length > 0,
    blockers: blockers.map((b) => ({
      n: b._id, title: b.title, columnName: columnOf(project, b.column).name, finished: b.column === FINAL,
    })),
    assignees: card.assignee_ids.map((id) => state.users.get(id) || id),
    labels: card.labels.map(labelName),
    moveTargets: final ? [] : project.columns
      .filter((c) => c.key !== FINAL && c.key !== card.column).map((c) => ({ id: c.key, name: c.name })),
    canShip: WORK_COLUMNS.includes(card.column),
    canPark: WORK_COLUMNS.includes(card.column),
    comments: card.comments.map((c) => ({
      id: c.n, body: c.body, author: state.users.get(c.author_id) || c.author_id, at: state.clock.show(c.at),
    })),
    activity: card.activity.slice().reverse().map((a) => ({
      at: state.clock.show(a.at), text: activityText(project, card, a, t), by: state.users.get(a.by) || a.by,
    })),
  };
}

function boardView(state, pid, t) {
  const project = requireProject(state, pid);
  const role = roleOf(project, state.actor);
  const columns = project.columns.slice().sort((a, b) => a.position - b.position).map((column) => {
    const cards = cardsIn(state, pid, column.key);
    return {
      id: column.key,
      name: column.name,
      wip: column.wip,
      load: column.wip === null ? `${cards.length}` : `${cards.length}/${column.wip}`,
      cards: cards.map((card) => ({
        n: card._id, title: card.title, done: ticked(card), total: card.checklist.length,
        blocked: openBlockers(state, card).length > 0,
      })),
    };
  });
  const cards = {};
  for (const card of state.cards.filter((c) => c.project_id === pid)) {
    cards[card._id] = cardView(state, project, card, t);
  }
  return {
    project: { id: project._id, name: project.name, summary: project.summary },
    access: { role, canEdit: allows(role, "member"), canAdmin: allows(role, "owner") },
    columns, cards,
  };
}

function settingsView(state, pid) {
  const project = requireProject(state, pid);
  return {
    project: { id: project._id, name: project.name },
    columns: project.columns.filter((c) => c.wip !== null).map((c) => ({
      id: c.key, name: c.name, wip: c.wip, holding: cardsIn(state, pid, c.key).length,
    })),
    max: WIP_MAX,
  };
}

/** Business state only, for the harness's committed-state digest: no UI state, credentials
 *  or counters. A card's history is business data (a tick undone is still on record). */
function committed(projects, cards) {
  const byNumber = cards.slice().sort((a, b) => a._id - b._id);
  return {
    projects: projects.slice().sort((a, b) => a.position - b.position || a._id.localeCompare(b._id))
      .map((p) => ({ id: p._id, limits: p.columns.map((c) => [c.key, c.wip]) })),
    cards: byNumber.map((c) => ({
      n: c._id, project: c.project_id, column: c.column, rank: c.position, title: c.title,
      checklist: c.checklist.map((item) => item.done), blockedBy: c.blocked_by,
      comments: c.comments.map((comment) => [comment.author_id, comment.body]),
      history: c.activity.map((entry) => [entry.kind, entry.by, entry.at, entry.detail]),
    })),
  };
}

module.exports = {
  RequestError, makeClock, roleOf, allows, requireCard, pullIn, moveCard, shipCard, shipReady,
  parkCard, setChecklistItem, addComment, addCard, setLimits, projectsView, boardView,
  settingsView, committed,
};
