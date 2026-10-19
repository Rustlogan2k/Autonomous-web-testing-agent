"use strict";
/*
 * Orbit — kanban boards for small teams. A single-page client over a JSON API, served by
 * Node's own `http` module. The data lives in MongoDB (store.js), reached with the suite's
 * vendored driver.
 *
 * Sign-in: everything but the sign-in pages and static assets is behind /login. The first
 * successful sign-in of a browser gives it its own database, seeded from seed/, so every
 * browser session starts from the same data. Pages redirect with 303; the API answers
 * 200 {ok: false, reason: "signed-out" | "forbidden"}; nothing answers 401 or 403.
 *
 * Roles are per project (owner > member > viewer). Each API route names the role it needs
 * on the project it touches; viewers read boards, members work on them, owners also change
 * the board's WIP limits.
 *
 * Requests of one session run one at a time. An API request loads the board it needs into a
 * unit of work; the route's handler changes those documents in memory; a route marked
 * `persist` writes the changed documents back before the response is sent.
 *
 * Domain refusals ("Doing is full", "finish the checklist first") are ordinary answers:
 * 200 with `{ok: false, message}`. 4xx is reserved for requests the UI never sends.
 */

const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const auth = require("./auth.js");
const board = require("./board.js");
const store = require("./store.js");

