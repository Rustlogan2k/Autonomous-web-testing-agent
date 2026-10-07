// Tally: a members' club chair election by ranked ballot (instant-runoff count).
// Node 22 built-ins only.
//
// /poll is one URL whose content the server chooses from the poll's phase: open (officer
// panel + voting kiosk), closing (confirmation), counted (results), declared. Phase changes
// are form POSTs answered with 303 back to /poll (publishing the declaration lands on the
// printable /declaration). At the kiosk, Start ballot and Cast ballot
// are fetch requests from a panel that stays on screen (static/poll.js) until Next voter.
// The count is made once, when the poll is closed, and stepped through round by round in the
// browser. A session's poll lives in one JSON file under WTA_DATA_DIR.

import { createHash } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import * as polls from "./poll.js";
import * as views from "./views.js";

const APP_ID = "tally";
const HERE = dirname(fileURLToPath(import.meta.url));
const APP_ROOT = dirname(HERE);
const SEED = JSON.parse(readFileSync(join(APP_ROOT, "seed", "poll.json"), "utf8"));
const PROFILE = process.env.WTA_VOCAB === "alt" ? "alt" : "default";
views.useVocabulary(JSON.parse(readFileSync(join(APP_ROOT, "vocab", `${PROFILE}.json`), "utf8")), SEED);
const t = views.t;
const HOOKS = process.env.WTA_TEST_HOOKS === "1";
const STORE = process.env.WTA_DATA_DIR || mkdtempSync(join(tmpdir(), "tally-"));
const MAX_BODY = 16 * 1024;
const CANDIDATE_IDS = SEED.candidates.map((c) => c.id);
const ASSETS = {
  "poll.js": "text/javascript; charset=utf-8",
  "tally.css": "text/css; charset=utf-8",
};

/** The poll keeps no dates, but the clock contract is honoured: "now" is WTA_FIXED_NOW. */
const NOW = process.env.WTA_FIXED_NOW || "2026-06-10T09:00:00"; // wta-clock

// -- sessions: one JSON file each ----------------------------------------------------------

const SESSION_DIR = join(STORE, "sessions");
mkdirSync(SESSION_DIR, { recursive: true });
const SID_SHAPE = /^p(\d{6})$/;
let lastSession = readdirSync(SESSION_DIR)
  .map((name) => SID_SHAPE.exec(name.replace(/\.json$/, "")))
  .reduce((high, match) => (match ? Math.max(high, Number(match[1])) : high), 0);

const sessionPath = (sid) => join(SESSION_DIR, `${sid}.json`);

function cookieValue(header, name) {
  for (const pair of String(header ?? "").split(";")) {
    const [key, ...rest] = pair.split("=");
    if (key.trim() === name) return rest.join("=").trim();
  }
  return "";
}

function loadSession(req) {
  const claimed = cookieValue(req.headers.cookie, "wta_sid");
  if (SID_SHAPE.test(claimed) && existsSync(sessionPath(claimed))) {
    return { sid: claimed, data: JSON.parse(readFileSync(sessionPath(claimed), "utf8")), isNew: false };
  }
  lastSession += 1;
  const sid = `p${String(lastSession).padStart(6, "0")}`;
  const data = { poll: polls.freshPoll(SEED), flash: null };
  writeFileSync(sessionPath(sid), JSON.stringify(data), { encoding: "utf8", flag: "wx" });
  return { sid, data, isNew: true };
}

function storeSession(session) {
  writeFileSync(sessionPath(session.sid), JSON.stringify(session.data), "utf8");
}

