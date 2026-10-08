// Orbit's API, exercised against a running clean build (E2E_BASE_URL) started with
// WTA_TEST_HOOKS=1 and its MongoDB home. Every test signs in its own browser session, so
// tests are independent and each starts from the seed.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

const BASE = (process.env.E2E_BASE_URL || "http://127.0.0.1:8080/").replace(/\/+$/, "");
const LOGIN = JSON.parse(readFileSync(new URL("../app.json", import.meta.url), "utf8")).login;
const PASSWORD = LOGIN.password_value;
const MEI = "mei.tanaka";
const ROSA = "rosa.lindqvist";
const OLA = "ola.nwosu";
const SESSION_CAP = Number(process.env.WTA_SESSION_CAP || 32);

/** One cookie jar: what a fresh browser context is to the server. Redirects are not followed. */
class Session {
  constructor() {
    this.cookie = "";
  }

  async send(method, path, { json, form } = {}) {
    const headers = this.cookie ? { cookie: this.cookie } : {};
    let body;
    if (json !== undefined) {
      headers["content-type"] = "application/json";
      body = typeof json === "string" ? json : JSON.stringify(json);
    } else if (form !== undefined) {
      headers["content-type"] = "application/x-www-form-urlencoded";
      body = new URLSearchParams(form).toString();
    }
    const response = await fetch(BASE + path, { method, headers, body, redirect: "manual" });
    const match = /wta_sid=([^;]+)/.exec(response.headers.get("set-cookie") || "");
    if (match) {
      this.cookie = `wta_sid=${match[1]}`;
    }
    const text = await response.text();
    return { status: response.status, location: response.headers.get("location"),
      setCookie: Boolean(match), text };
  }

  async call(path, body) {
    const reply = await this.send(body === undefined ? "GET" : "POST", path, { json: body });
    return { status: reply.status, data: reply.text ? JSON.parse(reply.text) : null };
  }

  signIn(username = MEI, password = PASSWORD, next = undefined) {
    return this.send("POST", "/login", { form: { username, password, ...(next ? { next } : {}) } });
  }

  async board(pid = "web") {
    const { status, data } = await this.call(`/api/boards/${pid}`);
    assert.equal(status, 200);
    return data.board;
  }

  async state() {
    const { status, data } = await this.call("/__wta/state");
    assert.equal(status, 200, "the harness starts the app with WTA_TEST_HOOKS=1");
    return data;
  }

  async sha() {
    return (await this.state()).sha;
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

async function fresh(username = MEI) {
  const session = new Session();
  const reply = await session.signIn(username);
  assert.equal(reply.status, 303, `${username} signs in`);
  return session;
}

// -- the board ---------------------------------------------------------------------------------

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
  assert.deepEqual(notes.assignees, ["Mei Tanaka"]);
  assert.deepEqual(notes.labels, ["Content", "Launch"]);
  const projects = (await s.call("/api/projects")).data.projects;
  assert.deepEqual(projects.map((p) => [p.id, p.role, p.cards, p.shipped]), [["web", "member", 10, 2], ["ops", "member", 4, 0]]);
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
  const seeded = (await s.board()).cards[4].activity.length;
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
  const history = (await s.board()).cards[4].activity;
  assert.equal(history.length, seeded + 2);
  assert.match(history[0].text, /Write the comparison table/, "newest first, naming the item");
  assert.equal(history[0].by, "Mei Tanaka");
  assert.equal(history[0].at, "10 Jun 09:02", "the app's clock: one minute per event");
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
  const seeded = (await s.board()).cards[5].comments;
  assert.ok(seeded.every((c) => !c.body.includes("functional testing")), "no seeded comment looks typed");
  assert.equal((await s.command("/api/cards/5/comments", { body: "" })).ok, false);
  assert.equal((await s.command("/api/cards/5/comments", { body: "A".repeat(2001) })).ok, false);
  assert.equal(await s.sha(), before);
  const body = "This is a typical multi-line\ncomment used for functional testing.";
  const added = await s.command("/api/cards/5/comments", { body });
  assert.equal(added.ok, true);
  const comments = (await s.board()).cards[5].comments;
  assert.deepEqual(comments.map((c) => c.body), [...seeded.map((c) => c.body), body]);
  const mine = comments[comments.length - 1];
  assert.equal(mine.author, "Mei Tanaka");
  assert.match(mine.at, /^\d{1,2} [A-Z][a-z]{2} \d{2}:\d{2}$/);
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
  assert.equal((await s.board("ops")).cards[15].title, "Test User Input", "and it was saved");
});

// -- sessions and state ----------------------------------------------------------------------

