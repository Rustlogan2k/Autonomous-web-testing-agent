// Orbit's API, exercised against a running clean build (E2E_BASE_URL).
// Every test opens its own session, so tests are independent and start from the seed.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";

const BASE = (process.env.E2E_BASE_URL || "http://127.0.0.1:8080/").replace(/\/+$/, "");

class Session {
  constructor() {
    this.cookie = "";
  }

  async call(path, body, { raw = false } = {}) {
    const headers = this.cookie ? { cookie: this.cookie } : {};
    const init = { method: body === undefined ? "GET" : "POST", headers };
    if (body !== undefined) {
      headers["content-type"] = "application/json";
      init.body = typeof body === "string" ? body : JSON.stringify(body);
    }
    const response = await fetch(BASE + path, init);
    const match = /wta_sid=([^;]+)/.exec(response.headers.get("set-cookie") || "");
    if (match) {
      this.cookie = `wta_sid=${match[1]}`;
    }
    const text = await response.text();
    return { status: response.status, data: raw ? text : (text ? JSON.parse(text) : null) };
  }

  async board(pid = "web") {
    const { status, data } = await this.call(`/api/boards/${pid}`);
    assert.equal(status, 200);
    return data.board;
  }

  async sha() {
    const { status, data } = await this.call("/__wta/state");
    assert.equal(status, 200, "the harness starts the app with WTA_TEST_HOOKS=1");
    return data.sha;
  }

  async command(path, body = {}) {
    const { status, data } = await this.call(path, body);
    assert.equal(status, 200, `${path} answers 200 even when it refuses`);
    return data;
  }

  tick(n, item, done = true) {
    return this.command(`/api/cards/${n}/checklist/${item}`, { done });
  }
}

const column = (board, id) => board.columns.find((c) => c.id === id);
const numbers = (board, id) => column(board, id).cards.map((c) => c.n);

async function fresh() {
  const session = new Session();
  await session.board();
  return session;
}

test("health check and a seeded board", async () => {
  const health = await new Session().call("/healthz");
  assert.deepEqual(health.data, { ok: true, app: "orbit" });
  const s = await fresh();
  const board = await s.board();
  assert.equal(board.project.name, "Website relaunch");
  assert.deepEqual(board.columns.map((c) => c.load), ["3", "3/3", "2/2", "2"]);
  const notes = board.cards[7];
  assert.equal(notes.title, "Release notes");
  assert.equal(notes.column, "review");
  assert.equal(notes.done, notes.total, "its checklist is complete");
  assert.equal(notes.blocked, true, "but it waits for #6, which has not shipped");
  assert.deepEqual(notes.blockers.map((b) => [b.n, b.finished]), [[6, false]]);
  const projects = (await s.call("/api/projects")).data.projects;
  assert.deepEqual(projects.map((p) => p.id), ["web", "ops"]);
});

test("Pull in is refused while Doing is full and changes nothing", async () => {
  const s = await fresh();
  const before = await s.sha();
  const result = await s.command("/api/boards/web/pull-in");
  assert.equal(result.ok, false);
  assert.match(result.message, /3\/3/);
  assert.equal(column(result.board, "doing").load, "3/3");
  assert.equal(await s.sha(), before);
});

test("after a Doing card is parked, Pull in takes the first unblocked backlog card", async () => {
  const s = await fresh();
  const parked = await s.command("/api/cards/4/park");
  assert.equal(parked.ok, true);
  assert.equal(column(parked.board, "doing").load, "2/3");
  assert.deepEqual(numbers(parked.board, "backlog"), [1, 2, 3, 4]);
  const pulled = await s.command("/api/boards/web/pull-in");
  assert.equal(pulled.ok, true);
  assert.deepEqual(numbers(pulled.board, "doing"), [5, 6, 1]);
  assert.equal(column(pulled.board, "doing").load, "3/3");
  // #2 is blocked by #3, so the next Pull in (after making room) skips it
  await s.command("/api/cards/5/park");
  const next = await s.command("/api/boards/web/pull-in");
  assert.deepEqual(numbers(next.board, "doing"), [6, 1, 3]);
});

