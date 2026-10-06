/*
 * Wayfarer — trip itinerary builder. Node 22 built-ins only.
 *
 * The browser owns the itinerary being edited (a draft in localStorage). The server owns
 * what has been agreed: trips, sealed itinerary versions and read-only share links.
 * Sealed versions are write-once files, so a version, once sealed, can never change:
 *
 *   $WTA_DATA_DIR/sessions/<sid>/trips.json                  trip list (mutable)
 *   $WTA_DATA_DIR/sessions/<sid>/versions/<trip>/v<N>.json   sealed version N (write-once)
 *   $WTA_DATA_DIR/share-index.json                           share token -> session + trip
 */
"use strict";

const http = require("node:http");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const crypto = require("node:crypto");

const APP_ID = "wayfarer";
const APP_ROOT = path.resolve(__dirname, "..");
const STATIC_DIR = path.join(__dirname, "static");
const SEED = JSON.parse(fs.readFileSync(path.join(APP_ROOT, "seed", "seed.json"), "utf8"));
const VOCAB_PROFILE = process.env.WTA_VOCAB === "alt" ? "alt" : "default";
const VOCAB = JSON.parse(fs.readFileSync(path.join(APP_ROOT, "vocab", `${VOCAB_PROFILE}.json`), "utf8"));
const FIXED_NOW = process.env.WTA_FIXED_NOW || "2026-06-10T09:00:00"; // wta-clock
const TODAY = FIXED_NOW.slice(0, 10);
const DATA_DIR = process.env.WTA_DATA_DIR || fs.mkdtempSync(path.join(os.tmpdir(), "wayfarer-"));
const TEST_HOOKS = process.env.WTA_TEST_HOOKS === "1";

const STOP_LENGTHS = ["30m", "1h", "2h", "half"];
const LIMITS = { tripName: 80, stopName: 120, minDays: 1, maxDays: 5, horizonDays: 730, body: 256 * 1024 };
const SID_PATTERN = /^ws-\d{6}$/;
const STOP_ID_PATTERN = /^s\d{1,6}$/;

class HttpError extends Error {
  constructor(status, message, field = "") {
    super(message);
    this.status = status;
    this.field = field;
  }
}

// -- calendar (string dates only; "today" comes from WTA_FIXED_NOW) ---------------------------

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function dayNumber(iso) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ""));
  if (!match) { return null; }
  const [year, month, day] = match.slice(1).map(Number);
  const stamp = Date.UTC(year, month - 1, day);
  const check = new Date(stamp);
  if (check.getUTCFullYear() !== year || check.getUTCMonth() !== month - 1 || check.getUTCDate() !== day) {
    return null;
  }
  return Math.round(stamp / 86400000);
}

function labelFor(dayIndex) {
  const when = new Date(dayIndex * 86400000);
  return `${WEEKDAYS[when.getUTCDay()]} ${String(when.getUTCDate()).padStart(2, "0")} `
    + `${MONTHS[when.getUTCMonth()]} ${when.getUTCFullYear()}`;
}

const TODAY_NUMBER = dayNumber(TODAY);

// -- storage --------------------------------------------------------------------------------

function writeAtomically(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const scratch = `${file}.writing`;
  fs.writeFileSync(scratch, JSON.stringify(value, null, 1));
  fs.renameSync(scratch, file);
}

function readJson(file, fallback) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") { return fallback; }
    throw error;
  }
}

const sessionsDir = path.join(DATA_DIR, "sessions");
const shareIndexFile = path.join(DATA_DIR, "share-index.json");

class VersionStore {
  constructor() {
    fs.mkdirSync(sessionsDir, { recursive: true });
    this.cache = new Map();
    this.shareIndex = readJson(shareIndexFile, {});
    const existing = fs.readdirSync(sessionsDir).filter((name) => SID_PATTERN.test(name));
    this.lastSession = existing.reduce((high, name) => Math.max(high, Number(name.slice(3))), 0);
  }

  sessionDir(sid) {
    return path.join(sessionsDir, sid);
  }

