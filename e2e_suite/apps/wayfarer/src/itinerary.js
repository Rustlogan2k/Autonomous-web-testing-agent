/*
 * Wayfarer's domain: trips, sealed itinerary versions and what each person may do with them.
 * Everything here is pure: the server loads documents, these functions decide and describe,
 * and the server writes what they return.
 *
 *   - A trip's owner pins, seals and shares; a member with the viewer role only reads.
 *   - Sealing stores the itinerary as the next version, write-once. Sealing an itinerary that
 *     matches the latest version creates nothing.
 *   - Dates are ISO strings; "today" is the app's fixed clock (WTA_FIXED_NOW).
 *   - The owner keeps the trip's packing list; the places catalogue is read by everyone and
 *     paged on the server (search, kind filter, sort).
 */
"use strict";

const { STOP_LENGTHS, STOP_KINDS, LIMITS } = require("./collections.js");

const HORIZON_DAYS = 730;
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const STOP_ID = /^s\d{1,6}$/;
const PLACES_PER_PAGE = 8;
const PLACE_SORTS = {
  name: [["name", 1], ["_id", 1]],
  "name-desc": [["name", -1], ["_id", 1]],
  region: [["region_id", 1], ["name", 1], ["_id", 1]],
};

class HttpError extends Error {
  constructor(status, message, field = "") {
    super(message);
    this.status = status;
    this.field = field;
  }
}

// -- calendar ---------------------------------------------------------------------------------

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

const dateOf = (number) => new Date(number * 86400000);

function labelFor(number) {
  const when = dateOf(number);
  return `${WEEKDAYS[when.getUTCDay()]} ${String(when.getUTCDate()).padStart(2, "0")} `
    + `${MONTHS[when.getUTCMonth()]} ${when.getUTCFullYear()}`;
}

const tripDayNumbers = (trip) => Array.from({ length: trip.days }, (_, i) => dayNumber(trip.start) + i);

// -- who may do what ---------------------------------------------------------------------------

/** The person's role on `trip` ("owner" or "viewer"), or null when it is not shared with them. */
function roleOn(trip, userId) {
  const membership = trip.members.find((m) => m.user_id === userId);
  return membership ? membership.role : null;
}

// -- input --------------------------------------------------------------------------------------

/** A new trip's fields, or (with `fixedDays`) an existing trip's edited ones: its length is
 *  fixed once it exists, because its sealed versions have that many days. */
function validateTrip(body, regionIds, today, fixedDays = null) {
  const name = typeof body.name === "string" ? body.name.trim() : "";
  if (!name) { throw new HttpError(422, "Give the trip a name.", "name"); }
  if (name.length > LIMITS.tripName) {
    throw new HttpError(422, `Trip names are at most ${LIMITS.tripName} characters.`, "name");
  }
  const start = dayNumber(body.start);
  const todayNumber = dayNumber(today);
  if (start === null) { throw new HttpError(422, "Choose the date the trip starts.", "start"); }
  if (start < todayNumber) { throw new HttpError(422, "A trip can't start in the past.", "start"); }
  if (start > todayNumber + HORIZON_DAYS) {
    throw new HttpError(422, "Trips can be planned up to two years ahead.", "start");
  }
  const days = fixedDays || Number(body.days);
  if (!Number.isInteger(days) || days < LIMITS.minDays || days > LIMITS.maxDays) {
    throw new HttpError(422, `A trip lasts ${LIMITS.minDays} to ${LIMITS.maxDays} days.`, "days");
  }
  const region = String(body.region || "");
  if (!regionIds.includes(region)) { throw new HttpError(422, "Choose where the trip goes.", "region"); }
  return { name, start: String(body.start), days, region };
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
      if (!STOP_ID.test(id) || seen.has(id)) {
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
      const kind = STOP_KINDS.includes(stop.kind) ? stop.kind : "other";
      return { id, name, length: stop.length, kind };
    });
  });
  if (countStops(clean) === 0) { throw new HttpError(422, "Pin at least one stop before sealing.", "days"); }
  const nextStop = Number.isInteger(body.next_stop) && body.next_stop > 0 ? body.next_stop : 0;
  return { days: clean, nextStop };
}

function validateItemText(body) {
  const text = typeof body.text === "string" ? body.text.trim() : "";
  if (!text) { throw new HttpError(422, "Name the item.", "text"); }
  if (text.length > LIMITS.itemText) {
    throw new HttpError(422, `Items are at most ${LIMITS.itemText} characters.`, "text");
  }
  return text;
}

/** The catalogue query a request asks for: Mongo filter and sort, and the page wanted. */
function placeQuery(params) {
  const filter = {};
  const kind = String(params.get("kind") || "");
  if (STOP_KINDS.includes(kind)) { filter.kind = kind; }
  const words = String(params.get("q") || "").trim().slice(0, 80);
  if (words) {
    // The search text is data, not a pattern: escape it before it becomes a $regex.
    const literal = words.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    filter.$or = [{ name: { $regex: literal, $options: "i" } }, { summary: { $regex: literal, $options: "i" } }];
  }
  const sortKey = Object.prototype.hasOwnProperty.call(PLACE_SORTS, params.get("sort")) ? params.get("sort") : "name";
  const page = /^[1-9]\d{0,3}$/.test(String(params.get("page") || "")) ? Number(params.get("page")) : 1;
  return { filter, sort: PLACE_SORTS[sortKey], sortKey, kind, words, page };
}

