# Skylark — a nature group's bird-sightings register

Members of a local nature group record the birds they see at the group's three sites (Marsh
hide, Wood edge, Lakeside), read the register newest first, load older records, filter by
site, flag records that look doubtful, and see how many birds the listed sightings add up to.

Node 22 built-ins only (`node:http`, `node:fs`, `node:crypto`); nothing to install.

## Run

```sh
PORT=8080 node src/server.js
```

Then open http://127.0.0.1:8080/ (it redirects to the feed, `/feed`).
**Docker:** `docker build -t skylark . && docker run -p 8080:8080 skylark`.

| variable | meaning |
|---|---|
| `PORT` | port to listen on (127.0.0.1; `HOST=0.0.0.0` listens on all interfaces) |
| `WTA_VOCAB` | `default` or `alt`: the same app with different wording |
| `WTA_FIXED_NOW` | the app's "now" (default `2026-06-10T09:00:00`); its date is "today" for new records |
| `WTA_DATA_DIR` | where session files are written (default: a new temp directory) |
| `WTA_TEST_HOOKS=1` | enables `GET /__wta/state` (the session's sightings and their sha256) |

**Tests:** `node --test "tests/**/*.test.mjs"` runs the unit tests of the register rules
(`tests/register.test.mjs`) and the HTTP tests (`tests/api.test.mjs`), which need a running
server started with `WTA_TEST_HOOKS=1` and `E2E_BASE_URL=http://127.0.0.1:8080/`.

## Views

| path | what it shows |
|---|---|
| `/feed` (`?site=marsh\|wood\|lake`) | the register, newest first: the first 6 sightings rendered by the server, the bird tallies for them, "Showing n of m", and **Load more** |
| `/sightings/new` | recording, step 1: which site |
| `/sightings/new/<site>` | recording, step 2: species, how many, date seen, notes; a live one-line summary of the unsent record |
| `/sightings/<ref>` | one sighting: its facts, its verification status, **Flag as doubtful** / **Restore to verified**, Share, Show on map |
| `/sites`, `/sites/<site>` | the three sites, their counts, the species seen there, and a link to the site's filtered feed |
| `/summary` | species totals for the current month (verified sightings only) |
| `/help` | how the register works |

## How it works

- **Feed: server-rendered first page + keyset "Load more".** `GET /feed` renders the six
  newest sightings for the chosen filter. The *Load more* button carries a keyset cursor, the
  `(date, id)` of the last row listed. `static/feed.js` asks `GET /feed/more?site=&after=`
  for the next page as JSON (`rows`, `more`, `after`, `birds`, `countedBirds`, `bySite`),
  appends the rows and updates "Showing n of m" (n is the number of rows actually in the list)
  and the tallies in place. There are no page numbers: the next page is everything strictly
  after the cursor in feed order (date descending, then id descending), so a page boundary
  inside a run of same-day records still lists each record exactly once. When no page
  remains the button is replaced by "End of sightings". The total ("of m") is the server's
  count for the filter when the page was rendered.
- **Tallies count verified sightings only.** A doubtful record stays listed (badged
  "Doubtful") but is left out of every bird count: the total and the per-site figures, on
  the server-rendered page and for every appended page alike.
- **Recording is two steps** with post/redirect/get: step 1 posts the site and is redirected
  to step 2; step 2 posts the record. A refusal (no species, a count outside 1-500, a date
  after today or before 2026-01-01, notes over 500 characters) sends the browser back to the
  form with the reason; a recorded sighting lands on the feed with "Sighting sN recorded".
- **Unsent input is a localStorage draft.** `static/record.js` writes the whole form to
  `localStorage` on every change of any control (selects included), synchronously, under a
  key the server namespaces per session (a hash of the session, never the session id itself)
  and per site. Reloading the form, coming back to it, or a refusal restores the draft. The
  draft is dropped when the server confirms the record (the feed page it lands on names the
  draft to drop) or when the member presses *Discard draft*.
- **Review.** Any member may flag a verified sighting as doubtful or restore a doubtful one.
  Each button is always shown; pressing it when there is nothing to change is refused with a
  message and changes nothing.
- **Persistence.** One JSON file per session (`wta_sid` cookie, HttpOnly) under
  `WTA_DATA_DIR/sessions/`, created from `seed/register.json`. A request reads the file,
  changes it and writes it back without awaiting in between, so requests of one session never
  interleave. One-time messages live in the same file but are not committed state.
- **Data, not labels.** Site names, species names and the group's verification categories
  ("Verified", "Doubtful") are register data in `seed/register.json`, the same under both
  vocabularies; every other visible word comes from `vocab/<profile>.json`.
- *Share* and *Show on map* on a sighting's page are conveniences of the browser only: Share
  reveals the record's address to copy, and maps need a connection the register does not use.

## Seed

22 verified sightings, s1-s22, dated 2026-05-20 to 2026-06-09 (Marsh hide 8, Wood edge 7,
Lakeside 7). In feed order the only same-day pairs are at positions 5/6 (s18 Lakeside, s17
Wood edge, 5 June), 11/12 (s12, s11, 30 May) and 17/18 (s6, s5, 25 May), each pair at two
different sites, so no single-site view has a tie and no seed page boundary (6/7, 12/13,
18/19) falls inside a pair. One record dated today shifts the 5 June pair onto the 6/7
boundary. Species (in form order): Blackbird, Blue tit, Heron, Kingfisher, Robin, Wren.

## Workflow graph

`feed → feed (Load more, filter) → sighting → feed | site`; `feed → record:where →
record:what → feed`; `feed → sites → site → feed (filtered)`; `feed → summary`, `feed → help`.

## Deviations from the plan

None in the faults, triggers, assertions, regions or patches. Two details of the plan's
workflow list are expressed differently so that the expectations stay data under both
vocabularies:

- "load every page → `#feed-end` present": the end marker's wording is a label ("End of
  sightings" / "No older records"), so presence is expressed as `contains: ""` (true exactly
  when the region exists) alongside the data checks `#shown-count` 22 and `#total-count` 22.
- "flag s20 doubtful → `#sighting-status` contains 'Doubtful'": the verification categories
  are register data (seed), not vocabulary, so 'Doubtful' is the same under both profiles and
  the expectation holds as planned. The alt profile's wording around them ("Mark as
  doubtful", "Record s20 marked as doubtful.") is phrased to match.
