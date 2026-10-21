# Orbit — kanban boards

A single-page app (vanilla JS, hash routes) over a JSON API served by Node's own `http`
module, storing its data in MongoDB. Node 22 built-ins plus the suite's vendored, pinned
MongoDB driver (`mongodb` 7.7.0, loaded with `createRequire` from `vendor/node/`); nothing
is installed at build time.

**Run:** from a build, which carries the driver as `vendor/node/` (the harness makes one at
`e2e_suite/.build/orbit/clean`, or copy `e2e_suite/vendor/node` next to `src/`):
`MONGODB_URL='mongodb://wta:wta-local-test@127.0.0.1:57017/orbit?authSource=admin&directConnection=true' PORT=8080 node src/server.js`,
then open http://127.0.0.1:8080/. The URL's path names the app's *home* database; the
suite's server is `python -m e2e_suite.harness.dbctl up`.
**Docker:** build from a build directory (`docker build -t orbit .`), then run it with
`-e MONGODB_URL=...` pointing at a reachable server.
Options: `WTA_VOCAB=alt` (alternate wording), `WTA_FIXED_NOW` (the app's "now", default
2026-06-10T09:00:00; activity times count on from it), `WTA_SESSION_CAP` (live session
databases, default 32), `WTA_TEST_HOOKS=1` (enables `GET /__wta/state`).
**Tests:** with the app running, `E2E_BASE_URL=http://127.0.0.1:8080/ node --test "tests/**/*.test.mjs"`.

## Demo accounts

All three use the fixture password `Orbit-kanban-26`.

| user | username | role |
|---|---|---|
| Mei Tanaka (the tester signs in as Mei) | `mei.tanaka` | member of both projects |
| Rosa Lindqvist | `rosa.lindqvist` | owner of both projects: can also change WIP limits |
| Ola Nwosu | `ola.nwosu` | viewer of Website relaunch only |

## What you can do

- **Sign in** (`/login`; *Forgot password?* explains who resets it). The header shows who is
  signed in; *Account* opens a menu with *Sign out*.
- **Projects** (the hub): the projects you belong to, *Website relaunch* and *Office move*.
- **Board** (`#/board/<project>`): Backlog, Doing (WIP limit 3; 2 on Office move), Review
  (WIP limit 2) and Shipped, each showing its load (`3/3`). *Pull in* moves the first
  unblocked Backlog card into Doing if Doing has room. Backlog's column menu opens a *New
  card* form (its *Add card* button is disabled until there is a title); Review's column
  menu has *Ship all ready*, which ships every Review card that could ship on its own.
- **Card** (`#/board/<project>/card/<n>`): a modal over the board with its own route, so a
  reload or Back reopens it. Tabs: *Details* (description, assignees, labels, "Blocked by"
  links to the cards it waits for), *Checklist* (each tick is saved by the server and the
  modal is redrawn from its answer), *Activity* (comments and history, with who did what).
  Footer: *Move to* + *Move*, *Ship*, *Park* (back to Backlog), *Watch card*, *Copy link*.
- **Board settings** (`#/board/<project>/settings`, owners only): the WIP limits of Doing
  and Review.

## Rules

- No column ever exceeds its WIP limit, whichever control moved the card; an owner cannot
  set a limit below what the column already holds.
- A card ships from Doing or Review only when its checklist is complete and every card it
  is blocked by has shipped; otherwise the server refuses and says why. Shipped is final.
- A card shows a **Blocked** badge while anything it depends on has not shipped.
- Viewers see boards read-only. A page a role may not open shows an access notice (HTTP 200);
  an API call it may not make answers `200 {ok: false, reason: "forbidden"}`.
- *Watch card* and *Copy link* are this browser tab's own conveniences; nothing is saved.

## Data model (MongoDB, `src/schema.js`)

Every collection has a `$jsonSchema` validator (strict, error) and is created, indexed and
seeded per session. `_id`s are strings or counter-derived integers, never ObjectIds.

- `users` {_id, username (unique), name, pw {salt, hash, iter}}: PBKDF2-SHA256, 20 000 iterations.
- `workspaces` {_id, name, member_ids[] → users}.
- `projects` {_id, workspace_id → workspaces, name (unique per workspace), columns[{key enum,
  name, wip, position}], members[{user_id → users, role enum owner|member|viewer}], labels[]};
  index on `members.user_id`.
- `cards` {_id: int, project_id → projects, column enum, position, checklist[{n, text, done}],
  blocked_by[] → cards, labels[], assignee_ids[] → users, comments[{n, author_id → users, body,
  at}], activity[oneOf created | moved | shipped | parked | checked | comment, each with
  `detail`]}; indexes {project_id, column, position}, {blocked_by}, {assignee_ids}.
- `counters` {_id: "seq", card, position, event}.

The rules that span documents (WIP limits, dependencies) are the domain's (`src/board.js`):
a validator sees one document at a time. No fault needs a constraint left out
(`absent_constraints` is empty).

## Implementation notes

- `src/server.js`: HTTP, sign-in, sessions, static files, the route table. Each API route
  names the project role it needs and whether it `persist`s.
- `src/store.js`: one database per signed-in browser session (`<home>_s000017`), created at
  the first sign-in from `seed/`; the registry in the home database; least-recently-used
  eviction beyond `WTA_SESSION_CAP`; a per-session request queue (requests of one session
  never interleave, so rapid clicks are deterministic); and the unit of work: a request
  loads the board it needs, the handler changes those documents in memory, and a `persist`
  route writes the changed documents back (one bulkWrite of replaceOne per collection).
- `src/board.js`: the domain rules and the JSON views the client renders.
- `src/auth.js`: password checks, the `next` rule, and the sign-in and forgotten-password pages.
- `src/static/`: the client. The vocabulary and the signed-in user are embedded into
  `index.html` when it is served.
- Signing out keeps the session's database: signing in again from the same browser resumes
  it. Pages redirect with 303; nothing answers 401 or 403. Domain refusals are
  `200 {ok: false, message}`; 4xx is only for requests the UI never sends.

All data belongs to your browser session (`wta_sid` cookie) and starts from `seed/`.

## Suite v2 notes (2026-10-08)

- Seed facts the workflows and faults pin are unchanged: the 14 cards' columns, checklists
  and dependencies and both boards' WIP limits. Enrichment adds depth only (assignees,
  labels, seeded comments and histories); no seeded comment contains the tester's text.
- ORB-1 and ORB-2 keep their anchors in `board.js`; ORB-3 is the route-table flag
  `handler: toggleChecklistItem, persist: true` (`false` skips the unit of work's write).
- The app stays CommonJS (no rewrite, plan D7), so the driver is loaded with
  `createRequire(__filename)("../vendor/node/node_modules/mongodb")` rather than
  `createRequire(import.meta.url)`; the resolved path is the same.

## Crash-type faults (plan §9, 2026-10-19)

| fault | class | where | what goes wrong |
|---|---|---|---|
| ORB-4 | uncaught exception | `app.js` (checklist click) | the item is looked up by position, not by its number: on card 13, whose numbers skip 3, ticking item 4 throws |
| ORB-5 | server error | `board.js` (pull in) | "nothing ready" is tested with `=== null`, but the search answers `undefined`: with room in Doing and only blocked cards in the backlog, pull in answers 500 |

- Seed change for ORB-4: card 13's checklist is numbered 1, 2, 4 (an item dropped before the board
  was set up); its texts and states are unchanged, and no workflow ticks it.
