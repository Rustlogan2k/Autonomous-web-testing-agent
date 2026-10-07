// Larder over HTTP, against a running clean build started with WTA_TEST_HOOKS=1.
// Run: E2E_BASE_URL=http://127.0.0.1:8080/ node --test tests/
// Every test opens its own session (cookie jar), so each starts from the seed.

import assert from "node:assert/strict";
import { describe, test } from "node:test";

const ORIGIN = (process.env.E2E_BASE_URL || "http://127.0.0.1:8080/").replace(/\/+$/, "");

/** A browser stand-in: keeps the session cookie and follows no redirects by itself. */
class Visitor {
  jar = "";

  async fetch(path, { method = "GET", body } = {}) {
    const headers = this.jar ? { Cookie: this.jar } : {};
    const init = { method, headers, redirect: "manual" };
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = typeof body === "string" ? body : JSON.stringify(body);
    }
    const response = await fetch(ORIGIN + path, init);
    const cookie = /wta_sid=[^;]+/.exec(response.headers.get("set-cookie") ?? "");
    if (cookie) this.jar = cookie[0];
    return { status: response.status, location: response.headers.get("location"), text: await response.text() };
  }

  /** An API call the browser app makes: always answered 200 with JSON. */
  async call(method, path, body = {}) {
    const answer = await this.fetch(path, { method, body });
    assert.equal(answer.status, 200, `${method} ${path}`);
    return JSON.parse(answer.text);
  }

  /** The kitchen the shell hands to the browser app for this session. */
  async boot(path = "/recipes") {
    const answer = await this.fetch(path);
    assert.equal(answer.status, 200, `GET ${path}`);
    const raw = /<script id="larder-boot" type="application\/json">([\s\S]*?)<\/script>/.exec(answer.text)[1];
    return JSON.parse(raw);
  }

  async state() {
    const answer = await this.fetch("/__wta/state");
    return JSON.parse(answer.text);
  }
}

