# Tally — a members' club chair election

At its AGM, Riverside Rowing Club elects its Chair by ranked ballot. The returning officer
issues ballot papers one at a time; each member ranks up to three of the four candidates at the
voting kiosk; the officer closes the poll; the instant-runoff count is shown round by round; and
the officer publishes the declaration.

Node 22 built-ins only (`node:http`, `node:fs`, `node:crypto`); nothing to install.

## Run

```sh
PORT=8080 node src/server.js
```

Then open http://127.0.0.1:8080/ (it redirects to `/poll`).
**Docker:** `docker build -t tally . && docker run -p 8080:8080 tally`.

| variable | meaning |
|---|---|
| `PORT` | port to listen on (127.0.0.1; `HOST=0.0.0.0` listens on all interfaces) |
| `WTA_VOCAB` | `default` or `alt`: the same app with different wording |
| `WTA_FIXED_NOW` | the app's "now" (default `2026-06-10T09:00:00`); the poll keeps no dates, so it is only reported |
| `WTA_DATA_DIR` | where session files are written (default: a new temp directory) |
| `WTA_TEST_HOOKS=1` | enables `GET /__wta/state` (the session's poll and its sha256) |

**Tests:** `node --test "tests/**/*.test.mjs"` runs the unit tests of the count, the
declaration and the phase machine (`tests/count.test.mjs`, no server needed; they pin the three
verified counts) and the HTTP tests (`tests/api.test.mjs`), which need a server started with
`WTA_TEST_HOOKS=1` and `E2E_BASE_URL=http://127.0.0.1:8080/`.

## Views

| path | what it shows |
|---|---|
| `/poll` — open | officer panel (status, papers issued, ballots cast, **Next voter**, **Close poll**) and the voting kiosk (the paper at the kiosk, **Start ballot**, three rank selects, **Cast ballot**, a status line; *Print a blank paper*) |
| `/poll` — closing | the figures, how many unused papers will be voided, **Close and count** / **Keep the poll open** |
| `/poll` — counted | the count, opened on its final round: each candidate's votes, continuing and exhausted ballots, the highest tally, the round's outcome; **Previous round** / **Next round**; **Publish declaration** |
| `/poll` — declared | as counted, with the declared result |
| `/declaration` | the printable declaration (or "no result declared yet") |
| `/register` | the 18 members, their paper numbers and whether they have voted |
| `/ballots` | the ballot audit, once the poll is closed (sealed before) |
| `/help` | the rules of the count |

## How it works

- **One URL, a server-side phase.** `/poll` is rendered from the poll's phase:
  `open → closing → counted → declared` (closing can return to open; nothing else goes back).
  Phase changes are form POSTs (`/poll/issue`, `/poll/close`, `/poll/cancel-close`,
  `/poll/confirm-close`, `/poll/declare`) answered with 303 to `/poll`, carrying a one-time
  message; a successfully published declaration lands on `/declaration` instead.
- **Kiosk loop.** *Next voter* issues the next paper (numbered from 12) to the next member on
  the register without one, and puts it at the kiosk; it is refused while the kiosk's paper is
  unused. *Start ballot* (`POST /api/kiosk/start`) opens the ballot panel; *Cast ballot*
  (`POST /api/ballots {paper, preferences}`) records the ranking. Both are fetch requests: the
  panel stays on screen and the status line and the officer's figures are updated in place from
  each JSON answer, so each press reaches the server. One paper, one ballot: a cast on a paper
  already used is answered `200 {ok: false}` ("Paper 12 has already been used") and records
  nothing. A ranking needs a 1st choice, no gaps and nobody twice.
- **Closing.** *Close poll* is refused while a voter is part-way through a ballot (a started,
  unused paper). Confirming the close voids any issued but unused paper and makes the count,
  once; it is stored with the poll.
- **The count** (`src/count.js`). Each round every ballot counts for its highest-ranked
  candidate still in the count; a ballot with none left is exhausted. A candidate with more than
  half of the continuing ballots is elected; otherwise the candidate with the fewest votes is
  eliminated (ties: fewer first preferences, then the one later on the paper). Two candidates
  left level end the count as *Tied*: nobody is elected and nothing can be declared.
- **Round stepper.** The count's rounds are embedded in the page as JSON; `static/poll.js`
  renders one round at a time, opening on the final round.
- **Declaration** (`src/declaration.js`). Publishing names the candidate the final round elected,
  with that candidate's final-round votes and the continuing ballots in that round.
- **Persistence.** One JSON file per session (`wta_sid` cookie, HttpOnly) under
  `WTA_DATA_DIR/sessions/`, created from `seed/poll.json`. A request reads it, changes it and
  writes it back without awaiting in between. The one-time message is not committed state.
- **Data, not labels.** The club, the office, candidate and member names are poll data; every
  other visible word comes from `vocab/<profile>.json`.
- *Print a blank paper* shows a printable blank ballot in the browser only; *Rules of the count*
  is a read-only page.

## Seed and verified counts

Candidates in ballot order: Ada Okoro (`cand-a`), Bram Lindqvist (`cand-b`), Cleo Mendes
(`cand-c`), Dev Patel (`cand-d`). 18 members; papers 1-11 issued to the first eleven and used by
the ballots (A,B,D) (A,D) (B,A) (B,C) (B,D,A) (C,B) (D) (D,A,B) (D,B) (D,B,A) (D,C,B).

- seed: R1 A2 B3 C1 D5, R2 A2 B4 D5, R3 B5 D6 — Dev elected with 6 of 11;
- seed + (D): R1 D 6 of 12 (exactly half: not elected), R2 A2 B4 D6, R3 B5 D7 — Dev, 7 of 12;
- seed + (B) + (A,B): R1 A3 B4 C1 D5, R2 A3 B5 D5, R3 B7 D6 — Bram, 7 of 13, although Dev led round 1.

## Seeded faults

| fault | class / subtype | where | what goes wrong |
|---|---|---|---|
| TALLY-1 | constraint_bypass / form-submission | `src/poll.js` | the paper is looked up without "not yet used": pressing Cast ballot repeatedly records the ballot several times (16 ballots from 12 papers) |
| TALLY-2 | calculation_error / calculation | `src/count.js` | the majority test is `>=` instead of `>`: exactly half of the continuing ballots elects (Dev with 6 of 12 in round 1) |
| TALLY-3 | state_corruption / wrong-target | `src/declaration.js` | the declaration takes the first round's leader instead of the final round's: it names Dev with 6 of 13 while the count elected Bram with 7 of 13 |

Each fault is one literal find/replace in `app.json`; witnesses and assertions are there and in
`validation_suite/PLAN.md` §3.7.

## Deviations from the plan

- **TALLY-3 witness lands on `/declaration`.** The plan's 12-step witness ends with *Publish
  declaration* and its assertion reads `/declaration$`, but the plan also says every phase change
  is answered with 303 back to `/poll`, so that witness would never reach the page the assertion
  reads. The smallest change that keeps the fault, its class, defect type, witness and assertion:
  a successful *Publish declaration* is answered with 303 to the printable `/declaration`
  (refusals still return to `/poll`). The workflow "publish the declaration" therefore ends on
  `/declaration` without a further click, and the state-dependent check for publishing goes back
  to `/poll` (`nav-poll`) before pressing the button again.
- **"Outstanding paper".** The plan says *Close poll* acts "with no ballot paper outstanding" while
  the closing page warns that unused papers will be voided. Both hold here by reading
  "outstanding" as a paper whose ballot has been started but not cast (a voter at the kiosk):
  closing is refused then; an issued paper not yet started is voided at close.
- A navigation link `nav-poll` (back to `/poll` from the other pages) and a `kiosk-paper` element
  (the number of the paper at the kiosk) are added; neither is a fault region.
- One workflow beyond the plan's four: "a member ranks two candidates".
