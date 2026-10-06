// Ledgerly — expense claims with multi-role approval. Node 22 built-ins only.
//
// Server-rendered pages and plain HTML forms with post/redirect/get throughout; the one
// client-side island is the claim editor's repeatable lines (static/claim-editor.js). State is
// an append-only event log per session (store.js), folded over the seed on every request
// (claims.js), so a page always shows exactly what the log says.

import crypto from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import * as claims from "./claims.js";
import { EventStore } from "./store.js";
import * as views from "./views.js";

const APP_ID = "ledgerly";
const SRC = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.dirname(SRC);
const SEED = JSON.parse(fs.readFileSync(path.join(ROOT, "seed", "ledger.json"), "utf8"));
const CFG = claims.config(SEED);
const PROFILE = process.env.WTA_VOCAB === "alt" ? "alt" : "default";
Object.assign(views.VOCAB, JSON.parse(fs.readFileSync(path.join(ROOT, "vocab", `${PROFILE}.json`), "utf8")));
const TEST_HOOKS = process.env.WTA_TEST_HOOKS === "1";
const DATA_DIR = process.env.WTA_DATA_DIR || fs.mkdtempSync(path.join(os.tmpdir(), "ledgerly-"));
const BODY_LIMIT = 256 * 1024;
const STATIC = {
  "claim-editor.js": "text/javascript; charset=utf-8",
  "money.js": "text/javascript; charset=utf-8",
  "ledgerly.css": "text/css; charset=utf-8",
};
const CLAIM_PATH = /^\/claims\/(CL-\d{3,6})$/;
const EDIT_PATH = /^\/claims\/(CL-\d{3,6})\/edit$/;

/** The application's clock: WTA_FIXED_NOW as "YYYY-MM-DDTHH:MM:SS". There is no other. */
function clockNow() {
  const raw = process.env.WTA_FIXED_NOW || "2026-06-10T09:00:00";
  const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(:\d{2})?/.exec(raw);
  if (!match) throw new Error(`WTA_FIXED_NOW is not an ISO date-time: ${raw}`);
  return `${match[1]}T${match[2]}${match[3] ?? ":00"}`;
}

const NOW = clockNow();
const store = new EventStore(DATA_DIR);

