// Tally over HTTP, against a running clean build started with WTA_TEST_HOOKS=1.
// Run: E2E_BASE_URL=http://127.0.0.1:8080/ node --test tests/
// Every test opens its own session (cookie jar), so each starts from the seed.

import assert from "node:assert/strict";
import { describe, test } from "node:test";

const ORIGIN = (process.env.E2E_BASE_URL || "http://127.0.0.1:8080/").replace(/\/+$/, "");

/** A browser stand-in: keeps the session cookie and follows no redirects by itself. */
class Visitor {
  jar = "";

  async fetch(path, { method = "GET", json, form } = {}) {
    const headers = this.jar ? { Cookie: this.jar } : {};
    const init = { method, headers, redirect: "manual" };
    if (json !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(json);
    } else if (form) {
      headers["Content-Type"] = "application/x-www-form-urlencoded";
      init.body = "";
    }
    const response = await fetch(ORIGIN + path, init);
    const cookie = /wta_sid=[^;]+/.exec(response.headers.get("set-cookie") ?? "");
    if (cookie) this.jar = cookie[0];
    return { status: response.status, location: response.headers.get("location"), text: await response.text() };
  }

  async page(path) {
    const answer = await this.fetch(path);
    assert.equal(answer.status, 200, `GET ${path}`);
    return answer.text;
  }

  /** A phase change: a form POST answered 303 back to /poll; returns the /poll page. */
  async act(action) {
    const answer = await this.fetch(`/poll/${action}`, { method: "POST", form: true });
    assert.equal(answer.status, 303, `POST /poll/${action}`);
    assert.equal(answer.location, "/poll");
    return this.page("/poll");
  }

  async api(path, body) {
    const answer = await this.fetch(path, { method: "POST", json: body });
    assert.equal(answer.status, 200, `POST ${path}`);
    return JSON.parse(answer.text);
  }

  /** One voter through the kiosk: issue a paper, start, cast. */
  async vote(...preferences) {
    await this.act("issue");
    const paper = (await this.state()).committed.poll.kiosk;
    assert.equal((await this.api("/api/kiosk/start", { paper })).ok, true);
    const cast = await this.api("/api/ballots", { paper, preferences });
    assert.equal(cast.ok, true, cast.message);
    return cast;
  }

  async state() {
    return JSON.parse(await this.page("/__wta/state"));
  }
}

/** Text of the element with this id (up to its first closing tag of the same name). */
function region(html, id) {
  const found = new RegExp(`<(\\w+)[^>]*\\sid="${id}"[^>]*>([\\s\\S]*?)</\\1>`).exec(html);
  return found ? found[2].replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim() : null;
}

const countData = (html) => JSON.parse(/<script id="count-data" type="application\/json">([\s\S]*?)<\/script>/.exec(html)[1]);

