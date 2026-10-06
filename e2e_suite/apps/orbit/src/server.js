"use strict";
/*
 * Orbit — kanban boards for small teams. A single-page client over a JSON API, served by
 * Node's own `http` module with no dependencies.
 *
 * State: one JSON file per browser session (`wta_sid` cookie) under WTA_DATA_DIR, seeded
 * from seed/orbit.json. Every API request reads the session file, and a route marked
 * `persist` writes it back before the response is sent. Handlers are synchronous between
 * that read and that write, so two requests of one session can never interleave.
 *
 * Domain refusals ("Doing is full", "finish the checklist first") are ordinary answers:
 * 200 with `{ok: false, message}`. 4xx is reserved for requests the UI never sends.
 */

const http = require("node:http");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const crypto = require("node:crypto");
const board = require("./board.js");

const APP_ID = "orbit";
const ROOT = path.resolve(__dirname, "..");
const STATIC_DIR = path.join(__dirname, "static");
const PROFILE = process.env.WTA_VOCAB === "alt" ? "alt" : "default";
const VOCAB = JSON.parse(fs.readFileSync(path.join(ROOT, "vocab", `${PROFILE}.json`), "utf8"));
const SEED = JSON.parse(fs.readFileSync(path.join(ROOT, "seed", "orbit.json"), "utf8"));
const clock = board.makeClock(process.env.WTA_FIXED_NOW || "2026-06-10T09:00:00"); // wta-clock
const DATA_DIR = process.env.WTA_DATA_DIR || fs.mkdtempSync(path.join(os.tmpdir(), "orbit-"));
const TEST_HOOKS = process.env.WTA_TEST_HOOKS === "1";
const BODY_LIMIT = 64 * 1024;
const SESSION_ID = /^s\d{6}$/;
const STATIC_TYPES = { ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8" };

/** A server-side label, with `{name}` placeholders filled in. */
function t(key, params = {}) {
  let text = Object.prototype.hasOwnProperty.call(VOCAB, key) ? VOCAB[key] : key;
  for (const [name, value] of Object.entries(params)) {
    text = text.split(`{${name}}`).join(String(value));
  }
  return text;
}

// -- sessions -------------------------------------------------------------------------------

fs.mkdirSync(DATA_DIR, { recursive: true });
let sessionCounter = fs.readdirSync(DATA_DIR)
  .map((name) => /^s(\d{6})\.json$/.exec(name))
  .filter(Boolean)
  .reduce((max, m) => Math.max(max, Number(m[1])), 0);

const sessionFile = (sid) => path.join(DATA_DIR, `${sid}.json`);

function readCookie(req, name) {
  for (const part of String(req.headers.cookie || "").split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) {
      return rest.join("=");
    }
  }
  return "";
}

/** The caller's session, created from the seed when it has none (or an unknown one). */
function openSession(req) {
  const sid = readCookie(req, "wta_sid");
  if (SESSION_ID.test(sid) && fs.existsSync(sessionFile(sid))) {
    return { sid, state: JSON.parse(fs.readFileSync(sessionFile(sid), "utf8")), isNew: false };
  }
  sessionCounter += 1;
  const fresh = `s${String(sessionCounter).padStart(6, "0")}`;
  const state = board.fromSeed(SEED);
  saveSession(fresh, state);
  return { sid: fresh, state, isNew: true };
}