function sortedJson(value) {
  if (Array.isArray(value)) return `[${value.map(sortedJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const keys = Object.keys(value).sort();
    return `{${keys.map((key) => `${JSON.stringify(key)}:${sortedJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

// -- responses -----------------------------------------------------------------------------

function reply(ctx, status, type, body, headers = {}) {
  const bytes = Buffer.from(body, "utf8");
  const head = { "Cache-Control": "no-store", "Content-Length": String(bytes.length), ...headers };
  if (type) head["Content-Type"] = type;
  if (ctx.session?.isNew) head["Set-Cookie"] = `wta_sid=${ctx.session.sid}; Path=/; HttpOnly; SameSite=Lax`;
  ctx.res.writeHead(status, head);
  ctx.res.end(bytes);
}

const html = (ctx, markup, status = 200) => reply(ctx, status, "text/html; charset=utf-8", markup);
const json = (ctx, value, status = 200) => reply(ctx, status, "application/json; charset=utf-8", JSON.stringify(value));

/**
 * A phase change: keep the outcome as a one-time message for /poll, and go back there.
 * A published declaration goes straight to the printable declaration instead.
 */
function afterPhaseChange(ctx, path, outcome) {
  const published = outcome.ok && path === "/poll/declare";
  ctx.session.data.flash = published ? null : { ok: outcome.ok, message: outcome.message };
  storeSession(ctx.session);
  reply(ctx, 303, null, "", { Location: published ? "/declaration" : "/poll" });
}

/** The waiting message, if any. Any page view uses it up. */
function takeFlash(ctx) {
  const waiting = ctx.session.data.flash;
  if (!waiting) return null;
  ctx.session.data.flash = null;
  storeSession(ctx.session);
  return ctx.path === "/poll" ? waiting : null;
}

// -- routes --------------------------------------------------------------------------------

function showPoll(ctx) {
  const flash = takeFlash(ctx);
  const poll = ctx.session.data.poll;
  const paper = polls.kioskPaper(poll);
  const status = paper && paper.used ? t("kiosk.cast", { paper: paper.number }) : "";
  html(ctx, views.pollPage({ poll, kioskPaper: paper, kioskStatus: status, unused: polls.unusedPapers(poll).length, flash }));
}

/** A kiosk request: the answer always carries the officer's figures, to update in place. */
function kioskAnswer(ctx, outcome) {
  const poll = ctx.session.data.poll;
  if (outcome.ok) storeSession(ctx.session);
  return json(ctx, {
    ok: outcome.ok, message: t(...outcome.message), cast: poll.ballots.length, issued: poll.papers.length,
  });
}

const PHASE_ACTIONS = {
  "/poll/issue": (poll) => polls.issuePaper(poll, SEED.members),
  "/poll/close": (poll) => polls.requestClose(poll),
  "/poll/cancel-close": (poll) => polls.cancelClose(poll),
  "/poll/confirm-close": (poll) => polls.confirmClose(poll, SEED.candidates),
  "/poll/declare": (poll) => polls.publishDeclaration(poll, SEED.candidates),
};

function route(ctx, body) {
  const { method, path } = ctx;
  const poll = ctx.session.data.poll;
  if (method === "GET") {
    if (path === "/__wta/state") {
      if (!HOOKS) return html(ctx, views.missingPage(), 404);
      const committed = { poll };
      const sha = createHash("sha256").update(sortedJson(committed)).digest("hex");
      return json(ctx, { session: ctx.session.sid, committed, sha });
    }
    if (path === "/") return reply(ctx, 303, null, "", { Location: "/poll" });
    if (path === "/poll") return showPoll(ctx);
    takeFlash(ctx);
    if (path === "/declaration") return html(ctx, views.declarationPage(poll));
    if (path === "/register") return html(ctx, views.registerPage(poll));
    if (path === "/ballots") return html(ctx, views.ballotsPage(poll));
    if (path === "/help") return html(ctx, views.helpPage());
    return html(ctx, views.missingPage(), 404);
  }
  if (method === "POST") {
    if (Object.prototype.hasOwnProperty.call(PHASE_ACTIONS, path)) return afterPhaseChange(ctx, path, PHASE_ACTIONS[path](poll));
    if (path === "/api/kiosk/start") return kioskAnswer(ctx, polls.startBallot(poll, Number(body.paper)));
    if (path === "/api/ballots") {
      return kioskAnswer(ctx, polls.castBallot(poll, { paper: Number(body.paper), preferences: body.preferences }, CANDIDATE_IDS, SEED.ranks));
    }
  }
  return html(ctx, views.missingPage(), 404);
}

// -- HTTP ----------------------------------------------------------------------------------

function readBody(req) {
  return new Promise((resolve, reject) => {
    const parts = [];
    let size = 0;
    req.on("data", (part) => {
      size += part.length;
      if (size > MAX_BODY) {
        reject(Object.assign(new Error("body too large"), { status: 413 }));
        req.destroy();
      } else {
        parts.push(part);
      }
    });
    req.on("end", () => {
      const text = Buffer.concat(parts).toString("utf8");
      if (!String(req.headers["content-type"] ?? "").startsWith("application/json")) return resolve({});
      try {
        const value = text ? JSON.parse(text) : {};
        resolve(value && typeof value === "object" && !Array.isArray(value) ? value : {});
      } catch {
        reject(Object.assign(new Error("body is not JSON"), { status: 400 }));
      }
    });
    req.on("error", reject);
  });
}

function sendAsset(res, name) {
  const type = Object.prototype.hasOwnProperty.call(ASSETS, name) ? ASSETS[name] : null;
  if (!type) {
    res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
    res.end("not found");
    return;
  }
  const bytes = readFileSync(join(HERE, "static", name));
  res.writeHead(200, { "Content-Type": type, "Content-Length": String(bytes.length), "Cache-Control": "no-cache" });
  res.end(bytes);
}

async function handle(req, res) {
  const url = new URL(req.url ?? "/", "http://127.0.0.1");
  if (url.pathname === "/healthz") {
    res.writeHead(200, { "Content-Type": "application/json", "Cache-Control": "no-store" });
    res.end(JSON.stringify({ ok: true, app: APP_ID }));
    return;
  }
  if (url.pathname.startsWith("/static/")) {
    sendAsset(res, url.pathname.slice("/static/".length));
    return;
  }
  const body = req.method === "POST" ? await readBody(req) : {};
  // From here on nothing awaits: a session's read, change and write happen in one turn of
  // the event loop, so two requests of one session never interleave.
  const ctx = { res, method: req.method, path: url.pathname, session: loadSession(req) };
  route(ctx, body);
}

const server = createServer((req, res) => {
  handle(req, res).catch((error) => {
    const status = error.status ?? 500;
    if (status === 500) process.stderr.write(`${error.stack ?? error}\n`);
    if (!res.headersSent) {
      res.writeHead(status, { "Content-Type": "application/json; charset=utf-8" });
      res.end(JSON.stringify({ ok: false, message: error.message }));
    } else {
      res.end();
    }
  });
});

const port = Number(process.env.PORT);
if (!Number.isInteger(port) || port <= 0) {
  process.stderr.write("PORT must be set to the port to listen on\n");
  process.exit(2);
}
server.listen(port, process.env.HOST || "127.0.0.1", () => {
  process.stdout.write(`${APP_ID} listening on ${port} (vocabulary ${PROFILE}, now ${NOW}, data ${STORE})\n`);
});
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => server.close(() => process.exit(0)));