/** One page of the catalogue. The page is the one asked for, even past the last page. */
function placesPage(query, total, items, regionNames) {
  return {
    page: query.page,
    pages: Math.max(1, Math.ceil(total / PLACES_PER_PAGE)),
    total,
    sort: query.sortKey,
    kind: query.kind,
    q: query.words,
    items: items.map((place) => placeView(place, regionNames)),
  };
}

function placeView(place, regionNames) {
  return { id: place._id, name: place.name, kind: place.kind, length: place.length,
    summary: place.summary, region: regionNames.get(place.region_id) };
}

// -- packing lists ------------------------------------------------------------------------------

function packingCounts(items) {
  return { packed: items.filter((item) => item.packed).length, total: items.length };
}

function packingView(items, canEdit) {
  return { items: items.map((item) => ({ id: item._id, n: item.n, text: item.text, packed: item.packed })),
    ...packingCounts(items), can_edit: canEdit };
}

// -- sealing --------------------------------------------------------------------------------------

const countStops = (days) => days.reduce((total, stops) => total + stops.length, 0);
const contentOf = (days) => JSON.stringify(days.map((stops) => stops.map((s) => [s.name, s.length, s.kind])));
const stopsByDay = (version) => version.days.map((day) => day.stops);

/** Whether sealing `days` would repeat the latest version (then nothing is created). */
function sameAsLatest(latest, days) {
  return Boolean(latest) && contentOf(stopsByDay(latest)) === contentOf(days);
}

/** The next version of `trip`: number, author, time and the next free stop number. */
function nextVersion(trip, latest, days, requestedNext, { by, at }) {
  const n = latest ? latest.n + 1 : 1;
  const highest = days.flat().reduce((high, stop) => Math.max(high, Number(stop.id.slice(1))), 0);
  return {
    _id: `${trip._id}:v${n}`,
    trip_id: trip._id,
    n,
    sealed_by: by,
    sealed_at: at,
    next_stop: Math.max(highest + 1, requestedNext || 0, latest ? latest.next_stop : 0),
    days: days.map((stops, i) => ({ day: i + 1, stops })),
  };
}

// -- views ----------------------------------------------------------------------------------------

function outlookFor(trip, region) {
  return tripDayNumbers(trip).map((number) => {
    const month = dateOf(number).getUTCMonth() + 1;
    const entry = region.outlook.find((m) => m.month === month);
    return { label: labelFor(number), sky: entry.sky, high_c: entry.high_c };
  });
}

/** The planner's view of a trip, for the person `viewer`. */
function tripView(trip, latest, region, people, viewer, packing) {
  const role = roleOn(trip, viewer);
  return {
    id: trip._id,
    name: trip.name,
    start: trip.start,
    start_label: labelFor(dayNumber(trip.start)),
    days: trip.days,
    day_labels: tripDayNumbers(trip).map(labelFor),
    region: region.name,
    outlook: outlookFor(trip, region),
    owner: people.get(trip.owner_id),
    role,
    can_edit: role === "owner",
    shared: Boolean(trip.share),
    region_id: trip.region_id,
    packing: packingCounts(packing),
    latest: latest && {
      version: latest.n,
      stop_count: countStops(stopsByDay(latest)),
      next_stop: latest.next_stop,
      days: stopsByDay(latest),
    },
  };
}

function tripSummary(trip, latest, people, viewer, packing) {
  return {
    id: trip._id, name: trip.name, start: trip.start, start_label: labelFor(dayNumber(trip.start)),
    days: trip.days, owner: people.get(trip.owner_id), role: roleOn(trip, viewer),
    day_labels: tripDayNumbers(trip).map(labelFor), packing: packingCounts(packing),
    latest_version: latest ? latest.n : null, stop_count: latest ? countStops(stopsByDay(latest)) : 0,
  };
}

/** What a share link shows: the trip's latest sealed version, never anyone's draft. */
function shareView(token, trip, latest) {
  const labels = tripDayNumbers(trip).map(labelFor);
  return {
    ok: true,
    token,
    trip: trip._id,
    trip_name: trip.name,
    start_label: labels[0],
    version: latest.n,
    stop_count: countStops(stopsByDay(latest)),
    days: stopsByDay(latest).map((stops, i) => ({ label: labels[i], stops })),
  };
}

/** A trip's sealed versions, newest first. */
function versionList(versions, people) {
  return versions.slice().sort((a, b) => b.n - a.n).map((v) => ({
    version: v.n, stop_count: countStops(stopsByDay(v)), sealed_by: people.get(v.sealed_by),
    sealed_on: labelFor(dayNumber(v.sealed_at.slice(0, 10))),
  }));
}

/** Business state for the harness's digest: no share tokens (they name the session), no
 *  counters, no credentials. */
function committedState(trips, versions, packing) {
  return {
    trips: trips.map((trip) => ({
      id: trip._id, name: trip.name, start: trip.start, days: trip.days, region: trip.region_id,
      owner: trip.owner_id, members: trip.members.map((m) => [m.user_id, m.role]),
      shared: Boolean(trip.share), latest: trip.latest,
    })),
    versions: versions.map((v) => ({
      trip: v.trip_id, version: v.n, by: v.sealed_by,
      days: stopsByDay(v).map((stops) => stops.map((s) => [s.id, s.name, s.length, s.kind])),
    })),
    packing: packing.map((item) => [item.trip_id, item.n, item.text, item.packed]),
  };
}

module.exports = {
  HttpError, PLACES_PER_PAGE, labelFor, dayNumber, roleOn, validateTrip, validateItinerary,
  validateItemText, placeQuery, placesPage, placeView, packingView, sameAsLatest, nextVersion,
  tripView, tripSummary, shareView, versionList, committedState,
};
