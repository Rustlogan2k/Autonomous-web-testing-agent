// Ledgerly's HTTP behaviour against the clean build (E2E_BASE_URL, started with WTA_TEST_HOOKS=1
// and its PostgreSQL home), node:test only. Every client signs in through the form first.
// Run: E2E_BASE_URL=http://127.0.0.1:8080/ node --test "tests/**/*.test.mjs"

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, test } from "node:test";

const BASE = (process.env.E2E_BASE_URL || "http://127.0.0.1:8080/").replace(/\/$/, "");
const PASSWORD = JSON.parse(readFileSync(new URL("../app.json", import.meta.url), "utf8")).login.password_value;
const CAP = Number(process.env.WTA_SESSION_CAP || 32);

/** One browser-like session: keeps the wta_sid cookie, follows redirects itself. */
class Client {
  constructor() {
    this.cookie = "";
  }

  async request(method, path, form, { json } = {}) {
    const headers = this.cookie ? { Cookie: this.cookie } : {};
    let body;
    if (form) {
      body = new URLSearchParams(form).toString();
      headers["Content-Type"] = "application/x-www-form-urlencoded";
    } else if (json) {
      body = JSON.stringify(json);
      headers["Content-Type"] = "application/json";
    }
    const response = await fetch(BASE + path, { method, headers, body, redirect: "manual" });
    const set = response.headers.get("set-cookie");
    if (set) this.cookie = set.split(";")[0];
    return { status: response.status, location: response.headers.get("location") ?? "", text: await response.text(),
             setCookie: Boolean(set) };
  }

  /** GET `path` and every redirect after it; the last answer is where it lands. */
  async get(path) {
    let reply = await this.request("GET", path);
    let target = path;
    for (let hops = 0; reply.status === 303 && hops < 5; hops += 1) {
      target = new URL(reply.location, BASE + "/").pathname;
      reply = await this.request("GET", target);
    }
    return { ...reply, target };
  }

  /** POST a form, then follow where it redirects (post/redirect/get, as a browser does). */
  async submit(path, form) {
    const posted = await this.request("POST", path, form);
    assert.equal(posted.status, 303, `POST ${path} should redirect`);
    const { target, ...page } = await this.get(new URL(posted.location, BASE + "/").pathname);
    return { target, page };
  }

  signIn(username = "pat.reid", password = PASSWORD) {
    return this.request("POST", "/login", { username, password });
  }

  async state() {
    return JSON.parse((await this.request("GET", "/__wta/state")).text);
  }

  async claim(id) {
    return (await this.state()).committed.claims.find((c) => c.id === id);
  }

  viewAs(viewer) {
    return this.submit("/viewer", { viewer });
  }

  lodge(lines, action = "lodge") {
    const form = { action };
    lines.forEach(([description, amount, currency, category = "other"], index) => {
      const n = index + 1;
      Object.assign(form, { [`description_${n}`]: description, [`amount_${n}`]: amount,
                            [`currency_${n}`]: currency, [`category_${n}`]: category });
    });
    return this.submit("/claims/new", form);
  }

  decide(id, action, note = "") {
    return this.submit(`/claims/${id}`, { action, note });
  }
}

async function signedIn(username = "pat.reid") {
  const client = new Client();
  const reply = await client.signIn(username);
  assert.equal(reply.status, 303, `${username} signs in`);
  return client;
}

function region(html, id) {
  const match = new RegExp(`<([a-z]+)[^>]*\\bid="${id}"[^>]*>([\\s\\S]*?)</\\1>`).exec(html);
  return match ? match[2].replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim() : null;
}

