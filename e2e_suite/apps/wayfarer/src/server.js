/*
 * Wayfarer — trip itinerary builder. Node 22 built-ins plus the suite's vendored MongoDB driver.
 *
 * The browser owns the itinerary being edited (a draft in localStorage). The server owns what
 * has been agreed: trips, sealed itinerary versions and share links, in a MongoDB database of
 * the signed-in browser's own (partitions.js). Sealed versions are write-once documents.
 *
 * Signing in is required for everything but the sign-in pages, the static assets and share
 * links: a share link (/share/<token>) shows the trip's latest sealed version to anyone.
 * Pages of a signed-out browser redirect to /login (303); the API answers
 * 200 {ok: false, reason: "signed-out"}. A trip not shared with you, or a change only its
 * owner may make, answers 200 {ok: false, reason: "forbidden"}. Nothing answers 401 or 403.
 */
"use strict";

const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { Partitions, readSeed, isSid } = require("./partitions.js");
const domain = require("./itinerary.js");
const signin = require("./signin.js");
const { STOP_LENGTHS, STOP_KINDS, LIMITS } = require("./collections.js");

const { HttpError } = domain;
const APP_ID = "wayfarer";
const APP_ROOT = path.resolve(__dirname, "..");
const STATIC_DIR = path.join(__dirname, "static");
const SEED = readSeed(path.join(APP_ROOT, "seed"));
const VOCAB_PROFILE = process.env.WTA_VOCAB === "alt" ? "alt" : "default";
const VOCAB = JSON.parse(fs.readFileSync(path.join(APP_ROOT, "vocab", `${VOCAB_PROFILE}.json`), "utf8"));
const FIXED_NOW = process.env.WTA_FIXED_NOW || "2026-06-10T09:00:00"; // wta-clock
const TODAY = FIXED_NOW.slice(0, 10);
const SEALED_AT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(FIXED_NOW) ? `${FIXED_NOW.slice(0, 16)}:00` : `${TODAY}T00:00:00`;
const TEST_HOOKS = process.env.WTA_TEST_HOOKS === "1";
const BODY_LIMIT = 256 * 1024;
const ACCOUNTS = new Map(SEED.users.map((user) => [user.username, user]));
const PEOPLE = new Map(SEED.users.map((user) => [user._id, user]));
const ORGANISERS = new Set(SEED.trips.map((trip) => trip.owner_id));

function stop(message) {
  console.error(`wayfarer: ${message}`);
  process.exit(2);
}

const MONGODB_URL = process.env.MONGODB_URL || stop("MONGODB_URL is required: it names the app's home database.");
const CAP_TEXT = process.env.WTA_SESSION_CAP || "32";
const SESSION_CAP = /^[1-9][0-9]*$/.test(CAP_TEXT) ? Number(CAP_TEXT) : stop("WTA_SESSION_CAP must be a positive whole number.");

const t = (key) => (Object.prototype.hasOwnProperty.call(VOCAB, key) ? VOCAB[key] : key);
const forbidden = () => [200, { ok: false, reason: "forbidden", error: t("msg.forbidden") }];

let partitions = null; // connected in main()

// -- reading one browser's data -----------------------------------------------------------------

async function tripIn(db, tripId) {
  const trip = await db.collection("trips").findOne({ _id: tripId });
  if (!trip) { throw new HttpError(404, "No such trip."); }
  return trip;
}

const latestOf = (db, trip) => (trip.latest ? db.collection("versions").findOne({ _id: `${trip._id}:v${trip.latest}` }) : null);

async function namesIn(db) {
  const users = await db.collection("users").find({}, { projection: { name: 1 } }).toArray();
  return new Map(users.map((user) => [user._id, user.name]));
}

const packingOf = (db, tripId) => db.collection("packing").find({ trip_id: tripId }).sort({ n: 1 }).toArray();
const regionList = (db) => db.collection("regions").find({}, { projection: { name: 1 } }).sort({ _id: 1 }).toArray();

/** A trip as the planner shows it, with the region choices its settings offer. */
async function viewOf(db, trip, viewer) {
  const [latest, region, people, packing, regions] = await Promise.all([
    latestOf(db, trip), db.collection("regions").findOne({ _id: trip.region_id }), namesIn(db),
    packingOf(db, trip._id), regionList(db),
  ]);
  return { ...domain.tripView(trip, latest, region, people, viewer, packing),
    regions: regions.map((r) => ({ id: r._id, name: r.name })) };
}