describe("tally", () => {
  test("health and the entry redirect", async () => {
    const health = await fetch(`${ORIGIN}/healthz`);
    assert.deepEqual(await health.json(), { ok: true, app: "tally" });
    const root = await new Visitor().fetch("/");
    assert.equal(root.status, 303);
    assert.equal(root.location, "/poll");
  });

  test("the open poll: 11 papers issued, 11 ballots cast, the kiosk waiting", async () => {
    const html = await new Visitor().page("/poll");
    assert.equal(region(html, "papers-issued"), "11");
    assert.equal(region(html, "ballots-cast"), "11");
    assert.equal(region(html, "kiosk-status"), "");
    assert.match(html, /id="next-voter"/);
    assert.doesNotMatch(html, /id="start-ballot"/);
  });

  test("fresh sessions start from the same seed and are kept apart", async () => {
    const a = new Visitor();
    const b = new Visitor();
    const [sa, sb] = [await a.state(), await b.state()];
    assert.notEqual(sa.session, sb.session);
    assert.equal(sa.sha, sb.sha);
    await a.act("issue");
    assert.notEqual((await a.state()).sha, sa.sha);
    assert.equal((await b.state()).sha, sb.sha);
  });

  test("one paper, one ballot: a second cast on a used paper is refused with a 200", async () => {
    const visitor = new Visitor();
    const html = await visitor.act("issue");
    assert.equal(region(html, "papers-issued"), "12");
    assert.equal(region(html, "kiosk-paper"), "12");
    assert.match(html, /id="ballot-panel" data-paper="12" hidden/);
    assert.equal((await visitor.api("/api/ballots", { paper: 12, preferences: ["cand-a"] })).ok, false, "not started yet");
    assert.equal((await visitor.api("/api/kiosk/start", { paper: 12 })).ok, true);
    const first = await visitor.api("/api/ballots", { paper: 12, preferences: ["cand-a", "", ""] });
    assert.deepEqual([first.ok, first.cast, first.issued], [true, 12, 12]);
    for (let i = 0; i < 4; i += 1) {
      const again = await visitor.api("/api/ballots", { paper: 12, preferences: ["cand-a", "", ""] });
      assert.deepEqual([again.ok, again.cast, again.issued], [false, 12, 12]);
      assert.match(again.message, /12/);
    }
    const page = await visitor.page("/poll");
    assert.equal(region(page, "ballots-cast"), "12");
    assert.match(region(page, "kiosk-status"), /12/);
  });

  test("bad rankings are refused and keep the paper", async () => {
    const visitor = new Visitor();
    await visitor.act("issue");
    await visitor.api("/api/kiosk/start", { paper: 12 });
    for (const preferences of [["", "", ""], ["", "cand-b", ""], ["cand-b", "cand-b", ""], ["cand-z"]]) {
      assert.equal((await visitor.api("/api/ballots", { paper: 12, preferences })).ok, false, JSON.stringify(preferences));
    }
    assert.equal((await visitor.state()).committed.poll.ballots.length, 11);
    assert.equal((await visitor.api("/api/ballots", { paper: 12, preferences: ["cand-b", "cand-a", "cand-c"] })).ok, true);
  });

  test("Next voter waits for the current paper; Close poll waits for a voter part-way through", async () => {
    const visitor = new Visitor();
    await visitor.act("issue");
    const refused = await visitor.act("issue");
    assert.equal(region(refused, "papers-issued"), "12");
    assert.match(region(refused, "flash"), /12/);
    await visitor.api("/api/kiosk/start", { paper: 12 });
    const stillOpen = await visitor.act("close");
    assert.equal((await visitor.state()).committed.poll.phase, "open");
    assert.match(region(stillOpen, "flash"), /12/);
  });

  test("seed + one ballot for Ada: closing voids nothing, the count opens on round 3 of 3", async () => {
    const visitor = new Visitor();
    await visitor.vote("cand-a", "", "");
    const closing = await visitor.act("close");
    assert.match(closing, /id="confirm-close"/);
    const counted = await visitor.act("confirm-close");
    const data = countData(counted);
    assert.equal(data.rounds.length, 3);
    assert.deepEqual(data.rounds.map((r) => r.leaderVotes), [5, 5, 6]);
    assert.deepEqual(data.rounds.at(-1).outcome, { kind: "elected", candidate: "cand-d" });
    assert.equal(data.rounds.at(-1).continuing, 11);
    assert.equal(region(counted, "papers-issued"), "12");
    assert.equal(region(counted, "ballots-cast"), "12");
  });

  test("seed + (D): exactly half in round 1 is not enough; Dev elected 7 of 12", async () => {
    const visitor = new Visitor();
    await visitor.vote("cand-d");
    await visitor.act("close");
    const data = countData(await visitor.act("confirm-close"));
    assert.deepEqual(data.rounds.map((r) => [r.leaderVotes, r.continuing, r.outcome.kind]),
      [[6, 12, "eliminated"], [6, 12, "eliminated"], [7, 12, "elected"]]);
  });

  test("seed + (B) + (A,B): Bram is elected and declared with 7 of 13", async () => {
    const visitor = new Visitor();
    await visitor.vote("cand-b");
    await visitor.vote("cand-a", "cand-b");
    await visitor.act("close");
    await visitor.act("confirm-close");
    const before = await visitor.page("/declaration");
    assert.equal(region(before, "declared-name"), null);
    const published = await visitor.fetch("/poll/declare", { method: "POST", form: true });
    assert.equal(published.status, 303);
    assert.equal(published.location, "/declaration", "a published declaration lands on the declaration");
    assert.equal((await visitor.state()).committed.poll.phase, "declared");
    assert.match(await visitor.page("/poll"), /Bram Lindqvist/);
    const page = await visitor.page("/declaration");
    assert.equal(region(page, "declared-name"), "Bram Lindqvist");
    assert.equal(region(page, "declared-votes"), "7");
    assert.equal(region(page, "declared-continuing"), "13");
    const again = await visitor.act("declare");
    assert.ok(region(again, "flash"));
  });

  test("a tied count cannot be declared", async () => {
    const visitor = new Visitor();
    await visitor.vote("cand-a", "cand-b");
    await visitor.act("close");
    const counted = await visitor.act("confirm-close");
    assert.deepEqual(countData(counted).rounds.at(-1).outcome, { kind: "tied", candidate: null });
    await visitor.act("declare");
    assert.equal((await visitor.state()).committed.poll.phase, "counted");
  });

  test("an issued, unused paper is voided at close; the register and the audit agree", async () => {
    const visitor = new Visitor();
    await visitor.act("issue");
    assert.match(await visitor.page("/ballots"), /sealed/i);
    await visitor.act("close");
    await visitor.act("confirm-close");
    const poll = (await visitor.state()).committed.poll;
    assert.equal(poll.papers[11].void, true);
    const register = await visitor.page("/register");
    assert.match(register, /Leo Novak<\/td><td>12<\/td>/);
    const audit = await visitor.page("/ballots");
    assert.equal((audit.match(/<tr><td>\d+<\/td>/g) || []).length, 11);
    assert.equal((await visitor.api("/api/ballots", { paper: 12, preferences: ["cand-a"] })).ok, false);
  });

  test("help and unknown pages", async () => {
    const visitor = new Visitor();
    assert.match(await visitor.page("/help"), /more than half/);
    assert.equal((await visitor.fetch("/nowhere")).status, 404);
  });
});
