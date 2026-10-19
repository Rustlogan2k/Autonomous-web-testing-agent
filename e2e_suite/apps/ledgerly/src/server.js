// Ledgerly — expense claims with multi-role approval. Node 22 built-ins plus the suite's
// vendored PostgreSQL driver.
//
// Server-rendered pages and plain HTML forms with post/redirect/get throughout; static/ui.js
// and static/claim-editor.js add client-side behaviour on top. Each signed-in browser has a
// schema of its own (db.js); every request runs alone in its session's lane, in one
// transaction, and a claim's state is always the fold of its events (claims.js).
//
// Signed out, a page answers 303 to /login and an API call 200 {ok: false, reason:
// "signed-out"}. A page someone may not open is an ordinary 200 page with an access notice.
// Nothing answers 401 or 403.

import crypto from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

import * as claims from "./claims.js";
import { Ledger, isSid, readSeed } from "./db.js";
import { authenticate, continueTo, forgotPage, signInPage } from "./gate.js";
import * as records from "./records.js";
import * as views from "./views.js";

const APP_ID = "ledgerly";
const SRC = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.dirname(SRC);
const SEED = readSeed(path.join(ROOT, "seed"));
const CFG = claims.config(SEED);
const SCHEMA_SQL = fs.readFileSync(path.join(SRC, "schema.sql"), "utf8");
const PROFILE = process.env.WTA_VOCAB === "alt" ? "alt" : "default";
Object.assign(views.VOCAB, JSON.parse(fs.readFileSync(path.join(ROOT, "vocab", `${PROFILE}.json`), "utf8")));
const TEST_HOOKS = process.env.WTA_TEST_HOOKS === "1";
const BODY_LIMIT = 256 * 1024;
const STATIC = {
  "claim-editor.js": "text/javascript; charset=utf-8",
  "money.js": "text/javascript; charset=utf-8",
  "ui.js": "text/javascript; charset=utf-8",
  "ledgerly.css": "text/css; charset=utf-8",
};
const CLAIM_PATH = /^\/claims\/(CL-\d{3,6})$/;
const EDIT_PATH = /^\/claims\/(CL-\d{3,6})\/edit$/;
const DISCARD_PATH = /^\/claims\/(CL-\d{3,6})\/discard$/;
const MERCHANT_PATH = /^\/merchants\/(M\d{3})(\/edit|\/delete)?$/;
const CENTRE_PATH = /^\/cost-centres\/(CC-\d{2})\/(rename|delete)$/;

function required(name, check, message) {
  const value = process.env[name];
  if (!value || !check(value)) {
    console.error(`ledgerly: ${message}`);
    process.exit(2);
  }
  return value;
}

const DATABASE_URL = required("DATABASE_URL", Boolean, "DATABASE_URL is required: it names the app's home database");
const SESSION_CAP = Number(process.env.WTA_SESSION_CAP ?? "32");
if (!Number.isInteger(SESSION_CAP) || SESSION_CAP < 1) {
  console.error("ledgerly: WTA_SESSION_CAP must be a positive whole number");
  process.exit(2);
}

/** The application's clock: WTA_FIXED_NOW as "YYYY-MM-DDTHH:MM:SS". There is no other. */
function clockNow() {
  const raw = process.env.WTA_FIXED_NOW || "2026-06-10T09:00:00";
  const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(:\d{2})?/.exec(raw);
  if (!match) throw new Error(`WTA_FIXED_NOW is not an ISO date-time: ${raw}`);
  return `${match[1]}T${match[2]}${match[3] ?? ":00"}`;
}

const NOW = clockNow();
let ledger = null; // connected in main()

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

function send(res, status, type, body, extra = {}) {
  const payload = Buffer.from(body ?? "", "utf8");
  res.writeHead(status, { "Cache-Control": "no-store", "Content-Length": String(payload.length),
                          ...(type ? { "Content-Type": type } : {}), ...extra });
  res.end(payload);
}

