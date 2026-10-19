# Wayfarer — trip itinerary builder

A single-page app (vanilla JS, hash routes) over a JSON API written with Node 22 built-ins and
the suite's vendored MongoDB driver (`mongodb` 7.7.0, loaded with `createRequire` from
`vendor/node/`). Nothing is installed at build time.

**There is deliberately no Dockerfile.** Wayfarer exists to exercise the product's current
limitation that an uploaded repository must bring its own Dockerfile: the product's intake
is expected to refuse it today. `package.json` describes how to run it, so a future stack
detector has something to read.

## Run

From a build, which carries the driver as `vendor/node/` (the harness makes one at
`e2e_suite/.build/wayfarer/clean`):

```sh
MONGODB_URL='mongodb://wta:wta-local-test@127.0.0.1:57017/wayfarer?authSource=admin&directConnection=true' \
PORT=8080 node src/server.js
```

| variable | meaning |
|---|---|
| `MONGODB_URL` | required: the server and, as its path, the app's home database |
| `PORT` | port to listen on (127.0.0.1; `HOST=0.0.0.0` listens on all interfaces) |
| `WTA_VOCAB` | `default` or `alt` — the same app with different wording |
| `WTA_FIXED_NOW` | the app's "now" (default `2026-06-10T09:00:00`); trip dates are checked against it |
| `WTA_SESSION_CAP` | how many browser sessions keep a database (default 32) |
| `WTA_TEST_HOOKS=1` | enables `GET /__wta/state` (the session's committed state and its sha256) |

Tests (against a running server): `E2E_BASE_URL=http://127.0.0.1:8080/ npm test`.

## Demo accounts

All use the fixture password `Wayfarer-trails-26`.

| user | username | |
|---|---|---|
| Noor Haddad (the tester signs in as Noor) | `noor.haddad` | owns *Coast to Coast* (t1); sees Sam's trip read-only |
| Sam Whitlock | `sam.whitlock` | owns *Fells and lakes* (t90), shared with Noor as a viewer |
| Omar Farouk | `omar.farouk` | no trips: t1 shows him an access notice |

## How it works

- **The itinerary being edited is a local draft** in `localStorage`, namespaced per browser
  session and written synchronously on every edit. "Draft saved" shows while it differs from
  the latest sealed version.
- **The server keeps what was agreed**: trips, write-once sealed versions, packing lists, and a
  read-only catalogue of places, in a MongoDB database of the browser's own (created at its first
  sign-in from `seed/`; `src/partitions.js`). Sealing an itinerary identical to the latest
  version creates nothing.
- **Share links** (`/share/<token>`, and `#/share/<token>` inside the planner) are public and
  always show the trip's latest *sealed* version, never a draft.

## Views (14) and what you can do

| view | |
|---|---|
| `/login`, `/login/help` | sign in; who resets a password |
| `#/trips` | your trips and those shared with you; plan a new trip (name, first day, length, region) |
| `#/trips/<id>` | **Itinerary**: pin stops per day; nudge up/down, strike, star; click a stop's name to open the **stop editor** (rename, time, move to another day); seal; share; weather outlook |
| `#/trips/<id>/packing` | **Packing**: add items, tick packed, click an item's name to rename it in place, remove; select several and mark packed / not packed / remove |
| `#/trips/<id>/history` | sealed versions, newest first |
| `#/trips/<id>/versions/<n>` | one sealed version; its owner can start a draft from it |
| `#/trips/<id>/settings` | rename, re-date or move the trip; delete it (confirmation dialog) |
| `#/share/<token>`, `/share/<token>` | the latest sealed version, read-only |
| `#/places` | the catalogue: search, filter by kind, sort, pages of eight (all in the address, so Back and Forward work) |
| `#/places/<id>` | one place; add it to a day of one of your trips (it joins that trip's draft) |

Entities with create, read, update and delete through the UI: **trips**, **stops** (pin, edit,
move, strike), **packing items**. Interaction patterns: server-side paging, sort and filter,
search, modal dialogs, tabs, click-to-edit, reordering and moving between days, bulk selection
with bulk actions, client-side routing with working Back/Forward.

A trip's owner edits it; a viewer reads it (no pin, seal, share, packing changes or settings).
Pages a role may not open show an access notice (HTTP 200); API calls answer
`200 {ok: false, reason: "forbidden"}`.

## Data model (MongoDB, `src/collections.js`)

Every collection has a `$jsonSchema` validator (strict, error); ids are strings, never ObjectIds.

- `users` {_id, username (unique), name, pw {salt, hash, iter}}
- `regions` {_id, name, outlook[12] {month, sky enum, high_c}}
- `trips` {_id "t1", owner_id → users, region_id → regions, name, start, days,
  members[{user_id → users, role enum owner|viewer}], latest, share: null | {token, issued_seq}}
- `versions` {_id "t1:v1", trip_id → trips, n (unique per trip), sealed_by → users, sealed_at,
  next_stop, days[{day, stops[{id, name, length enum, kind enum}]}]}
- `places` {_id, region_id → regions, name, kind enum, length enum, summary}
- `packing` {_id, trip_id → trips, n (unique per trip), text, packed, added_by → users}
- `counters` {next_trip, next_item, shares}

## Seeded faults

| fault | where | what goes wrong |
|---|---|---|
| WAY-1 | `app.js` (nudge) | after a strike, a nudge reorders a stale copy of the day: the screen and the saved draft disagree, so a reload shows another order |
| WAY-2 | `app.js` (share view) | the share view prefers this browser's draft, leaking unsealed stops into the shared itinerary |
| WAY-3 | `app.js` (catalogue filter) | choosing a kind keeps the page number: from page 2, "Food" opens on "Page 2 of 1" with nothing listed |
| WAY-4 | `app.js` (packing counts) | the Packing tab's badge keeps the counts loaded with the trip, so after ticking an item it disagrees with "N of M packed" |

## Suite v2 notes (2026-10-17)

- t1's version 1 and the v1 workflows, decoys, state-dependent actions and benign traces are
  unchanged. Sam's shared trip is `t90`, outside the new-trip counter (new trips are t2, t3, ...).
- Share links stay public, so they moved to their own address, `/share/<token>`, resolved through
  the home database's `share_links` (a share page cannot live behind the sign-in that protects `/`).
  An unknown or withdrawn link answers `200 {ok: false, reason: "not-found"}` and the page says so.
- The app stays CommonJS (no rewrite, plan D7): the driver is loaded with
  `createRequire(__filename)("../vendor/node/node_modules/mongodb")`.
- The itinerary view of t1 has about 44 controls (5 stops x 5 + 3 pin forms + toolbar, tabs and
  header): slightly above the ~40 guideline, well within the tester's 100-action cap (about 66 actions).