  exists(sid) {
    return SID_PATTERN.test(sid) && (this.cache.has(sid) || fs.existsSync(path.join(this.sessionDir(sid), "trips.json")));
  }

  /** A new session starts from the seed: its trips, and each seeded trip sealed as version 1. */
  createSession() {
    this.lastSession += 1;
    const sid = `ws-${String(this.lastSession).padStart(6, "0")}`;
    const meta = { next_trip: SEED.next_trip, trips: [] };
    const versions = new Map();
    for (const trip of SEED.trips) {
      meta.trips.push({ id: trip.id, name: trip.name, start: trip.start, days: trip.days, share: null });
      versions.set(trip.id, []);
    }
    this.cache.set(sid, { meta, versions });
    writeAtomically(path.join(this.sessionDir(sid), "trips.json"), meta);
    for (const trip of SEED.trips) {
      this.appendVersion(sid, trip.id, trip.sealed, 0);
    }
    return sid;
  }

  load(sid) {
    if (!this.cache.has(sid)) {
      const meta = readJson(path.join(this.sessionDir(sid), "trips.json"), null);
      if (!meta) { throw new HttpError(500, "session storage is missing"); }
      const versions = new Map();
      for (const trip of meta.trips) {
        const dir = path.join(this.sessionDir(sid), "versions", trip.id);
        const files = fs.existsSync(dir) ? fs.readdirSync(dir).filter((f) => /^v\d+\.json$/.test(f)) : [];
        const list = files.map((f) => readJson(path.join(dir, f), null)).filter(Boolean);
        list.sort((a, b) => a.version - b.version);
        versions.set(trip.id, list);
      }
      this.cache.set(sid, { meta, versions });
    }
    return this.cache.get(sid);
  }

  saveMeta(sid) {
    writeAtomically(path.join(this.sessionDir(sid), "trips.json"), this.load(sid).meta);
  }

  trip(sid, tripId) {
    const trip = this.load(sid).meta.trips.find((t) => t.id === tripId);
    if (!trip) { throw new HttpError(404, "No such trip."); }
    return trip;
  }

  versions(sid, tripId) {
    return this.load(sid).versions.get(tripId) || [];
  }

  latest(sid, tripId) {
    const list = this.versions(sid, tripId);
    return list.length ? list[list.length - 1] : null;
  }

  /** Seal a new version. The file is created exclusively: an existing version is never rewritten. */
  appendVersion(sid, tripId, days, requestedNext) {
    const list = this.load(sid).versions.get(tripId);
    const previous = list.length ? list[list.length - 1] : null;
    const highest = days.flat().reduce((high, stop) => Math.max(high, Number(stop.id.slice(1))), 0);
    const version = {
      version: previous ? previous.version + 1 : 1,
      sealed_at: FIXED_NOW,
      next_stop: Math.max(highest + 1, requestedNext || 0, previous ? previous.next_stop : 0),
      days: days.map((stops) => stops.map((s) => ({ id: s.id, name: s.name, length: s.length }))),
    };
    const file = path.join(this.sessionDir(sid), "versions", tripId, `v${version.version}.json`);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, JSON.stringify(version, null, 1), { flag: "wx" });
    list.push(version);
    return version;
  }

  addTrip(sid, fields) {
    const { meta, versions } = this.load(sid);
    const trip = { id: `t${meta.next_trip}`, ...fields, share: null };
    meta.next_trip += 1;
    meta.trips.push(trip);
    versions.set(trip.id, []);
    this.saveMeta(sid);
    return trip;
  }

  issueShare(sid, tripId) {
    const trip = this.trip(sid, tripId);
    if (trip.share) { return { token: trip.share, created: false }; }
    const token = crypto.createHash("sha256").update(`wayfarer-share:${sid}:${tripId}`).digest("hex").slice(0, 16);
    trip.share = token;
    this.saveMeta(sid);
    this.shareIndex[token] = { sid, trip: tripId };
    writeAtomically(shareIndexFile, this.shareIndex);
    return { token, created: true };
  }

  resolveShare(token) {
    const entry = Object.prototype.hasOwnProperty.call(this.shareIndex, token) ? this.shareIndex[token] : null;
    if (!entry || !this.exists(entry.sid)) { throw new HttpError(404, "This share link does not exist."); }
    return entry;
  }
}