// What a route hands back; sent once its transaction has committed.
const html = (body) => ({ status: 200, type: "text/html; charset=utf-8", body });
const json = (value) => ({ status: 200, type: "application/json", body: JSON.stringify(value) });
const redirect = (location, headers = {}) => ({ status: 303, headers: { Location: location, ...headers } });

// -- who is asking --------------------------------------------------------------------------

/** The person signed in, whom they act as, and whom they may act as. */
function whoIs(row) {
  const principals = claims.principalsOf(CFG, row.user_id);
  const principal = principals.includes(row.acting_as) ? row.acting_as : (principals[0] ?? row.user_id);
  return { user: row.user_id, principal, principals };
}

const isAdmin = (who) => claims.roleOf(CFG, who.user) === "administrator";

function page(who, title, body, options) {
  return html(views.layout(CFG, who, title, body, options));
}

const notice = (who) => page(who, views.t("notice.title"), views.accessNotice());

/** Leave a message for the page at `path` (shown once there). */
function flashFor(ctx, path, fields) {
  return ledger.setFlash(ctx.row.sid, { path, ...fields });
}

/** The pending message, cleared on every page view; returned only on its own page. */
async function takeFlash(ctx) {
  const pending = ctx.row.flash;
  if (!pending) return null;
  await ledger.setFlash(ctx.row.sid, null);
  return pending.path === ctx.path ? pending : null;
}

// -- routes: claims ---------------------------------------------------------------------------

function home(ctx) {
  const role = claims.roleOf(CFG, ctx.who.principal);
  if (role === "manager") return redirect("/approvals");
  if (role === "finance") return redirect("/finance");
  return redirect("/claims");
}

async function switchViewer(ctx) {
  const viewer = ctx.form.get("viewer") ?? "";
  if (ctx.who.principals.includes(viewer)) await ledger.setActingAs(ctx.row.sid, viewer);
  return redirect("/");
}

async function claimsList(ctx) {
  if (claims.roleOf(CFG, ctx.who.principal) !== "employee") return notice(ctx.who);
  const flash = await takeFlash(ctx);
  const list = await records.loadClaims(ctx.client);
  return page(ctx.who, views.t("home.employee_title"), views.myClaims(CFG, ctx.who.principal, list, flash));
}

async function approvals(ctx) {
  if (claims.roleOf(CFG, ctx.who.principal) !== "manager") return notice(ctx.who);
  if (ctx.method === "POST") {
    const list = await records.loadClaims(ctx.client);
    const ids = ctx.form.getAll("ids");
    let approved = 0;
    let refused = 0;
    for (const id of ids) {
      const claim = claims.findClaim(list, id);
      const outcome = claim ? claims.decideCommand(CFG, ctx.who.principal, claim, "approve", "") : { refused: "x" };
      if (outcome.refused) {
        refused += 1;
      } else {
        await records.record(ctx.client, outcome.events, recording(ctx));
        approved += 1;
      }
    }
    await flashFor(ctx, "/approvals", ids.length
      ? { notice: "msg.bulk_done", detail: `${approved} ${views.t("label.approved")}, ${refused} ${views.t("label.not_approved")}` }
      : { error: "msg.pick_first" });
    return redirect("/approvals");
  }
  const flash = await takeFlash(ctx);
  return page(ctx.who, views.t("home.manager_title"), views.approvalsPage(CFG, await records.loadClaims(ctx.client), flash));
}

async function financeQueue(ctx) {
  if (claims.roleOf(CFG, ctx.who.principal) !== "finance") return notice(ctx.who);
  await takeFlash(ctx);
  return page(ctx.who, views.t("home.finance_title"), views.financePage(CFG, await records.loadClaims(ctx.client)));
}

/** What `record` needs: who clicked, when, and each owner's cost centre. */
function recording(ctx) {
  return { actor: ctx.who.user, at: NOW, costCentreOf: (person) => CFG.people[person]?.costCentre ?? "CC-01" };
}

