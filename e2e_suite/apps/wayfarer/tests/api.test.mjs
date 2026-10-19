// Wayfarer API tests, against a running clean build started with WTA_TEST_HOOKS=1 and its
// MongoDB home:  E2E_BASE_URL=http://127.0.0.1:8080/ node --test "tests/**/*.test.mjs"
// Every client signs in through the form, like a browser, and is its own session.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const BASE = (process.env.E2E_BASE_URL || "http://127.0.0.1:8080/").replace(/\/?$/, "/");
const LOGIN = JSON.parse(readFileSync(new URL("../app.json", import.meta.url), "utf8")).login;
const NOOR = "noor.haddad";
const SAM = "sam.whitlock";
const OMAR = "omar.farouk";
const CAP = Number(process.env.WTA_SESSION_CAP || 32);

/** A browser-like client: one cookie jar, redirects not followed, so each hop is visible. */
class Client {
  constructor() { this.cookie = ""; }

  async call(method, path, body, { form = false } = {}) {
    const headers = {};
    if (this.cookie) { headers.Cookie = this.cookie; }
    let payload;
    if (body !== undefined) {
      headers["Content-Type"] = form ? "application/x-www-form-urlencoded" : "application/json";
      payload = form ? new URLSearchParams(body).toString() : JSON.stringify(body);
    }
    const response = await fetch(new URL(path.replace(/^\//, ""), BASE), { method, headers, body: payload, redirect: "manual" });
    const setCookie = response.headers.get("set-cookie");
    if (setCookie) { this.cookie = setCookie.split(";")[0]; }
    const type = response.headers.get("content-type") || "";
    const data = type.includes("json") ? await response.json() : await response.text();
    return { status: response.status, data, location: response.headers.get("location"), setCookie: Boolean(setCookie) };
  }

  get(path) { return this.call("GET", path); }
  post(path, body) { return this.call("POST", path, body); }
  signIn(username = NOOR, password = LOGIN.password_value, next) {
    return this.call("POST", "/login", { username, password, ...(next ? { next } : {}) }, { form: true });
  }
}

async function signedIn(username = NOOR) {
  const client = new Client();
  const reply = await client.signIn(username);
  assert.equal(reply.status, 303, `${username} signs in`);
  return client;
}

const seedDays = async (client) => (await client.get("/api/trips/t1")).data.latest.days;
const plus = (days, dayIndex, stop) => days.map((stops, i) => (i === dayIndex ? [...stops, stop] : stops));

// -- signing in ---------------------------------------------------------------------------------

test("health, and everything but the sign-in pages and share links sits behind /login", async () => {
  const anonymous = new Client();
  assert.deepEqual((await anonymous.get("/healthz")).data, { ok: true, app: "wayfarer" });
  const page = await anonymous.get("/");
  assert.equal(page.status, 303);
  assert.equal(page.location, "/login?next=%2F");
  assert.equal(page.setCookie, false, "an anonymous request creates nothing");
  assert.deepEqual((await anonymous.get("/api/trips")).data, { ok: false, reason: "signed-out" });
  assert.equal((await anonymous.get("/login")).status, 200);
  assert.equal((await anonymous.get("/login/help")).status, 200);
  const unknownShare = await anonymous.get("/share/0000000000000000");
  assert.equal(unknownShare.status, 200, "a share page is public");
  assert.match(unknownShare.data, /"mode":"share"/);
  assert.deepEqual((await anonymous.get("/__wta/state")).data, { session: null, user: null, committed: null, sha: null });
});

test("a wrong password is refused with a message and no session", async () => {
  for (const [username, password] of [[NOOR, "wrong"], ["Test User Input", LOGIN.password_value], [NOOR, ""]]) {
    const client = new Client();
    const reply = await client.signIn(username, password);
    assert.equal(reply.status, 200);
    assert.equal(reply.setCookie, false);
    assert.match(reply.data, /<p class="problem" id="login-error" role="alert">[^<]+<\/p>/);
  }
});

test("signing in lands on a same-site next; the page embeds who is signed in", async () => {
  const client = new Client();
  const reply = await client.signIn(NOOR, LOGIN.password_value, "/index.html");
  assert.equal(reply.location, "/index.html");
  assert.match(client.cookie, /^wta_sid=ws-\d{6}$/);
  const page = await client.get("/");
  assert.match(page.data, /"user":\{"name":"Noor Haddad","role":"Trip organiser"\}/);
  assert.match(page.data, /"workspace":"[0-9a-f]{12}"/);
  assert.equal((await client.get("/login?next=%2F%2Fevil.example")).location, "/");
  for (const next of ["//evil.example/", "https://evil.example/", "/login"]) {
    assert.equal((await new Client().signIn(NOOR, LOGIN.password_value, next)).location, "/", next);
  }
});

test("signing out keeps the trips; signing in again in the same browser resumes them", async () => {
  const client = await signedIn();
  const days = await seedDays(client);
  await client.post("/api/trips/t1/seal", { days: plus(days, 0, { id: "s6", name: "Pier", length: "1h" }) });
  const before = (await client.get("/__wta/state")).data;
  const out = await client.post("/sign-out");
  assert.equal(out.status, 303);
  assert.equal(out.location, "/login");
  assert.equal((await client.get("/__wta/state")).data.user, null);
  assert.equal((await client.get("/")).status, 303);
  const back = await client.signIn();
  assert.equal(back.setCookie, false, "the same session");
  const after = (await client.get("/__wta/state")).data;
  assert.equal(after.session, before.session);
  assert.equal(after.sha, before.sha);
});

// -- trips and sealed versions ---------------------------------------------------------------

test("a new session starts from the seed: Coast to Coast sealed as version 1, and Sam's trip read-only", async () => {
  const client = await signedIn();
  const list = (await client.get("/api/trips")).data;
  assert.deepEqual(list.trips.map((trip) => [trip.id, trip.name, trip.role, trip.latest_version, trip.stop_count]),
    [["t1", "Coast to Coast", "owner", 1, 5], ["t90", "Fells and lakes", "viewer", 2, 4]]);
  assert.deepEqual(list.regions.map((r) => r.id), ["coast", "highlands", "lakes"]);
  const trip = (await client.get("/api/trips/t1")).data;
  assert.deepEqual(trip.latest.days[0].map((s) => s.name), ["Harbour walk", "Fish market", "Lighthouse"]);
  assert.equal(trip.day_labels[0], "Thu 18 Jun 2026");
  assert.deepEqual(trip.outlook[0], { label: "Thu 18 Jun 2026", sky: "sun", high_c: 17 });
  assert.deepEqual(trip.packing, { packed: 1, total: 6 });
  assert.equal(trip.can_edit, true);
  assert.equal(trip.shared, false);
});

test("creating a trip lists it, unsealed, with its own days, region and number", async () => {
  const client = await signedIn();
  const created = await client.post("/api/trips", { name: "  Lakes weekend ", start: "2026-06-15", days: 2, region: "lakes" });
  assert.equal(created.status, 201);
  assert.equal(created.data.id, "t2", "the seeded t90 is outside the new-trip counter");
  assert.equal(created.data.name, "Lakes weekend");
  assert.equal(created.data.region, "Lake District");
  assert.deepEqual(created.data.day_labels, ["Mon 15 Jun 2026", "Tue 16 Jun 2026"]);
  assert.equal(created.data.latest, null);
  const ids = (await client.get("/api/trips")).data.trips.map((trip) => trip.id);
  assert.deepEqual(ids, ["t1", "t2", "t90"]);
});

test("trip creation is validated", async () => {
  const client = await signedIn();
  const cases = [
    [{ name: "", start: "2026-06-15", days: 2, region: "coast" }, "name"],
    [{ name: "A".repeat(81), start: "2026-06-15", days: 2, region: "coast" }, "name"],
    [{ name: "Trip", start: "2026-06-09", days: 2, region: "coast" }, "start"],
    [{ name: "Trip", start: "9999-12-31", days: 2, region: "coast" }, "start"],
    [{ name: "Trip", start: "2026-02-30", days: 2, region: "coast" }, "start"],
    [{ name: "Trip", start: "2026-06-15", days: 0, region: "coast" }, "days"],
    [{ name: "Trip", start: "2026-06-15", days: 6, region: "coast" }, "days"],
    [{ name: "Trip", start: "2026-06-15", days: 2, region: "atlantis" }, "region"],
  ];
  for (const [body, field] of cases) {
    const reply = await client.post("/api/trips", body);
    assert.equal(reply.status, 422, JSON.stringify(body));
    assert.equal(reply.data.field, field, JSON.stringify(body));
  }
  assert.equal((await client.get("/api/trips")).data.trips.length, 2);
});

test("sealing an unchanged itinerary creates nothing; an edit creates the next version", async () => {
  const client = await signedIn();
  const days = await seedDays(client);
  const same = await client.post("/api/trips/t1/seal", { days, next_stop: 6 });
  assert.equal(same.status, 200);
  assert.equal(same.data.created, false);
  const edited = plus(days, 0, { id: "s6", name: "Test User Input", length: "30m", kind: "other" });
  const sealed = await client.post("/api/trips/t1/seal", { days: edited, next_stop: 7 });
  assert.equal(sealed.status, 201);
  assert.equal(sealed.data.trip.latest.version, 2);
  assert.equal(sealed.data.trip.latest.stop_count, 6);
  assert.equal((await client.post("/api/trips/t1/seal", { days: edited, next_stop: 7 })).data.created, false);
  const history = (await client.get("/api/trips/t1/versions")).data.versions;
  assert.deepEqual(history.map((v) => [v.version, v.stop_count, v.sealed_by]), [[2, 6, "Noor Haddad"], [1, 5, "Noor Haddad"]]);
});

test("sealed versions are immutable: version 1 is unchanged after version 2", async () => {
  const client = await signedIn();
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
  const client = await signedIn();
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
    assert.equal((await client.post("/api/trips/t1/seal", body)).status, 422, JSON.stringify(body).slice(0, 120));
  }
  assert.equal((await client.post("/api/trips/t9/seal", { days })).status, 404);
  assert.equal((await client.get("/api/trips/t1")).data.latest.version, 1);
});

test("an owner edits a trip's details and deletes a trip with everything in it", async () => {
  const client = await signedIn();
  const edited = await client.post("/api/trips/t1", { name: "Coast walk", start: "2026-06-20", region: "highlands", days: 5 });
  assert.equal(edited.status, 200);
  assert.equal(edited.data.name, "Coast walk");
  assert.equal(edited.data.days, 3, "a trip keeps its length: its versions have that many days");
  assert.equal(edited.data.region, "Western Highlands");
  assert.equal((await client.post("/api/trips/t1", { name: "", start: "2026-06-20", region: "coast" })).status, 422);
  const trip = (await client.post("/api/trips", { name: "Short", start: "2026-07-01", days: 1, region: "coast" })).data;
  await client.post(`/api/trips/${trip.id}/seal`, { days: [[{ id: "s1", name: "Pier", length: "1h" }]] });
  await client.post(`/api/trips/${trip.id}/packing`, { text: "Hat" });
  const { token } = (await client.post(`/api/trips/${trip.id}/share`)).data;
  assert.deepEqual((await client.post(`/api/trips/${trip.id}/delete`)).data, { ok: true, deleted: trip.id });
  assert.equal((await client.get(`/api/trips/${trip.id}`)).status, 404);
  assert.equal((await new Client().get(`/api/share/${token}`)).data.reason, "not-found", "its share link is withdrawn");
  const state = (await client.get("/__wta/state")).data.committed;
  assert.ok(!state.versions.some((v) => v.trip === trip.id) && !state.packing.some((item) => item[0] === trip.id));
});

// -- share links -----------------------------------------------------------------------------

test("a share link always shows the latest sealed version, to anyone, signed in or not", async () => {
  const owner = await signedIn();
  const first = await owner.post("/api/trips/t1/share");
  assert.equal(first.status, 201);
  assert.match(first.data.token, /^[0-9a-f]{16}$/);
  assert.equal(first.data.share.stop_count, 5);
  const repeat = await owner.post("/api/trips/t1/share");
  assert.equal(repeat.data.created, false);
  assert.equal(repeat.data.token, first.data.token);
  const days = await seedDays(owner);
  await owner.post("/api/trips/t1/seal", { days: plus(days, 2, { id: "s6", name: "Night market", length: "2h" }) });
  const stranger = new Client();
  const shared = await stranger.get(`/api/share/${first.data.token}`);
  assert.equal(shared.status, 200);
  assert.equal(shared.data.version, 2);
  assert.deepEqual(shared.data.days[2].stops.map((s) => s.name), ["Sandy bay", "Night market"]);
  assert.equal((await stranger.get(`/share/${first.data.token}`)).status, 200);
  assert.deepEqual((await stranger.get("/api/share/0123456789abcdef")).data.reason, "not-found");
});

test("nothing can be shared before it has been sealed", async () => {
  const client = await signedIn();
  const trip = (await client.post("/api/trips", { name: "Unsealed", start: "2026-07-01", days: 1, region: "coast" })).data;
  assert.equal((await client.post(`/api/trips/${trip.id}/share`)).status, 409);
  assert.equal((await client.get(`/api/trips/${trip.id}`)).data.shared, false);
});

// -- roles ---------------------------------------------------------------------------------------

test("a viewer reads a shared trip and changes nothing; a stranger cannot open it", async () => {
  const noor = await signedIn();
  const before = (await noor.get("/__wta/state")).data.sha;
  const t90 = (await noor.get("/api/trips/t90")).data;
  assert.equal(t90.role, "viewer");
  assert.equal(t90.can_edit, false);
  assert.equal(t90.owner, "Sam Whitlock");
  const forbidden = { ok: false, reason: "forbidden" };
  const attempts = [
    ["/api/trips/t90/seal", { days: t90.latest.days }], ["/api/trips/t90/share", {}],
    ["/api/trips/t90", { name: "Mine", start: "2026-07-04", region: "lakes" }], ["/api/trips/t90/delete", {}],
    ["/api/trips/t90/packing", { text: "Hat" }], ["/api/trips/t90/packing/k8", { packed: true }], ["/api/trips/t90/packing/k8/delete", {}],
    ["/api/trips/t90/packing/bulk", { ids: ["k8"], action: "pack" }],
  ];
  for (const [path, body] of attempts) {
    const reply = await noor.post(path, body);
    assert.equal(reply.status, 200, `${path}: never 403`);
    assert.deepEqual({ ok: reply.data.ok, reason: reply.data.reason }, forbidden, path);
  }
  assert.equal((await noor.get("/__wta/state")).data.sha, before);
  const omar = await signedIn(OMAR);
  assert.deepEqual((await omar.get("/api/trips")).data.trips, []);
  assert.equal((await omar.get("/api/trips/t1")).data.reason, "forbidden");
  assert.equal((await omar.get("/api/trips/t1/packing")).data.reason, "forbidden");
  const sam = await signedIn(SAM);
  assert.equal((await sam.get("/api/trips/t90")).data.can_edit, true);
});

// -- packing lists --------------------------------------------------------------------------------

test("a packing list: add, rename, tick, remove, and bulk actions on a selection", async () => {
  const client = await signedIn();
  const list = (await client.get("/api/trips/t1/packing")).data;
  assert.deepEqual(list.items.map((item) => [item.id, item.packed]),
    [["k1", true], ["k2", false], ["k3", false], ["k4", false], ["k5", false], ["k6", false]]);
  assert.equal(list.can_edit, true);
  const added = (await client.post("/api/trips/t1/packing", { text: "  Test User Input " })).data;
  assert.equal(added.items.at(-1).text, "Test User Input");
  assert.equal(added.items.at(-1).id, "k10");
  assert.equal((await client.post("/api/trips/t1/packing", { text: " " })).status, 422);
  assert.equal((await client.post("/api/trips/t1/packing", { text: "A".repeat(81) })).status, 422);
  assert.equal((await client.post("/api/trips/t1/packing/k3", { text: "Paper map" })).data.items[2].text, "Paper map");
  const ticked = (await client.post("/api/trips/t1/packing/k2", { packed: true })).data;
  assert.deepEqual([ticked.packed, ticked.total], [2, 7]);
  assert.equal((await client.post("/api/trips/t1/packing/k2", { packed: "yes" })).status, 400);
  const bulk = (await client.post("/api/trips/t1/packing/bulk", { ids: ["k4", "k5", "k8"], action: "pack" })).data;
  assert.equal(bulk.packed, 4, "k8 is another trip's item and is left alone");
  const removed = (await client.post("/api/trips/t1/packing/bulk", { ids: ["k1", "k2"], action: "delete" })).data;
  assert.deepEqual(removed.items.map((item) => item.id), ["k3", "k4", "k5", "k6", "k10"]);
  assert.equal((await client.post("/api/trips/t1/packing/k6/delete")).data.total, 4);
  const again = (await client.post("/api/trips/t1/packing/k6/delete")).data;
  assert.deepEqual([again.gone, again.total], [true, 4], "removing it twice answers the list, not an error");
  assert.equal((await client.post("/api/trips/t1/packing/k8", { packed: true })).data.gone, true,
    "another trip's item is not on this list");
  assert.equal((await client.post("/api/trips/t1/packing/bulk", { ids: [], action: "pack" })).status, 400);
  assert.deepEqual((await client.get("/api/trips/t1")).data.packing, { packed: 2, total: 4 });
});

// -- the places catalogue ---------------------------------------------------------------------------

test("the catalogue pages by eight, filters by kind, searches literally and sorts with a tie-break", async () => {
  const client = await signedIn();
  const first = (await client.get("/api/places")).data;
  assert.deepEqual([first.total, first.page, first.pages, first.items.length], [24, 1, 3, 8]);
  assert.equal(first.items[0].name, "Aira Force");
  const third = (await client.get("/api/places?page=3")).data;
  assert.equal(third.items.length, 8);
  const all = [...first.items, ...(await client.get("/api/places?page=2")).data.items, ...third.items].map((p) => p.id);
  assert.equal(new Set(all).size, 24, "pages neither repeat nor skip a place");
  const food = (await client.get("/api/places?kind=food")).data;
  assert.deepEqual([food.total, food.pages], [5, 1]);
  assert.ok(food.items.every((place) => place.kind === "food"));
  assert.deepEqual((await client.get("/api/places?kind=food&page=2")).data.items, [], "a page past the last is empty");
  assert.deepEqual((await client.get("/api/places?q=pier")).data.items.map((p) => p.name), ["Saltburn pier", "Seafood shack"]);
  assert.equal((await client.get("/api/places?q=.*")).data.total, 0, "search text is not a pattern");
  assert.equal((await client.get("/api/places?sort=name-desc")).data.items[0].name, "Whitby Abbey steps");
  assert.equal((await client.get("/api/places?sort=region")).data.items[0].name, "Boggle Hole");
  const place = (await client.get("/api/places/p02")).data;
  assert.equal(place.place.name, "Kipper smokehouse");
  assert.deepEqual(place.trips.map((trip) => trip.id), ["t1"], "only trips you organise can take a place");
  assert.equal((await client.get("/api/places/p99")).status, 404);
});

// -- sessions and state ------------------------------------------------------------------------

test("sessions are isolated, and the state hook covers business data only", async () => {
  const a = await signedIn();
  const b = await signedIn();
  const days = await seedDays(a);
  await a.post("/api/trips/t1/seal", { days: plus(days, 0, { id: "s6", name: "Only in A", length: "1h" }) });
  assert.equal((await b.get("/api/trips/t1")).data.latest.version, 1);
  const state = (await b.get("/__wta/state")).data;
  assert.match(state.sha, /^[0-9a-f]{64}$/);
  assert.match(state.session, /^ws-\d{6}$/);
  assert.equal(state.user, NOOR);
  assert.ok(!/salt|hash|token|_s\d{6}/.test(JSON.stringify(state.committed)));
  assert.equal((await (await signedIn(SAM)).get("/__wta/state")).data.sha, state.sha, "every user starts from one seed");
});

test("one session's requests run one at a time", async () => {
  const client = await signedIn();
  const days = await seedDays(client);
  const edited = plus(days, 0, { id: "s6", name: "Pier", length: "1h" });
  const replies = await Promise.all([1, 2, 3, 4].map(() => client.post("/api/trips/t1/seal", { days: edited })));
  assert.equal(replies.filter((reply) => reply.data.created).length, 1, "the same seal sent four times creates one version");
  assert.equal((await client.get("/api/trips/t1")).data.latest.version, 2);
});

test(`beyond WTA_SESSION_CAP (${CAP}) the least recently used session is signed out`, async () => {
  const first = await signedIn();
  const seed = (await first.get("/__wta/state")).data;
  for (let i = 0; i < CAP; i += 1) { await signedIn(); }
  assert.equal((await first.get("/__wta/state")).data.session, null);
  assert.equal((await first.get("/")).status, 303, "simply signed out");
  const back = await first.signIn();
  assert.ok(back.setCookie, "a new session");
  const now = (await first.get("/__wta/state")).data;
  assert.notEqual(now.session, seed.session);
  assert.equal(now.sha, seed.sha);
});
