# Orbit — kanban boards

A single-page app (vanilla JS, hash routes) over a JSON API served by Node's own `http`
module. No dependencies: Node 22 built-ins only.

**Run:** `PORT=8080 node src/server.js`, then open http://127.0.0.1:8080/.
**Docker:** `docker build -t orbit . && docker run -p 8080:8080 orbit`.
Options: `WTA_VOCAB=alt` (alternate wording), `WTA_FIXED_NOW` (the app's "now", default
2026-06-10T09:00:00; activity times count on from it), `WTA_DATA_DIR` (where session files
live; default a fresh temp directory), `WTA_TEST_HOOKS=1` (enables `GET /__wta/state`).
**Tests:** with the app running, `E2E_BASE_URL=http://127.0.0.1:8080/ node --test tests/`.

## What you can do

- **Projects** (the hub): two projects, *Website relaunch* and *Office move*.
- **Board** (`#/board/<project>`): Backlog, Doing (WIP limit 3; 2 on Office move), Review
  (WIP limit 2) and Shipped, each showing its load (`3/3`). *Pull in* moves the first
  unblocked Backlog card into Doing if Doing has room. Backlog's column menu opens a *New
  card* form (its *Add card* button is disabled until there is a title); Review's column
  menu has *Ship all ready*, which ships every Review card that could ship on its own.
- **Card** (`#/board/<project>/card/<n>`): a modal over the board with its own route, so a
  reload or Back reopens it. Tabs: *Details* (description, "Blocked by" links to the cards it
  waits for), *Checklist* (each tick is saved by the server and the modal is redrawn from
  its answer), *Activity* (comments and history). Footer: *Move to* + *Move*, *Ship*,
  *Park* (back to Backlog), *Watch card*, *Copy link*.

## Rules

- No column ever exceeds its WIP limit, whichever control moved the card.
- A card ships from Doing or Review only when its checklist is complete and every card it
  is blocked by has shipped; otherwise the server refuses and says why. Shipped is final.
- A card shows a **Blocked** badge while anything it depends on has not shipped.
- *Watch card* and *Copy link* are this browser tab's own conveniences; nothing is saved.

## Implementation notes

- `src/server.js` — HTTP, sessions, static files, the route table. Every API request reads
  the session's JSON file; routes marked `persist` write it back before answering. Between
  the read and the write nothing awaits, so requests of one session never interleave.
- `src/board.js` — the domain rules and the JSON views the client renders.
- `src/static/` — the client. The vocabulary is embedded into `index.html` when it is served.
- Domain refusals are `200 {ok: false, message}`; 4xx is only for requests the UI never sends.

All data belongs to your browser session (`wta_sid` cookie) and starts from `seed/orbit.json`.