/** The trip `tripId` if `user` may read it (`need` "viewer") or change it (`need` "owner"). */
async function tripFor(db, user, tripId, need) {
  const trip = await tripIn(db, tripId);
  const role = domain.roleOn(trip, user._id);
  return role && (need === "viewer" || role === "owner") ? trip : null;
}

// -- the API (signed in) ----------------------------------------------------------------------------

async function listTrips({ db, user }) {
  const trips = await db.collection("trips").find({ "members.user_id": user._id }).sort({ _id: 1 }).toArray();
  const versionIds = trips.filter((trip) => trip.latest).map((trip) => `${trip._id}:v${trip.latest}`);
  const [versions, regions, people, packing] = await Promise.all([
    db.collection("versions").find({ _id: { $in: versionIds } }).toArray(),
    regionList(db),
    namesIn(db),
    db.collection("packing").find({ trip_id: { $in: trips.map((trip) => trip._id) } }).toArray(),
  ]);
  const latest = new Map(versions.map((version) => [version.trip_id, version]));
  const itemsOf = (tripId) => packing.filter((item) => item.trip_id === tripId);
  return [200, {
    trips: trips.map((trip) => domain.tripSummary(trip, latest.get(trip._id), people, user._id, itemsOf(trip._id))),
    regions: regions.map((region) => ({ id: region._id, name: region.name })),
  }];
}

async function createTrip({ db, user, body }) {
  const regionIds = (await db.collection("regions").find({}, { projection: { _id: 1 } }).toArray()).map((r) => r._id);
  const fields = domain.validateTrip(body, regionIds, TODAY);
  const counters = await db.collection("counters").findOneAndUpdate({ _id: "seq" }, { $inc: { next_trip: 1 } });
  const trip = {
    _id: `t${counters.next_trip}`, owner_id: user._id, region_id: fields.region, name: fields.name,
    start: fields.start, days: fields.days, members: [{ user_id: user._id, role: "owner" }],
    latest: null, share: null,
  };
  await db.collection("trips").insertOne(trip);
  return [201, await viewOf(db, trip, user._id)];
}

async function showTrip({ db, user }, tripId) {
  const trip = await tripIn(db, tripId);
  if (!domain.roleOn(trip, user._id)) { return forbidden(); }
  return [200, await viewOf(db, trip, user._id)];
}

async function showVersion({ db, user }, tripId, number) {
  const trip = await tripIn(db, tripId);
  if (!domain.roleOn(trip, user._id)) { return forbidden(); }
  const version = await db.collection("versions").findOne({ trip_id: tripId, n: Number(number) });
  if (!version) { throw new HttpError(404, "No such version."); }
  const days = version.days.map((day) => day.stops);
  return [200, { version: version.n, next_stop: version.next_stop, days, latest: trip.latest,
    stop_count: days.reduce((total, stops) => total + stops.length, 0), trip: await viewOf(db, trip, user._id) }];
}

async function seal({ db, user, body }, tripId) {
  const trip = await tripIn(db, tripId);
  if (domain.roleOn(trip, user._id) !== "owner") { return forbidden(); }
  const { days, nextStop } = domain.validateItinerary(trip, body);
  const latest = await latestOf(db, trip);
  if (domain.sameAsLatest(latest, days)) {
    return [200, { created: false, trip: await viewOf(db, trip, user._id) }]; // nothing new to seal
  }
  const version = domain.nextVersion(trip, latest, days, nextStop, { by: user._id, at: SEALED_AT });
  await db.collection("versions").insertOne(version); // a sealed version is never rewritten
  await db.collection("trips").updateOne({ _id: trip._id }, { $set: { latest: version.n } });
  trip.latest = version.n;
  return [201, { created: true, trip: await viewOf(db, trip, user._id) }];
}

async function share({ db, user, sid }, tripId) {
  const trip = await tripIn(db, tripId);
  if (domain.roleOn(trip, user._id) !== "owner") { return forbidden(); }
  const latest = await latestOf(db, trip);
  if (!latest) { throw new HttpError(409, "Seal the itinerary before sharing it."); }
  let created = false;
  if (!trip.share) {
    // Derived, not random (the suite is deterministic): an identifier, not a secret.
    const token = crypto.createHash("sha256").update(`wayfarer-share:${sid}:${trip._id}`).digest("hex").slice(0, 16);
    const counters = await db.collection("counters").findOneAndUpdate({ _id: "seq" }, { $inc: { shares: 1 } },
      { returnDocument: "after" });
    trip.share = { token, issued_seq: counters.shares };
    await db.collection("trips").updateOne({ _id: trip._id }, { $set: { share: trip.share } });
    await partitions.publish(token, sid, trip._id);
    created = true;
  }
  return [created ? 201 : 200, { created, token: trip.share.token, share: domain.shareView(trip.share.token, trip, latest) }];
}