const APP_ID = "orbit";
const ROOT = path.resolve(__dirname, "..");
const STATIC_DIR = path.join(__dirname, "static");
const PROFILE = process.env.WTA_VOCAB === "alt" ? "alt" : "default";
const VOCAB = JSON.parse(fs.readFileSync(path.join(ROOT, "vocab", `${PROFILE}.json`), "utf8"));
const SEED = store.loadSeed(path.join(ROOT, "seed"));
const clock = board.makeClock(process.env.WTA_FIXED_NOW || "2026-06-10T09:00:00"); // wta-clock
const TEST_HOOKS = process.env.WTA_TEST_HOOKS === "1";
const BODY_LIMIT = 64 * 1024;
const COOKIE = "wta_sid";
const STATIC_TYPES = { ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8" };
/** Credentials are checked against the seeded accounts. */
const ACCOUNTS = new Map(SEED.users.map((user) => [user.username, user]));
const USERS = new Map(SEED.users.map((user) => [user._id, user]));

function fail(message) {
  process.stderr.write(`orbit: ${message}\n`);
  process.exit(2);
}

const MONGODB_URL = process.env.MONGODB_URL || fail("MONGODB_URL is required (the app's home database)");
const SESSION_CAP = /^[1-9]\d*$/.test(process.env.WTA_SESSION_CAP || "32")
  ? Number(process.env.WTA_SESSION_CAP || "32") : fail("WTA_SESSION_CAP must be a positive integer");

/** A server-side label, with `{name}` placeholders filled in. */
function t(key, params = {}) {
  let text = Object.prototype.hasOwnProperty.call(VOCAB, key) ? VOCAB[key] : key;
  for (const [name, value] of Object.entries(params)) {
    text = text.split(`{${name}}`).join(String(value));
  }
  return text;
}

/** JSON with sorted keys, so equal states always hash equally. */
function canonical(value) {
  if (Array.isArray(value)) {
    return `[${value.map(canonical).join(",")}]`;
  }
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((k) => `${JSON.stringify(k)}:${canonical(value[k])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

/** The role shown next to the user's name: their highest role on any project. */
function headlineRole(userId) {
  const ranked = ["owner", "member", "viewer"];
  const roles = SEED.projects.map((p) => board.roleOf(p, userId)).filter(Boolean);
  return ranked.find((role) => roles.includes(role)) || "viewer";
}

// -- routes ---------------------------------------------------------------------------------

function boardOf(ctx, pid) {
  return board.boardView(ctx.scope, pid, t);
}

function cardCommand(operation) {
  return (ctx, [n], body) => {
    const card = board.requireCard(ctx.scope, Number(n));
    const outcome = operation(ctx.scope, card, body);
    return { ...outcome, board: boardOf(ctx, card.project_id) };
  };
}

async function listProjects(ctx) {
  const { projects, counts } = await store.projectsOf(ctx.db, ctx.user._id);
  return { projects: board.projectsView(projects, counts, ctx.user._id) };
}

function showBoard(ctx, [pid]) {
  return { board: boardOf(ctx, pid) };
}

function pullIn(ctx, [pid]) {
  return { ...board.pullIn(ctx.scope, pid, t), board: boardOf(ctx, pid) };
}

function shipReady(ctx, [pid]) {
  return { ...board.shipReady(ctx.scope, pid, t), board: boardOf(ctx, pid) };
}

function addCard(ctx, [pid], body) {
  return { ...board.addCard(ctx.scope, pid, body.title, t), board: boardOf(ctx, pid) };
}

function showSettings(ctx, [pid]) {
  return { ok: true, settings: board.settingsView(ctx.scope, pid) };
}

function saveSettings(ctx, [pid], body) {
  return { ...board.setLimits(ctx.scope, pid, body.limits, t), settings: board.settingsView(ctx.scope, pid) };
}

const moveCard = cardCommand((state, card, body) => board.moveCard(state, card, body.to, t));
const shipCard = cardCommand((state, card) => board.shipCard(state, card, t));
const parkCard = cardCommand((state, card) => board.parkCard(state, card, t));
const addComment = cardCommand((state, card, body) => board.addComment(state, card, body.body, t));

function toggleChecklistItem(ctx, [n, item], body) {
  const card = board.requireCard(ctx.scope, Number(n));
  const outcome = board.setChecklistItem(ctx.scope, card, Number(item), body.done, t);
  return { ...outcome, board: boardOf(ctx, card.project_id) };
}

// `scope`: what the route works on ("board": the project in the path; "card": the card's
// project) and the role it needs there. `persist`: write the changed documents back.
const ROUTES = [
  { method: "GET", pattern: /^\/api\/projects$/, handler: listProjects },
  { method: "GET", pattern: /^\/api\/boards\/([a-z]+)$/, scope: "board", need: "viewer", handler: showBoard },
  { method: "POST", pattern: /^\/api\/boards\/([a-z]+)\/pull-in$/, scope: "board", need: "member", handler: pullIn, persist: true },
  { method: "POST", pattern: /^\/api\/boards\/([a-z]+)\/ship-ready$/, scope: "board", need: "member", handler: shipReady, persist: true },
  { method: "POST", pattern: /^\/api\/boards\/([a-z]+)\/cards$/, scope: "board", need: "member", handler: addCard, persist: true },
  { method: "GET", pattern: /^\/api\/boards\/([a-z]+)\/settings$/, scope: "board", need: "owner", handler: showSettings },
  { method: "POST", pattern: /^\/api\/boards\/([a-z]+)\/settings$/, scope: "board", need: "owner", handler: saveSettings, persist: true },
  { method: "POST", pattern: /^\/api\/cards\/(\d+)\/move$/, scope: "card", need: "member", handler: moveCard, persist: true },
  { method: "POST", pattern: /^\/api\/cards\/(\d+)\/ship$/, scope: "card", need: "member", handler: shipCard, persist: true },
  { method: "POST", pattern: /^\/api\/cards\/(\d+)\/park$/, scope: "card", need: "member", handler: parkCard, persist: true },
  { method: "POST", pattern: /^\/api\/cards\/(\d+)\/checklist\/(\d+)$/, scope: "card", need: "member", handler: toggleChecklistItem, persist: true },
  { method: "POST", pattern: /^\/api\/cards\/(\d+)\/comments$/, scope: "card", need: "member", handler: addComment, persist: true },
];

/** Load what `route` works on into `work`; null when the user may not touch it. */
async function routeScope(ctx, route, params, work) {
  const pid = route.scope === "card" ? await store.cardProject(ctx.db, Number(params[0])) : params[0];
  if (!pid) {
    throw new board.RequestError(404, "No such card.");
  }
  const scope = await store.loadBoard(work, pid);
  if (!scope) {
    throw new board.RequestError(404, "No such project.");
  }
  if (!board.allows(board.roleOf(scope.projects[0], ctx.user._id), route.need)) {
    return null;
  }
  return Object.assign(scope, { actor: ctx.user._id, clock });
}

async function api(res, ctx, method, pathname, text) {
  if (!ctx.user) {
    return sendJson(res, 200, { ok: false, reason: "signed-out" });
  }
  try {
    const body = parseBody(text);
    for (const route of ROUTES) {
      const match = route.method === method ? route.pattern.exec(pathname) : null;
      if (!match) {
        continue;
      }
      const params = match.slice(1);
      const work = new store.Work(ctx.db);
      const scope = route.scope ? await routeScope(ctx, route, params, work) : null;
      if (route.scope && !scope) {
        return sendJson(res, 200, { ok: false, reason: "forbidden", message: t("notice.forbidden") });
      }
      const payload = await route.handler({ ...ctx, scope }, params, body);
      if (route.persist) {
        work.adopt("cards", scope.cards);
        await work.commit();
      }
      return sendJson(res, 200, payload);
    }
    throw new board.RequestError(404, "not found");
  } catch (error) {
    if (error instanceof board.RequestError) {
      return sendJson(res, error.status, { ok: false, error: error.message });
    }
    throw error;
  }
}

async function testState(res, ctx) {
  if (!TEST_HOOKS) {
    return send(res, 404, "not found", "text/plain; charset=utf-8");
  }
  if (!ctx.session) {
    return sendJson(res, 200, { session: null, user: null, committed: null, sha: null });
  }
  const { projects, cards } = await store.everything(ctx.db);
  const committed = board.committed(projects, cards);
  const sha = crypto.createHash("sha256").update(canonical(committed)).digest("hex");
  return sendJson(res, 200, {
    session: ctx.session._id, user: ctx.user ? ctx.user.username : null, committed, sha,
  });
}

// -- sign in and out ------------------------------------------------------------------------

async function signIn(res, ctx, text) {
  const form = new URLSearchParams(text);
  const next = auth.safeNext(form.get("next"));
  const account = ACCOUNTS.get((form.get("username") || "").trim());
  const valid = await auth.verify(account ? account.pw : null, form.get("password") || "");
  if (!valid) {
    return sendHtml(res, 200, auth.loginPage(t, { error: t("login.failed"), next }));
  }
  const { sid, isNew } = await data.signIn(ctx.cookie, account._id);
  return redirect(res, next, isNew ? { "Set-Cookie": sessionCookie(sid) } : {});
}

async function signOut(res, ctx) {
  if (ctx.session) {
    await data.signOut(ctx.session._id); // the partition stays: signing in again resumes it
  }
  return redirect(res, "/login");
}

function sessionCookie(sid) {
  return `${COOKIE}=${sid}; Path=/; HttpOnly; SameSite=Lax`;
}

// -- HTTP -----------------------------------------------------------------------------------

function send(res, status, body, type, extraHeaders = {}) {
  const payload = Buffer.isBuffer(body) ? body : Buffer.from(String(body), "utf8");
  res.writeHead(status, {
    "Content-Type": type,
    "Content-Length": payload.length,
    "Cache-Control": "no-store",
    ...extraHeaders,
  });
  res.end(payload);
}

function sendJson(res, status, value) {
  send(res, status, JSON.stringify(value), "application/json; charset=utf-8");
}

function sendHtml(res, status, html) {
  send(res, status, html, "text/html; charset=utf-8");
}

function redirect(res, location, extraHeaders = {}) {
  send(res, 303, "", "text/plain; charset=utf-8", { Location: location, ...extraHeaders });
}

function readCookie(req, name) {
  for (const part of String(req.headers.cookie || "").split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) {
      return rest.join("=");
    }
  }
  return "";
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > BODY_LIMIT) {
        reject(new board.RequestError(413, "Request body too large."));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

function parseBody(text) {
  if (!text.trim()) {
    return {};
  }
  let value;
  try {
    value = JSON.parse(text);
  } catch {
    throw new board.RequestError(400, "Request body is not JSON.");
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new board.RequestError(400, "Request body must be a JSON object.");
  }
  return value;
}

/** The page shell, with the vocabulary and the signed-in user embedded so the client renders
 *  without a round trip. */
function indexHtml(user) {
  const shell = fs.readFileSync(path.join(STATIC_DIR, "index.html"), "utf8");
  const vocab = JSON.stringify(VOCAB).replace(/</g, "\\u003c");
  const who = t("label.signed_in_as", { name: user.name, role: t(`role.${headlineRole(user._id)}`) });
  const account = `<div class="account">
      <span id="signed-in-as" class="who">${auth.esc(who)}</span>
      <button id="nav-account" type="button" aria-haspopup="menu" aria-expanded="false"
        aria-controls="account-menu">${auth.esc(t("nav.account"))}</button>
      <div id="account-menu" class="menu account-menu" role="menu" hidden>
        <form method="post" action="/logout">
          <button id="sign-out" type="submit" role="menuitem">${auth.esc(t("act.sign_out"))}</button>
        </form>
      </div>
    </div>`;
  return shell
    .replace("<!--VOCAB-->", `<script id="vocab-data" type="application/json">${vocab}</script>`)
    .replace("<!--TITLE-->", auth.esc(t("app.title")))
    .replace("<!--ACCOUNT-->", account);
}

function isAsset(pathname) {
  const name = pathname.slice(1);
  return pathname === "/favicon.ico"
    || (/^[a-z]+\.(js|css)$/.test(name) && fs.existsSync(path.join(STATIC_DIR, name)));
}

function serveAsset(res, pathname) {
  if (pathname === "/favicon.ico") {
    return send(res, 204, "", "image/x-icon");
  }
  const name = pathname.slice(1);
  return send(res, 200, fs.readFileSync(path.join(STATIC_DIR, name)), STATIC_TYPES[path.extname(name)]);
}

/** One request of one browser session (run alone within that session). */
async function dispatch(req, res, url, cookie, text) {
  const session = await data.session(cookie);
  const user = session && session.user_id ? USERS.get(session.user_id) : null;
  const ctx = { cookie, session, user, db: session ? data.database(session.partition) : null };
  const { pathname } = url;
  const { method } = req;
  if (pathname === "/login" && method === "POST") {
    return signIn(res, ctx, text);
  }
  if (pathname === "/login" && method === "GET") {
    const next = auth.safeNext(url.searchParams.get("next"));
    return user ? redirect(res, next) : sendHtml(res, 200, auth.loginPage(t, { next }));
  }
  if (pathname === "/login/forgot" && method === "GET") {
    return sendHtml(res, 200, auth.forgotPage(t));
  }
  if (pathname === "/logout" && method === "POST") {
    return signOut(res, ctx);
  }
  if (pathname === "/__wta/state" && method === "GET") {
    return testState(res, ctx);
  }
  if (pathname.startsWith("/api/")) {
    return api(res, ctx, method, pathname, text);
  }
  if (method === "GET" && (pathname === "/" || pathname === "/index.html")) {
    return user ? sendHtml(res, 200, indexHtml(user))
      : redirect(res, `/login?next=${encodeURIComponent(pathname)}`);
  }
  return send(res, 404, "not found", "text/plain; charset=utf-8");
}

async function handle(req, res) {
  const url = new URL(req.url, "http://127.0.0.1");
  if (req.method === "GET" && url.pathname === "/healthz") {
    const ok = await data.ping();
    return sendJson(res, ok ? 200 : 503, { ok, app: APP_ID });
  }
  if (req.method === "GET" && isAsset(url.pathname)) {
    return serveAsset(res, url.pathname);
  }
  let text = "";
  try {
    text = req.method === "POST" ? await readBody(req) : "";
  } catch (error) {
    if (error instanceof board.RequestError) {
      return sendJson(res, error.status, { ok: false, error: error.message });
    }
    throw error;
  }
  const cookie = readCookie(req, COOKIE);
  return store.isSessionId(cookie)
    ? data.exclusive(cookie, () => dispatch(req, res, url, cookie, text))
    : dispatch(req, res, url, "", text);
}

let data = null; // the Store, once connected

async function main() {
  data = await store.Store.open(MONGODB_URL, { seed: SEED, cap: SESSION_CAP });
  const server = http.createServer((req, res) => {
    handle(req, res).catch((error) => {
      process.stderr.write(`${error.stack || error}\n`);
      if (!res.headersSent) {
        sendJson(res, 500, { ok: false, error: "Something went wrong." });
      }
    });
  });
  const stop = () => server.close(() => data.close().finally(() => process.exit(0)));
  process.on("SIGTERM", stop);
  process.on("SIGINT", stop);
  server.listen(Number(process.env.PORT || 8080), process.env.HOST || "127.0.0.1");
}

main().catch((error) => {
  process.stderr.write(`orbit: cannot start: ${error.stack || error}\n`);
  process.exit(1);
});