// -- plumbing -------------------------------------------------------------------------------

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function parseCookies(header) {
  const jar = {};
  for (const part of String(header ?? "").split(";")) {
    const index = part.indexOf("=");
    if (index > 0) jar[part.slice(0, index).trim()] = part.slice(index + 1).trim();
  }
  return jar;
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > BODY_LIMIT) {
        reject(Object.assign(new Error("request body too large"), { status: 413 }));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

function send(ctx, status, type, body, extra = {}) {
  const headers = { "Cache-Control": "no-store", ...extra };
  if (type) headers["Content-Type"] = type;
  if (ctx.setCookie) headers["Set-Cookie"] = `wta_sid=${ctx.sid}; Path=/; HttpOnly; SameSite=Lax`;
  const payload = Buffer.from(body ?? "", "utf8");
  headers["Content-Length"] = String(payload.length);
  ctx.res.writeHead(status, headers);
  ctx.res.end(payload);
}

const sendJson = (ctx, status, value) => send(ctx, status, "application/json", JSON.stringify(value));
const redirect = (ctx, location) => send(ctx, 303, null, "", { Location: location });

function page(ctx, title, body, { status = 200, island = false } = {}) {
  send(ctx, status, "text/html; charset=utf-8", views.layout(CFG, ctx.state.viewer, title, body, { island }));
}

/** Append events to this session's log and keep the in-memory copy in step. */
function commit(ctx, events) {
  const stamped = events.map((event) => ({ at: NOW, ...event }));
  store.append(ctx.sid, stamped, ctx.events.length);
  ctx.events.push(...stamped);
}

/** A one-time message for the next page. Shown only if that page is `path`, then gone. */
function flash(path, fields) {
  return { type: "flash", path, ...fields };
}

function takeFlash(ctx) {
  const pending = ctx.state.flash;
  if (!pending) return null;
  commit(ctx, [{ type: "flash_cleared" }]);
  return pending.path === ctx.path ? pending : null;
}

// -- routes ---------------------------------------------------------------------------------

function home(ctx) {
  const role = claims.roleOf(CFG, ctx.state.viewer);
  takeFlash(ctx);
  if (role === "manager") return page(ctx, views.t("home.manager_title"), views.managerHome(CFG, ctx.state));
  if (role === "finance") return page(ctx, views.t("home.finance_title"), views.financeHome(CFG, ctx.state));
  return page(ctx, views.t("home.employee_title"), views.employeeHome(CFG, ctx.state));
}

function switchViewer(ctx) {
  const viewer = ctx.form.get("viewer") ?? "";
  if (CFG.people[viewer] && viewer !== ctx.state.viewer) commit(ctx, [{ type: "viewer_switched", viewer }]);
  return redirect(ctx, "/");
}

function showEditor(ctx, claim) {
  const pending = takeFlash(ctx);
  if (claims.roleOf(CFG, ctx.state.viewer) !== "employee") {
    return page(ctx, views.t("editor.new_title"), views.infoPage("editor.new_title", "msg.only_claimant"));
  }
  if (claim && (claim.owner !== ctx.state.viewer || !claims.editable(claim))) {
    return redirect(ctx, `/claims/${claim.id}`);
  }
  const rows = pending?.rows?.length ? pending.rows
    : claim ? views.rowsFromLines(claim.lines) : [views.BLANK_ROW];
  const title = claim ? `${views.t("editor.edit_title")} ${claim.id}` : views.t("editor.new_title");
  return page(ctx, title, views.editorPage(CFG, claim, rows, pending), { island: true });
}

function postEditor(ctx, claim) {
  const action = ctx.form.get("action") ?? "save";
  const rows = claims.readRows(ctx.form);
  const back = claim ? `/claims/${claim.id}/edit` : "/claims/new";
  if (action === "template") {
    // Templates are announced but not built: keep what was typed, store nothing.
    commit(ctx, [flash(back, { notice: "msg.template_soon", rows })]);
    return redirect(ctx, back);
  }
  const outcome = claims.editCommand(ctx.state, CFG, claim, action, rows, NOW);
  if (outcome.refused) {
    const target = claim ? `/claims/${claim.id}` : "/claims/new";
    commit(ctx, [flash(target, { error: outcome.refused })]);
    return redirect(ctx, target);
  }
  if (outcome.invalid) {
    commit(ctx, [flash(back, { errors: outcome.invalid, rows })]);
    return redirect(ctx, back);
  }
  const target = `/claims/${outcome.claim}`;
  commit(ctx, [...outcome.events, flash(target, { notice: outcome.notice })]);
  return redirect(ctx, target);
}

function showClaim(ctx, claim) {
  const pending = takeFlash(ctx);
  return page(ctx, `${views.t("claim.title")} ${claim.id}`, views.claimPage(CFG, ctx.state, claim, pending));
}

function postDecision(ctx, claim) {
  const target = `/claims/${claim.id}`;
  const outcome = claims.decideCommand(ctx.state, CFG, claim, ctx.form.get("action") ?? "",
                                       ctx.form.get("note"), NOW);
  if (outcome.refused) {
    commit(ctx, [flash(target, { error: outcome.refused })]);
  } else {
    commit(ctx, [...outcome.events, flash(target, { notice: outcome.notice })]);
  }
  return redirect(ctx, target);
}

function notFound(ctx) {
  takeFlash(ctx);
  return page(ctx, views.t("missing.title"), views.infoPage("missing.title", "missing.body"), { status: 404 });
}

function route(ctx) {
  const { method, path: where } = ctx;
  if (method === "GET" && where === "/__wta/state") {
    if (!TEST_HOOKS) return notFound(ctx);
    const committed = claims.committed(ctx.state);
    const sha = crypto.createHash("sha256").update(canonical(committed)).digest("hex");
    return sendJson(ctx, 200, { session: ctx.sid, committed, sha });
  }
  if (method === "GET" && where === "/") return home(ctx);
  if (method === "POST" && where === "/viewer") return switchViewer(ctx);
  if (where === "/claims/new") {
    if (method === "GET") return showEditor(ctx, null);
    if (method === "POST") return postEditor(ctx, null);
  }
  let match = CLAIM_PATH.exec(where);
  if (match) {
    const claim = claims.findClaim(ctx.state, match[1]);
    if (claim && method === "GET") return showClaim(ctx, claim);
    if (claim && method === "POST") return postDecision(ctx, claim);
  }
  match = EDIT_PATH.exec(where);
  if (match) {
    const claim = claims.findClaim(ctx.state, match[1]);
    if (claim && method === "GET") return showEditor(ctx, claim);
    if (claim && method === "POST") return postEditor(ctx, claim);
  }
  return notFound(ctx);
}

// -- the server -----------------------------------------------------------------------------

function serveStatic(res, name) {
  const type = STATIC[name];
  if (!type) {
    res.writeHead(404, { "Content-Type": "text/plain" });
    res.end("not found");
    return;
  }
  const body = fs.readFileSync(path.join(SRC, "static", name));
  res.writeHead(200, { "Content-Type": type, "Content-Length": String(body.length), "Cache-Control": "no-cache" });
  res.end(body);
}

async function handle(req, res) {
  const url = new URL(req.url ?? "/", "http://127.0.0.1");
  if (url.pathname === "/healthz") {
    res.writeHead(200, { "Content-Type": "application/json", "Cache-Control": "no-store" });
    res.end(JSON.stringify({ ok: true, app: APP_ID }));
    return;
  }
  if (url.pathname.startsWith("/static/")) return serveStatic(res, url.pathname.slice("/static/".length));
  const body = req.method === "POST" ? await readBody(req) : "";
  let sid = parseCookies(req.headers.cookie).wta_sid;
  let setCookie = false;
  if (!store.exists(sid)) {
    sid = store.open(NOW);
    setCookie = true;
  }
  const events = store.read(sid);
  const ctx = {
    res, sid, setCookie, events, method: req.method, path: url.pathname,
    form: new URLSearchParams(body), state: claims.fold(SEED, events),
  };
  route(ctx);
}

const server = http.createServer((req, res) => {
  handle(req, res).catch((error) => {
    const status = error.status ?? 500;
    if (status === 500) console.error(error);
    if (!res.headersSent) {
      res.writeHead(status, { "Content-Type": "text/plain; charset=utf-8" });
      res.end(status === 413 ? "request too large" : "internal error");
    } else {
      res.end();
    }
  });
});

const port = Number(process.env.PORT);
if (!Number.isInteger(port) || port <= 0) {
  console.error("PORT must be set to the port to listen on");
  process.exit(2);
}
server.listen(port, process.env.HOST || "127.0.0.1", () => {
  console.log(`${APP_ID} listening on port ${port} (vocabulary ${PROFILE}, data in ${DATA_DIR})`);
});
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => server.close(() => process.exit(0)));
}