describe("signing in", () => {
  test("health, and everything but the sign-in pages sits behind /login", async () => {
    assert.deepEqual(await (await fetch(`${BASE}/healthz`)).json(), { ok: true, app: "ledgerly" });
    const anonymous = new Client();
    for (const path of ["/", "/claims", "/merchants", "/claims/CL-101"]) {
      const reply = await anonymous.request("GET", path);
      assert.equal(reply.status, 303, path);
      assert.equal(reply.location, `/login?next=${encodeURIComponent(path)}`);
      assert.equal(reply.setCookie, false, "an anonymous request creates nothing");
    }
    assert.equal((await anonymous.request("GET", "/login/forgot")).status, 200);
    const rename = await anonymous.request("POST", "/cost-centres/CC-02/rename", null, { json: { name: "X" } });
    assert.deepEqual(JSON.parse(rename.text), { ok: false, reason: "signed-out" });
    assert.deepEqual(await anonymous.state(), { session: null, user: null, committed: null, sha: null });
  });

  test("a wrong password is refused with a message and no session", async () => {
    for (const [username, password] of [["pat.reid", "wrong"], ["Test User Input", PASSWORD], ["pat.reid", ""]]) {
      const client = new Client();
      const reply = await client.signIn(username, password);
      assert.equal(reply.status, 200);
      assert.equal(reply.setCookie, false);
      assert.ok(region(reply.text, "login-error"), "the refusal says why");
    }
  });

  test("Pat signs in acting for Ana; next stays on this site; a signed-in /login moves on", async () => {
    const client = new Client();
    const reply = await client.request("POST", "/login", { username: "pat.reid", password: PASSWORD, next: "/reports" });
    assert.equal(reply.location, "/reports");
    assert.match(client.cookie, /^wta_sid=ldg-\d{6}$/);
    const home = await client.get("/");
    assert.equal(home.target, "/claims");
    assert.equal(region(home.text, "signed-in-as"), "Pat Reid · expenses administrator");
    assert.match(home.text, /<option value="ana" selected>/);
    assert.equal((await client.request("GET", "/login")).status, 303);
    for (const next of ["//evil.example", "https://evil.example/", "/login"]) {
      const other = new Client();
      assert.equal((await other.request("POST", "/login", { username: "pat.reid", password: PASSWORD, next })).location, "/", next);
    }
  });

  test("signing out keeps the data; signing in again in the same browser resumes it", async () => {
    const client = await signedIn();
    await client.lodge([["Lunch", "42", "GBP"]]);
    const before = await client.state();
    const out = await client.request("POST", "/logout");
    assert.equal(out.location, "/login");
    assert.equal((await client.state()).user, null);
    assert.equal((await client.request("GET", "/claims")).status, 303);
    const back = await client.signIn();
    assert.equal(back.setCookie, false);
    const after = await client.state();
    assert.equal(after.session, before.session);
    assert.equal(after.sha, before.sha);
  });
});