// -- domain ---------------------------------------------------------------------------------

const countStops = (days) => days.reduce((total, stops) => total + stops.length, 0);
const contentOf = (days) => JSON.stringify(days.map((stops) => stops.map((s) => [s.name, s.length])));
const dayLabels = (trip) => Array.from({ length: trip.days }, (_, i) => labelFor(dayNumber(trip.start) + i));

function tripView(store, sid, trip) {
  const latest = store.latest(sid, trip.id);
  return {
    id: trip.id,
    name: trip.name,
    start: trip.start,
    start_label: labelFor(dayNumber(trip.start)),
    days: trip.days,
    day_labels: dayLabels(trip),
    shared: Boolean(trip.share),
    latest: latest && {
      version: latest.version,
      stop_count: countStops(latest.days),
      next_stop: latest.next_stop,
      days: latest.days,
    },
  };
}

function tripSummary(store, sid, trip) {
  const latest = store.latest(sid, trip.id);
  return {
    id: trip.id, name: trip.name, start: trip.start, start_label: labelFor(dayNumber(trip.start)),
    days: trip.days, latest_version: latest ? latest.version : null,
    stop_count: latest ? countStops(latest.days) : 0,
  };
}

function shareView(store, token) {
  const { sid, trip: tripId } = store.resolveShare(token);
  const trip = store.trip(sid, tripId);
  const latest = store.latest(sid, tripId);
  const labels = dayLabels(trip);
  return {
    token,
    trip: trip.id,
    trip_name: trip.name,
    start_label: labels[0],
    version: latest.version,
    stop_count: countStops(latest.days),
    days: latest.days.map((stops, i) => ({ label: labels[i], stops })),
  };
}

function validateTrip(body) {
  const name = typeof body.name === "string" ? body.name.trim() : "";
  if (!name) { throw new HttpError(422, "Give the trip a name.", "name"); }
  if (name.length > LIMITS.tripName) {
    throw new HttpError(422, `Trip names are at most ${LIMITS.tripName} characters.`, "name");
  }
  const start = dayNumber(body.start);
  if (start === null) { throw new HttpError(422, "Choose the date the trip starts.", "start"); }
  if (start < TODAY_NUMBER) { throw new HttpError(422, "A trip can't start in the past.", "start"); }
  if (start > TODAY_NUMBER + LIMITS.horizonDays) {
    throw new HttpError(422, "Trips can be planned up to two years ahead.", "start");
  }
  const days = Number(body.days);
  if (!Number.isInteger(days) || days < LIMITS.minDays || days > LIMITS.maxDays) {
    throw new HttpError(422, `A trip lasts ${LIMITS.minDays} to ${LIMITS.maxDays} days.`, "days");
  }
  return { name, start: String(body.start), days };
}

function validateItinerary(trip, body) {
  const days = body.days;
  if (!Array.isArray(days) || days.length !== trip.days) {
    throw new HttpError(422, `The itinerary must have exactly ${trip.days} days.`, "days");
  }
  const seen = new Set();
  const clean = days.map((stops, dayIndex) => {
    if (!Array.isArray(stops)) { throw new HttpError(422, `Day ${dayIndex + 1} is not a list of stops.`, "days"); }
    return stops.map((stop) => {
      if (!stop || typeof stop !== "object") { throw new HttpError(422, "Every stop must be an object.", "days"); }
      const id = String(stop.id || "");
      if (!STOP_ID_PATTERN.test(id) || seen.has(id)) {
        throw new HttpError(422, `Stop id ${JSON.stringify(id)} is missing, malformed or repeated.`, "days");
      }
      seen.add(id);
      const name = typeof stop.name === "string" ? stop.name.trim() : "";
      if (!name || name.length > LIMITS.stopName) {
        throw new HttpError(422, `Stop names are 1 to ${LIMITS.stopName} characters.`, "stop_name");
      }
      if (!STOP_LENGTHS.includes(stop.length)) {
        throw new HttpError(422, `Unknown stop length ${JSON.stringify(stop.length)}.`, "stop_length");
      }
      return { id, name, length: stop.length };
    });
  });
  if (countStops(clean) === 0) { throw new HttpError(422, "Pin at least one stop before sealing.", "days"); }
  const nextStop = Number.isInteger(body.next_stop) && body.next_stop > 0 ? body.next_stop : 0;
  return { days: clean, nextStop };
}

