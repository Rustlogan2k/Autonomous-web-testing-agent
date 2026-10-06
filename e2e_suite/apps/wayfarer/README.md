# Wayfarer — trip itinerary builder

A single-page app (vanilla JS, hash routes) over a small JSON API written with Node 22
built-ins only. No dependencies, nothing to install.

**There is deliberately no Dockerfile.** Wayfarer exists to exercise the product's current
limitation that an uploaded repository must bring its own Dockerfile: the product's intake
is expected to refuse it today. `package.json` describes how to run it, so a future stack
detector has something to read.

## Run

```sh
PORT=8080 node src/server.js     # or: PORT=8080 npm start
```

Then open http://127.0.0.1:8080/.

| variable | meaning |
|---|---|
| `PORT` | port to listen on (127.0.0.1; `HOST=0.0.0.0` listens on all interfaces) |
| `WTA_VOCAB` | `default` or `alt` — the same app with different wording |
| `WTA_FIXED_NOW` | the app's "now" (default `2026-06-10T09:00:00`); trip start dates are checked against it |
| `WTA_DATA_DIR` | where sealed versions are written (default: a new temp directory) |
| `WTA_TEST_HOOKS=1` | enables `GET /__wta/state` (the session's committed server state and its sha256) |

Tests (against a running server): `E2E_BASE_URL=http://127.0.0.1:8080/ npm test`.

## How it works

- **The itinerary being edited is a local draft.** It lives in the browser's
  `localStorage`, namespaced per session, and is written synchronously on every edit before
  the screen changes. A "Draft saved" flag shows while the draft differs from the latest
  sealed version.
- **The server keeps sealed versions.** "Seal itinerary" sends the draft; the server writes
  it as version N+1, one write-once file per version
  (`$WTA_DATA_DIR/sessions/<sid>/versions/<trip>/v<N>.json`). Sealing an itinerary identical
  to the latest version creates nothing.
- **Share links** (`#/share/<token>`) are read-only and always show the trip's *latest
  sealed* version — never anyone's unsealed draft. A link works from any browser.
- Everything server-side is scoped to the `wta_sid` session cookie and starts from
  `seed/seed.json`: one trip, "Coast to Coast", three days, sealed as version 1.

## What you can do

- **Trips** (`#/trips`): list trips; plan a new one (name, first day, 1–5 days).
- **Planner** (`#/trips/<id>`): per day, pin a stop (name and time to allow); per stop,
  nudge it up or down, strike it, or star it as a favourite (favourites are a personal
  bookmark kept in this browser, not part of the itinerary); seal the itinerary; share it;
  look at the seasonal weather outlook.
- **Share view** (`#/share/<token>`): the sealed version's number and stop count as
  recorded by the server, the itinerary itself, and how many stops are listed.

## API

| method | path | |
|---|---|---|
| GET | `/healthz` | `{"ok": true, "app": "wayfarer"}` |
| GET | `/api/trips` | trip summaries |
| POST | `/api/trips` | `{name, start, days}` → the new trip (422 on invalid input) |
| GET | `/api/trips/<id>` | trip with its latest sealed version |
| GET | `/api/trips/<id>/versions/<n>` | one sealed version |
| POST | `/api/trips/<id>/seal` | `{days: [[{id, name, length}]], next_stop}` → `{created, trip}` |
| POST | `/api/trips/<id>/share` | issue (or return) the trip's share token; 409 before any seal |
| GET | `/api/share/<token>` | the latest sealed version behind a share token |

Share tokens are derived deterministically from the session and trip (the suite requires
determinism); they are identifiers, not secrets.