describe("claims", () => {
  test("a new session starts from the seed, and every user's seed is the same", async () => {
    const a = await (await signedIn()).state();
    const b = await (await signedIn("raj.patel")).state();
    assert.notEqual(a.session, b.session);
    assert.equal(a.sha, b.sha);
    assert.deepEqual(a.committed.claims.map((c) => [c.id, c.status]),
                     [["CL-101", "approved"], ["CL-102", "awaiting"], ["CL-103", "awaiting"]]);
    assert.ok(!/salt|hash|ldg-|s_\d{6}/.test(JSON.stringify(a.committed)), "no credentials or partition names");
  });

  test("foreign lines are converted at the fixed rates and totalled in sterling", async () => {
    const pat = await signedIn();
    const { target, page } = await pat.lodge([["Lunch", "42", "GBP"], ["Hotel", "42", "EUR"], ["Cab", "42", "USD"]]);
    assert.equal(target, "/claims/CL-104");
    assert.equal(region(page.text, "claim-total"), "£111.30");
    const claim = await pat.claim("CL-104");
    assert.equal(claim.status, "awaiting");
    assert.equal(claim.owner, "ana");
    assert.deepEqual(claim.history.map((h) => h.slice(0, 3)), [["claim_drafted", "ana", "pat"], ["lodged", "ana", "pat"]],
                     "recorded as Ana, via Pat");
    assert.match(page.text, /Lodged — Ana Ruiz via Pat Reid/);
  });

  test("the editor's server-rendered summary converts the same way", async () => {
    const pat = await signedIn();
    const { page } = await pat.submit("/claims/new", {
      action: "template", description_1: "Dinner", amount_1: "42", currency_1: "EUR", category_1: "meals",
      description_2: "Taxi", amount_2: "42", currency_2: "USD", category_2: "travel",
    });
    assert.equal(region(page.text, "sum-eur"), "€42.00");
    assert.equal(region(page.text, "sum-usd"), "$42.00");
    assert.equal(region(page.text, "sum-total"), "£69.30");
  });

  test("a manager approves up to £50 and is refused above it", async () => {
    const client = await signedIn();
    const queue = await client.viewAs("ben");
    assert.equal(queue.target, "/approvals");
    const small = await client.decide("CL-102", "approve");
    const claim = await client.claim("CL-102");
    assert.deepEqual([claim.status, claim.approved_by, claim.approved_via], ["approved", "ben", "pat"]);
    assert.equal(region(small.page.text, "claim-approver"), "Ben Okafor (manager)", "via Pat Reid is outside it");
    const before = (await client.state()).sha;
    const large = await client.decide("CL-103", "approve");
    assert.equal((await client.state()).sha, before, "a refused approval changes nothing");
    assert.match(region(large.page.text, "claim-error"), /over your approval limit/);
  });

  test("the manager's limit counts converted lines: £42 + €42 is £78.12, over the limit", async () => {
    const client = await signedIn();
    await client.lodge([["Lunch", "42", "GBP"], ["Hotel", "42", "EUR"]]);
    await client.viewAs("ben");
    const { page } = await client.decide("CL-104", "approve");
    assert.equal((await client.claim("CL-104")).status, "awaiting");
    assert.equal(region(page.text, "claim-approver"), "Not approved yet");
  });

  test("escalating sends a claim to finance, who approve or reject it", async () => {
    const client = await signedIn();
    await client.viewAs("ben");
    await client.decide("CL-103", "escalate");
    assert.equal((await client.claim("CL-103")).status, "with_finance");
    const queue = await client.viewAs("finance");
    assert.equal(queue.target, "/finance");
    assert.match(region(queue.page.text, "finance-queue"), /CL-103/);
    await client.decide("CL-103", "approve");
    assert.equal((await client.claim("CL-103")).approved_by, "finance");
    const other = await signedIn();
    await other.viewAs("ben");
    await other.decide("CL-103", "escalate");
    await other.viewAs("finance");
    await other.decide("CL-103", "reject");
    assert.equal((await other.claim("CL-103")).status, "with_finance", "a rejection needs a reason");
    await other.decide("CL-103", "reject", "Not a business expense");
    assert.equal((await other.claim("CL-103")).status, "rejected");
  });

  test("send back needs a note; the claimant edits and resubmits, and it returns to the manager", async () => {
    const client = await signedIn();
    await client.viewAs("ben");
    await client.decide("CL-102", "send_back");
    assert.equal((await client.claim("CL-102")).status, "awaiting", "no note, no send back");
    const sent = await client.decide("CL-102", "send_back", "Add the receipt number");
    assert.equal((await client.claim("CL-102")).status, "sent_back");
    assert.match(region(sent.page.text, "claim-latest"), /Add the receipt number/);
    await client.viewAs("ana");
    assert.match((await client.get("/claims/CL-102/edit")).text, /id="resubmit-claim"/);
    const { page } = await client.submit("/claims/CL-102/edit", {
      action: "resubmit", description_1: "Taxi from the station (receipt 4471)", amount_1: "18.40",
      currency_1: "GBP", category_1: "travel" });
    const claim = await client.claim("CL-102");
    assert.equal(claim.status, "awaiting");
    assert.equal(claim.lines[0].description, "Taxi from the station (receipt 4471)");
    assert.match(region(page.text, "claim-latest"), /^Resubmitted/);
    assert.match(region((await client.viewAs("ben")).page.text, "approval-queue"), /CL-102/);
  });

  test("validation: an empty amount, a missing description and an oversized amount are refused", async () => {
    const client = await signedIn();
    const before = (await client.state()).sha;
    const empty = await client.lodge([["Lunch", "", "GBP"]]);
    assert.equal(empty.target, "/claims/new");
    assert.match(region(empty.page.text, "editor-errors"), /Line 1: enter an amount/);
    assert.match(empty.page.text, /value="Lunch"/, "what was typed is kept");
    await client.lodge([["", "42", "GBP"]]);
    await client.lodge([["Laptop", "999999999", "GBP"]]);
    await client.lodge([["Lunch", "4.505", "GBP"]]);
    assert.equal((await client.state()).sha, before);
    assert.equal((await client.lodge([["Lunch", "42", "GBP"]])).target, "/claims/CL-104");
  });

  test("drafts are saved, edited and lodged later, or discarded; templates save nothing", async () => {
    const client = await signedIn();
    const before = (await client.state()).sha;
    await client.lodge([["Lunch", "42", "GBP"]], "template");
    assert.equal((await client.state()).sha, before);
    assert.equal((await client.lodge([["Lunch", "42", "GBP"]], "save")).target, "/claims/CL-104");
    await client.submit("/claims/CL-104/edit", { action: "lodge", description_1: "Lunch", amount_1: "21",
                                                 currency_1: "EUR", category_1: "meals" });
    assert.deepEqual((await client.claim("CL-104")).lines.map((l) => [l.currency, l.minor]), [["EUR", 2100]]);
    await client.lodge([["Coffee", "3", "GBP"]], "save");
    const discarded = await client.submit("/claims/CL-105/discard", {});
    assert.equal(discarded.target, "/claims");
    assert.equal(await client.claim("CL-105"), undefined);
    assert.equal((await client.lodge([["Tea", "2", "GBP"]], "save")).target, "/claims/CL-106", "numbers are never reused");
    const lodged = await client.submit("/claims/CL-104/discard", {});
    assert.match(region(lodged.page.text, "claim-error"), /never lodged/);
  });

  test("the manager approves several claims at once, each within the limit", async () => {
    const client = await signedIn();
    await client.viewAs("ben");
    const { page } = await client.submit("/approvals", { action: "approve", ids: "CL-102" });
    assert.match(region(page.text, "queue-notice"), /1 approved, 0 not approved/);
    const form = new URLSearchParams([["action", "approve"], ["ids", "CL-103"], ["ids", "CL-999"]]);
    const posted = await client.request("POST", "/approvals", Object.fromEntries(form));
    assert.equal(posted.status, 303);
    assert.equal((await client.claim("CL-103")).status, "awaiting", "over the limit stays waiting");
  });
});