test("sessions are isolated and each starts from the seed", async () => {
  const a = await fresh();
  const b = await fresh();
  const seedSha = await b.sha();
  assert.equal(await a.sha(), seedSha);
  await a.command("/api/cards/4/park");
  assert.notEqual(await a.sha(), seedSha);
  assert.equal(await b.sha(), seedSha, "another session's change is invisible here");
  assert.equal(column(await b.board(), "doing").load, "3/3");
  const c = await fresh(ROSA);
  assert.equal(await c.sha(), seedSha, "every user starts from the same seed");
});

test("the state hook is the sha256 of the canonical committed state, without credentials", async () => {
  const s = await fresh();
  const data = await s.state();
  const canonical = (v) => Array.isArray(v) ? `[${v.map(canonical).join(",")}]`
    : v && typeof v === "object"
      ? `{${Object.keys(v).sort().map((k) => `${JSON.stringify(k)}:${canonical(v[k])}`).join(",")}}`
      : JSON.stringify(v);
  assert.equal(data.sha, createHash("sha256").update(canonical(data.committed)).digest("hex"));
  assert.match(data.session, /^s\d{6}$/);
  assert.equal(data.user, MEI);
  const text = JSON.stringify(data);
  assert.ok(!/salt|hash|pw|_s\d{6}/.test(text.replace(data.sha, "")), "no credentials or partition names");
  const anonymous = await new Session().state();
  assert.deepEqual(anonymous, { session: null, user: null, committed: null, sha: null });
});

test("concurrent requests of one session are serialised", async () => {
  const s = await fresh();
  await s.command("/api/cards/4/park"); // Doing 2/3: room for exactly one more
  const pulls = await Promise.all([1, 2, 3, 4].map(() => s.command("/api/boards/web/pull-in")));
  assert.equal(pulls.filter((p) => p.ok).length, 1, "exactly one Pull in finds room");
  const board = await s.board();
  assert.equal(column(board, "doing").load, "3/3");
  const ticks = await Promise.all([1, 2, 3].map(() => s.tick(8, 3)));
  assert.ok(ticks.every((r) => r.ok));
  const history = (await s.board()).cards[8].activity.filter((a) => /Thank-you page/.test(a.text));
  assert.equal(history.length, 1, "the same tick sent three times is recorded once");
});

// -- sign-in, sign-out, eviction ---------------------------------------------------------------

test("the app is behind its sign-in page; the sign-in pages are public", async () => {
  const anonymous = new Session();
  const page = await anonymous.send("GET", "/");
  assert.equal(page.status, 303);
  assert.equal(page.location, "/login?next=%2F");
  assert.equal(page.setCookie, false, "anonymous requests create no session");
  assert.equal((await anonymous.send("GET", "/index.html")).status, 303);
  const login = await anonymous.send("GET", "/login");
  assert.equal(login.status, 200);
  for (const id of ["login-username", "login-password", "login-submit", "forgot-password"]) {
    assert.match(login.text, new RegExp(`id="${id}"`));
  }
  assert.equal((await anonymous.send("GET", "/login/forgot")).status, 200);
  assert.equal((await anonymous.send("GET", "/styles.css")).status, 200);
  assert.equal((await anonymous.send("GET", "/app.js")).status, 200);
  const api = await anonymous.call("/api/projects");
  assert.equal(api.status, 200, "never 401");
  assert.deepEqual(api.data, { ok: false, reason: "signed-out" });
  assert.deepEqual((await anonymous.call("/api/cards/4/park", {})).data, { ok: false, reason: "signed-out" });
});

test("a wrong password or unknown user is refused with a message and no session", async () => {
  for (const [username, password] of [[MEI, `${PASSWORD}-wrong`], ["Test User Input", PASSWORD], [MEI, ""]]) {
    const s = new Session();
    const reply = await s.signIn(username, password);
    assert.equal(reply.status, 200);
    assert.equal(reply.setCookie, false);
    assert.match(reply.text, /<p id="login-error"[^>]*>[^<]+<\/p>/);
    assert.equal((await s.state()).session, null);
  }
});

test("signing in lands on next (same origin only) and the shell shows who is signed in", async () => {
  const s = new Session();
  const reply = await s.signIn(MEI, PASSWORD, "/index.html");
  assert.equal(reply.status, 303);
  assert.equal(reply.location, "/index.html");
  assert.ok(reply.setCookie);
  const shell = await s.send("GET", "/");
  assert.equal(shell.status, 200);
  assert.match(shell.text, /<span id="signed-in-as" class="who">Mei Tanaka · Member<\/span>/);
  assert.match(shell.text, /<script id="vocab-data" type="application\/json">/);
  assert.match(shell.text, /id="nav-account"/);
  const again = await s.send("GET", "/login");
  assert.equal(again.status, 303, "a signed-in browser never sees the sign-in form");
  assert.equal(again.location, "/");
  assert.equal((await s.send("GET", "/login?next=%2Findex.html")).location, "/index.html");
  assert.equal((await s.send("GET", "/login?next=%2F%2Felsewhere.example")).location, "/");
  for (const next of ["//elsewhere.example/", "/\\elsewhere", "https:x", "/login"]) {
    assert.equal((await new Session().signIn(MEI, PASSWORD, next)).location, "/", `next=${next}`);
  }
});