/** Whether the person acted as may see this claim: its claimant, or a manager or finance. */
function maySee(who, claim) {
  const role = claims.roleOf(CFG, who.principal);
  return claim.owner === who.principal || role === "manager" || role === "finance";
}

async function showEditor(ctx, claim) {
  const pending = await takeFlash(ctx);
  if (claims.roleOf(CFG, ctx.who.principal) !== "employee") {
    return page(ctx.who, views.t("editor.new_title"), views.infoPage("editor.new_title", "msg.only_claimant"));
  }
  if (claim && (claim.owner !== ctx.who.principal || !claims.editable(claim))) return redirect(`/claims/${claim.id}`);
  const rows = pending?.rows?.length ? pending.rows : claim ? views.rowsFromLines(claim.lines) : [views.BLANK_ROW];
  const title = claim ? `${views.t("editor.edit_title")} ${claim.id}` : views.t("editor.new_title");
  return page(ctx.who, title, views.editorPage(CFG, claim, rows, pending), { island: true });
}

async function postEditor(ctx, claim) {
  const action = ctx.form.get("action") ?? "save";
  const rows = claims.readRows(ctx.form);
  const back = claim ? `/claims/${claim.id}/edit` : "/claims/new";
  if (action === "template") {
    // Templates are announced but not built: keep what was typed, store nothing.
    await flashFor(ctx, back, { notice: "msg.template_soon", rows });
    return redirect(back);
  }
  const nextId = `CL-${await records.peekNumber(ctx.client, "claim")}`;
  const outcome = claims.editCommand(CFG, ctx.who.principal, claim, nextId, action, rows);
  if (outcome.refused) {
    const target = claim ? `/claims/${claim.id}` : "/claims/new";
    await flashFor(ctx, target, { error: outcome.refused });
    return redirect(target);
  }
  if (outcome.invalid) {
    await flashFor(ctx, back, { errors: outcome.invalid, rows });
    return redirect(back);
  }
  if (outcome.created) await records.takeNumber(ctx.client, "claim");
  await records.record(ctx.client, outcome.events, recording(ctx));
  const target = `/claims/${outcome.claim}`;
  await flashFor(ctx, target, { notice: outcome.notice });
  return redirect(target);
}

async function showClaim(ctx, claim) {
  if (!maySee(ctx.who, claim)) return notice(ctx.who);
  const flash = await takeFlash(ctx);
  return page(ctx.who, `${views.t("claim.title")} ${claim.id}`, views.claimPage(CFG, ctx.who.principal, claim, flash));
}

async function postDecision(ctx, claim) {
  const target = `/claims/${claim.id}`;
  const outcome = claims.decideCommand(CFG, ctx.who.principal, claim, ctx.form.get("action") ?? "", ctx.form.get("note"));
  if (outcome.refused) {
    await flashFor(ctx, target, { error: outcome.refused });
  } else {
    await records.record(ctx.client, outcome.events, recording(ctx));
    await flashFor(ctx, target, { notice: outcome.notice });
  }
  return redirect(target);
}

async function discard(ctx, claim) {
  const outcome = claims.discardCommand(CFG, ctx.who.principal, claim);
  if (outcome.refused) {
    await flashFor(ctx, `/claims/${claim.id}`, { error: outcome.refused });
    return redirect(`/claims/${claim.id}`);
  }
  await records.discardClaim(ctx.client, claim.id);
  await flashFor(ctx, "/claims", { notice: outcome.notice, detail: claim.id });
  return redirect("/claims");
}

// -- routes: merchants and cost centres -------------------------------------------------------

async function merchants(ctx) {
  if (ctx.method === "POST") return newMerchant(ctx);
  const query = records.merchantQuery(ctx.url.searchParams, CFG.categories);
  const result = await records.listMerchants(ctx.client, query);
  const flash = await takeFlash(ctx);
  return page(ctx.who, views.t("merchants.title"),
              views.merchantsPage(CFG, { ...query, page: result.page }, result, isAdmin(ctx.who), flash));
}

