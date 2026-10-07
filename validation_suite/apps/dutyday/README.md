# Dutyday — a food bank's volunteer rota

The shift rota of a community food bank: three shifts a day, Monday to Saturday (morning sort
09:00–12:00, afternoon pack 13:00–17:00, evening deliver 17:30–20:00), two volunteers each.
The coordinator fills the month's shifts from a calendar grid; volunteers check their week and
withdraw from shifts on their own page.

**No JavaScript at all.** Pages are rendered by a Python server (standard library + SQLite) and
everything a page shows is chosen by its address: the month (`/rota?month=2026-07`), the day
(`/rota/day/2026-07-01`) and a volunteer's week (`/volunteers/priya?week=2026-06-29`). Every
change is an HTML form post answered with a 303 redirect to the page that shows the result
(post/redirect/get); only a one-time notice travels with the session from the post to that page.
Withdrawing goes through a confirmation page instead of a dialog.

**Run:** `PORT=8080 python src/server.py`, then open http://127.0.0.1:8080/rota.
**Docker:** `docker build -t dutyday . && docker run -p 8080:8080 dutyday`.
Options as in the suite conventions: `WTA_VOCAB` (`default`/`alt`), `WTA_FIXED_NOW` (default
`2026-06-10T09:00:00`, a Wednesday), `WTA_DATA_DIR`, `WTA_TEST_HOOKS=1` (enables `GET /__wta/state`).
**Tests:** with the server running, `E2E_BASE_URL=http://127.0.0.1:8080/ python -m unittest discover -s tests`.

## Layout

| file | role |
|---|---|
| `src/server.py` | HTTP plumbing, sessions, the route table and the post/redirect/get handlers |
| `src/rota.py` | the rules: season calendar, shift statuses, weekly limit, assignments, session seeding |
| `src/pages.py` | every page, rendered from the vocabulary profile |
| `src/static/rota.css` | the only static asset |
| `seed/rota.json` | season (1 June – 31 August 2026), slots, six volunteers, 27 assignments |

## Views

* `/rota?month=YYYY-MM` — month grid (Mon–Sun); each day shows its open places, read from the
  shifts' stored statuses. Previous/next month within the season, *Go to a date*, *Print this
  month* (`/rota/print?month=`), *Calendar feed* (shows the feed address as text).
* `/rota/day/YYYY-MM-DD` — the three shifts: roster (each name links to that volunteer's week),
  `n of 2 needed`, status (Open / Needs 1 / Full), *Remove* per volunteer and an *Assign* form per
  shift. Sundays are closed; days before today are read-only.
* `/volunteers` and `/volunteers/<v>?week=YYYY-MM-DD` — the week's shifts with *Withdraw* links,
  hours against the 12-hour limit, and the next shift with the number of days to go.
* `/assignments/<id>/withdraw` — confirmation page (*Yes, withdraw* / *Keep this shift*).
* `/help` — the rules.

## Rules

Two places per shift; a shift's status is derived from its assignments and stored on the shift
row, refreshed by every path that changes assignments. Assigning is refused on a past day, on a
full shift, for a volunteer already on that shift or with another shift that day, and when it
would take the volunteer over 12 hours in the Monday–Sunday week. A withdrawal from a shift that
has already happened is refused.

## Seeded faults

| fault | class / subtype | what goes wrong |
|---|---|---|
| DUTY-1 | constraint_bypass / missing-validation | the weekly limit is summed only over the part of the week in the shift's month |
| DUTY-2 | calculation_error / calculation | days until the next shift subtracts day-of-month numbers instead of dates |
| DUTY-3 | broken_transition / state-transition | withdrawing from the volunteer's page does not refresh the shift's stored status |

Witnesses, assertions and patches are in `app.json`.

## Deviations from the plan

* **Regions added for the workflows.** The plan's workflows read "the day roster" and "the URL
  /rota/day/2026-06-15", but the harness's workflow check reads regions only. Four regions were
  added: `roster_morning`, `roster_afternoon`, `roster_evening` (`#roster-<slot>`, the roster
  list) and `day_date` (`#day-date`, the day page's heading, e.g. "Monday 15 June 2026"). No fault
  assertion uses them.
* **One shift per volunteer per day.** Each roster name is a link with the id `vol-<volunteer>`
  (the witnesses click `vol-priya` and `vol-ben` on a day page). If one volunteer could be on two
  shifts of the same day, that id would occur twice on the page. The rota therefore refuses a
  second shift on the same day ("Ana already has a shift that day"), a common food-bank rule.
  The seed, every trace and every fault are unaffected (no seeded volunteer has two shifts on a
  day, and no trace assigns one).
* **The season.** The plan does not bound the calendar; the rota covers June–August 2026 (month
  and week paging stop at its ends), so every shift row exists from the start of a session.
* **A fifth workflow** (Ana withdraws from Friday 12 June) exercises the confirmation page under
  both vocabularies. It is additional to the plan's four.