describe("larder", () => {
  test("health and the entry redirect", async () => {
    const health = await fetch(`${ORIGIN}/healthz`);
    assert.deepEqual(await health.json(), { ok: true, app: "larder" });
    const root = await new Visitor().fetch("/");
    assert.equal(root.status, 303);
    assert.equal(root.location, "/recipes");
  });

  test("one shell for every app path; unknown paths get it with a 404", async () => {
    const visitor = new Visitor();
    for (const path of ["/recipes", "/recipes/r2", "/recipes/r2/method", "/list", "/settings", "/conversions"]) {
      const answer = await visitor.fetch(path);
      assert.equal(answer.status, 200, path);
      assert.match(answer.text, /<script src="\/static\/app.js"><\/script>/);
      assert.match(answer.text, /<link rel="icon" href="data:,">/);
    }
    assert.equal((await visitor.fetch("/recipes/r9")).status, 404);
    assert.equal((await visitor.fetch("/pantry")).status, 404);
    for (const asset of ["app.js", "units.js", "larder.css"]) {
      assert.equal((await visitor.fetch(`/static/${asset}`)).status, 200, asset);
    }
  });

  test("the shell carries the seeded kitchen", async () => {
    const boot = await new Visitor().boot();
    assert.deepEqual(boot.servings, { r1: 4, r2: 4, r3: 4, r4: 6 });
    assert.deepEqual(boot.settings, { unitSystem: "metric", ovenScale: "celsius" });
    assert.deepEqual(boot.list, []);
    assert.deepEqual(boot.recipes.map((r) => [r.id, r.name, r.lines[0].amount, r.lines[0].unit]),
      [["r1", "Shortbread", 250, "g"], ["r2", "Pancakes", 150, "g"], ["r3", "Tomato soup", 800, "g"], ["r4", "Lemonade", 1000, "ml"]]);
    assert.equal(boot.vocab["nav.recipes"].length > 0, true);
  });

  test("fresh sessions start from the same seed and are kept apart", async () => {
    const a = new Visitor();
    const b = new Visitor();
    const [sa, sb] = [await a.state(), await b.state()];
    assert.notEqual(sa.session, sb.session);
    assert.equal(sa.sha, sb.sha);
    await a.call("POST", "/api/recipes/r1/servings", { change: 1 });
    assert.notEqual((await a.state()).sha, sa.sha);
    assert.equal((await b.state()).sha, sb.sha);
  });

  test("the servings stepper moves one at a time within 1-12", async () => {
    const visitor = new Visitor();
    assert.deepEqual(await visitor.call("POST", "/api/recipes/r1/servings", { change: 1 }), { ok: true, servings: 5, message: "" });
    for (let i = 0; i < 4; i += 1) await visitor.call("POST", "/api/recipes/r1/servings", { change: -1 });
    const refused = await visitor.call("POST", "/api/recipes/r1/servings", { change: -1 });
    assert.equal(refused.ok, false);
    assert.equal(refused.servings, 1);
    assert.match(refused.message, /1/);
    const before = (await visitor.state()).sha;
    await visitor.call("POST", "/api/recipes/r1/servings", { change: -1 });
    assert.equal((await visitor.state()).sha, before, "a refused step changes nothing");
    assert.equal((await visitor.call("POST", "/api/recipes/r9/servings", { change: 1 })).ok, false);
    assert.equal((await visitor.boot("/recipes/r1")).servings.r1, 1);
  });

  test("a recipe goes on the list once, at its current servings", async () => {
    const visitor = new Visitor();
    await visitor.call("POST", "/api/recipes/r1/servings", { change: 1 });
    await visitor.call("POST", "/api/recipes/r1/servings", { change: 1 });
    const added = await visitor.call("POST", "/api/list", { recipe: "r1" });
    assert.equal(added.ok, true);
    assert.deepEqual(added.list.map((item) => [item.id, item.name, item.amount, item.unit, item.aisle]), [
      ["i1", "plain flour", 375, "g", "baking"], ["i2", "butter", 187.5, "g", "dairy"], ["i3", "caster sugar", 112.5, "g", "baking"],
    ]);
    const again = await visitor.call("POST", "/api/list", { recipe: "r1" });
    assert.equal(again.ok, false);
    assert.match(again.message, /Shortbread/);
    assert.equal(again.list.length, 3);
    assert.equal((await visitor.call("POST", "/api/list", { recipe: "r7" })).ok, false);
  });

  test("ticking an item off, putting it back, clearing ticked items", async () => {
    const visitor = new Visitor();
    await visitor.call("POST", "/api/list", { recipe: "r2" });
    assert.equal((await visitor.call("POST", "/api/list/clear-ticked")).ok, false);
    const ticked = await visitor.call("POST", "/api/list/items/i2/tick");
    assert.deepEqual(ticked.list.map((item) => item.ticked), [false, true, false]);
    const back = await visitor.call("POST", "/api/list/items/i2/tick");
    assert.deepEqual(back.list.map((item) => item.ticked), [false, false, false]);
    await visitor.call("POST", "/api/list/items/i3/tick");
    const cleared = await visitor.call("POST", "/api/list/clear-ticked");
    assert.deepEqual(cleared.list.map((item) => item.id), ["i1", "i2"]);
    assert.equal((await visitor.call("POST", "/api/list/items/i3/tick")).ok, false);
  });

  test("saving one settings group keeps the other", async () => {
    const visitor = new Visitor();
    assert.deepEqual(await visitor.call("PATCH", "/api/settings", { unitSystem: "us" }), { ok: true });
    assert.deepEqual(await visitor.call("PATCH", "/api/settings", { ovenScale: "fahrenheit" }), { ok: true });
    assert.deepEqual((await visitor.boot("/settings")).settings, { unitSystem: "us", ovenScale: "fahrenheit" });
    assert.deepEqual(await visitor.call("PATCH", "/api/settings", { ovenScale: "gas" }), { ok: true });
    assert.deepEqual((await visitor.state()).committed.settings, { unitSystem: "us", ovenScale: "gas" });
  });

  test("unknown settings are refused with a 200 and change nothing", async () => {
    const visitor = new Visitor();
    const before = (await visitor.state()).sha;
    for (const body of [{ unitSystem: "imperial" }, { ovenScale: "" }, { theme: "dark" }, { unitSystem: "us", ovenScale: "gas" }, {}]) {
      const answer = await visitor.call("PATCH", "/api/settings", body);
      assert.equal(answer.ok, false, JSON.stringify(body));
    }
    assert.equal((await visitor.state()).sha, before);
  });
});