async function newMerchant(ctx) {
  if (!isAdmin(ctx.who)) return notice(ctx.who);
  if (ctx.method === "GET") {
    await takeFlash(ctx);
    return page(ctx.who, views.t("merchants.new_title"),
                views.merchantForm(CFG, null, { name: "", city: "", category: CFG.categories[0], preferred: false }, []));
  }
  const { values, problems } = records.merchantValues(ctx.form, CFG.categories);
  if (!problems.length) {
    const id = `M${String(await records.peekNumber(ctx.client, "merchant")).padStart(3, "0")}`;
    const clash = await records.saveMerchant(ctx.client, id, values, true);
    if (!clash) {
      await records.takeNumber(ctx.client, "merchant");
      await flashFor(ctx, `/merchants/${id}`, { notice: "msg.merchant_saved" });
      return redirect(`/merchants/${id}`);
    }
    problems.push(clash);
  }
  return page(ctx.who, views.t("merchants.new_title"), views.merchantForm(CFG, null, values, problems));
}

async function merchant(ctx, id, part) {
  const found = await records.findMerchant(ctx.client, id);
  if (!found) return null;
  if (!part && ctx.method === "GET") {
    const flash = await takeFlash(ctx);
    return page(ctx.who, found.name, views.merchantPage(CFG, found, isAdmin(ctx.who), flash));
  }
  if (!isAdmin(ctx.who)) return notice(ctx.who);
  if (part === "/delete" && ctx.method === "POST") {
    await records.deleteMerchant(ctx.client, id);
    await flashFor(ctx, "/merchants", { notice: "msg.merchant_deleted", detail: found.name });
    return redirect("/merchants");
  }
  if (part === "/edit" && ctx.method === "GET") {
    await takeFlash(ctx);
    return page(ctx.who, found.name, views.merchantForm(CFG, found, found, []));
  }
  if (part === "/edit" && ctx.method === "POST") {
    const { values, problems } = records.merchantValues(ctx.form, CFG.categories);
    if (!problems.length) {
      const clash = await records.saveMerchant(ctx.client, id, values, false);
      if (!clash) {
        await flashFor(ctx, `/merchants/${id}`, { notice: "msg.merchant_saved" });
        return redirect(`/merchants/${id}`);
      }
      problems.push(clash);
    }
    return page(ctx.who, found.name, views.merchantForm(CFG, found, values, problems));
  }
  return null;
}

/** Approved spending in pence by cost centre and by category, from the claims as they stand. */
function approvedSpend(list) {
  const byCentre = new Map();
  const byCategory = new Map();
  for (const claim of list.filter((c) => c.status === "approved")) {
    for (const line of claim.lines) {
      const pence = claims.claimTotalPence([line], CFG.rates);
      byCentre.set(claim.costCentre, (byCentre.get(claim.costCentre) ?? 0) + pence);
      byCategory.set(line.category, (byCategory.get(line.category) ?? 0) + pence);
    }
  }
  return { byCentre, byCategory };
}

async function costCentres(ctx) {
  if (ctx.method === "POST") {
    if (!isAdmin(ctx.who)) return notice(ctx.who);
    const name = String(ctx.form.get("name") ?? "").trim();
    const budget = /^\d{1,7}(\.\d{1,2})?$/.test(String(ctx.form.get("budget") ?? "").trim())
      ? Math.round(Number(ctx.form.get("budget")) * 100) : null;
    const problem = (await records.centreNameProblem(ctx.client, name, "")) || (budget === null ? "msg.budget_invalid" : "");
    if (problem) {
      await flashFor(ctx, "/cost-centres", { error: problem });
    } else {
      const code = await records.addCentre(ctx.client, name, budget);
      await flashFor(ctx, "/cost-centres", { notice: "msg.centre_added", detail: code });
    }
    return redirect("/cost-centres");
  }
  const flash = await takeFlash(ctx);
  const { byCentre } = approvedSpend(await records.loadClaims(ctx.client));
  return page(ctx.who, views.t("centres.title"),
              views.costCentresPage(CFG, await records.listCentres(ctx.client), byCentre, isAdmin(ctx.who), flash));
}

