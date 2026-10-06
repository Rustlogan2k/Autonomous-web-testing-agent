// Ledgerly's HTTP behaviour against the clean build (E2E_BASE_URL), node:test only.
// Run: E2E_BASE_URL=http://127.0.0.1:8080/ node --test tests/

import assert from "node:assert/strict";
import { describe, test } from "node:test";

const BASE = (process.env.E2E_BASE_URL || "http://127.0.0.1:8080/").replace(/\/$/, "");

/** One browser-like session: keeps the wta_sid cookie, does not follow redirects. */
class Client {
  constructor() {
    this.cookie = "";
  }

  async request(method, path, form) {
    const headers = this.cookie ? { Cookie: this.cookie } : {};
    let body;
    if (form) {
      body = new URLSearchParams(form).toString();
      headers["Content-Type"] = "application/x-www-form-urlencoded";
    }
    const response = await fetch(BASE + path, { method, headers, body, redirect: "manual" });
    const set = response.headers.get("set-cookie");
    if (set) this.cookie = set.split(";")[0];
    return { status: response.status, location: response.headers.get("location") ?? "", text: await response.text() };
  }

  get(path) {
    return this.request("GET", path);
  }

  /** POST a form, then GET the page it redirects to (post/redirect/get, as a browser does). */
  async submit(path, form) {
    const posted = await this.request("POST", path, form);
    assert.equal(posted.status, 303, `POST ${path} should redirect`);
    const target = new URL(posted.location, BASE + "/").pathname;
    return { target, page: await this.get(target) };
  }

