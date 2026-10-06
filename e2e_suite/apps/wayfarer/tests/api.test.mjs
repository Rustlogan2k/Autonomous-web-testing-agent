// Wayfarer API tests. Run against a live server:
//   E2E_BASE_URL=http://127.0.0.1:8080/ node --test tests/
import { test } from "node:test";
import assert from "node:assert/strict";

const BASE = (process.env.E2E_BASE_URL || "http://127.0.0.1:8080/").replace(/\/?$/, "/");

/** A browser-like client: keeps its own wta_sid cookie, so each client is its own session. */
class Client {
  constructor() { this.cookie = ""; }

  async call(method, path, body) {
    const headers = {};
    if (this.cookie) { headers.Cookie = this.cookie; }
    if (body !== undefined) { headers["Content-Type"] = "application/json"; }
    const response = await fetch(new URL(path.replace(/^\//, ""), BASE), {
      method, headers, body: body === undefined ? undefined : JSON.stringify(body),
    });
    const setCookie = response.headers.get("set-cookie");
    if (setCookie) { this.cookie = setCookie.split(";")[0]; }
    const type = response.headers.get("content-type") || "";
    const data = type.includes("json") ? await response.json() : await response.text();
    return { status: response.status, data };
  }

  get(path) { return this.call("GET", path); }
  post(path, body) { return this.call("POST", path, body); }
}

const seedDays = async (client) => (await client.get("/api/trips/t1")).data.latest.days;
const plus = (days, dayIndex, stop) => days.map((stops, i) => (i === dayIndex ? [...stops, stop] : stops));

test("health endpoint names the app", async () => {
  const { status, data } = await new Client().get("/healthz");
  assert.equal(status, 200);
  assert.deepEqual(data, { ok: true, app: "wayfarer" });
});

test("the page embeds boot data and sets the session cookie", async () => {
  const client = new Client();
  const { status, data } = await client.get("/");
  assert.equal(status, 200);
  assert.match(client.cookie, /^wta_sid=ws-\d{6}$/);
  assert.match(data, /"workspace":"[0-9a-f]{12}"/);
  assert.ok(!data.includes("__WAYFARER_BOOT__"));
});

test("a new session starts from the seed: Coast to Coast, sealed as version 1", async () => {
  const client = new Client();
  const list = await client.get("/api/trips");
  assert.equal(list.status, 200);
  assert.equal(list.data.trips.length, 1);
  assert.deepEqual(
    { name: list.data.trips[0].name, days: list.data.trips[0].days, v: list.data.trips[0].latest_version, n: list.data.trips[0].stop_count },
    { name: "Coast to Coast", days: 3, v: 1, n: 5 });
  const trip = (await client.get("/api/trips/t1")).data;
  assert.deepEqual(trip.latest.days[0].map((s) => s.name), ["Harbour walk", "Fish market", "Lighthouse"]);
  assert.equal(trip.day_labels[0], "Thu 18 Jun 2026");
  assert.equal(trip.shared, false);
});

test("creating a trip lists it, unsealed, with its own days", async () => {
  const client = new Client();
  const created = await client.post("/api/trips", { name: "  Lakes weekend ", start: "2026-06-15", days: 2 });
  assert.equal(created.status, 201);
  assert.equal(created.data.id, "t2");
  assert.equal(created.data.name, "Lakes weekend");
  assert.deepEqual(created.data.day_labels, ["Mon 15 Jun 2026", "Tue 16 Jun 2026"]);
  assert.equal(created.data.latest, null);
  const names = (await client.get("/api/trips")).data.trips.map((trip) => trip.name);
  assert.deepEqual(names, ["Coast to Coast", "Lakes weekend"]);
});

test("trip creation is validated", async () => {
  const client = new Client();
  const cases = [
    [{ name: "", start: "2026-06-15", days: 2 }, "name"],
    [{ name: "A".repeat(81), start: "2026-06-15", days: 2 }, "name"],
    [{ name: "Trip", start: "2026-06-09", days: 2 }, "start"],
    [{ name: "Trip", start: "9999-12-31", days: 2 }, "start"],
    [{ name: "Trip", start: "2026-02-30", days: 2 }, "start"],
    [{ name: "Trip", start: "2026-06-15", days: 0 }, "days"],
    [{ name: "Trip", start: "2026-06-15", days: 6 }, "days"],
  ];
  for (const [body, field] of cases) {
    const reply = await client.post("/api/trips", body);
    assert.equal(reply.status, 422, JSON.stringify(body));
    assert.equal(reply.data.field, field, JSON.stringify(body));
  }
  assert.equal((await client.get("/api/trips")).data.trips.length, 1);
  const notJson = await client.call("POST", "/api/trips", undefined);
  assert.equal(notJson.status, 422);
});

test("sealing an unchanged itinerary creates nothing; an edit creates the next version", async () => {
  const client = new Client();
  const days = await seedDays(client);
  const same = await client.post("/api/trips/t1/seal", { days, next_stop: 6 });
  assert.equal(same.status, 200);
  assert.equal(same.data.created, false);
  assert.equal(same.data.trip.latest.version, 1);

  const edited = plus(days, 0, { id: "s6", name: "Test User Input", length: "30m" });
  const sealed = await client.post("/api/trips/t1/seal", { days: edited, next_stop: 7 });
  assert.equal(sealed.status, 201);
  assert.equal(sealed.data.created, true);
  assert.equal(sealed.data.trip.latest.version, 2);
  assert.equal(sealed.data.trip.latest.stop_count, 6);

  const again = await client.post("/api/trips/t1/seal", { days: edited, next_stop: 7 });
  assert.equal(again.data.created, false);
  assert.equal(again.data.trip.latest.version, 2);
});

test("sealed versions are immutable: version 1 is unchanged after version 2", async () => {
  const client = new Client();
  const days = await seedDays(client);
  const reordered = [[days[0][2], days[0][0], days[0][1]], days[1], days[2]];
  assert.equal((await client.post("/api/trips/t1/seal", { days: reordered })).status, 201);
  const v1 = await client.get("/api/trips/t1/versions/1");
  const v2 = await client.get("/api/trips/t1/versions/2");
  assert.deepEqual(v1.data.days[0].map((s) => s.name), ["Harbour walk", "Fish market", "Lighthouse"]);
  assert.deepEqual(v2.data.days[0].map((s) => s.name), ["Lighthouse", "Harbour walk", "Fish market"]);
  assert.equal((await client.get("/api/trips/t1/versions/3")).status, 404);
});

test("seal payloads are validated", async () => {
  const client = new Client();
  const days = await seedDays(client);
  const bad = [
    { days: days.slice(0, 2) },
    { days: [[], [], []] },
    { days: plus(days, 1, { id: "s1", name: "Duplicate id", length: "1h" }) },
    { days: plus(days, 1, { id: "s9", name: "   ", length: "1h" }) },
    { days: plus(days, 1, { id: "s9", name: "B".repeat(121), length: "1h" }) },
    { days: plus(days, 1, { id: "s9", name: "Pier", length: "forever" }) },
    { days: plus(days, 1, { id: "../x", name: "Pier", length: "1h" }) },
  ];
  for (const body of bad) {
    const reply = await client.post("/api/trips/t1/seal", body);
    assert.equal(reply.status, 422, JSON.stringify(body).slice(0, 120));
  }
  assert.equal((await client.post("/api/trips/t9/seal", { days })).status, 404);
  assert.equal((await client.get("/api/trips/t1")).data.latest.version, 1);
});

test("a share link always shows the latest sealed version, to anyone", async () => {
  const owner = new Client();
  const first = await owner.post("/api/trips/t1/share");
  assert.equal(first.status, 201);
  assert.match(first.data.token, /^[0-9a-f]{16}$/);
  assert.equal(first.data.share.version, 1);
  assert.equal(first.data.share.stop_count, 5);

  const repeat = await owner.post("/api/trips/t1/share");
  assert.equal(repeat.status, 200);
  assert.equal(repeat.data.created, false);
  assert.equal(repeat.data.token, first.data.token);

  const days = await seedDays(owner);
  await owner.post("/api/trips/t1/seal", { days: plus(days, 2, { id: "s6", name: "Night market", length: "2h" }) });

  const stranger = new Client();
  const shared = await stranger.get(`/api/share/${first.data.token}`);
  assert.equal(shared.status, 200);
  assert.equal(shared.data.version, 2);
  assert.equal(shared.data.stop_count, 6);
  assert.deepEqual(shared.data.days[2].stops.map((s) => s.name), ["Sandy bay", "Night market"]);
  assert.equal((await stranger.get("/api/share/0123456789abcdef")).status, 404);
  // The stranger's own planner is untouched.
  assert.equal((await stranger.get("/api/trips/t1")).data.latest.version, 1);
});

test("nothing can be shared before it has been sealed", async () => {
  const client = new Client();
  const trip = (await client.post("/api/trips", { name: "Unsealed", start: "2026-07-01", days: 1 })).data;
  const share = await client.post(`/api/trips/${trip.id}/share`);
  assert.equal(share.status, 409);
  assert.equal((await client.get(`/api/trips/${trip.id}`)).data.shared, false);
});

test("sessions are isolated", async () => {
  const a = new Client();
  const b = new Client();
  const days = await seedDays(a);
  await seedDays(b);
  await a.post("/api/trips/t1/seal", { days: plus(days, 0, { id: "s6", name: "Only in A", length: "1h" }) });
  await a.post("/api/trips", { name: "A's trip", start: "2026-08-01", days: 2 });
  assert.equal((await a.get("/api/trips/t1")).data.latest.version, 2);
  assert.equal((await b.get("/api/trips/t1")).data.latest.version, 1);
  assert.equal((await b.get("/api/trips")).data.trips.length, 1);
  assert.equal((await b.get("/api/trips/t2")).status, 404);
});

test("test hook: committed state changes on a seal, not on reads", async (t) => {
  const client = new Client();
  const first = await client.get("/__wta/state");
  if (first.status === 404) { t.skip("WTA_TEST_HOOKS is off"); return; }
  assert.match(first.data.sha, /^[0-9a-f]{64}$/);
  await client.get("/api/trips");
  await client.post("/api/trips/t1/seal", { days: await seedDays(client) }); // unchanged: no version
  assert.equal((await client.get("/__wta/state")).data.sha, first.data.sha);
  const days = await seedDays(client);
  await client.post("/api/trips/t1/seal", { days: plus(days, 0, { id: "s6", name: "Pier", length: "1h" }) });
  const after = await client.get("/__wta/state");
  assert.notEqual(after.data.sha, first.data.sha);
  assert.equal(after.data.committed.trips[0].versions.length, 2);
  // A fresh session's committed state equals the seed's, whatever other sessions did.
  assert.equal((await new Client().get("/__wta/state")).data.sha, first.data.sha);
});