function committedState(store, sid) {
  const { meta } = store.load(sid);
  return {
    trips: meta.trips.map((trip) => ({
      id: trip.id,
      name: trip.name,
      start: trip.start,
      days: trip.days,
      // Whether a share link exists; the token itself is derived from the session id,
      // which differs between otherwise identical sessions.
      shared: Boolean(trip.share),
      versions: store.versions(sid, trip.id).map((v) => ({ version: v.version, days: v.days })),
    })),
  };
}

/** What the page needs before its first render, embedded in index.html (no extra round trip). */
function bootData(sid) {
  return {
    vocab: VOCAB,
    today: TODAY,
    lengths: STOP_LENGTHS,
    limits: { tripName: LIMITS.tripName, stopName: LIMITS.stopName, minDays: LIMITS.minDays,
      maxDays: LIMITS.maxDays, horizonDays: LIMITS.horizonDays },
    // Namespaces this browser's local drafts, so two sessions never read each other's.
    workspace: crypto.createHash("sha256").update(`wayfarer-workspace:${sid}`).digest("hex").slice(0, 12),
  };
}

function canonical(value) {
  if (Array.isArray(value)) { return `[${value.map(canonical).join(",")}]`; }
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((k) => `${JSON.stringify(k)}:${canonical(value[k])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

// -- HTTP -----------------------------------------------------------------------------------

const store = new VersionStore();

const STATIC_FILES = {
  "/": ["index.html", "text/html; charset=utf-8"],
  "/index.html": ["index.html", "text/html; charset=utf-8"],
  "/app.js": ["app.js", "text/javascript; charset=utf-8"],
  "/styles.css": ["styles.css", "text/css; charset=utf-8"],
};

function sessionOf(request) {
  const header = request.headers.cookie || "";
  for (const part of header.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === "wta_sid") {
      const sid = rest.join("=");
      if (store.exists(sid)) { return { sid, fresh: false }; }
    }
  }
  return { sid: store.createSession(), fresh: true };
}

function send(response, status, body, type, session) {
  const headers = { "Content-Type": type, "Content-Length": Buffer.byteLength(body), "Cache-Control": "no-store" };
  if (session && session.fresh) {
    headers["Set-Cookie"] = `wta_sid=${session.sid}; Path=/; HttpOnly; SameSite=Lax`;
  }
  response.writeHead(status, headers);
  response.end(body);
}

const sendJson = (response, status, payload, session) =>
  send(response, status, JSON.stringify(payload), "application/json; charset=utf-8", session);

function readBody(request) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    request.on("data", (chunk) => {
      size += chunk.length;
      if (size > LIMITS.body) {
        reject(new HttpError(413, "Request body too large."));
        request.destroy();
        return;
      }
      chunks.push(chunk);
    });
    request.on("end", () => {
      if (!size) { return resolve({}); }
      let data;
      try {
        data = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      } catch {
        return reject(new HttpError(400, "Request body is not JSON."));
      }
      if (!data || typeof data !== "object" || Array.isArray(data)) {
        return reject(new HttpError(400, "Request body must be a JSON object."));
      }
      resolve(data);
    });
    request.on("error", reject);
  });
}

/** Routes: [method, pattern, handler(context, ...captures)]. */
const ROUTES = [
  ["GET", /^\/__wta\/state$/, ({ sid }) => {
    if (!TEST_HOOKS) { throw new HttpError(404, "Not found."); }
    const committed = committedState(store, sid);
    const sha = crypto.createHash("sha256").update(canonical(committed)).digest("hex");
    return [200, { session: sid, committed, sha }];
  }],
  ["GET", /^\/api\/trips$/, ({ sid }) =>
    [200, { trips: store.load(sid).meta.trips.map((trip) => tripSummary(store, sid, trip)) }]],
  ["POST", /^\/api\/trips$/, ({ sid, body }) => {
    const trip = store.addTrip(sid, validateTrip(body));
    return [201, tripView(store, sid, trip)];
  }],
  ["GET", /^\/api\/trips\/(t\d+)$/, ({ sid }, tripId) => [200, tripView(store, sid, store.trip(sid, tripId))]],
  ["GET", /^\/api\/trips\/(t\d+)\/versions\/(\d+)$/, ({ sid }, tripId, number) => {
    store.trip(sid, tripId);
    const version = store.versions(sid, tripId).find((v) => v.version === Number(number));
    if (!version) { throw new HttpError(404, "No such version."); }
    return [200, { ...version, stop_count: countStops(version.days) }];
  }],
  ["POST", /^\/api\/trips\/(t\d+)\/seal$/, ({ sid, body }, tripId) => {
    const trip = store.trip(sid, tripId);
    const { days, nextStop } = validateItinerary(trip, body);
    const latest = store.latest(sid, tripId);
    if (latest && contentOf(latest.days) === contentOf(days)) {
      return [200, { created: false, trip: tripView(store, sid, trip) }]; // nothing new to seal
    }
    store.appendVersion(sid, tripId, days, nextStop);
    return [201, { created: true, trip: tripView(store, sid, trip) }];
  }],
  ["POST", /^\/api\/trips\/(t\d+)\/share$/, ({ sid }, tripId) => {
    store.trip(sid, tripId);
    if (!store.latest(sid, tripId)) { throw new HttpError(409, "Seal the itinerary before sharing it."); }
    const { token, created } = store.issueShare(sid, tripId);
    return [created ? 201 : 200, { created, token, share: shareView(store, token) }];
  }],
  ["GET", /^\/api\/share\/([0-9a-f]{16})$/, (_context, token) => [200, shareView(store, token)]],
];

async function handle(request, response) {
  const pathname = (request.url || "/").split("?")[0];
  if (request.method === "GET" && pathname === "/healthz") {
    return sendJson(response, 200, { ok: true, app: APP_ID });
  }
  const session = sessionOf(request);
  if (request.method === "GET" && STATIC_FILES[pathname]) {
    const [file, type] = STATIC_FILES[pathname];
    let body = fs.readFileSync(path.join(STATIC_DIR, file), "utf8");
    if (file === "index.html") {
      const boot = JSON.stringify(bootData(session.sid)).replace(/</g, "\\u003c"); // keeps a </script> in a label inert
      body = body.replace("__WAYFARER_BOOT__", () => boot);
    }
    return send(response, 200, body, type, session);
  }
  try {
    for (const [method, pattern, handler] of ROUTES) {
      const match = pattern.exec(pathname);
      if (!match || method !== request.method) { continue; }
      const body = method === "POST" ? await readBody(request) : {};
      const [status, payload] = handler({ sid: session.sid, body }, ...match.slice(1));
      return sendJson(response, status, payload, session);
    }
    throw new HttpError(404, "Not found.");
  } catch (error) {
    if (!(error instanceof HttpError)) {
      console.error(error);
      return sendJson(response, 500, { error: "Something went wrong." }, session);
    }
    return sendJson(response, error.status, { error: error.message, field: error.field }, session);
  }
}

const server = http.createServer((request, response) => {
  handle(request, response).catch((error) => {
    console.error(error);
    if (!response.headersSent) { sendJson(response, 500, { error: "Something went wrong." }); }
  });
});

const port = Number(process.env.PORT || 8080);
server.listen(port, process.env.HOST === "0.0.0.0" ? "0.0.0.0" : "127.0.0.1", () => {
  console.log(`wayfarer listening on ${port} (vocab ${VOCAB_PROFILE}, data ${DATA_DIR})`);
});
