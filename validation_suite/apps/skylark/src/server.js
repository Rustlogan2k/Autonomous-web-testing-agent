// Skylark: a local nature group's bird-sightings register. Node 22 built-ins only.
//
// The feed's first page is rendered on the server; "Load more" (static/feed.js) asks
// /feed/more for the next keyset page as JSON and appends it. Everything else is a plain
// server-rendered page or HTML form answered with post/redirect/get. A session's register
// lives in one JSON file under WTA_DATA_DIR; unsent record-form input lives only in the
// browser (static/record.js keeps it in localStorage).

import { createHash } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import * as reg from "./sightings.js";
import * as views from "./views.js";

const APP_ID = "skylark";
const HERE = dirname(fileURLToPath(import.meta.url));
const APP_ROOT = dirname(HERE);
const SEED = JSON.parse(readFileSync(join(APP_ROOT, "seed", "register.json"), "utf8"));
const PROFILE = process.env.WTA_VOCAB === "alt" ? "alt" : "default";
views.useVocabulary(JSON.parse(readFileSync(join(APP_ROOT, "vocab", `${PROFILE}.json`), "utf8")), SEED);
const t = views.t;
const HOOKS = process.env.WTA_TEST_HOOKS === "1";
const STORE = process.env.WTA_DATA_DIR || mkdtempSync(join(tmpdir(), "skylark-"));
const MAX_BODY = 64 * 1024;
const SITE_IDS = SEED.sites.map((site) => site.id);
const SPECIES_IDS = SEED.species.map((kind) => kind.id);
const ASSETS = {
  "feed.js": "text/javascript; charset=utf-8",
  "record.js": "text/javascript; charset=utf-8",
  "sighting.js": "text/javascript; charset=utf-8",
  "skylark.css": "text/css; charset=utf-8",
};

/** "Today" is the date part of WTA_FIXED_NOW; the register has no other clock. */
const TODAY = (() => {
  const raw = process.env.WTA_FIXED_NOW || "2026-06-10T09:00:00"; // wta-clock
  const match = /^(\d{4}-\d{2}-\d{2})(T|$)/.exec(raw);
  if (!match) throw new Error(`WTA_FIXED_NOW must be an ISO date-time, got ${raw}`);
  return match[1];
})();

// -- sessions: one JSON file each ----------------------------------------------------------

const SESSION_DIR = join(STORE, "sessions");
mkdirSync(SESSION_DIR, { recursive: true });
const SID_SHAPE = /^r(\d{6})$/;
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
  const sid = `r${String(lastSession).padStart(6, "0")}`;
  const data = { sightings: SEED.sightings.map((row) => ({ ...row })), flash: null };
  writeFileSync(sessionPath(sid), JSON.stringify(data), { encoding: "utf8", flag: "wx" });
  return { sid, data, isNew: true };
}

function storeSession(session) {
  writeFileSync(sessionPath(session.sid), JSON.stringify(session.data), "utf8");
}

/** Business state only (no flash messages): what the harness compares between sessions. */
function committedState(data) {
  return { sightings: [...data.sightings].sort((a, b) => a.id - b.id) };
}

