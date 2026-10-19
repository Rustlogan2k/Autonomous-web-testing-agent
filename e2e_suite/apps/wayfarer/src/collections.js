/*
 * Wayfarer's collections and the rules MongoDB enforces on them. Every session database is
 * built from this list (validator first, then indexes, then the seed), so a document the app
 * writes is checked by the server as well as by the code that wrote it.
 *
 *   users     who can sign in (PBKDF2 credentials, never sent to a browser)
 *   regions   where trips go, each with its month-by-month weather outlook
 *   trips     owner_id -> users, region_id -> regions, members[].user_id -> users (owner or
 *             read-only viewer), the latest sealed version number, and the share link if one
 *             was issued
 *   versions  sealed itineraries, write-once: "<trip>:v<n>", trip_id -> trips,
 *             sealed_by -> users, days[] of stops[]; one version number per trip
 *   places    the read-only catalogue of places to visit, region_id -> regions
 *   packing   a trip's packing list, trip_id -> trips, added_by -> users; one position per list
 *   counters  the next trip and packing-item numbers and the share sequence (auxiliary)
 */
"use strict";

const STOP_LENGTHS = ["30m", "1h", "2h", "half"];
const STOP_KINDS = ["walk", "food", "sight", "beach", "other"];
const TRIP_ROLES = ["owner", "viewer"];
const SKIES = ["sun", "cloud", "showers"];
const LIMITS = { tripName: 80, stopName: 120, itemText: 80, minDays: 1, maxDays: 5 };

const text = (min, max) => ({ bsonType: "string", minLength: min, maxLength: max });
const pattern = (regex) => ({ bsonType: "string", pattern: regex });
const whole = (minimum, maximum) => ({ bsonType: "int", minimum, ...(maximum ? { maximum } : {}) });
const userRef = pattern("^[a-z]+$");

/** A closed object: exactly these fields, all required unless listed in `optional`. */
function shape(properties, optional = []) {
  return {
    bsonType: "object",
    required: Object.keys(properties).filter((name) => !optional.includes(name)),
    additionalProperties: false,
    properties,
  };
}

const stop = shape({
  id: pattern("^s[0-9]{1,6}$"),
  name: text(1, LIMITS.stopName),
  length: { enum: STOP_LENGTHS },
  kind: { enum: STOP_KINDS },
});

const COLLECTIONS = [
  {
    name: "users",
    validator: shape({
      _id: userRef,
      username: pattern("^[a-z]+\\.[a-z]+$"),
      name: text(1, 80),
      pw: shape({ salt: pattern("^[0-9a-f]{32}$"), hash: pattern("^[0-9a-f]{64}$"), iter: whole(1000) }),
    }),
    indexes: [{ key: { username: 1 }, name: "one_account_per_username", unique: true }],
  },
  {
    name: "regions",
    validator: shape({
      _id: pattern("^[a-z]+$"),
      name: text(1, 80),
      outlook: {
        bsonType: "array", minItems: 12, maxItems: 12,
        items: shape({ month: whole(1, 12), sky: { enum: SKIES }, high_c: { bsonType: "int", minimum: -30, maximum: 45 } }),
      },
    }),
    indexes: [],
  },
  {
    name: "trips",
    validator: shape({
      _id: pattern("^t[0-9]+$"),
      owner_id: userRef,
      region_id: pattern("^[a-z]+$"),
      name: text(1, LIMITS.tripName),
      start: pattern("^[0-9]{4}-[0-9]{2}-[0-9]{2}$"),
      days: whole(LIMITS.minDays, LIMITS.maxDays),
      members: { bsonType: "array", minItems: 1, items: shape({ user_id: userRef, role: { enum: TRIP_ROLES } }) },
      latest: { bsonType: ["int", "null"], minimum: 1 },
      share: { oneOf: [{ bsonType: "null" }, shape({ token: pattern("^[0-9a-f]{16}$"), issued_seq: whole(1) })] },
    }),
    indexes: [
      { key: { "members.user_id": 1 }, name: "trips_by_member" },
      { key: { owner_id: 1 }, name: "trips_by_owner" },
    ],
  },
  {
    name: "versions",
    validator: shape({
      _id: pattern("^t[0-9]+:v[0-9]+$"),
      trip_id: pattern("^t[0-9]+$"),
      n: whole(1),
      sealed_by: userRef,
      sealed_at: pattern("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}$"),
      next_stop: whole(1),
      days: {
        bsonType: "array", minItems: LIMITS.minDays, maxItems: LIMITS.maxDays,
        items: shape({ day: whole(1, LIMITS.maxDays), stops: { bsonType: "array", items: stop } }),
      },
    }),
    indexes: [{ key: { trip_id: 1, n: 1 }, name: "one_version_number_per_trip", unique: true }],
  },
  {
    name: "places",
    validator: shape({
      _id: pattern("^p[0-9]{2,4}$"),
      region_id: pattern("^[a-z]+$"),
      name: text(1, LIMITS.stopName),
      kind: { enum: STOP_KINDS },
      length: { enum: STOP_LENGTHS },
      summary: text(1, 300),
    }),
    indexes: [
      { key: { region_id: 1, kind: 1, name: 1 }, name: "places_by_region_and_kind" },
      { key: { kind: 1, name: 1 }, name: "places_by_kind" },
    ],
  },
  {
    name: "packing",
    validator: shape({
      _id: pattern("^k[0-9]+$"),
      trip_id: pattern("^t[0-9]+$"),
      n: whole(1),
      text: text(1, LIMITS.itemText),
      packed: { bsonType: "bool" },
      added_by: userRef,
    }),
    indexes: [{ key: { trip_id: 1, n: 1 }, name: "one_position_per_list", unique: true }],
  },
  {
    name: "counters",
    validator: shape({ _id: { enum: ["seq"] }, next_trip: whole(1), shares: whole(0), next_item: whole(1) }),
    indexes: [],
  },
];

module.exports = { COLLECTIONS, STOP_LENGTHS, STOP_KINDS, LIMITS };