/** Click-to-rename (JSON, from static/ui.js) and delete (a form, from the dialog). */
async function costCentre(ctx, code, action) {
  const exists = (await records.listCentres(ctx.client)).some((centre) => centre.code === code);
  if (action === "rename") {
    if (!isAdmin(ctx.who)) return json({ ok: false, reason: "forbidden" });
    if (!exists) return json({ ok: false, error: views.t("msg.centre_gone") });
    let body = {};
    try { body = JSON.parse(ctx.text || "{}"); } catch { return { status: 400, type: "text/plain", body: "not JSON" }; }
    const name = String(body.name ?? "").trim();
    const problem = await records.centreNameProblem(ctx.client, name, code);
    if (problem) return json({ ok: false, error: views.t(problem) });
    await records.renameCentre(ctx.client, code, name);
    return json({ ok: true, name });
  }
  if (!isAdmin(ctx.who)) return notice(ctx.who);
  if (!exists) return redirect("/cost-centres");
  const removed = await records.deleteCentre(ctx.client, code);
  await flashFor(ctx, "/cost-centres", removed ? { notice: "msg.centre_deleted", detail: code } : { error: "msg.centre_in_use" });
  return redirect("/cost-centres");
}

async function reports(ctx) {
  await takeFlash(ctx);
  const { byCentre, byCategory } = approvedSpend(await records.loadClaims(ctx.client));
  const centres = (await records.listCentres(ctx.client)).map((centre) => ({
    name: centre.name, budget: centre.budget_pence, spent: byCentre.get(centre.code) ?? 0,
  }));
  return page(ctx.who, views.t("reports.title"), views.reportsPage(CFG, byCategory, centres));
}

/** The signed-in routes. Each runs inside the session's schema and transaction. */
async function signedIn(ctx) {
  const { method, path: where } = ctx;
  if (where === "/" && method === "GET") return home(ctx);
  if (where === "/viewer" && method === "POST") return switchViewer(ctx);
  if (where === "/claims" && method === "GET") return claimsList(ctx);
  if (where === "/approvals") return approvals(ctx);
  if (where === "/finance" && method === "GET") return financeQueue(ctx);
  if (where === "/merchants") return merchants(ctx);
  if (where === "/merchants/new") return newMerchant(ctx);
  if (where === "/cost-centres") return costCentres(ctx);
  if (where === "/reports" && method === "GET") return reports(ctx);
  if (where === "/claims/new") return method === "POST" ? postEditor(ctx, null) : showEditor(ctx, null);
  let match = MERCHANT_PATH.exec(where);
  if (match) return merchant(ctx, match[1], match[2]);
  match = CENTRE_PATH.exec(where);
  if (match && method === "POST") return costCentre(ctx, match[1], match[2]);
  for (const [pattern, get, post] of [[CLAIM_PATH, showClaim, postDecision], [EDIT_PATH, showEditor, postEditor],
                                      [DISCARD_PATH, null, discard]]) {
    match = pattern.exec(where);
    if (!match) continue;
    const claim = claims.findClaim(await records.loadClaims(ctx.client), match[1]);
    if (!claim) return null;
    const handler = method === "POST" ? post : get;
    return handler ? handler(ctx, claim) : null;
  }
  return null;
}

// -- the server -----------------------------------------------------------------------------

function serveStatic(res, name) {
  const type = STATIC[name];
  if (!type) return send(res, 404, "text/plain", "not found");
  return send(res, 200, type, fs.readFileSync(path.join(SRC, "static", name), "utf8"));
}

async function testState(row) {
  if (!row) return json({ session: null, user: null, committed: null, sha: null });
  return ledger.within(row.partition, async (client) => {
    const committed = await records.committed(client, await records.loadClaims(client));
    const sha = crypto.createHash("sha256").update(canonical(committed)).digest("hex");
    return json({ session: row.sid, user: row.user_id ? SEED.users.find((u) => u.id === row.user_id).username : null,
                  committed, sha });
  });
}

