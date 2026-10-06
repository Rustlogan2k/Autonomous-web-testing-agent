"use strict";
/*
 * Orbit's domain: projects, columns with work-in-progress (WIP) limits, and cards with
 * checklists and dependencies. Every function here works on one session's state object;
 * the server owns loading it before a request and saving it afterwards.
 *
 * Rules of the board:
 *   - A column with a WIP limit never holds more cards than the limit, whichever way a
 *     card arrives (Pull in, or Move to from the card).
 *   - A card ships only from Doing or Review, only with its checklist complete, and only
 *     once every card it is blocked by has shipped. Shipped is final.
 *   - A card is "blocked" while any card it depends on has not shipped.
 */

const FINAL = "shipped";
const BACKLOG = "backlog";
const DOING = "doing";
const REVIEW = "review";
const WORK_COLUMNS = [DOING, REVIEW];
const TITLE_LIMIT = 120;
const COMMENT_LIMIT = 2000;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** A malformed request (unknown card, bad field): the UI never sends one. */
class RequestError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

/** Activity timestamps: the app's fixed "now" plus one minute per recorded event. */
function makeClock(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?/.exec(String(iso));
  if (!m) {
    throw new Error(`WTA_FIXED_NOW is not an ISO date-time: ${iso}`);
  }
  const base = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4]), Number(m[5]),
    Number(m[6] || 0));
  const pad = (value) => String(value).padStart(2, "0");
  return (minutesAfter) => {
    const at = new Date(base + minutesAfter * 60000);
    return `${at.getUTCDate()} ${MONTHS[at.getUTCMonth()]} ${pad(at.getUTCHours())}:${pad(at.getUTCMinutes())}`;
  };
}

function fromSeed(seed) {
  const cards = seed.cards.map((card, index) => ({
    n: card.n,
    project: card.project,
    column: card.column,
    rank: index + 1,
    title: card.title,
    description: card.description,
    checklist: card.checklist.map(([text, done], i) => ({ id: i + 1, text, done: Boolean(done) })),
    blockedBy: [...card.blocked_by],
    comments: [],
    activity: [],
  }));
  return {
    seq: { card: Math.max(...cards.map((c) => c.n)), rank: cards.length, activity: 0, comment: 0 },
    projects: seed.projects.map((p) => ({
      id: p.id, name: p.name, summary: p.summary, columns: p.columns.map((c) => ({ ...c })),
    })),
    cards,
  };
}

// -- lookups ----------------------------------------------------------------------------------

function findProject(state, pid) {
  return state.projects.find((p) => p.id === pid) || null;
}

function requireProject(state, pid) {
  const project = findProject(state, pid);
  if (!project) {
    throw new RequestError(404, "No such project.");
  }
  return project;
}

function requireCard(state, n) {
  const card = state.cards.find((c) => c.n === n);
  if (!card) {
    throw new RequestError(404, "No such card.");
  }
  return card;
}

function columnOf(project, id) {
  return project.columns.find((c) => c.id === id) || null;
}

function cardsIn(state, pid, columnId) {
  return state.cards.filter((c) => c.project === pid && c.column === columnId)
    .sort((a, b) => a.rank - b.rank);
}

function openBlockers(state, card) {
  return card.blockedBy.map((n) => state.cards.find((c) => c.n === n))
    .filter((blocker) => blocker && blocker.column !== FINAL);
}

function ticked(card) {
  return card.checklist.filter((item) => item.done).length;
}

function checklistDone(card) {
  return card.checklist.every((item) => item.done);
}

function hasRoom(state, project, column) {
  return column.wip === null || cardsIn(state, project.id, column.id).length < column.wip;
}

// -- changes ------------------------------------------------------------------------------------

const done = (message) => ({ ok: true, message });
const refused = (message) => ({ ok: false, message });

function log(state, card, text) {
  state.seq.activity += 1;
  card.activity.push({ seq: state.seq.activity, text });
  return state.seq.activity;
}

function relocate(state, card, columnId) {
  state.seq.rank += 1;
  card.column = columnId;
  card.rank = state.seq.rank; // arrives at the bottom of its new column
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
  log(state, next, t("activity.pulled", { to: doing.name }));
  return done(t("msg.pulled", { n: next.n, title: next.title, column: doing.name }));
}

function moveCard(state, card, to, t) {
  const project = requireProject(state, card.project);
  const target = columnOf(project, String(to));
  if (!target || target.id === FINAL) {
    throw new RequestError(400, "Choose a column to move the card to.");
  }
  if (card.column === FINAL) {
    return refused(t("msg.card_final"));
  }
  if (card.column === target.id) {
    return refused(t("msg.already_there", { n: card.n, column: target.name }));
  }
  const inTarget = cardsIn(state, project.id, target.id);
  if (target.wip !== null && inTarget.length >= target.wip) {
    return refused(t("msg.wip_full", { column: target.name, count: inTarget.length, wip: target.wip }));
  }
  const from = columnOf(project, card.column);
  relocate(state, card, target.id);
  log(state, card, t("activity.moved", { from: from.name, to: target.name }));
  return done(t("msg.moved", { n: card.n, column: target.name }));
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
    return t("msg.blocked", { n: blockers[0].n, title: blockers[0].title });
  }
  return null;
}

