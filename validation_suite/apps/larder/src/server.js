// Larder: a personal recipe box. Node 22 built-ins only.
//
// One HTML shell is served for every app path (/recipes, /recipes/<id>, /recipes/<id>/method,
// /list, /settings, /conversions); the browser app (static/app.js) routes with the History
// API. The shell carries the session's kitchen as JSON, so the first render and every later
// route change need no request. Each change (a servings step, the list, a settings group) is
// one JSON request to /api/...; the session's kitchen lives in one JSON file per session.

import { createHash } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import * as kitchen from "./kitchen.js";

const APP_ID = "larder";
const HERE = dirname(fileURLToPath(import.meta.url));
const APP_ROOT = dirname(HERE);
const SEED = JSON.parse(readFileSync(join(APP_ROOT, "seed", "larder.json"), "utf8"));
const PROFILE = process.env.WTA_VOCAB === "alt" ? "alt" : "default";
const VOCAB = JSON.parse(readFileSync(join(APP_ROOT, "vocab", `${PROFILE}.json`), "utf8"));
const HOOKS = process.env.WTA_TEST_HOOKS === "1";
const STORE = process.env.WTA_DATA_DIR || mkdtempSync(join(tmpdir(), "larder-"));
const MAX_BODY = 16 * 1024;
const DEFAULT_SETTINGS = Object.freeze({ ...SEED.settings });
const RECIPES = new Map(SEED.recipes.map((recipe) => [recipe.id, recipe]));
const SHELL = readFileSync(join(HERE, "static", "shell.html"), "utf8");
const ASSETS = {
  "app.js": "text/javascript; charset=utf-8",
  "units.js": "text/javascript; charset=utf-8",
  "larder.css": "text/css; charset=utf-8",
};
/** Paths the browser app knows; anything else gets the shell's not-found view and a 404. */
const APP_PATHS = [/^\/recipes$/, /^\/recipes\/(r\d+)(\/method)?$/, /^\/list$/, /^\/settings$/, /^\/conversions$/];

/** The app keeps no dates, but the clock contract is honoured: "now" is WTA_FIXED_NOW. */
const NOW = process.env.WTA_FIXED_NOW || "2026-06-10T09:00:00"; // wta-clock

function t(key, params = {}) {
  const text = Object.prototype.hasOwnProperty.call(VOCAB, key) ? VOCAB[key] : key;
  return Object.entries(params).reduce((out, [name, value]) => out.split(`{${name}}`).join(String(value)), text);
}

const say = (message) => (message ? t(...message) : "");

// -- sessions: one JSON file each ----------------------------------------------------------

const SESSION_DIR = join(STORE, "sessions");
mkdirSync(SESSION_DIR, { recursive: true });
const SID_SHAPE = /^k(\d{6})$/;
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
  const sid = `k${String(lastSession).padStart(6, "0")}`;
  const data = kitchen.freshKitchen({ ...SEED, settings: DEFAULT_SETTINGS });
  writeFileSync(sessionPath(sid), JSON.stringify(data), { encoding: "utf8", flag: "wx" });
  return { sid, data, isNew: true };
}

function storeSession(session) {
  writeFileSync(sessionPath(session.sid), JSON.stringify(session.data), "utf8");
}

/** Everything the session has committed: servings chosen, the list, the kitchen settings. */
function committedState(data) {
  return { servings: data.servings, list: data.list, settings: data.settings };
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

const json = (ctx, value, status = 200) => reply(ctx, status, "application/json; charset=utf-8", JSON.stringify(value));

/** JSON that is safe inside <script type="application/json">. */
const embeddable = (value) => JSON.stringify(value).replace(/</g, "\\u003c").replace(/>/g, "\\u003e").replace(/&/g, "\\u0026");

const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (ch) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[ch]);