function sortedJson(value) {
  if (Array.isArray(value)) return `[${value.map(sortedJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    const keys = Object.keys(value).sort();
    return `{${keys.map((key) => `${JSON.stringify(key)}:${sortedJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

/** Where this session's unsent record-form drafts live in the browser's localStorage. */
function draftKey(sid, siteId) {
  const space = createHash("sha256").update(`skylark-drafts:${sid}`).digest("hex").slice(0, 12);
  return `skylark:${space}:draft:${siteId}`;
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

/** Post/redirect/get: remember a one-time message for `target`, then send the browser there. */
function redirect(ctx, target, flash = null) {
  if (flash) ctx.session.data.flash = { path: target.split("?")[0], ...flash };
  storeSession(ctx.session);
  reply(ctx, 303, null, "", { Location: target });
}

/** The waiting message, if it was left for this page. Any page view uses it up. */
function takeFlash(ctx) {
  const waiting = ctx.session.data.flash;
  if (!waiting) return null;
  ctx.session.data.flash = null;
  storeSession(ctx.session);
  return waiting.path === ctx.path ? waiting : null;
}

// -- register views ------------------------------------------------------------------------

/** The fields a feed row is drawn from, on the server and in static/feed.js alike. */
function rowFields(row) {
  return {
    ref: reg.refOf(row.id), date: row.date, count: row.count, site: row.site, status: row.status,
    speciesName: SEED.species.find((kind) => kind.id === row.species)?.name ?? row.species,
    siteName: views.siteName(row.site),
    statusName: SEED.statuses[row.status] ?? row.status,
  };
}

const siteFilter = (value) => (SITE_IDS.includes(value) ? value : "all");

function findSighting(ctx, ref) {
  const id = reg.idFromRef(ref);
  return id === null ? null : ctx.session.data.sightings.find((row) => row.id === id) ?? null;
}

// -- routes --------------------------------------------------------------------------------

function showFeed(ctx) {
  const flash = takeFlash(ctx);
  const site = siteFilter(ctx.query.get("site"));
  const rows = reg.matching(ctx.session.data.sightings, site);
  const first = reg.pageOf(rows);
  html(ctx, views.feedPage({
    site, rows: first.rows.map(rowFields), total: rows.length, more: first.more, next: first.next,
    tally: reg.tallyOf(first.rows, SITE_IDS), flash,
    dropDraft: flash?.sentFrom ? draftKey(ctx.session.sid, flash.sentFrom) : null,
  }));
}

function morePage(ctx) {
  const cursor = reg.parseCursor(ctx.query.get("after"));
  if (!cursor) return json(ctx, { ok: false, message: t("error.cursor") }, 400);
  const rows = reg.matching(ctx.session.data.sightings, siteFilter(ctx.query.get("site")));
  const next = reg.pageOf(rows, cursor);
  const { birds, countedBirds, bySite } = reg.tallyOf(next.rows, SITE_IDS);
  return json(ctx, {
    ok: true, rows: next.rows.map(rowFields), more: next.more, after: next.next, birds, countedBirds, bySite,
  });
}

function showWhere(ctx) {
  html(ctx, views.whereStep({ flash: takeFlash(ctx) }));
}

function chooseWhere(ctx) {
  const site = ctx.form.get("site") ?? "";
  if (!SITE_IDS.includes(site)) return redirect(ctx, "/sightings/new", { tone: "error", key: "error.site" });
  return redirect(ctx, `/sightings/new/${site}`);
}

function showWhat(ctx, siteId) {
  const site = SEED.sites.find((s) => s.id === siteId);
  if (!site) return redirect(ctx, "/sightings/new");
  return html(ctx, views.whatStep({ site, today: TODAY, draftKey: draftKey(ctx.session.sid, site.id), flash: takeFlash(ctx) }));
}

function recordSighting(ctx, siteId) {
  if (!SITE_IDS.includes(siteId)) return redirect(ctx, "/sightings/new", { tone: "error", key: "error.site" });
  const outcome = reg.checkNewSighting(Object.fromEntries(ctx.form), { site: siteId, speciesIds: SPECIES_IDS, today: TODAY });
  if (outcome.problem) return redirect(ctx, `/sightings/new/${siteId}`, { tone: "error", key: outcome.problem });
  const sightings = ctx.session.data.sightings;
  const record = { id: reg.nextId(sightings), ...outcome.sighting };
  sightings.push(record);
  return redirect(ctx, "/feed", { tone: "notice", key: "flash.recorded", params: { ref: reg.refOf(record.id) }, sentFrom: siteId });
}

function showSighting(ctx, row) {
  html(ctx, views.sightingPage({ row: { ...rowFields(row), notes: row.notes }, flash: takeFlash(ctx) }));
}

function reviewSighting(ctx, row) {
  const outcome = reg.review(row, ctx.form.get("action") ?? "");
  const ref = reg.refOf(row.id);
  return redirect(ctx, `/sightings/${ref}`, { tone: outcome.changed ? "notice" : "error", key: outcome.message, params: { ref } });
}

function showSites(ctx) {
  takeFlash(ctx);
  const summaries = Object.fromEntries(SEED.sites.map((site) =>
    [site.id, reg.siteSummary(ctx.session.data.sightings, site.id, SEED.species)]));
  html(ctx, views.sitesPage({ summaries }));
}

function showSite(ctx, site) {
  takeFlash(ctx);
  html(ctx, views.sitePage({ site, summary: reg.siteSummary(ctx.session.data.sightings, site.id, SEED.species) }));
}

function showSummary(ctx) {
  takeFlash(ctx);
  const month = TODAY.slice(0, 7);
  html(ctx, views.summaryPage({ month, lines: reg.monthTotals(ctx.session.data.sightings, month, SEED.species) }));
}

function notFound(ctx) {
  takeFlash(ctx);
  html(ctx, views.messagePage("missing.title", "missing.body"), 404);
}

function route(ctx) {
  const { method, path } = ctx;
  if (method === "GET" && path === "/__wta/state") {
    if (!HOOKS) return notFound(ctx);
    const committed = committedState(ctx.session.data);
    const sha = createHash("sha256").update(sortedJson(committed)).digest("hex");
    return json(ctx, { session: ctx.session.sid, committed, sha });
  }
  if (method === "GET") {
    if (path === "/") return redirect(ctx, "/feed");
    if (path === "/feed") return showFeed(ctx);
    if (path === "/feed/more") return morePage(ctx);
    if (path === "/sightings/new") return showWhere(ctx);
    if (path === "/sites") return showSites(ctx);
    if (path === "/summary") return showSummary(ctx);
    if (path === "/help") {
      takeFlash(ctx);
      return html(ctx, views.helpPage());
    }
  }
  if (method === "POST" && path === "/sightings/new") return chooseWhere(ctx);
  let match = /^\/sightings\/new\/([a-z]+)$/.exec(path);
  if (match && method === "GET") return showWhat(ctx, match[1]);
  if (match && method === "POST") return recordSighting(ctx, match[1]);
  match = /^\/sightings\/(s\d+)(\/review)?$/.exec(path);
  const row = match ? findSighting(ctx, match[1]) : null;
  if (row && method === "GET" && !match[2]) return showSighting(ctx, row);
  if (row && method === "POST" && match[2]) return reviewSighting(ctx, row);
  match = /^\/sites\/([a-z]+)$/.exec(path);
  const site = match ? SEED.sites.find((s) => s.id === match[1]) : null;
  if (site && method === "GET") return showSite(ctx, site);
  return notFound(ctx);
}

// -- HTTP ----------------------------------------------------------------------------------

function readForm(req) {
  return new Promise((resolve, reject) => {
    const parts = [];
    let size = 0;
    req.on("data", (part) => {
      size += part.length;
      if (size > MAX_BODY) {
        reject(Object.assign(new Error("form too large"), { status: 413 }));
        req.destroy();
      } else {
        parts.push(part);
      }
    });
    req.on("end", () => resolve(new URLSearchParams(Buffer.concat(parts).toString("utf8"))));
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
  const form = req.method === "POST" ? await readForm(req) : new URLSearchParams();
  // From here on nothing awaits: a session's read, change and write happen in one turn of
  // the event loop, so two requests of one session never interleave.
  const ctx = { res, method: req.method, path: url.pathname, query: url.searchParams, form, session: loadSession(req) };
  route(ctx);
}

const server = createServer((req, res) => {
  handle(req, res).catch((error) => {
    const status = error.status ?? 500;
    if (status === 500) process.stderr.write(`${error.stack ?? error}\n`);
    if (!res.headersSent) {
      res.writeHead(status, { "Content-Type": "text/plain; charset=utf-8" });
      res.end(status === 413 ? "form too large" : "internal error");
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
  process.stdout.write(`${APP_ID} listening on ${port} (vocabulary ${PROFILE}, today ${TODAY}, data ${STORE})\n`);
});
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => server.close(() => process.exit(0)));