async function updateTrip({ db, user, body }, tripId) {
  const trip = await tripFor(db, user, tripId, "owner");
  if (!trip) { return forbidden(); }
  const regionIds = (await regionList(db)).map((r) => r._id);
  const fields = domain.validateTrip(body, regionIds, TODAY, trip.days);
  const changes = { name: fields.name, start: fields.start, region_id: fields.region };
  await db.collection("trips").updateOne({ _id: trip._id }, { $set: changes });
  return [200, await viewOf(db, { ...trip, ...changes }, user._id)];
}

async function deleteTrip({ db, user }, tripId) {
  const trip = await tripFor(db, user, tripId, "owner");
  if (!trip) { return forbidden(); }
  await db.collection("versions").deleteMany({ trip_id: trip._id });
  await db.collection("packing").deleteMany({ trip_id: trip._id });
  await db.collection("trips").deleteOne({ _id: trip._id });
  if (trip.share) { await partitions.unpublish(trip.share.token); }
  return [200, { ok: true, deleted: trip._id }];
}

async function listVersions({ db, user }, tripId) {
  const trip = await tripFor(db, user, tripId, "viewer");
  if (!trip) { return forbidden(); }
  const [versions, people] = await Promise.all([
    db.collection("versions").find({ trip_id: trip._id }).toArray(), namesIn(db),
  ]);
  return [200, { trip: await viewOf(db, trip, user._id), versions: domain.versionList(versions, people) }];
}

// -- packing lists (the trip's owner changes them; viewers read) ------------------------------------

async function packingReply(db, trip, user) {
  const items = await packingOf(db, trip._id);
  const canEdit = domain.roleOn(trip, user._id) === "owner";
  return [200, { trip: await viewOf(db, trip, user._id), ...domain.packingView(items, canEdit) }];
}

async function showPacking({ db, user }, tripId) {
  const trip = await tripFor(db, user, tripId, "viewer");
  return trip ? packingReply(db, trip, user) : forbidden();
}

async function addItem({ db, user, body }, tripId) {
  const trip = await tripFor(db, user, tripId, "owner");
  if (!trip) { return forbidden(); }
  const text = domain.validateItemText(body);
  const [counters, last] = await Promise.all([
    db.collection("counters").findOneAndUpdate({ _id: "seq" }, { $inc: { next_item: 1 } }),
    db.collection("packing").find({ trip_id: trip._id }).sort({ n: -1 }).limit(1).toArray(),
  ]);
  await db.collection("packing").insertOne({ _id: `k${counters.next_item}`, trip_id: trip._id,
    n: last.length ? last[0].n + 1 : 1, text, packed: false, added_by: user._id });
  return packingReply(db, trip, user);
}

/** Item `itemId` of trip `tripId`'s list, when `user` owns the trip. An item that is no longer
 *  there (removed by an earlier click on a list the page had not redrawn yet) is not an error:
 *  the answer is the list as it now stands, marked `gone`. */
async function itemOn(db, user, tripId, itemId) {
  const trip = await tripFor(db, user, tripId, "owner");
  const item = trip && await db.collection("packing").findOne({ _id: itemId, trip_id: trip._id });
  return { trip, item };
}

async function goneReply(db, trip, user) {
  const [status, payload] = await packingReply(db, trip, user);
  return [status, { ...payload, gone: true }];
}

async function updateItem({ db, user, body }, tripId, itemId) {
  const { item, trip } = await itemOn(db, user, tripId, itemId);
  if (!trip) { return forbidden(); }
  if (!item) { return goneReply(db, trip, user); }
  const changes = {};
  if (body.text !== undefined) { changes.text = domain.validateItemText(body); }
  if (body.packed !== undefined) {
    if (typeof body.packed !== "boolean") { throw new HttpError(400, "Say whether the item is packed."); }
    changes.packed = body.packed;
  }
  if (Object.keys(changes).length) { // an empty $set would be legal, but there is nothing to write
    await db.collection("packing").updateOne({ _id: item._id }, { $set: changes });
  }
  return packingReply(db, trip, user);
}

