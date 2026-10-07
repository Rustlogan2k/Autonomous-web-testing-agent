# Sideline: a Sunday football league's results, confirmations and table

The Brookvale & District Sunday League (Division One, six clubs) runs its results through
three front doors over the same data:

* **League secretary** (`/sec/...`) enters each score after the match and corrects scores.
* **Club captains** (`/club/<club>/...`) confirm their club's results or dispute a score.
* **Public** (`/table`, `/results`, `/teams/<club>`) sees a table and results built only from
  results *both* clubs have confirmed.

Every portal is a server-rendered projection of the same result rows (Python standard
library `http.server` + SQLite). Every change is an HTML form post answered with a redirect
(post/redirect/get); the page after the redirect shows a one-time message in `#flash`. The
only script (`src/static/sideline.js`) re-sorts the public table in the browser, toggles a
print layout and stars a club; none of it talks to the server.

**Run:** `PORT=8080 python src/server.py`, then open http://127.0.0.1:8080/.
**Docker:** `docker build -t sideline . && docker run -p 8080:8080 sideline`.
Options as in the suite conventions: `WTA_VOCAB` (`default`/`alt`), `WTA_FIXED_NOW`
(default `2026-06-10T09:00:00`), `WTA_DATA_DIR`, `WTA_TEST_HOOKS=1` (enables `GET /__wta/state`).
**Tests:** with the server running (test hooks on),
`E2E_BASE_URL=http://127.0.0.1:8080/ python -m unittest discover -s tests`.

## Layout

| file | role |
|---|---|
| `src/server.py` | HTTP plumbing, session cookie, the route table for the three portals, post/redirect/get |
| `src/store.py` | one SQLite database, rows partitioned by session (`wta_sid`), seeding, flash messages, the committed snapshot |
| `src/results.py` | the result state machine: enter, correct, confirm, dispute |
| `src/standings.py` | the public table, derived from confirmed results on every request |
| `src/pages.py` | every view's HTML; all labels from the vocabulary |
| `src/static/` | the table sort / print layout / follow star script, and the stylesheet |
| `seed/league.json` | six clubs, twelve fixtures (nine confirmed, three awaiting a score) |

## Rules

A fixture is *scheduled* until the secretary enters a score; it is then *provisional*.
Each club's captain confirms it once; when both have, it is *Confirmed* and counts in the
public table. A captain who has not confirmed may instead *dispute* it, with a reason (1 to
300 characters); a disputed result cannot be confirmed until the secretary saves a score
again. Saving the same score changes nothing; saving a different score is a correction: it
clears both confirmations and any dispute and returns the result to provisional.

The table ranks by points (win 3, draw 1), then goal difference, then goals for, then name.
Seed table: Cobham Town 8 pts (8-6), Ashby Rovers 5 (1-0), Eastleigh Albion 3 (5-5),
Dunmore 3 (5-6), Brookfield 2 (3-3), Fenwick 2 (2-4), based on 9 confirmed results. Table
cells carry ids by league position (`#pts-1` is the leader's points) and keep them when a
visitor re-sorts by goals for or points; the chosen order is kept in the address (`?sort=gf`).

## Workflows, decoys, state-dependent actions

See `app.json`. Workflows: F12 entered 0-0 and confirmed by both clubs (10 results counted);
a club disputes a newly entered F8; the public table's leader; sorting by goals for; a new
result waiting for the home captain; a correction sending a confirmed result back to both
clubs. Decoys: *Print layout* (table), *Follow this club* (team page). State-dependent:
*Confirm* (records a confirmation on a result awaiting it, refused on a confirmed one) and
*Save score* (nothing on an unchanged score, a correction on a changed one).

## Seeded faults

| fault | class / subtype | defect | file |
|---|---|---|---|
| SIDE-1 | calculation_error / calculation | the ranking key puts goals for before goal difference | `src/standings.py` |
| SIDE-2 | constraint_bypass / cross-page | the standings query counts a result either club has confirmed | `src/standings.py` |
| SIDE-3 | broken_transition / state-transition | a correction clears the confirmations but keeps the status | `src/results.py` |

## Deviations from the plan

1. **Workflow "a club disputes F8".** The plan expects `#result-status` to contain
   "Disputed". Workflows run under both vocabularies and the status word is a label (it
   reads "Challenged" under `alt`), which plan §6 rule 4 forbids as a workflow expectation.
   The workflow instead expects data: the dispute reason the tester typed in `#dispute-text`,
   the score `0–1` in `#result-score` and `0` in `#confirm-count`. Two regions were added
   for this (`dispute_text`, `result_score`); the faults are unchanged.
2. **Score selects start at the current score** (0-0 for a fixture with no score), with no
   blank option. This is what makes SIDE-2's two select steps removable (the plan's minimal
   length 8, which admission measured).
3. **SIDE-1's shortest trigger is shorter than planned (8, not 13).** Not a design change
   but a property of the planned fault set, measured at admission. On the all-faults build
   the minimizer found: enter F7 (default 0-0), only Brookfield confirms, open the table.
   SIDE-2's patch publishes that one-sided result, which leaves Brookfield (3 pts, 3-3)
   level with Eastleigh Albion (3 pts, 5-5) and Dunmore (3 pts, 5-6); SIDE-1's key ranks
   Dunmore (GD −1) above Brookfield (GD 0). The control without SIDE-1's patch is silent on
   that trace, so it is a genuine SIDE-1 trigger by the harness's rule. On a build with only
   SIDE-1's patch the shortest trigger is 11 steps (F7 0-0 confirmed by both clubs); the
   plan's 13-step witness (F7 2-0) is kept unchanged. Any planned design with SIDE-2's
   one-sided publishing has this shortcut, and a blank select option would only lengthen
   it to 10 while breaking SIDE-2's planned minimum, so the faults were left as designed.
4. **Additions** (not changes): a third benign trace (enter F8, the away club disputes it,
   open the public table), two extra workflows (a new result awaiting the home captain; a
   correction), and a public `nav-table` link, a secretary `nav-sec` link, a club `nav-club`
   link, `cancel-edit` and `back-to-club` links for navigation within each portal.
