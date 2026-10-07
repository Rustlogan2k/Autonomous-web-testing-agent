// Skylark over HTTP, against a running clean build started with WTA_TEST_HOOKS=1.
// Run: E2E_BASE_URL=http://127.0.0.1:8080/ node --test tests/
// Every test opens its own session (cookie jar), so each starts from the seed.

import assert from "node:assert/strict";
import { describe, test } from "node:test";

const ORIGIN = (process.env.E2E_BASE_URL || "http://127.0.0.1:8080/").replace(/\/+$/, "");

/** A browser stand-in: keeps the session cookie and follows no redirects by itself. */
class Visitor {
  jar = "";

  async fetch(path, { form } = {}) {
    const headers = this.jar ? { Cookie: this.jar } : {};
    const init = { method: form ? "POST" : "GET", headers, redirect: "manual" };
    if (form) {
      headers["Content-Type"] = "application/x-www-form-urlencoded";
      init.body = new URLSearchParams(form).toString();
    }
    const response = await fetch(ORIGIN + path, init);
    const cookie = /wta_sid=[^;]+/.exec(response.headers.get("set-cookie") ?? "");
    if (cookie) this.jar = cookie[0];
    return { status: response.status, location: response.headers.get("location"), body: await response.text() };
  }

  async page(path) {
    const answer = await this.fetch(path);
    assert.equal(answer.status, 200, `GET ${path}`);
    return answer.body;
  }

  /** Submit a form and land where the server sends the browser (post/redirect/get). */
  async post(path, form) {
    const answer = await this.fetch(path, { form });
    assert.equal(answer.status, 303, `POST ${path} redirects`);
    return { target: answer.location, body: await this.page(answer.location) };
  }

  async more(after, site = "all") {
    const answer = await this.fetch(`/feed/more?${new URLSearchParams({ site, after })}`);
    assert.equal(answer.status, 200);
    return JSON.parse(answer.body);
  }

  async state() {
    return JSON.parse(await this.page("/__wta/state"));
  }

  record(site, fields) {
    return this.post(`/sightings/new/${site}`, { species: "heron", count: "1", seen_on: "2026-06-10", notes: "", ...fields });
  }
}

/** Text of the element with this id (up to its first closing tag of the same name). */
function region(html, id) {
  const found = new RegExp(`<(\\w+)[^>]*\\sid="${id}"[^>]*>([\\s\\S]*?)</\\1>`).exec(html);
  return found ? found[2].replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim() : null;
}

const listedRefs = (html) => [...html.matchAll(/id="sighting-(s\d+)"/g)].map((m) => m[1]);
const cursorIn = (html) => /id="load-more"[^>]*data-after="([^"]+)"/.exec(html)?.[1] ?? null;

/** Load the feed and every further page, as the client does; return refs and the end state. */
async function loadAll(visitor, site = "all") {
  const html = await visitor.page(site === "all" ? "/feed" : `/feed?site=${site}`);
  const listed = listedRefs(html);
  const sizes = [listed.length];
  let after = cursorIn(html);
  while (after) {
    const page = await visitor.more(after, site);
    listed.push(...page.rows.map((row) => row.ref));
    sizes.push(page.rows.length);
    after = page.more ? page.after : null;
  }
  return { listed, sizes, total: Number(region(html, "total-count")) };
}