test("Move to respects every WIP limit exactly", async () => {
  const s = await fresh();
  const intoDoing = await s.command("/api/cards/1/move", { to: "doing" });
  assert.equal(intoDoing.ok, false, "Doing is exactly full (3/3)");
  assert.equal(column(intoDoing.board, "doing").load, "3/3");
  const intoReview = await s.command("/api/cards/4/move", { to: "review" });
  assert.equal(intoReview.ok, false, "Review is exactly full (2/2)");
  assert.equal(column(intoReview.board, "review").load, "2/2");
  const same = await s.command("/api/cards/4/move", { to: "doing" });
  assert.equal(same.ok, false, "a card is never moved to the column it is in");
  await s.command("/api/cards/5/park");
  const moved = await s.command("/api/cards/1/move", { to: "doing" });
  assert.equal(moved.ok, true);
  assert.equal(column(moved.board, "doing").load, "3/3");
  const again = await s.command("/api/cards/2/move", { to: "doing" });
  assert.equal(again.ok, false);
  for (const c of again.board.columns.filter((col) => col.wip !== null)) {
    assert.ok(c.cards.length <= c.wip, `${c.name} holds ${c.load}`);
  }
});

test("Ship needs a complete checklist; ticking the rest makes it ship", async () => {
  const s = await fresh();
  const before = await s.sha();
  const refused = await s.command("/api/cards/8/ship");
  assert.equal(refused.ok, false);
  assert.match(refused.message, /2 of 3/);
  assert.equal(await s.sha(), before, "a refused Ship commits nothing");
  const ticked = await s.tick(8, 3);
  assert.equal(ticked.board.cards[8].done, 3);
  const shipped = await s.command("/api/cards/8/ship");
  assert.equal(shipped.ok, true);
  assert.ok(numbers(shipped.board, "shipped").includes(8));
  assert.equal(column(shipped.board, "review").load, "1/2");
});