async function deleteItem({ db, user }, tripId, itemId) {
  const { item, trip } = await itemOn(db, user, tripId, itemId);
  if (!trip) { return forbidden(); }
  if (!item) { return goneReply(db, trip, user); }
  await db.collection("packing").deleteOne({ _id: item._id });
  return packingReply(db, trip, user);
}

const BULK_UPDATES = { pack: { $set: { packed: true } }, unpack: { $set: { packed: false } } };

async function bulkItems({ db, user, body }, tripId) {
  const trip = await tripFor(db, user, tripId, "owner");
  if (!trip) { return forbidden(); }
  const ids = Array.isArray(body.ids) ? body.ids.filter((id) => /^k\d+$/.test(String(id))) : [];
  if (!ids.length || !(body.action === "delete" || BULK_UPDATES[body.action])) {
    throw new HttpError(400, "Choose items and what to do with them.");
  }
  const selection = { trip_id: trip._id, _id: { $in: ids } }; // never another trip's items
  if (body.action === "delete") {
    await db.collection("packing").deleteMany(selection);
  } else {
    await db.collection("packing").updateMany(selection, BULK_UPDATES[body.action]);
  }
  return packingReply(db, trip, user);
}

// -- the places catalogue (read-only, paged on the server) -----------------------------------------

async function listPlaces({ db }, url) {
  const query = domain.placeQuery(url.searchParams);
  const places = db.collection("places");
  const [total, items, regions] = await Promise.all([
    places.countDocuments(query.filter),
    places.find(query.filter).sort(query.sort).skip((query.page - 1) * domain.PLACES_PER_PAGE)
      .limit(domain.PLACES_PER_PAGE).toArray(),
    regionList(db),
  ]);
  return [200, domain.placesPage(query, total, items, new Map(regions.map((r) => [r._id, r.name])))];
}

async function showPlace({ db, user }, placeId) {
  const place = await db.collection("places").findOne({ _id: placeId });
  if (!place) { throw new HttpError(404, "No such place."); }
  const [regions, trips] = await Promise.all([
    regionList(db),
    db.collection("trips").find({ members: { $elemMatch: { user_id: user._id, role: "owner" } } })
      .sort({ _id: 1 }).toArray(),
  ]);
  const owned = await Promise.all(trips.map(async (trip) => {
    const latest = await latestOf(db, trip);
    return { id: trip._id, name: trip.name, days: trip.days, latest: latest && {
      version: latest.n, next_stop: latest.next_stop, days: latest.days.map((day) => day.stops) } };
  }));
  return [200, { place: domain.placeView(place, new Map(regions.map((r) => [r._id, r.name]))), trips: owned }];
}

/** Routes of the signed-in API: [method, pattern, handler(context, ...captures)]. */
const ROUTES = [
  ["GET", /^\/api\/trips$/, listTrips],
  ["POST", /^\/api\/trips$/, createTrip],
  ["GET", /^\/api\/trips\/(t\d+)$/, showTrip],
  ["POST", /^\/api\/trips\/(t\d+)$/, updateTrip],
  ["POST", /^\/api\/trips\/(t\d+)\/delete$/, deleteTrip],
  ["GET", /^\/api\/trips\/(t\d+)\/versions$/, listVersions],
  ["GET", /^\/api\/trips\/(t\d+)\/versions\/(\d+)$/, showVersion],
  ["POST", /^\/api\/trips\/(t\d+)\/seal$/, seal],
  ["POST", /^\/api\/trips\/(t\d+)\/share$/, share],
  ["GET", /^\/api\/trips\/(t\d+)\/packing$/, showPacking],
  ["POST", /^\/api\/trips\/(t\d+)\/packing$/, addItem],
  ["POST", /^\/api\/trips\/(t\d+)\/packing\/bulk$/, bulkItems],
  ["POST", /^\/api\/trips\/(t\d+)\/packing\/(k\d+)$/, updateItem],
  ["POST", /^\/api\/trips\/(t\d+)\/packing\/(k\d+)\/delete$/, deleteItem],
  ["GET", /^\/api\/places$/, listPlaces],
  ["GET", /^\/api\/places\/(p\d+)$/, showPlace],
];