describe("roles", () => {
  test("only the claimant drafts, only the manager and finance decide, each in turn", async () => {
    const client = await signedIn();
    const before = (await client.state()).sha;
    await client.decide("CL-102", "approve"); // acting as Ana, who cannot approve her own claim
    await client.viewAs("ben");
    await client.request("POST", "/claims/new", { action: "lodge", description_1: "Lunch", amount_1: "12", currency_1: "GBP", category_1: "meals" });
    assert.match((await client.get("/claims/new")).text, /Only the claimant drafts/);
    await client.viewAs("finance");
    await client.decide("CL-102", "approve"); // still with the manager, not finance
    assert.equal((await client.state()).sha, before);
  });

  test("pages a role may not open answer 200 with a notice and change nothing", async () => {
    const ana = await signedIn("ana.ruiz");
    const before = (await ana.state()).sha;
    for (const path of ["/approvals", "/finance", "/merchants/new", "/merchants/M001/edit"]) {
      const page = await ana.get(path);
      assert.equal(page.status, 200, path);
      assert.ok(region(page.text, "access-notice"), path);
    }
    const rename = await ana.request("POST", "/cost-centres/CC-02/rename", null, { json: { name: "Mine" } });
    assert.deepEqual(JSON.parse(rename.text), { ok: false, reason: "forbidden" });
    assert.ok(!(await ana.get("/claims")).text.includes("viewing-as"), "no one to act for");
    const raj = await signedIn("raj.patel");
    assert.ok(region((await raj.get("/claims/CL-101")).text, "access-notice"), "not Raj's claim");
    assert.equal((await ana.state()).sha, before);
  });
});