test("a blocked card cannot ship until its blocker has shipped", async () => {
  const s = await fresh();
  const blocked = await s.command("/api/cards/7/ship");
  assert.equal(blocked.ok, false);
  assert.match(blocked.message, /#6/);
  const backlogShip = await s.command("/api/cards/1/ship");
  assert.equal(backlogShip.ok, false, "backlog cards cannot ship");
  await s.tick(6, 3);
  await s.tick(6, 4);
  const blocker = await s.command("/api/cards/6/ship");
  assert.equal(blocker.ok, true, "a Doing card with a complete checklist ships");
  assert.equal(blocker.board.cards[7].blocked, false);
  const notes = await s.command("/api/cards/7/ship");
  assert.equal(notes.ok, true);
  const shipped = column(notes.board, "shipped").cards;
  assert.ok(shipped.every((card) => !card.blocked), "nothing in Shipped is blocked");
});

test("Ship all ready ships only Review cards that could ship on their own", async () => {
  const s = await fresh();
  const before = await s.sha();
  const nothing = await s.command("/api/boards/web/ship-ready");
  assert.equal(nothing.ok, false, "Release notes is blocked and Contact form is unfinished");
  assert.deepEqual(numbers(nothing.board, "review"), [7, 8]);
  assert.equal(await s.sha(), before);
  await s.tick(8, 3);
  const some = await s.command("/api/boards/web/ship-ready");
  assert.equal(some.ok, true);
  assert.deepEqual(numbers(some.board, "review"), [7]);
  assert.deepEqual(numbers(some.board, "shipped"), [9, 10, 8]);
  assert.ok(column(some.board, "shipped").cards.every((card) => !card.blocked));
});

test("a checklist tick is saved before the answer and survives a fresh read", async () => {
  const s = await fresh();
  const before = await s.sha();
  const ticked = await s.tick(4, 2);
  assert.equal(ticked.ok, true);
  assert.equal(ticked.board.cards[4].done, 2);
  const reread = await s.board();
  assert.equal(reread.cards[4].done, 2, "a reload shows the same progress");
  assert.equal(reread.cards[4].checklist[1].done, true);
  assert.notEqual(await s.sha(), before);
  await s.tick(4, 2, false);
  assert.equal((await s.board()).cards[4].done, 1, "unticking is saved too");
  const same = await s.tick(4, 1, true);
  assert.equal(same.ok, true, "setting an item to what it already is changes nothing");
  assert.equal((await s.board()).cards[4].done, 1);
  const history = (await s.board()).cards[4].activity.map((a) => a.text);
  assert.equal(history.length, 2);
});

test("shipped cards are final", async () => {
  const s = await fresh();
  const before = await s.sha();
  assert.equal((await s.tick(9, 1, false)).ok, false);
  assert.equal((await s.command("/api/cards/9/move", { to: "doing" })).ok, false);
  assert.equal((await s.command("/api/cards/9/park")).ok, false);
  assert.equal((await s.command("/api/cards/9/ship")).ok, false);
  assert.equal(await s.sha(), before);
  const view = (await s.board()).cards[9];
  assert.equal(view.final, true);
  assert.deepEqual(view.moveTargets, []);
});

test("comments are validated, kept exactly as typed, and saved", async () => {
  const s = await fresh();
  const before = await s.sha();
  assert.equal((await s.command("/api/cards/5/comments", { body: "" })).ok, false);
  assert.equal((await s.command("/api/cards/5/comments", { body: "A".repeat(2001) })).ok, false);
  assert.equal(await s.sha(), before);
  const body = "This is a typical multi-line\ncomment used for functional testing.";
  const added = await s.command("/api/cards/5/comments", { body });
  assert.equal(added.ok, true);
  const comments = (await s.board()).cards[5].comments;
  assert.deepEqual(comments.map((c) => c.body), [body]);
  assert.match(comments[0].at, /^\d{1,2} [A-Z][a-z]{2} \d{2}:\d{2}$/);
});

test("new cards land at the bottom of the backlog and titles are validated", async () => {
  const s = await fresh();
  assert.equal((await s.command("/api/boards/ops/cards", { title: "   " })).ok, false);
  assert.equal((await s.command("/api/boards/ops/cards", { title: "A".repeat(121) })).ok, false);
  const added = await s.command("/api/boards/ops/cards", { title: "Test User Input" });
  assert.equal(added.ok, true);
  assert.deepEqual(numbers(added.board, "backlog"), [13, 14, 15]);
  assert.equal(added.board.cards[15].title, "Test User Input");
  assert.equal((await s.board("web")).cards[15], undefined, "the card belongs to its own project");
});

test("sessions are isolated and each starts from the seed", async () => {
  const a = await fresh();
  const b = await fresh();
  const seedSha = await b.sha();
  assert.equal(await a.sha(), seedSha);
  await a.command("/api/cards/4/park");
  assert.notEqual(await a.sha(), seedSha);
  assert.equal(await b.sha(), seedSha, "another session's change is invisible here");
  assert.equal(column(await b.board(), "doing").load, "3/3");
  const c = await fresh();
  assert.equal(await c.sha(), seedSha);
});

test("the state hook is the sha256 of the canonical committed state", async () => {
  const s = await fresh();
  const { data } = await s.call("/__wta/state");
  const canonical = (v) => Array.isArray(v) ? `[${v.map(canonical).join(",")}]`
    : v && typeof v === "object"
      ? `{${Object.keys(v).sort().map((k) => `${JSON.stringify(k)}:${canonical(v[k])}`).join(",")}}`
      : JSON.stringify(v);
  assert.equal(data.sha, createHash("sha256").update(canonical(data.committed)).digest("hex"));
  assert.match(data.session, /^s\d{6}$/);
});

test("the page shell sets the session cookie and embeds the vocabulary", async () => {
  const s = new Session();
  const page = await s.call("/", undefined, { raw: true });
  assert.equal(page.status, 200);
  assert.match(s.cookie, /^wta_sid=s\d{6}$/);
  assert.match(page.data, /<script id="vocab-data" type="application\/json">/);
  assert.equal((await s.call("/app.js", undefined, { raw: true })).status, 200);
  assert.equal((await s.call("/styles.css", undefined, { raw: true })).status, 200);
});

test("malformed requests are rejected without changing anything", async () => {
  const s = await fresh();
  const before = await s.sha();
  assert.equal((await s.call("/api/cards/99/ship", {})).status, 404);
  assert.equal((await s.call("/api/boards/nope")).status, 404);
  assert.equal((await s.call("/api/cards/4/checklist/9", { done: true })).status, 404);
  assert.equal((await s.call("/api/cards/4/checklist/2", { done: "yes" })).status, 400);
  assert.equal((await s.call("/api/cards/1/move", { to: "shipped" })).status, 400);
  assert.equal((await s.call("/api/cards/1/move", "{not json")).status, 400);
  assert.equal(await s.sha(), before);
});