describe("skylark", () => {
  test("health and the entry redirect", async () => {
    const health = await fetch(`${ORIGIN}/healthz`);
    assert.deepEqual(await health.json(), { ok: true, app: "skylark" });
    const root = await new Visitor().fetch("/");
    assert.equal(root.status, 303);
    assert.equal(root.location, "/feed");
  });

  test("fresh sessions start from the same seed and are kept apart", async () => {
    const a = new Visitor();
    const b = new Visitor();
    const [sa, sb] = [await a.state(), await b.state()];
    assert.notEqual(sa.session, sb.session);
    assert.equal(sa.sha, sb.sha);
    await a.post("/sightings/s20/review", { action: "flag" });
    assert.notEqual((await a.state()).sha, sa.sha);
    assert.equal((await b.state()).sha, sb.sha);
  });

  test("the first page is server-rendered: six newest, tallies, and a keyset cursor", async () => {
    const html = await new Visitor().page("/feed");
    assert.deepEqual(listedRefs(html), ["s22", "s21", "s20", "s19", "s18", "s17"]);
    assert.equal(region(html, "shown-count"), "6");
    assert.equal(region(html, "total-count"), "22");
    assert.equal(region(html, "tally-total"), "17");
    assert.deepEqual(["marsh", "wood", "lake"].map((site) => region(html, `tally-${site}`)), ["5", "8", "4"]);
    assert.equal(cursorIn(html), "2026-06-05.17");
    assert.equal(region(html, "feed-end"), null, "the end marker only appears after the last page");
  });

  test("the seed's pages are 6/6/6/4 and list every sighting once", async () => {
    const { listed, sizes, total } = await loadAll(new Visitor());
    assert.deepEqual(sizes, [6, 6, 6, 4]);
    assert.equal(total, 22);
    assert.deepEqual(listed, Array.from({ length: 22 }, (_, i) => `s${22 - i}`));
  });

  test("a page of JSON carries all-row and verified-only bird sums", async () => {
    const visitor = new Visitor();
    await visitor.post("/sightings/s14/review", { action: "flag" });
    const page = await visitor.more("2026-06-05.17");
    assert.deepEqual(page.rows.map((row) => row.ref), ["s16", "s15", "s14", "s13", "s12", "s11"]);
    assert.equal(page.rows[2].statusName, "Doubtful");
    assert.equal(page.birds, 14);
    assert.equal(page.countedBirds, 11);
    assert.deepEqual(page.bySite, { marsh: 7, wood: 0, lake: 4 });
    assert.equal(page.after, "2026-05-30.11");
  });

  test("recording today's sighting puts the 5 June pair on the first page boundary; still nothing skipped", async () => {
    const visitor = new Visitor();
    const { target, body } = await visitor.record("marsh", {});
    assert.equal(target, "/feed");
    assert.equal(region(body, "flash"), "Sighting s23 recorded.");
    assert.equal(listedRefs(body)[0], "s23");
    assert.equal(cursorIn(body), "2026-06-05.18");
    const { listed, sizes, total } = await loadAll(visitor);
    assert.deepEqual(sizes, [6, 6, 6, 5]);
    assert.equal(total, 23);
    assert.equal(new Set(listed).size, 23);
    assert.ok(listed.indexOf("s18") === 5 && listed.indexOf("s17") === 6);
  });

  test("each site filter lists only that site's sightings, all of them, without ties", async () => {
    const expected = { marsh: 8, wood: 7, lake: 7 };
    for (const [site, count] of Object.entries(expected)) {
      const visitor = new Visitor();
      const { listed, total, sizes } = await loadAll(visitor, site);
      assert.equal(total, count, site);
      assert.equal(listed.length, count, site);
      assert.deepEqual(sizes, [6, count - 6], site);
      const state = await visitor.state();
      const sites = new Set(listed.map((ref) => state.committed.sightings.find((row) => `s${row.id}` === ref).site));
      assert.deepEqual([...sites], [site]);
    }
    const all = await new Visitor().page("/feed?site=all");
    assert.equal(region(all, "total-count"), "22");
  });

  test("a doubtful sighting stays listed and leaves the first page's tallies", async () => {
    const visitor = new Visitor();
    const { body } = await visitor.post("/sightings/s20/review", { action: "flag" });
    assert.equal(region(body, "sighting-status"), "Doubtful");
    const feed = await visitor.page("/feed");
    assert.ok(listedRefs(feed).includes("s20"));
    assert.equal(region(feed, "tally-total"), "15");
    assert.equal(region(feed, "tally-wood"), "6");
  });

  test("flag and restore are refused when there is nothing to change, and change nothing", async () => {
    const visitor = new Visitor();
    const seed = (await visitor.state()).sha;
    const refusedRestore = await visitor.post("/sightings/s20/review", { action: "restore" });
    assert.match(region(refusedRestore.body, "flash"), /already verified/);
    assert.equal((await visitor.state()).sha, seed);
    await visitor.post("/sightings/s20/review", { action: "flag" });
    const flagged = (await visitor.state()).sha;
    const again = await visitor.post("/sightings/s20/review", { action: "flag" });
    assert.match(region(again.body, "flash"), /already flagged/);
    assert.equal((await visitor.state()).sha, flagged);
    const restored = await visitor.post("/sightings/s20/review", { action: "restore" });
    assert.equal(region(restored.body, "sighting-status"), "Verified");
    assert.equal((await visitor.state()).sha, seed);
  });

  test("the two-step record form: where, then what", async () => {
    const visitor = new Visitor();
    const step1 = await visitor.page("/sightings/new");
    assert.match(step1, /id="where-site"/);
    const chosen = await visitor.post("/sightings/new", { site: "lake" });
    assert.equal(chosen.target, "/sightings/new/lake");
    assert.equal(region(chosen.body, "draft-summary"), "No species yet × 1 at Lakeside, today");
    const bad = await visitor.post("/sightings/new", { site: "moor" });
    assert.equal(bad.target, "/sightings/new");
    assert.ok(region(bad.body, "flash"));
  });

  test("invalid sightings are refused with a message back on the form, and change nothing", async () => {
    const visitor = new Visitor();
    const seed = (await visitor.state()).sha;
    const cases = [
      [{ species: "" }, /species/],
      [{ count: "0" }, /1 to 500/],
      [{ count: "999999999" }, /1 to 500/],
      [{ seen_on: "2026-06-15" }, /after today/],
      [{ seen_on: "0001-01-01" }, /2026-01-01/],
      [{ seen_on: "" }, /date/],
      [{ notes: "A".repeat(2048) }, /500 characters/],
    ];
    for (const [fields, message] of cases) {
      const { target, body } = await visitor.record("wood", fields);
      assert.equal(target, "/sightings/new/wood", JSON.stringify(fields).slice(0, 40));
      assert.match(region(body, "flash"), message);
    }
    assert.equal((await visitor.state()).sha, seed);
  });

  test("the draft key is namespaced per session and per site, and does not reveal the session", async () => {
    const a = new Visitor();
    const b = new Visitor();
    const keyOf = (html) => /data-draft-key="([^"]+)"/.exec(html)[1];
    const [ka, kb] = [keyOf(await a.page("/sightings/new/marsh")), keyOf(await b.page("/sightings/new/marsh"))];
    assert.match(ka, /^skylark:[0-9a-f]{12}:draft:marsh$/);
    assert.notEqual(ka, kb);
    assert.equal(keyOf(await a.page("/sightings/new/wood")), ka.replace(/marsh$/, "wood"));
    assert.ok(!ka.includes((await a.state()).session));
  });

  test("a recorded sighting asks the feed to drop its draft, once", async () => {
    const visitor = new Visitor();
    const form = await visitor.page("/sightings/new/marsh");
    const key = /data-draft-key="([^"]+)"/.exec(form)[1];
    const { body } = await visitor.record("marsh", {});
    assert.ok(body.includes(`<meta name="skylark-sent-draft" content="${key}">`));
    assert.ok(!(await visitor.page("/feed")).includes("skylark-sent-draft"));
  });

  test("sites, a site, this month's totals and help all render", async () => {
    const visitor = new Visitor();
    await visitor.post("/sightings/s14/review", { action: "flag" });
    const sites = await visitor.page("/sites");
    for (const id of ["marsh", "wood", "lake"]) assert.match(sites, new RegExp(`id="site-${id}"`));
    const wood = await visitor.page("/sites/wood");
    assert.match(wood, /href="\/feed\?site=wood"/);
    const month = await visitor.page("/summary");
    assert.match(month, /2026-06/);
    assert.match(month, /<tfoot><tr><th>[^<]+<\/th><td class="num">8<\/td><td class="num">20<\/td>/);
    await visitor.page("/help");
  });
});