/** A share link, for anyone: read in the owning session's lane, so it never races a seal. */
async function publicShare(token) {
  const link = await partitions.resolve(token);
  const gone = { ok: false, reason: "not-found", error: t("msg.missing_share") };
  if (!link) { return gone; }
  return partitions.lane(link.sid, async () => {
    const db = partitions.database(link);
    const trip = await db.collection("trips").findOne({ _id: link.trip_id });
    const latest = trip && await latestOf(db, trip);
    return latest ? domain.shareView(token, trip, latest) : gone;
  });
}

// -- the committed-state hook ----------------------------------------------------------------------

function canonical(value) {
  if (Array.isArray(value)) { return `[${value.map(canonical).join(",")}]`; }
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((k) => `${JSON.stringify(k)}:${canonical(value[k])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

async function testState(row, user) {
  if (!row) { return { session: null, user: null, committed: null, sha: null }; }
  const db = partitions.database(row);
  const [trips, versions, packing] = await Promise.all([
    db.collection("trips").find({}).sort({ _id: 1 }).toArray(),
    db.collection("versions").find({}).sort({ trip_id: 1, n: 1 }).toArray(),
    db.collection("packing").find({}).sort({ trip_id: 1, n: 1 }).toArray(),
  ]);
  const committed = domain.committedState(trips, versions, packing);
  const sha = crypto.createHash("sha256").update(canonical(committed)).digest("hex");
  return { session: row._id, user: user ? user.username : null, committed, sha };
}

// -- HTTP ------------------------------------------------------------------------------------------

const ASSETS = { "/app.js": ["app.js", "text/javascript; charset=utf-8"], "/styles.css": ["styles.css", "text/css; charset=utf-8"] };

function send(response, status, body, type, headers = {}) {
  response.writeHead(status, { "Content-Type": type, "Content-Length": Buffer.byteLength(body), "Cache-Control": "no-store", ...headers });
  response.end(body);
}

const sendJson = (response, status, payload) => send(response, status, JSON.stringify(payload), "application/json; charset=utf-8");
const sendPage = (response, html) => send(response, 200, html, "text/html; charset=utf-8");
const redirect = (response, where, headers = {}) => send(response, 303, "", "text/plain; charset=utf-8", { Location: where, ...headers });

function cookieOf(request) {
  for (const part of String(request.headers.cookie || "").split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === "wta_sid") { return rest.join("="); }
  }
  return "";
}

function readBody(request) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    request.on("data", (chunk) => {
      size += chunk.length;
      if (size > BODY_LIMIT) {
        reject(new HttpError(413, "Request body too large."));
        request.destroy();
        return;
      }
      chunks.push(chunk);
    });
    request.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    request.on("error", reject);
  });
}

function jsonBody(text) {
  if (!text) { return {}; }
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    throw new HttpError(400, "Request body is not JSON.");
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    throw new HttpError(400, "Request body must be a JSON object.");
  }
  return data;
}

/** The planner's page, with what it needs before its first render embedded (no extra round trip). */
function shell(sid, user, extra) {
  const boot = {
    vocab: VOCAB,
    today: TODAY,
    lengths: STOP_LENGTHS,
    kinds: STOP_KINDS,
    limits: { tripName: LIMITS.tripName, stopName: LIMITS.stopName, itemText: LIMITS.itemText, minDays: LIMITS.minDays, maxDays: LIMITS.maxDays, horizonDays: 730 },
    // Namespaces this browser's local drafts, so two sessions never read each other's.
    workspace: sid ? crypto.createHash("sha256").update(`wayfarer-workspace:${sid}`).digest("hex").slice(0, 12) : "public",
    user: user && { name: user.name, role: t(ORGANISERS.has(user._id) ? "role.organiser" : "role.traveller") },
    ...extra,
  };
  const page = fs.readFileSync(path.join(STATIC_DIR, "index.html"), "utf8");
  return page.replace("__WAYFARER_BOOT__", () => JSON.stringify(boot).replace(/</g, "\\u003c"));
}

async function signInPost(response, sid, text) {
  const form = new URLSearchParams(text);
  const next = signin.destination(form.get("next"));
  const account = await signin.check(ACCOUNTS.get((form.get("username") || "").trim()), form.get("password"));
  if (!account) { return sendPage(response, signin.signInPage(t, { next, refusal: t("msg.sign_in_refused") })); }
  const { sid: kept, created } = await partitions.signIn(sid, account._id);
  return redirect(response, next, created ? { "Set-Cookie": `wta_sid=${kept}; Path=/; HttpOnly; SameSite=Lax` } : {});
}

/** One request of one browser (run in that browser's lane when it has a session cookie). */
async function respond(request, response, url, sid, text) {
  const row = await partitions.find(sid);
  const user = row && row.user_id ? PEOPLE.get(row.user_id) : null;
  const { pathname } = url;
  const method = request.method;
  if (pathname === "/login") {
    if (method === "POST") { return signInPost(response, sid, text); }
    const next = signin.destination(url.searchParams.get("next"));
    return user ? redirect(response, next) : sendPage(response, signin.signInPage(t, { next }));
  }
  if (method === "GET" && pathname === "/login/help") { return sendPage(response, signin.helpPage(t)); }
  if (method === "POST" && pathname === "/sign-out") {
    if (row) { await partitions.signOut(row._id); } // the trips stay: signing in again resumes them
    return redirect(response, "/login");
  }
  if (method === "GET" && pathname === "/__wta/state" && TEST_HOOKS) { return sendJson(response, 200, await testState(row, user)); }
  const shared = /^\/share\/([0-9a-f]{16})$/.exec(pathname);
  if (method === "GET" && shared) { return sendPage(response, shell(row && row._id, user, { mode: "share", token: shared[1] })); }
  if (method === "GET" && (pathname === "/" || pathname === "/index.html")) {
    return user ? sendPage(response, shell(row._id, user, { mode: "planner" }))
      : redirect(response, `/login?next=${encodeURIComponent(pathname)}`);
  }
  if (!pathname.startsWith("/api/")) { return send(response, 404, "Not found.", "text/plain; charset=utf-8"); }
  if (!user) { return sendJson(response, 200, { ok: false, reason: "signed-out" }); }
  for (const [routeMethod, pattern, handler] of ROUTES) {
    const match = routeMethod === method ? pattern.exec(pathname) : null;
    if (match) {
      const context = { db: partitions.database(row), user, sid: row._id, body: method === "POST" ? jsonBody(text) : {} };
      // A route without captures (the places list) reads its query string instead.
      const [status, payload] = await handler(context, ...(match.length > 1 ? match.slice(1) : [url]));
      return sendJson(response, status, payload);
    }
  }
  throw new HttpError(404, "Not found.");
}

async function handle(request, response) {
  const url = new URL(request.url || "/", "http://localhost");
  const { pathname } = url;
  if (request.method === "GET" && pathname === "/healthz") {
    const ok = await partitions.healthy();
    return sendJson(response, ok ? 200 : 503, { ok, app: APP_ID });
  }
  if (request.method === "GET" && ASSETS[pathname]) {
    const [file, type] = ASSETS[pathname];
    return send(response, 200, fs.readFileSync(path.join(STATIC_DIR, file), "utf8"), type);
  }
  const token = /^\/api\/share\/([0-9a-f]{16})$/.exec(pathname);
  if (request.method === "GET" && token) { return sendJson(response, 200, await publicShare(token[1])); }
  const text = request.method === "POST" ? await readBody(request) : "";
  const sid = cookieOf(request);
  return isSid(sid) ? partitions.lane(sid, () => respond(request, response, url, sid, text))
    : respond(request, response, url, "", text);
}

async function main() {
  partitions = await Partitions.connect(MONGODB_URL, { seed: SEED, cap: SESSION_CAP });
  const server = http.createServer((request, response) => {
    handle(request, response).catch((error) => {
      if (response.headersSent) { return; }
      if (error instanceof HttpError) {
        return sendJson(response, error.status, { error: error.message, field: error.field });
      }
      console.error(error);
      return sendJson(response, 500, { error: "Something went wrong." });
    });
  });
  const port = Number(process.env.PORT || 8080);
  server.listen(port, process.env.HOST === "0.0.0.0" ? "0.0.0.0" : "127.0.0.1", () => {
    console.log(`wayfarer listening on ${port} (vocab ${VOCAB_PROFILE}, home database ready)`);
  });
  for (const signal of ["SIGINT", "SIGTERM"]) {
    process.on(signal, () => server.close(() => partitions.client.close().finally(() => process.exit(0))));
  }
}

main().catch((error) => {
  console.error(`wayfarer: cannot start: ${error.stack || error}`);
  process.exit(1);
});
