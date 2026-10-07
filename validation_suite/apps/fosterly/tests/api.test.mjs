// Fosterly over HTTP, against a running clean build started with WTA_TEST_HOOKS=1.
// Run: E2E_BASE_URL=http://127.0.0.1:8080/ node --test tests/
// Every test opens its own session (cookie jar), so each starts from the seed.

import assert from "node:assert/strict";
import { describe, test } from "node:test";

const ORIGIN = (process.env.E2E_BASE_URL || "http://127.0.0.1:8080/").replace(/\/+$/, "");

/** A browser stand-in: keeps the session cookie and follows no redirects by itself. */
class Visitor {
  jar = "";

  async fetch(path, { method = "GET", body, type } = {}) {
    const headers = this.jar ? { Cookie: this.jar } : {};
    if (type) headers["Content-Type"] = type;
    const response = await fetch(ORIGIN + path, { method, headers, body, redirect: "manual" });
    const cookie = /wta_sid=[^;]+/.exec(response.headers.get("set-cookie") ?? "");
    if (cookie) this.jar = cookie[0];
    return { status: response.status, location: response.headers.get("location"), body: await response.text() };
  }

  async page(path) {
    const answer = await this.fetch(path);
    assert.equal(answer.status, 200, `GET ${path}`);
    return answer.body;
  }

  /** Press a form button and land where the server sends the browser (post/redirect/get). */
  async post(path) {
    const answer = await this.fetch(path, { method: "POST", body: "", type: "application/x-www-form-urlencoded" });
    assert.equal(answer.status, 303, `POST ${path} redirects`);
    const target = new URL(answer.location, ORIGIN).pathname;
    return { target, body: await this.page(target) };
  }

  /** One autosaved field, as static/apply.js sends it. */
  async save(ref, section, name, value) {
    const answer = await this.fetch(`/api/drafts/${ref}/${section}`, {
      method: "PATCH", body: JSON.stringify({ [name]: value }), type: "application/json",
    });
    assert.equal(answer.status, 200, "autosave never answers an error status");
    return JSON.parse(answer.body);
  }

  async state() {
    return JSON.parse(await this.page("/__wta/state"));
  }

  draft(state, ref) {
    return state.committed.drafts.find((draft) => draft.ref === ref);
  }
}

/** Text of the element with this id (up to its first closing tag of the same name). */
function region(html, id) {
  const found = new RegExp(`<(\\w+)[^>]*\\sid="${id}"[^>]*>([\\s\\S]*?)</\\1>`).exec(html);
  return found ? found[2].replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim() : null;
}

describe("runtime contract", () => {
  test("health and seed state", async () => {
    const health = await (await fetch(`${ORIGIN}/healthz`)).json();
    assert.deepEqual(health, { ok: true, app: "fosterly" });
    const visitor = new Visitor();
    const start = await visitor.page("/");
    assert.match(start, /id="resume-FOS-1001"/);
    const state = await visitor.state();
    assert.deepEqual(state.committed.drafts.map((draft) => draft.ref), ["FOS-1001"]);
    assert.equal(state.committed.applications.length, 0);
    assert.match(state.sha, /^[0-9a-f]{64}$/);
  });

  test("two sessions do not see each other's drafts", async () => {
    const one = new Visitor();
    const two = new Visitor();
    await one.page("/");
    await two.page("/");
    const { target } = await one.post("/drafts");
    assert.equal(target, "/apply/FOS-1002");
    assert.equal((await two.state()).committed.drafts.length, 1);
    const again = await two.post("/drafts");
    assert.equal(again.target, "/apply/FOS-1002", "references are per session, from the seed");
  });
});