function saveSession(sid, state) {
  fs.writeFileSync(sessionFile(sid), JSON.stringify(state), "utf8");
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

// -- routes ---------------------------------------------------------------------------------

function boardOf(ctx, pid) {
  return board.boardView(ctx.state, pid, clock);
}

function cardCommand(operation) {
  return (ctx, [n], body) => {
    const card = board.requireCard(ctx.state, Number(n));
    const outcome = operation(ctx.state, card, body);
    return { ...outcome, board: boardOf(ctx, card.project) };
  };
}

function listProjects(ctx) {
  return { projects: board.projectsView(ctx.state) };
}

function showBoard(ctx, [pid]) {
  return { board: boardOf(ctx, pid) };
}

function pullIn(ctx, [pid]) {
  return { ...board.pullIn(ctx.state, pid, t), board: boardOf(ctx, pid) };
}

function shipReady(ctx, [pid]) {
  return { ...board.shipReady(ctx.state, pid, t), board: boardOf(ctx, pid) };
}

function addCard(ctx, [pid], body) {
  return { ...board.addCard(ctx.state, pid, body.title, t), board: boardOf(ctx, pid) };
}

const moveCard = cardCommand((state, card, body) => board.moveCard(state, card, body.to, t));
const shipCard = cardCommand((state, card) => board.shipCard(state, card, t));
const parkCard = cardCommand((state, card) => board.parkCard(state, card, t));
const addComment = cardCommand((state, card, body) => board.addComment(state, card, body.body, t));

function toggleChecklistItem(ctx, [n, item], body) {
  const card = board.requireCard(ctx.state, Number(n));
  const outcome = board.setChecklistItem(ctx.state, card, Number(item), body.done, t);
  return { ...outcome, board: boardOf(ctx, card.project) };
}

function testState(ctx) {
  const committed = board.committed(ctx.state);
  const sha = crypto.createHash("sha256").update(canonical(committed)).digest("hex");
  return { session: ctx.sid, committed, sha };
}

const ROUTES = [
  { method: "GET", pattern: /^\/api\/projects$/, handler: listProjects },
  { method: "GET", pattern: /^\/api\/boards\/([a-z]+)$/, handler: showBoard },
  { method: "POST", pattern: /^\/api\/boards\/([a-z]+)\/pull-in$/, handler: pullIn, persist: true },
  { method: "POST", pattern: /^\/api\/boards\/([a-z]+)\/ship-ready$/, handler: shipReady, persist: true },
  { method: "POST", pattern: /^\/api\/boards\/([a-z]+)\/cards$/, handler: addCard, persist: true },
  { method: "POST", pattern: /^\/api\/cards\/(\d+)\/move$/, handler: moveCard, persist: true },
  { method: "POST", pattern: /^\/api\/cards\/(\d+)\/ship$/, handler: shipCard, persist: true },
  { method: "POST", pattern: /^\/api\/cards\/(\d+)\/park$/, handler: parkCard, persist: true },
  { method: "POST", pattern: /^\/api\/cards\/(\d+)\/checklist\/(\d+)$/, handler: toggleChecklistItem, persist: true },
  { method: "POST", pattern: /^\/api\/cards\/(\d+)\/comments$/, handler: addComment, persist: true },
];

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

function sendJson(res, status, value, session) {
  const headers = session && session.isNew
    ? { "Set-Cookie": `wta_sid=${session.sid}; Path=/; HttpOnly; SameSite=Lax` } : {};
  send(res, status, JSON.stringify(value), "application/json; charset=utf-8", headers);
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

/** The page shell, with the vocabulary embedded so the client renders without a round trip. */
function indexHtml() {
  const shell = fs.readFileSync(path.join(STATIC_DIR, "index.html"), "utf8");
  const vocab = JSON.stringify(VOCAB).replace(/</g, "\\u003c");
  return shell.replace("<!--VOCAB-->",
    `<script id="vocab-data" type="application/json">${vocab}</script>`).replace("<!--TITLE-->", t("app.title"));
}

function serveStatic(req, res, pathname) {
  if (pathname === "/" || pathname === "/index.html") {
    const session = openSession(req); // the cookie is set before the client's first API call
    const headers = session.isNew
      ? { "Set-Cookie": `wta_sid=${session.sid}; Path=/; HttpOnly; SameSite=Lax` } : {};
    return send(res, 200, indexHtml(), "text/html; charset=utf-8", headers);
  }
  if (pathname === "/favicon.ico") {
    return send(res, 204, "", "image/x-icon");
  }
  const name = pathname.slice(1);
  if (/^[a-z]+\.(js|css)$/.test(name) && fs.existsSync(path.join(STATIC_DIR, name))) {
    return send(res, 200, fs.readFileSync(path.join(STATIC_DIR, name)), STATIC_TYPES[path.extname(name)]);
  }
  return send(res, 404, "not found", "text/plain; charset=utf-8");
}

async function handle(req, res) {
  const { pathname } = new URL(req.url, "http://127.0.0.1");
  if (req.method === "GET" && pathname === "/healthz") {
    return sendJson(res, 200, { ok: true, app: APP_ID });
  }
  if (req.method === "GET" && !pathname.startsWith("/api/") && !pathname.startsWith("/__wta/")) {
    return serveStatic(req, res, pathname);
  }
  let session = null;
  try {
    const text = req.method === "POST" ? await readBody(req) : "";
    // From here to the response, nothing awaits: the session's read, change and write
    // happen in one turn of the event loop.
    const body = parseBody(text);
    session = openSession(req);
    if (req.method === "GET" && pathname === "/__wta/state") {
      if (!TEST_HOOKS) {
        throw new board.RequestError(404, "not found");
      }
      return sendJson(res, 200, testState(session), session);
    }
    for (const route of ROUTES) {
      const match = route.method === req.method ? route.pattern.exec(pathname) : null;
      if (match) {
        const payload = route.handler(session, match.slice(1), body);
        if (route.persist) {
          saveSession(session.sid, session.state);
        }
        return sendJson(res, 200, payload, session);
      }
    }
    throw new board.RequestError(404, "not found");
  } catch (error) {
    if (error instanceof board.RequestError) {
      return sendJson(res, error.status, { ok: false, error: error.message }, session);
    }
    process.stderr.write(`${error.stack || error}\n`);
    return sendJson(res, 500, { ok: false, error: "Something went wrong." }, session);
  }
}

const server = http.createServer((req, res) => {
  handle(req, res).catch((error) => {
    process.stderr.write(`${error.stack || error}\n`);
    if (!res.headersSent) {
      send(res, 500, "error", "text/plain; charset=utf-8");
    }
  });
});

server.listen(Number(process.env.PORT || 8080), process.env.HOST || "127.0.0.1");