/** One request of one browser (in that browser's lane when it has a session cookie). */
async function respond(req, url, sid, text) {
  const row = await ledger.session(sid);
  const where = url.pathname;
  const method = req.method;
  const form = new URLSearchParams(text);
  if (where === "/login" && method === "POST") {
    const next = continueTo(form.get("next"));
    const account = await authenticate(SEED.users, form.get("username"), form.get("password"));
    if (!account) return html(signInPage({ next, refused: true }));
    const principals = claims.principalsOf(CFG, account.id);
    const session = await ledger.signIn(sid, account.id, principals[0] ?? account.id);
    return redirect(next, session.created ? { "Set-Cookie": `wta_sid=${session.sid}; Path=/; HttpOnly; SameSite=Lax` } : {});
  }
  if (where === "/login" && method === "GET") {
    const next = continueTo(url.searchParams.get("next"));
    return row?.user_id ? redirect(next) : html(signInPage({ next }));
  }
  if (where === "/login/forgot" && method === "GET") return html(forgotPage());
  if (where === "/logout" && method === "POST") {
    if (row) await ledger.signOut(row.sid); // the schema stays: signing in again resumes it
    return redirect("/login");
  }
  if (where === "/__wta/state" && method === "GET" && TEST_HOOKS) return testState(row);
  if (!row?.user_id) {
    if (where.startsWith("/cost-centres/") && method === "POST") return json({ ok: false, reason: "signed-out" });
    return method === "GET" ? redirect(`/login?next=${encodeURIComponent(where)}`) : redirect("/login");
  }
  const who = whoIs(row);
  return ledger.within(row.partition, async (client) => {
    const ctx = { row, who, client, method, url, path: where, form, text };
    return (await signedIn(ctx)) ?? { ...page(who, views.t("missing.title"), views.infoPage("missing.title", "missing.body")), status: 404 };
  });
}

async function handle(req, res) {
  const url = new URL(req.url ?? "/", "http://127.0.0.1");
  if (url.pathname === "/healthz") {
    const ok = await ledger.healthy();
    return send(res, ok ? 200 : 503, "application/json", JSON.stringify({ ok, app: APP_ID }));
  }
  if (url.pathname.startsWith("/static/")) return serveStatic(res, url.pathname.slice("/static/".length));
  const text = req.method === "POST" ? await readBody(req) : "";
  const sid = parseCookies(req.headers.cookie).wta_sid;
  const answer = isSid(sid) ? await ledger.lane(sid, () => respond(req, url, sid, text)) : await respond(req, url, "", text);
  return send(res, answer.status, answer.type, answer.body, answer.headers);
}

async function main() {
  ledger = await Ledger.open(DATABASE_URL, { schemaSql: SCHEMA_SQL, seed: SEED, cap: SESSION_CAP });
  const server = http.createServer((req, res) => {
    handle(req, res).catch((error) => {
      const status = error.status ?? 500;
      if (status === 500) console.error(error);
      if (!res.headersSent) send(res, status, "text/plain; charset=utf-8", status === 413 ? "request too large" : "internal error");
      else res.end();
    });
  });
  const port = Number(process.env.PORT);
  if (!Number.isInteger(port) || port <= 0) {
    console.error("PORT must be set to the port to listen on");
    process.exit(2);
  }
  server.listen(port, process.env.HOST || "127.0.0.1", () => {
    console.log(`${APP_ID} listening on port ${port} (vocabulary ${PROFILE})`);
  });
  for (const signal of ["SIGINT", "SIGTERM"]) {
    process.on(signal, () => server.close(() => ledger.close().finally(() => process.exit(0))));
  }
}

main().catch((error) => {
  console.error(`ledgerly: cannot start: ${error.stack ?? error}`);
  process.exit(1);
});
