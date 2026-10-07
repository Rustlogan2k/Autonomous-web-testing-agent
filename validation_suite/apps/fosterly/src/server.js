// Fosterly: an animal rescue's application to become a foster carer. Node 22 built-ins only.
//
// The application is one server-rendered page of accordion sections. static/apply.js sends
// every change of every field at once as a PATCH of that one field to
// /api/drafts/<ref>/<section>; the server checks it, stores it in the session's draft and
// answers {ok, completeness}. Drafts are listed on the start page by reference and can be
// continued at any time; submitting a complete draft turns it into an immutable application.
// A session's drafts and applications live in one JSON file under WTA_DATA_DIR.

import { createHash } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import * as store from "./applications.js";
import * as views from "./views.js";

const APP_ID = "fosterly";
const HERE = dirname(fileURLToPath(import.meta.url));
const APP_ROOT = dirname(HERE);
const SEED = JSON.parse(readFileSync(join(APP_ROOT, "seed", "fosterly.json"), "utf8"));
const PROFILE = process.env.WTA_VOCAB === "alt" ? "alt" : "default";
views.useVocabulary(JSON.parse(readFileSync(join(APP_ROOT, "vocab", `${PROFILE}.json`), "utf8")), SEED);
const t = views.t;
const HOOKS = process.env.WTA_TEST_HOOKS === "1";
const DATA_DIR = process.env.WTA_DATA_DIR || mkdtempSync(join(tmpdir(), "fosterly-"));
const MAX_BODY = 64 * 1024;
const ASSETS = {
  "apply.js": "text/javascript; charset=utf-8",
  "review.js": "text/javascript; charset=utf-8",
  "fosterly.css": "text/css; charset=utf-8",
};

/** "Today" is the date part of WTA_FIXED_NOW; the app has no other clock. */
const TODAY = (() => {
  const raw = process.env.WTA_FIXED_NOW || "2026-06-10T09:00:00"; // wta-clock
  const match = /^(\d{4}-\d{2}-\d{2})(T|$)/.exec(raw);
  if (!match) throw new Error(`WTA_FIXED_NOW must be an ISO date-time, got ${raw}`);
  return match[1];
})();

// -- sessions: one JSON file each --------------------------------------------------------------

const SESSION_DIR = join(DATA_DIR, "sessions");
mkdirSync(SESSION_DIR, { recursive: true });
const SID_SHAPE = /^f(\d{6})$/;
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
  const sid = `f${String(lastSession).padStart(6, "0")}`;
  const data = { ...store.storeFromSeed(SEED), flash: null };
  writeFileSync(sessionPath(sid), JSON.stringify(data), { encoding: "utf8", flag: "wx" });
  return { sid, data, isNew: true };
}