describe("merchants and cost centres", () => {
  test("merchants page by eight, search literally, filter by category and sort with a tie-break", async () => {
    const client = await signedIn();
    const first = await client.get("/merchants");
    assert.equal(region(first.text, "merchants-total"), "25 merchants");
    assert.equal(region(first.text, "merchants-pages"), "4");
    const last = await client.get("/merchants?page=4");
    assert.equal(region(last.text, "merchants-list"), "Merchant City Category The Station Inn York Hotel");
    assert.equal(region((await client.get("/merchants?page=99")).text, "merchants-page"), "4", "a page past the end shows the last");
    assert.equal(region((await client.get("/merchants?q=hotel")).text, "merchants-total"), "4 merchants");
    assert.equal(region((await client.get("/merchants?q=%25")).text, "merchants-total"), "0 merchants", "search is literal");
    const meals = await client.get("/merchants?category=meals&sort=city");
    assert.match(region(meals.text, "merchants-list"), /^Merchant City Category Riverside Cafe Bristol Meals Noodle Bar Leeds/);
  });

  test("an administrator adds, edits and deletes merchants", async () => {
    const client = await signedIn();
    const added = await client.submit("/merchants/new", { name: "Night Bus", city: "Leeds", category: "travel" });
    assert.equal(added.target, "/merchants/M026");
    const clash = await client.request("POST", "/merchants/new", { name: "Northern Rail", city: "Leeds", category: "travel" });
    assert.match(region(clash.text, "merchant-errors"), /already listed/);
    const bad = await client.request("POST", "/merchants/new", { name: "", city: "x".repeat(61), category: "spa" });
    assert.equal(bad.status, 200);
    const edited = await client.submit("/merchants/M026/edit", { name: "Night Bus", city: "York", category: "travel", preferred: "yes" });
    assert.equal(region(edited.page.text, "merchant-city"), "York");
    await client.submit("/merchants/M026/delete", {});
    assert.equal((await client.state()).committed.merchants.length, 25);
  });

  test("cost centres: add, rename in place (refusals keep the old name), and delete only when unused", async () => {
    const client = await signedIn();
    const added = await client.submit("/cost-centres", { name: "Training", budget: "1500" });
    assert.match(region(added.page.text, "centres-notice"), /CC-05/);
    const rename = async (code, name) => JSON.parse((await client.request("POST", `/cost-centres/${code}/rename`, null, { json: { name } })).text);
    assert.deepEqual(await rename("CC-05", " Learning "), { ok: true, name: "Learning" });
    for (const name of ["", "Sales", "x".repeat(61)]) assert.equal((await rename("CC-05", name)).ok, false, name);
    const centres = (await client.state()).committed.cost_centres;
    assert.deepEqual(centres.find((c) => c.code === "CC-05"), { code: "CC-05", name: "Learning", budget_pence: 150000 });
    const used = await client.submit("/cost-centres/CC-02/delete", {});
    assert.match(region(used.page.text, "centres-error"), /still use/);
    await client.submit("/cost-centres/CC-04/delete", {});
    assert.deepEqual((await client.state()).committed.cost_centres.map((c) => c.code), ["CC-01", "CC-02", "CC-03", "CC-05"]);
  });

  test("the report counts approved claims only, in sterling", async () => {
    const client = await signedIn();
    const report = await client.get("/reports");
    assert.match(region(report.text, "report-categories"), /Travel £38.50/);
  });
});

describe("sessions", () => {
  test("sessions are isolated", async () => {
    const first = await signedIn();
    const second = await signedIn();
    const seed = (await second.state()).sha;
    await first.lodge([["Lunch", "42", "GBP"]]);
    assert.notEqual((await first.state()).sha, seed);
    assert.equal((await second.state()).sha, seed);
    assert.equal((await second.get("/claims/CL-104")).status, 404);
  });

  test("one session's requests run one at a time", async () => {
    const client = await signedIn();
    await client.viewAs("ben");
    const replies = await Promise.all([1, 2, 3].map(() => client.request("POST", "/claims/CL-102", { action: "approve" })));
    assert.ok(replies.every((reply) => reply.status === 303));
    const claim = await client.claim("CL-102");
    assert.equal(claim.history.filter((h) => h[0] === "approved").length, 1, "approved once, refused twice");
  });

  test(`beyond WTA_SESSION_CAP (${CAP}) the least recently used session is signed out`, async () => {
    const first = await signedIn();
    const seed = await first.state();
    for (let i = 0; i < CAP; i += 1) await signedIn();
    assert.equal((await first.state()).session, null);
    assert.equal((await first.request("GET", "/claims")).status, 303);
    assert.ok((await first.signIn()).setCookie, "a new session");
    const now = await first.state();
    assert.notEqual(now.session, seed.session);
    assert.equal(now.sha, seed.sha);
  });
});