describe("autosave", () => {
  test("an accepted answer is stored at once and survives a reload", async () => {
    const visitor = new Visitor();
    const { target } = await visitor.post("/drafts");
    const ref = target.split("/").pop();
    const answer = await visitor.save(ref, "availability", "available_from", "2026-06-15");
    assert.equal(answer.ok, true);
    assert.equal(visitor.draft(await visitor.state(), ref).answers.available_from, "2026-06-15");
    const html = await visitor.page(target);
    assert.equal(region(html, "summary-availability"), "From 15 Jun 2026");
  });

  test("a refused answer changes nothing and is answered with 200 and a reason", async () => {
    const visitor = new Visitor();
    const { target } = await visitor.post("/drafts");
    const ref = target.split("/").pop();
    const before = (await visitor.state()).sha;
    for (const [section, name, value] of [["availability", "available_from", "0001-01-01"],
      ["about", "email", "not-an-email"], ["about", "full_name", "A".repeat(2048)],
      ["experience", "years_experience", "999999999"], ["declaration", "declaration", true]]) {
      const answer = await visitor.save(ref, section, name, value);
      assert.equal(answer.ok, false, `${name}=${String(value).slice(0, 12)}`);
      assert.ok(answer.message);
    }
    assert.equal((await visitor.state()).sha, before);
  });

  test("filling About you completes one section and allows the declaration", async () => {
    const visitor = new Visitor();
    const { target } = await visitor.post("/drafts");
    const ref = target.split("/").pop();
    await visitor.save(ref, "about", "full_name", "Test User Input");
    await visitor.save(ref, "about", "email", "tester@example.com");
    const answer = await visitor.save(ref, "about", "phone", "+1-202-555-0143");
    assert.equal(answer.completeness.done, 1);
    assert.equal(answer.completeness.sections.about, true);
    const signed = await visitor.save(ref, "declaration", "declaration", true);
    assert.equal(signed.completeness.done, 2);
  });

  test("a field is accepted only through its own section", async () => {
    const visitor = new Visitor();
    await visitor.page("/");
    const answer = await visitor.save("FOS-1001", "about", "available_from", "2026-08-01");
    assert.equal(answer.ok, true);
    assert.equal(visitor.draft(await visitor.state(), "FOS-1001").answers.available_from, "2026-07-01");
  });
});

describe("submission", () => {
  test("FOS-1001 is submitted as FA-2001 and leaves the saved list", async () => {
    const visitor = new Visitor();
    await visitor.page("/");
    const { target, body } = await visitor.post("/apply/FOS-1001/submit");
    assert.equal(target, "/applications/FA-2001");
    assert.equal(region(body, "sub-ref"), "FA-2001");
    assert.equal(region(body, "sub-other-animals"), "No");
    assert.equal(region(body, "sub-animals"), "");
    assert.equal(region(body, "sub-available"), "From 1 Jul 2026, at least 4 weeks");
    assert.doesNotMatch(await visitor.page("/"), /resume-FOS-1001/);
    const gone = await visitor.page("/apply/FOS-1001");
    assert.match(gone, /FA-2001/);
  });

  test("an incomplete draft is refused with a message and nothing changes", async () => {
    const visitor = new Visitor();
    const { target } = await visitor.post("/drafts");
    const before = (await visitor.state()).sha;
    const refused = await visitor.post(`${target}/submit`);
    assert.equal(refused.target, `${target}/review`);
    assert.ok(region(refused.body, "flash"));
    assert.equal(region(refused.body, "review-status"), "0 of 5 sections complete");
    assert.equal((await visitor.state()).sha, before);
  });

  test("two cats are submitted as '2 x Cat'", async () => {
    const visitor = new Visitor();
    await visitor.page("/");
    await visitor.save("FOS-1001", "home", "other_animals", true);
    await visitor.save("FOS-1001", "home", "animal_species", "cat");
    await visitor.save("FOS-1001", "home", "animal_count", "2");
    const { body } = await visitor.post("/apply/FOS-1001/submit");
    assert.equal(region(body, "sub-other-animals"), "Yes");
    assert.equal(region(body, "sub-animals"), "2 x Cat");
  });

  test("animals hidden by unticking the box are not on the application", async () => {
    const visitor = new Visitor();
    await visitor.page("/");
    await visitor.save("FOS-1001", "home", "other_animals", true);
    await visitor.save("FOS-1001", "home", "animal_species", "cat");
    await visitor.save("FOS-1001", "home", "animal_count", "2");
    await visitor.save("FOS-1001", "home", "other_animals", false);
    const { body } = await visitor.post("/apply/FOS-1001/submit");
    assert.equal(region(body, "sub-other-animals"), "No");
    assert.equal(region(body, "sub-animals"), "");
  });

  test("a ticked box with no animal described keeps the draft unsubmitted", async () => {
    const visitor = new Visitor();
    await visitor.page("/");
    await visitor.save("FOS-1001", "home", "other_animals", true);
    const refused = await visitor.post("/apply/FOS-1001/submit");
    assert.equal(refused.target, "/apply/FOS-1001/review");
    assert.equal((await visitor.state()).committed.applications.length, 0);
  });
});