function ship(state, card, t) {
  relocate(state, card, FINAL);
  log(state, card, t("activity.shipped"));
}

function shipCard(state, card, t) {
  const refusal = shipRefusal(state, card, t);
  if (refusal) {
    return refused(refusal);
  }
  ship(state, card, t);
  return done(t("msg.shipped", { n: card.n, title: card.title }));
}

/** Review's bulk action: ship every card in Review that could ship on its own. */
function shipReady(state, pid, t) {
  requireProject(state, pid);
  const ready = cardsIn(state, pid, REVIEW).filter((card) => shipRefusal(state, card, t) === null);
  if (!ready.length) {
    return refused(t("msg.none_ready"));
  }
  for (const card of ready) {
    ship(state, card, t);
  }
  return done(t("msg.shipped_many", { count: ready.length, list: ready.map((c) => `#${c.n}`).join(", ") }));
}

function parkCard(state, card, t) {
  const project = requireProject(state, card.project);
  if (card.column === FINAL) {
    return refused(t("msg.card_final"));
  }
  const backlog = columnOf(project, BACKLOG);
  if (card.column === BACKLOG) {
    return refused(t("msg.already_there", { n: card.n, column: backlog.name }));
  }
  relocate(state, card, BACKLOG);
  log(state, card, t("activity.parked", { to: backlog.name }));
  return done(t("msg.parked", { n: card.n, column: backlog.name }));
}

function setChecklistItem(state, card, itemId, value, t) {
  const item = card.checklist.find((entry) => entry.id === itemId);
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
    log(state, card, value ? t("activity.ticked", { item: item.text }) : t("activity.unticked", { item: item.text }));
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
  state.seq.comment += 1;
  const id = state.seq.comment;
  const at = log(state, card, t("activity.commented"));
  card.comments.push({ id, body, at });
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
  state.seq.rank += 1;
  const card = {
    n: state.seq.card, project: project.id, column: BACKLOG, rank: state.seq.rank, title,
    description: "", checklist: [], blockedBy: [], comments: [], activity: [],
  };
  state.cards.push(card);
  log(state, card, t("activity.created", { column: columnOf(project, BACKLOG).name }));
  return done(t("msg.added", { n: card.n, column: columnOf(project, BACKLOG).name }));
}

// -- views --------------------------------------------------------------------------------------

function projectsView(state) {
  return state.projects.map((p) => {
    const cards = state.cards.filter((c) => c.project === p.id);
    return {
      id: p.id, name: p.name, summary: p.summary, cards: cards.length,
      shipped: cards.filter((c) => c.column === FINAL).length,
    };
  });
}

function cardView(state, project, card, clock) {
  const blockers = card.blockedBy.map((n) => state.cards.find((c) => c.n === n)).filter(Boolean);
  const final = card.column === FINAL;
  return {
    n: card.n,
    title: card.title,
    description: card.description,
    column: card.column,
    columnName: columnOf(project, card.column).name,
    final,
    checklist: card.checklist.map((item) => ({ ...item })),
    done: ticked(card),
    total: card.checklist.length,
    blocked: openBlockers(state, card).length > 0,
    blockers: blockers.map((b) => ({
      n: b.n, title: b.title, columnName: columnOf(project, b.column).name, finished: b.column === FINAL,
    })),
    moveTargets: final ? [] : project.columns
      .filter((c) => c.id !== FINAL && c.id !== card.column).map((c) => ({ id: c.id, name: c.name })),
    canShip: WORK_COLUMNS.includes(card.column),
    canPark: WORK_COLUMNS.includes(card.column),
    comments: card.comments.map((c) => ({ id: c.id, body: c.body, at: clock(c.at) })),
    activity: card.activity.slice().reverse().map((a) => ({ at: clock(a.seq), text: a.text })),
  };
}

function boardView(state, pid, clock) {
  const project = requireProject(state, pid);
  const columns = project.columns.map((column) => {
    const cards = cardsIn(state, pid, column.id);
    return {
      id: column.id,
      name: column.name,
      wip: column.wip,
      load: column.wip === null ? `${cards.length}` : `${cards.length}/${column.wip}`,
      cards: cards.map((card) => ({
        n: card.n, title: card.title, done: ticked(card), total: card.checklist.length,
        blocked: openBlockers(state, card).length > 0,
      })),
    };
  });
  const cards = {};
  for (const card of state.cards.filter((c) => c.project === pid)) {
    cards[card.n] = cardView(state, project, card, clock);
  }
  return { project: { id: project.id, name: project.name, summary: project.summary }, columns, cards };
}

/** Business state only (no UI state), for the harness's committed-state digest. */
function committed(state) {
  return {
    cards: state.cards.map((c) => ({
      n: c.n, project: c.project, column: c.column, rank: c.rank, title: c.title,
      checklist: c.checklist.map((item) => item.done), blockedBy: c.blockedBy,
      comments: c.comments.map((comment) => comment.body),
    })),
    activity: state.seq.activity,
  };
}

module.exports = {
  RequestError, makeClock, fromSeed, requireCard, pullIn, moveCard, shipCard, shipReady, parkCard,
  setChecklistItem, addComment, addCard, projectsView, boardView, committed,
};