  async state() {
    return JSON.parse((await this.get("/__wta/state")).text);
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

function region(html, id) {
  const match = new RegExp(`<([a-z]+)[^>]*\\bid="${id}"[^>]*>([\\s\\S]*?)</\\1>`).exec(html);
  return match ? match[2].replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim() : null;
}

describe("ledgerly", () => {
  test("health", async () => {
    const response = await fetch(`${BASE}/healthz`);
    assert.deepEqual(await response.json(), { ok: true, app: "ledgerly" });
  });

  test("a new session starts from the seed, and two fresh sessions are identical", async () => {
    const a = await new Client().state();
    const b = await new Client().state();
    assert.notEqual(a.session, b.session);
    assert.equal(a.sha, b.sha);
    assert.deepEqual(a.committed.claims.map((c) => [c.id, c.status]),
                     [["CL-101", "approved"], ["CL-102", "awaiting"], ["CL-103", "awaiting"]]);
  });

  test("foreign lines are converted at the fixed rates and totalled in sterling", async () => {
    const ana = new Client();
    const { target, page } = await ana.lodge([["Lunch", "42", "GBP"], ["Hotel", "42", "EUR"], ["Cab", "42", "USD"]]);
    assert.equal(target, "/claims/CL-104");
    // 42 + 42 * 0.86 + 42 * 0.79 = 42 + 36.12 + 33.18
    assert.equal(region(page.text, "claim-total"), "£111.30");
    const claim = await ana.claim("CL-104");
    assert.equal(claim.status, "awaiting");
    assert.deepEqual(claim.lines.map((l) => [l.currency, l.minor]), [["GBP", 4200], ["EUR", 4200], ["USD", 4200]]);
  });

  test("the editor's server-rendered summary converts the same way", async () => {
    const ana = new Client();
    const { page } = await ana.submit("/claims/new", {
      action: "template", description_1: "Dinner", amount_1: "42", currency_1: "EUR", category_1: "meals",
      description_2: "Taxi", amount_2: "42", currency_2: "USD", category_2: "travel",
    });
    assert.equal(region(page.text, "sum-eur"), "€42.00");
    assert.equal(region(page.text, "sum-usd"), "$42.00");
    assert.equal(region(page.text, "sum-total"), "£69.30");
  });

  test("a manager approves up to £50 and is refused above it", async () => {
    const client = new Client();
    await client.viewAs("ben");
    const small = await client.decide("CL-102", "approve");
    assert.equal((await client.claim("CL-102")).status, "approved");
    assert.match(region(small.page.text, "claim-approver"), /Ben Okafor \(manager\)/);
    const before = (await client.state()).sha;
    const large = await client.decide("CL-103", "approve");
    assert.equal((await client.state()).sha, before, "a refused approval changes nothing");
    assert.match(region(large.page.text, "claim-error"), /over your approval limit/);
  });

  test("the manager's limit counts converted lines: £42 + €42 is £78.12, over the limit", async () => {
    const client = new Client();
    await client.lodge([["Lunch", "42", "GBP"], ["Hotel", "42", "EUR"]]);
    await client.viewAs("ben");
    const { page } = await client.decide("CL-104", "approve");
    assert.equal((await client.claim("CL-104")).status, "awaiting");
    assert.equal(region(page.text, "claim-approver"), "Not approved yet");
  });

  test("escalating sends a claim to finance, who approve or reject it", async () => {
    const client = new Client();
    await client.viewAs("ben");
    await client.decide("CL-103", "escalate");
    assert.equal((await client.claim("CL-103")).status, "with_finance");
    await client.viewAs("finance");
    const queue = await client.get("/");
    assert.match(region(queue.text, "finance-queue"), /CL-103/);
    const { page } = await client.decide("CL-103", "approve");
    const claim = await client.claim("CL-103");
    assert.equal(claim.status, "approved");
    assert.equal(claim.approved_by, "finance");
    assert.match(region(page.text, "claim-approver"), /Finance desk \(finance\)/);

    const other = new Client();
    await other.viewAs("ben");
    await other.decide("CL-103", "escalate");
    await other.viewAs("finance");
    await other.decide("CL-103", "reject");
    assert.equal((await other.claim("CL-103")).status, "with_finance", "a rejection needs a reason");
    await other.decide("CL-103", "reject", "Not a business expense");
    assert.equal((await other.claim("CL-103")).status, "rejected");
  });

  test("roles: only the claimant drafts, only the manager and finance decide, each in turn", async () => {
    const client = new Client();
    const before = (await client.state()).sha;
    await client.decide("CL-102", "approve"); // Ana cannot approve her own claim
    await client.viewAs("ben");
    const posted = await client.request("POST", "/claims/new",
      { action: "lodge", description_1: "Lunch", amount_1: "12", currency_1: "GBP", category_1: "meals" });
    assert.equal(posted.status, 303);
    const editor = await client.get("/claims/new");
    assert.match(editor.text, /Only the claimant drafts/);
    await client.viewAs("finance");
    await client.decide("CL-102", "approve"); // still with the manager, not finance
    assert.equal((await client.state()).sha, before);
  });

  test("send back needs a note; the claimant edits and resubmits, and it returns to the manager", async () => {
    const client = new Client();
    await client.viewAs("ben");
    await client.decide("CL-102", "send_back");
    assert.equal((await client.claim("CL-102")).status, "awaiting", "no note, no send back");
    const sent = await client.decide("CL-102", "send_back", "Add the receipt number");
    assert.equal((await client.claim("CL-102")).status, "sent_back");
    assert.match(region(sent.page.text, "claim-latest"), /Add the receipt number/);
    await client.viewAs("ana");
    const editor = await client.get("/claims/CL-102/edit");
    assert.match(editor.text, /id="resubmit-claim"/);
    const { page } = await client.submit("/claims/CL-102/edit", {
      action: "resubmit", description_1: "Taxi from the station (receipt 4471)", amount_1: "18.40",
      currency_1: "GBP", category_1: "travel",
    });
    const claim = await client.claim("CL-102");
    assert.equal(claim.status, "awaiting");
    assert.equal(claim.lines[0].description, "Taxi from the station (receipt 4471)");
    assert.equal(region(page.text, "claim-status"), "Awaiting approval");
    assert.match(region(page.text, "claim-latest"), /^Resubmitted/);
    await client.viewAs("ben");
    assert.match(region((await client.get("/")).text, "approval-queue"), /CL-102/);
  });

  test("validation: an empty amount, a missing description and an oversized amount are refused", async () => {
    const client = new Client();
    const before = (await client.state()).sha;
    const empty = await client.lodge([["Lunch", "", "GBP"]]);
    assert.equal(empty.target, "/claims/new");
    assert.match(region(empty.page.text, "editor-errors"), /Line 1: enter an amount/);
    assert.match(empty.page.text, /value="Lunch"/, "what was typed is kept");
    await client.lodge([["", "42", "GBP"]]);
    await client.lodge([["Laptop", "999999999", "GBP"]]);
    await client.lodge([["Lunch", "4.505", "GBP"]]);
    assert.equal((await client.state()).sha, before);
    const ok = await client.lodge([["Lunch", "42", "GBP"]]);
    assert.equal(ok.target, "/claims/CL-104");
  });

  test("drafts are saved, edited and lodged later; templates save nothing", async () => {
    const client = new Client();
    const before = (await client.state()).sha;
    await client.lodge([["Lunch", "42", "GBP"]], "template");
    assert.equal((await client.state()).sha, before);
    const saved = await client.lodge([["Lunch", "42", "GBP"]], "save");
    assert.equal(saved.target, "/claims/CL-104");
    assert.equal((await client.claim("CL-104")).status, "draft");
    await client.submit("/claims/CL-104/edit", { action: "lodge", description_1: "Lunch", amount_1: "21",
                                                 currency_1: "EUR", category_1: "meals" });
    const claim = await client.claim("CL-104");
    assert.equal(claim.status, "awaiting");
    assert.deepEqual(claim.lines.map((l) => [l.currency, l.minor]), [["EUR", 2100]]);
  });

  test("sessions are isolated", async () => {
    const first = new Client();
    const second = new Client();
    const seed = (await second.state()).sha;
    await first.lodge([["Lunch", "42", "GBP"]]);
    assert.notEqual((await first.state()).sha, seed);
    assert.equal((await second.state()).sha, seed);
    assert.equal((await second.get("/claims/CL-104")).status, 404);
  });
});