/** The shell: the same page for every app path, carrying this session's kitchen. */
function shell(ctx, status) {
  const boot = {
    vocab: VOCAB,
    recipes: SEED.recipes,
    range: SEED.servings_range,
    servings: ctx.session.data.servings,
    list: ctx.session.data.list,
    settings: ctx.session.data.settings,
  };
  const page = SHELL
    .replace("__TITLE__", () => escapeHtml(t("app.name")))
    .replace("__NOSCRIPT__", () => escapeHtml(t("app.noscript")))
    .replace("__BOOT__", () => embeddable(boot));
  reply(ctx, status, "text/html; charset=utf-8", page);
}

// -- API -----------------------------------------------------------------------------------

function changeServings(ctx, recipe, body) {
  const outcome = kitchen.stepServings(ctx.session.data, recipe, Number(body.change), SEED.servings_range);
  if (outcome.ok) storeSession(ctx.session);
  return json(ctx, { ok: outcome.ok, servings: outcome.servings, message: say(outcome.message) });
}

function addRecipe(ctx, body) {
  const recipe = RECIPES.get(String(body.recipe ?? ""));
  if (!recipe) return json(ctx, { ok: false, list: ctx.session.data.list, message: t("notice.recipe_missing") });
  const outcome = kitchen.addToList(ctx.session.data, recipe);
  if (outcome.ok) storeSession(ctx.session);
  return json(ctx, { ok: outcome.ok, list: ctx.session.data.list, message: say(outcome.message) });
}

function listChange(ctx, outcome) {
  if (outcome.ok) storeSession(ctx.session);
  return json(ctx, { ok: outcome.ok, list: ctx.session.data.list, message: say(outcome.message) });
}

/** A settings group is saved on its own; the answer is only whether it was accepted. */
function saveSettings(ctx, body) {
  const patch = kitchen.settingsPatch(body);
  if (!patch) return json(ctx, { ok: false, message: t("notice.settings_invalid") });
  const session = ctx.session.data;
  session.settings = { ...session.settings, ...patch };
  storeSession(ctx.session);
  return json(ctx, { ok: true });
}

function api(ctx, body) {
  const { method, path } = ctx;
  let match = /^\/api\/recipes\/(r\d+)\/servings$/.exec(path);
  if (match && method === "POST") {
    const recipe = RECIPES.get(match[1]);
    if (!recipe) return json(ctx, { ok: false, message: t("notice.recipe_missing") });
    return changeServings(ctx, recipe, body);
  }
  if (path === "/api/list" && method === "POST") return addRecipe(ctx, body);
  match = /^\/api\/list\/items\/(i\d+)\/tick$/.exec(path);
  if (match && method === "POST") return listChange(ctx, kitchen.toggleItem(ctx.session.data, match[1]));
  if (path === "/api/list/clear-ticked" && method === "POST") return listChange(ctx, kitchen.clearTicked(ctx.session.data));
  if (path === "/api/settings" && method === "PATCH") return saveSettings(ctx, body);
  return json(ctx, { ok: false, message: t("missing.body") }, 404);
}

// -- routes --------------------------------------------------------------------------------

function route(ctx, body) {
  const { method, path } = ctx;
  if (path.startsWith("/api/")) return api(ctx, body);
  if (method === "GET" && path === "/__wta/state") {
    if (!HOOKS) return shell(ctx, 404);
    const committed = committedState(ctx.session.data);
    const sha = createHash("sha256").update(sortedJson(committed)).digest("hex");
    return json(ctx, { session: ctx.session.sid, committed, sha });
  }
  if (method !== "GET") return json(ctx, { ok: false, message: t("missing.body") }, 405);
  if (path === "/") return reply(ctx, 303, null, "", { Location: "/recipes" });
  const known = APP_PATHS.some((pattern) => {
    const match = pattern.exec(path);
    return match && (!match[1] || RECIPES.has(match[1]));
  });
  return shell(ctx, known ? 200 : 404);
}

// -- HTTP ----------------------------------------------------------------------------------

function readJson(req) {
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
      if (!text) return resolve({});
      try {
        const value = JSON.parse(text);
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
  const body = req.method === "GET" || req.method === "HEAD" ? {} : await readJson(req);
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