function saveSession(session) {
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

// -- responses ---------------------------------------------------------------------------------

function reply(ctx, status, type, body, headers = {}) {
  const bytes = Buffer.from(body, "utf8");
  const head = { "Cache-Control": "no-store", "Content-Length": String(bytes.length), ...headers };
  if (type) head["Content-Type"] = type;
  if (ctx.session?.isNew) head["Set-Cookie"] = `wta_sid=${ctx.session.sid}; Path=/; HttpOnly; SameSite=Lax`;
  ctx.res.writeHead(status, head);
  ctx.res.end(bytes);
}

const html = (ctx, markup, status = 200) => reply(ctx, status, "text/html; charset=utf-8", markup);
const json = (ctx, value) => reply(ctx, 200, "application/json; charset=utf-8", JSON.stringify(value));

/** Post/redirect/get: leave a one-time message for `target`, then send the browser there. */
function redirect(ctx, target, flash = null) {
  if (flash) ctx.session.data.flash = { path: target, ...flash };
  saveSession(ctx.session);
  reply(ctx, 303, null, "", { Location: target });
}

/** The waiting message, if it was left for this page. Any page view uses it up. */
function takeFlash(ctx) {
  const waiting = ctx.session.data.flash;
  if (!waiting) return null;
  ctx.session.data.flash = null;
  saveSession(ctx.session);
  return waiting.path === ctx.path ? waiting : null;
}

// -- routes ------------------------------------------------------------------------------------

function showStart(ctx) {
  html(ctx, views.startPage({ drafts: ctx.session.data.drafts, flash: takeFlash(ctx) }));
}

function startDraft(ctx) {
  const ref = store.openDraft(ctx.session.data);
  redirect(ctx, `/apply/${ref}`);
}

/** A draft page whose draft has gone: submitted (say where to) or never existed. */
function draftGone(ctx, ref) {
  takeFlash(ctx);
  const application = store.submittedFrom(ctx.session.data, ref);
  if (application) return html(ctx, views.submittedDraftPage({ draftRef: ref, application }));
  return html(ctx, views.messagePage("missing.title", "missing.body"), 404);
}

function showApply(ctx, ref) {
  const draft = store.findDraft(ctx.session.data, ref);
  if (!draft) return draftGone(ctx, ref);
  return html(ctx, views.applyPage({ draft, today: TODAY, flash: takeFlash(ctx) }));
}

function autosave(ctx, ref, section) {
  const draft = store.findDraft(ctx.session.data, ref);
  if (!draft) return json(ctx, { ok: false, message: t("error.draft_missing") });
  let body;
  try {
    body = JSON.parse(ctx.body || "null");
  } catch {
    return json(ctx, { ok: false, message: t("error.value") });
  }
  const outcome = store.saveAnswers(draft, section, body, TODAY);
  if (!outcome.ok) return json(ctx, { ok: false, field: outcome.field ?? null, message: t(outcome.problem) });
  saveSession(ctx.session);
  return json(ctx, { ok: true, completeness: outcome.completeness });
}

function showReview(ctx, ref) {
  const draft = store.findDraft(ctx.session.data, ref);
  if (!draft) return draftGone(ctx, ref);
  return html(ctx, views.reviewPage({ draft, flash: takeFlash(ctx) }));
}

function submitDraft(ctx, ref) {
  const data = ctx.session.data;
  const earlier = store.submittedFrom(data, ref);
  if (earlier) return redirect(ctx, `/applications/${earlier.ref}`);
  const outcome = store.submit(data, ref, TODAY);
  if (outcome.ok) {
    const target = `/applications/${outcome.application.ref}`;
    return redirect(ctx, target, { tone: "notice", key: "flash.submitted", params: { ref: outcome.application.ref } });
  }
  if (outcome.missing) {
    return redirect(ctx, `/apply/${ref}/review`, {
      tone: "error", key: outcome.problem, params: { sections: views.missingSections(outcome.missing) },
    });
  }
  return redirect(ctx, "/", { tone: "error", key: outcome.problem });
}

function showApplication(ctx, ref) {
  const application = store.findApplication(ctx.session.data, ref);
  if (!application) {
    takeFlash(ctx);
    return html(ctx, views.messagePage("missing.title", "missing.body"), 404);
  }
  return html(ctx, views.applicationPage({ application, flash: takeFlash(ctx) }));
}

function route(ctx) {
  const { method, path } = ctx;
  if (method === "GET" && path === "/__wta/state") {
    if (!HOOKS) return html(ctx, views.messagePage("missing.title", "missing.body"), 404);
    const committed = store.committed(ctx.session.data);
    const sha = createHash("sha256").update(sortedJson(committed)).digest("hex");
    return json(ctx, { session: ctx.session.sid, committed, sha });
  }
  if (method === "GET" && path === "/") return showStart(ctx);
  if (method === "POST" && path === "/drafts") return startDraft(ctx);
  if (method === "GET" && path === "/applications") {
    takeFlash(ctx);
    return html(ctx, views.applicationsPage(ctx.session.data));
  }
  if (method === "GET" && path === "/help/fostering") {
    takeFlash(ctx);
    return html(ctx, views.helpPage());
  }
  let match = /^\/api\/drafts\/(FOS-\d+)\/([a-z]+)$/.exec(path);
  if (match && method === "PATCH") return autosave(ctx, match[1], match[2]);
  match = /^\/apply\/(FOS-\d+)(\/review|\/submit)?$/.exec(path);
  if (match && method === "GET" && !match[2]) return showApply(ctx, match[1]);
  if (match && method === "GET" && match[2] === "/review") return showReview(ctx, match[1]);
  if (match && method === "POST" && match[2] === "/submit") return submitDraft(ctx, match[1]);
  match = /^\/applications\/(FA-\d+)$/.exec(path);
  if (match && method === "GET") return showApplication(ctx, match[1]);
  takeFlash(ctx);
  return html(ctx, views.messagePage("missing.title", "missing.body"), 404);
}

// -- HTTP --------------------------------------------------------------------------------------

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
    req.on("end", () => resolve(Buffer.concat(parts).toString("utf8")));
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
  const raw = req.method === "GET" || req.method === "HEAD" ? "" : await readBody(req);
  // Nothing awaits from here on: a session's read, change and write happen in one turn of the
  // event loop, so two requests of one session never interleave.
  const ctx = {
    res, method: req.method, path: url.pathname, query: url.searchParams, body: raw,
    form: new URLSearchParams(raw), session: loadSession(req),
  };
  route(ctx);
}

const server = createServer((req, res) => {
  handle(req, res).catch((error) => {
    const status = error.status ?? 500;
    if (status === 500) process.stderr.write(`${error.stack ?? error}\n`);
    if (!res.headersSent) {
      res.writeHead(status, { "Content-Type": "text/plain; charset=utf-8" });
      res.end(status === 413 ? "body too large" : "internal error");
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
  process.stdout.write(`${APP_ID} listening on ${port} (vocabulary ${PROFILE}, today ${TODAY}, data ${DATA_DIR})\n`);
});
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => server.close(() => process.exit(0)));