test("signing out keeps the data; signing in again in the same browser resumes it", async () => {
  const s = await fresh();
  await s.command("/api/cards/4/park");
  const before = await s.state();
  const out = await s.send("POST", "/logout");
  assert.equal(out.status, 303);
  assert.equal(out.location, "/login");
  const signedOut = await s.state();
  assert.equal(signedOut.user, null);
  assert.equal(signedOut.sha, before.sha);
  assert.equal((await s.send("GET", "/")).status, 303, "protected again");
  assert.deepEqual((await s.call("/api/boards/web")).data, { ok: false, reason: "signed-out" });
  const back = await s.signIn();
  assert.equal(back.status, 303);
  assert.equal(back.setCookie, false, "the same session, no new cookie");
  const after = await s.state();
  assert.equal(after.session, before.session);
  assert.equal(after.sha, before.sha);
  assert.equal(column(await s.board(), "doing").load, "2/3");
});

test(`beyond WTA_SESSION_CAP (${SESSION_CAP}) the least recently used session is signed out`, async () => {
  const first = await fresh();
  const seedSha = await first.sha();
  const firstSid = (await first.state()).session;
  for (let i = 0; i < SESSION_CAP; i += 1) {
    const other = await fresh();
    if (i === SESSION_CAP - 1) {
      assert.equal(await other.sha(), seedSha);
    }
  }
  assert.equal((await first.state()).session, null, "its partition was dropped");
  const page = await first.send("GET", "/");
  assert.equal(page.status, 303, "it is simply signed out, never an error");
  const back = await first.signIn();
  assert.equal(back.status, 303);
  assert.ok(back.setCookie, "a new session");
  const state = await first.state();
  assert.notEqual(state.session, firstSid);
  assert.equal(state.sha, seedSha, "which starts from the seed");
});

// -- roles -------------------------------------------------------------------------------------

test("a viewer reads the boards shared with them and changes nothing", async () => {
  const ola = await fresh(OLA);
  const before = await ola.sha();
  const projects = (await ola.call("/api/projects")).data.projects;
  assert.deepEqual(projects.map((p) => [p.id, p.role]), [["web", "viewer"]]);
  const board = await ola.board("web");
  assert.deepEqual(board.access, { role: "viewer", canEdit: false, canAdmin: false });
  const forbidden = { ok: false, reason: "forbidden" };
  for (const [path, body] of [["/api/boards/web/pull-in", {}], ["/api/cards/4/park", {}],
    ["/api/cards/8/checklist/3", { done: true }], ["/api/cards/5/comments", { body: "Hello" }],
    ["/api/boards/web/cards", { title: "Mine" }], ["/api/cards/1/move", { to: "doing" }]]) {
    const { status, data } = await ola.call(path, body);
    assert.equal(status, 200, `${path}: never 403`);
    assert.deepEqual({ ok: data.ok, reason: data.reason }, forbidden, path);
  }
  const ops = await ola.call("/api/boards/ops");
  assert.equal(ops.status, 200);
  assert.equal(ops.data.reason, "forbidden", "not a member of the office move");
  assert.equal((await ola.call("/api/cards/11/park", {})).data.reason, "forbidden");
  assert.equal(await ola.sha(), before);
});

test("only a project owner changes WIP limits, never below what a column holds", async () => {
  const mei = await fresh();
  assert.equal((await mei.call("/api/boards/web/settings")).data.reason, "forbidden");
  assert.equal((await mei.call("/api/boards/web/settings", { limits: { doing: "4" } })).data.reason, "forbidden");
  const rosa = await fresh(ROSA);
  const before = await rosa.sha();
  const view = (await rosa.call("/api/boards/web/settings")).data.settings;
  assert.deepEqual(view.columns.map((c) => [c.id, c.wip, c.holding]), [["doing", 3, 3], ["review", 2, 2]]);
  for (const limits of [{ doing: "2" }, { doing: "0" }, { doing: "42" }, { doing: "x" },
    { doing: "5", review: "1" }]) {
    const refused = await rosa.command("/api/boards/web/settings", { limits });
    assert.equal(refused.ok, false, JSON.stringify(limits));
  }
  assert.equal(await rosa.sha(), before, "a refused change changes nothing, in full");
  const saved = await rosa.command("/api/boards/web/settings", { limits: { doing: "4", review: "2" } });
  assert.equal(saved.ok, true);
  assert.equal(column(await rosa.board(), "doing").load, "3/4");
  assert.equal((await rosa.board()).access.canAdmin, true);
  assert.equal((await rosa.command("/api/boards/web/pull-in")).ok, true, "the new room is real");
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
