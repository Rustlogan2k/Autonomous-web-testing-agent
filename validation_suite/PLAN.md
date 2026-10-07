# Held-out validation set: plan (8 applications, 24 seeded faults)

**Status:** planned, not built. **Date:** 2026-10-07. **Machine-readable twin:** `validation_suite/plan.json`
(generated from the same design and self-checked: grammar kinds, regions, trace schema, witness lengths,
expression safety).

**What this is.** The final, never-seen evaluation set for the functional tester. Each application is a
small, coherent web app with a real backend, built to `e2e_suite/CONVENTIONS.md` (zero dependencies,
session-cookie state, `/healthz`, `/__wta/state`, two vocabulary profiles, deterministic, offline, faults
as one literal find/replace patch each), so the unchanged harness in `e2e_suite/harness/` can admit it.
When built, the apps live in `validation_suite/apps/<app_id>/` with the suite's layout.

**Independence.** Designed without reading the tester's strategy code (`hybrid/`, `rl/`), its v2 plan,
its v2 pre-registration or any `reports/v2_*` file. Fault designs come from standard functional fault
taxonomies (computation and rounding, boundary/off-by-one, data and state handling, interface/navigation,
persistence, input validation). The only tester facts used are the ones every fault must respect to be
fair at all: the action space (`envs/action_registry.py`), the fixed input values (`envs/input_values.py`)
and the oracle grammar (`oracle/assertions.py`, `oracle/faults.py`, `harness/ext_oracle.py`).
Security testing is out of scope; every fault is a functional defect.

## 1. The set at a glance

| # | app_id | Domain | Architecture / interaction pattern | Stack | Docker | Views | Est. lines |
|---|---|---|---|---|---|---|---|
| 1 | `dutyday` | Volunteer shift rota for a community food bank | Zero-JavaScript server pages; month **calendar grid**; all view state in the URL (query strings); confirmation pages instead of dialogs | Python stdlib `http.server` + sqlite | yes | 7 | 640 |
| 2 | `markbook` | School markbook for one class + each student's own view | Server-rendered **matrix with click-to-edit cells** (fetch returns recomputed row/column); **role route trees** `/teacher/…` and `/student/…` | Python stdlib `wsgiref` + sqlite (file per session) | yes | 7 | 720 |
| 3 | `sideline` | Sunday football league: results, confirmations, table | **Multi-role portals** (secretary / club captains / public) over shared data; derived standings; client-sortable table | Python stdlib `http.server` + sqlite | yes | 9 | 700 |
| 4 | `skylark` | Citizen-science bird-sightings register | Server-rendered first page + **"Load more" keyset-cursor appends**; tallies over everything loaded; two-step record form with a localStorage draft | Node built-ins + JSON files | yes | 8 | 760 |
| 5 | `larder` | Recipe box: scale servings, metric/US measures, shopping list | **History-API SPA** (pushState/popstate on real paths) with a **master–detail split pane** | Node built-ins + JSON files | yes | 7 | 760 |
| 6 | `fosterly` | Animal-rescue foster-carer application | One page of **accordion sections with per-field autosave**, nested conditional sub-section, completeness gate, **save-and-resume by reference** | Node built-ins + JSON files | yes | 6 | 720 |
| 7 | `tally` | Members' club chair election (ranked ballot, instant-runoff count) | **Single URL whose view is a server-side phase** (open / closing / counted / declared); kiosk loop with a fetch ballot panel; client **round stepper** | Node built-ins + JSON files | yes | 8 | 760 |
| 8 | `quizcraft` | Survey authoring (a cycling club's members' survey) | **Command-stack editor with undo/redo** over a revisioned document; editor / preview / history panes on one URL (`replaceState`) | Python stdlib `http.server` + sqlite | **no** | 7 | 820 |

Stacks: 4 Python, 4 Node. `quizcraft` deliberately has no Dockerfile (and no compose file): the product
intake (`intake/runner.py::detect_build_definition`) refuses it today, exactly as with `wayfarer`; this time
the refused repository is a Python one, so a future stack detector is exercised on a second language.

## 2. What "genuinely different" means, and the difference matrix

Rule applied to every app: a domain not used by any of the 14 existing apps (and not a near-duplicate of
shop, banking, booking/appointments, support ticketing, expenses, kanban, inventory grid, travel planner,
media library, account settings or asset register); an architecture or interaction pattern none of the 14
uses; and a different workflow shape or state model (most differ in both). The nearest existing app is the
one a reviewer would most plausibly call "the same thing again".

| New app | Nearest existing | Domain | Architecture / interaction | Workflow shape | State model |
|---|---|---|---|---|---|
| `dutyday` | `clinicflow` (also `bagstreet`) | staff-side volunteer rostering vs patient booking | zero JS, everything in the URL, month calendar grid, confirmation pages vs server wizard + client conditionals | calendar hub with day/volunteer spokes vs branching linear wizard | assignments + a stored derived status per shift; no draft row |
| `markbook` | `stackroom` (also `ledgerly`) | school marks vs warehouse stock | 2-D matrix, click-to-edit cells answered with recomputed derived cells; second role's view of the same data vs paged JSON grid | matrix hub + role view vs paged grid hub | template sqlite file per session; derived columns; per-row visibility |
| `sideline` | `ledgerly` (also `helpline`) | league results/standings vs expense claims | three path-prefixed role portals, derived ranking, client-sortable table vs "viewing as" switch + form island | two-party confirmation machine feeding a ranking vs approval chain | status + per-club flags, standings recomputed per request vs append-only event log |
| `skylark` | `stackroom` (also `bagstreet`) | wildlife records vs stock lines | SSR first page + client-appended keyset pages, no page numbers vs client-rendered offset pages | incrementally growing feed vs paged grid | server records + localStorage draft of unsent input |
| `larder` | `wayfarer` (also `orbit`, `tidewater`) | recipes and kitchen measures vs travel | History-API routes + popstate rendering, master–detail split pane vs hash router | master–detail (list stays on screen) vs hub-and-spoke | all committed server-side; route state in History API; no localStorage |
| `fosterly` | `account_hub` (also `support_portal`) | foster-carer application vs SaaS settings / broadband request | accordion, per-field PATCH autosave, nested conditional section, resume by reference vs sessionStorage panels / static multi-page stages | any-order sections behind a completeness gate vs linear stages | field-level server draft written on every change + immutable submissions |
| `tally` | `helpline` (also `booking_desk`, `ledgerly`) | election counting vs support tickets | one URL whose content is a server-side phase, fetch ballot panel, client round stepper vs per-ticket pages with HTML fragments | repeated kiosk loop then one-way close/count/declare vs per-item status machine | phase + papers + ballots; count derived once at close |
| `quizcraft` | `wayfarer` (also `orbit`) | survey authoring vs travel | in-memory command stack (undo/redo), respondent preview with skip logic, single URL with replaceState panes vs hash-routed planner | edit/preview loop on one screen vs hub-and-spoke | working copy + undo/redo stacks in page memory only; server append-only revisions; Python not Node |

Interaction patterns new to the evaluation corpus (none of the 14 has them): History-API routing with
popstate rendering, master–detail split view, calendar grid, zero-JavaScript app, click-to-edit matrix with
derived cells, accordion with field autosave, save-and-resume by reference, keyset "load more", localStorage
draft restore of a form, undo/redo command stack, respondent preview with skip logic, single-URL phase
machine, kiosk loop, round-by-round results stepper, path-prefixed multi-role portals, client-side table
re-sort, unit-system rendering.

New `workflow_graph.kind` labels used (the harness's diversity module only counts its own eight; extra
labels are harmless): `calendar-grid`, `master-detail`, `editor-loop`, `accordion`, `save-and-resume`,
`feed`, `multi-role-portal`, `multi-role-views`, `phase-machine`, `kiosk-loop`, `round-stepper`.

## 3. The applications

Region names map to `#id` selectors exactly as in `app.json` (`regions`). Trigger traces use the replay schema; typed values are the tester's own fixed values. "Minimal" is the shortest trigger the design admits (the harness's ddmin should not get below it).

### 3.1 `dutyday` — Dutyday

**Domain:** volunteer shift rota for a community food bank. **User goal:** Fill the month's sorting, packing and delivery shifts with volunteers within each volunteer's weekly hours limit, and let volunteers withdraw from shifts.

**Architecture:** `server-rendered-calendar+zero-javascript`. No JavaScript at all. Month, day and week are carried in paths and query strings (GET links); every change is an HTML form POST answered with 303 to a GET page (post/redirect/get); withdrawal goes through a confirmation page instead of a dialog.

**Stack:** `python-stdlib(http.server)+sqlite`, started with `python src/server.py`. **Dockerfile:** yes  
**Persistence:** sqlite rows partitioned by session; each shift row stores a derived status (Open / Needs n / Full) kept in step with its assignments.  
**Entry:** `/rota`. **Workflow shape:** calendar-grid, hub-and-spoke, revisit — month grid is the hub; days and volunteer weeks are spokes; month and week paging by links; confirmation pages for withdrawal.  
**Features:** server-rendered, zero-javascript, calendar-grid, query-string-state, server-state, table, cross-page-state, dynamic-validation, date-arithmetic.  
**Nearest existing app:** `clinicflow` (also `bagstreet`); differs on *domain*: staff-side volunteer rostering vs patient appointment booking; *architecture*: zero-JavaScript server pages with all view state in the URL, vs a server wizard with client conditionals and a draft row; *interaction*: month calendar grid and week paging; confirmation pages instead of dialogs; *workflow shape*: calendar hub with day/volunteer spokes vs branching linear wizard; *state model*: assignments plus a stored derived status per shift; no draft.

**Views:** /rota?month=YYYY-MM (month grid Mon-Sun; each day shows open places); /rota/day/YYYY-MM-DD (three shifts: roster, status, assign form); /volunteers; /volunteers/<v>?week=YYYY-MM-DD (that week's shifts, hours against the 12-hour limit, next shift); /assignments/<id>/withdraw (confirmation page); /rota/print?month= (print layout); /help.

**Seed and rules:** Clock 2026-06-10 (Wed). Shifts Mon-Sat: morning sort 09:00-12:00 (3 h), afternoon pack 13:00-17:00 (4 h), evening deliver 17:30-20:00 (2.5 h); 2 volunteers each; Sundays closed; days before 10 Jun read-only. Volunteers (select order): Ana, Ben, Chidi, Dana, Ezra, Priya. Priya: Sat 13 Jun morning, Wed 1 Jul afternoon, Thu 2 Jul afternoon, Fri 3 Jul evening (10.5 h in the week of 29 Jun). Ben: only past June shifts, nothing upcoming. Ana: Fri 12 Jun afternoon (with Dana; assignment id a14, shift Full) and Mon 15 Jun morning. Every volunteer with an upcoming shift has one in June. Weekly limit 12 h per ISO week (Mon-Sun).

**Controls (DOM ids):** `month-prev`, `month-next`, `day-YYYY-MM-DD`, assign-morning|afternoon|evening (select), `assign-<slot>-go`, `remove-<assignment>`, `vol-<id>`, `nav-rota`, `nav-volunteers`, `withdraw-<assignment>`, `confirm-withdraw`, `cancel-withdraw`, jump-date (date) + jump-go, `week-prev`, `week-next`, `print-month`, `calendar-feed`.  
**Regions:** `month_title`=`#month-title`, `status_morning`=`#status-morning`, `status_afternoon`=`#status-afternoon`, `status_evening`=`#status-evening`, `count_morning`=`#count-morning`, `count_afternoon`=`#count-afternoon`, `count_evening`=`#count-evening`, `flash`=`#flash`, `week_hours`=`#week-hours`, `next_in`=`#next-in`, `next_when`=`#next-when`, `vol_shifts`=`#vol-shifts`. #count-afternoon holds only the number of volunteers assigned (0, 1 or 2; 'of 2 needed' sits outside the region); #status-afternoon 'Full' / 'Needs 1' / 'Open'; #week-hours e.g. '10.5 of 12'; #next-in only the whole number of days until the next shift (absent when there is none).

**Decoys:** print-month (print layout only); calendar-feed (shows a fixed feed address as text).  
**State-dependent actions:** the slot's Assign is refused on a Full shift and assigns on a shift with places; confirm-withdraw is refused for a past shift (before 10 Jun) and withdraws from a future one.  
**Workflows (each also run under the `alt` vocabulary; expectations are data, never labels):** assign Chidi to Mon 15 Jun morning -> day roster contains 'Chidi'; page to July -> #month-title contains 'July 2026'; try Priya on Sat 4 Jul morning (over the limit) -> #flash contains '12'; jump to 2026-06-15 -> URL /rota/day/2026-06-15.  
**Benign traces (must stay silent on clean and buggy):** `day-2026-06-15` → select `assign-morning`=chidi → `assign-morning-go` → `vol-chidi` | `month-next` → `day-2026-07-04` → select `assign-evening`=ana → `assign-evening-go` → `nav-rota`.

**DUTY-1** — `constraint_bypass` / `missing-validation` — *period boundary: weekly limit summed over only the calendar month's part of the week* (defect type not in the existing 14 apps).

- *Defect:* The 12-hour weekly limit is checked over the part of the ISO week that falls in the same month as the shift being assigned. For a week spanning a month end, hours on the other side of the boundary are ignored, so Priya can be given a fourth shift that takes her to 14.5 hours.
- *Expected behaviour:* Nobody is rostered for more than 12 hours in any Monday-Sunday week.
- *Trigger (4 steps; minimal 4):* `day-2026-06-30` → select `assign-afternoon`=priya → `assign-afternoon-go` → `vol-priya`
- *Observable violation:* Priya's week of 29 June shows 14.5 of 12 hours.
- *Oracle (extension):* `region_arithmetic` on `/volunteers/[a-z]+`: `hours <= 12` with hours=week_hours
- *Patch (`src/rota.py`):* week_window(day) clips the Monday-Sunday window to the month of `day`. Sketch: `return monday, monday + timedelta(days=6)  # the ISO week, whatever the month` → `return max(monday, day.replace(day=1)), min(monday + timedelta(days=6), month_end(day))  # the ISO week, whatever the month`
- *Silent on clean:* Seeds keep every week at 12 h or less and the clean check sums the whole ISO week; the volunteer page shows the stored total.
- *Not triggered by the other patches:* DUTY-2 changes only the days-until figure; DUTY-3 only the status refresh on withdrawal.

**DUTY-2** — `calculation_error` / `calculation` — *date arithmetic: day-of-month subtraction across a month boundary* (defect type not in the existing 14 apps).

- *Defect:* 'Days until next shift' subtracts day-of-month numbers instead of dates. Within June it is right; for a next shift in July it goes negative (1 - 10 = -9).
- *Expected behaviour:* The days until a volunteer's next shift is the calendar difference between the two dates.
- *Trigger (5 steps; minimal 5):* `month-next` → `day-2026-07-01` → select `assign-morning`=ben → `assign-morning-go` → `vol-ben`
- *Observable violation:* Ben's page says his next shift (Wed 1 Jul) is -9 days away; it is 21.
- *Oracle (existing):* `text_absent` on `/volunteers/[a-z]+`: region `next_in` expected "-"
- *Patch (`src/rota.py`):* days_until(shift_day) = (shift_day - TODAY).days becomes shift_day.day - TODAY.day. Sketch: `return (shift_day - TODAY).days` → `return shift_day.day - TODAY.day`
- *Silent on clean:* 'Next shift' only ever lists shifts on or after today, so the clean figure is >= 0; #next-in is absent when there is no next shift; seeds give every volunteer with upcoming shifts a June one, so only a newly assigned (or newly uncovered) July shift exposes it.
- *Not triggered by the other patches:* DUTY-1's patch affects limit checks (Ben is far under the limit); DUTY-3's only shift status.

**DUTY-3** — `broken_transition` / `state-transition` — *derived status not recomputed on an alternate path*.

- *Defect:* Withdrawing from the volunteer's own page removes the assignment but does not recompute the shift's stored status (removing from the day page does), so the shift stays 'Full' with one volunteer and the month grid stops offering it.
- *Expected behaviour:* A shift shown as Full always has both volunteers.
- *Trigger (6 steps; minimal 6):* `nav-volunteers` → `vol-ana` → `withdraw-a14` → `confirm-withdraw` → `nav-rota` → `day-2026-06-12`
- *Observable violation:* Fri 12 Jun afternoon reads 'Full' with 1 volunteer assigned (of 2 needed).
- *Oracle (existing):* `text_absent` on `/rota/day/\d{4}-\d{2}-\d{2}$`: region `status_afternoon` expected "Full" **AND** `text_absent` on `/rota/day/\d{4}-\d{2}-\d{2}$`: region `count_afternoon` expected "1"
- *Patch (`src/server.py`):* the volunteer-page withdraw handler loses its refresh_status() call. Sketch: `refresh_status(conn, sid, shift_id)  # a withdrawal can reopen the shift` → `(removed)`
- *Silent on clean:* Every clean path that changes assignments recomputes the status in the same transaction; on Sundays (no shifts) the regions are absent, which satisfies text_absent.
- *Not triggered by the other patches:* DUTY-1 and DUTY-2 patches change no status; the witness assigns nobody.

### 3.2 `markbook` — Markbook

**Domain:** school assessment: a teacher's markbook for one class, with each student's view of published marks. **User goal:** Enter and correct marks, see each student's weighted percentage and level, set assessment maxima, publish assessments, keep private notes, and check what a student sees.

**Architecture:** `server-rendered-matrix+click-to-edit-cells+role-route-trees`. The markbook is a server-rendered students x assessments matrix. Each cell is a button; clicking it swaps in a single number input with Save mark/Cancel (one editor at a time); Save sends a fetch PATCH and the server answers with the recomputed row (weighted %, level) and column (class average) as JSON, which replace those cells. Teacher pages live under /teacher/..., the student's own view under /student/<s>.

**Stack:** `python-stdlib-wsgiref+sqlite`, started with `python src/server.py`. **Dockerfile:** yes  
**Persistence:** one sqlite file per session copied from a seeded template; marks, assessments (max, weight, published), notes with a visibility column.  
**Entry:** `/teacher`. **Workflow shape:** data-grid, hub-and-spoke, multi-role-views, revisit — matrix hub with assessment and student spokes; a second role's view of the same data.  
**Features:** server-rendered, inline-edit, table, multi-role, server-state, enable-disable, dynamic-validation, cross-page-state, derived-columns.  
**Nearest existing app:** `stackroom` (also `ledgerly`); differs on *domain*: school marks vs warehouse stock; *architecture*: server-rendered 2-D matrix with click-to-edit cells returning recomputed rows/columns, plus role route trees; vs client-rendered paged grid over a JSON API; *interaction*: click a cell to edit, derived columns update in place; publish/withdraw; preview as another role; *workflow shape*: matrix hub + second-role view vs grid hub with paging; *state model*: template sqlite file per session; derived values recomputed server-side; visibility per row.

**Views:** /teacher (classes hub); /teacher/classes/10b (markbook matrix: 6 students x 4 assessments + weighted % + level + class averages); /teacher/classes/10b/assessments/<a> (maximum marks, publish / withdraw); /teacher/classes/10b/students/<s> (marks, canned feedback, private note); /student/<s> (student view: published marks, provisional %, feedback); /teacher/classes/10b/report (level distribution); /help.

**Seed and rules:** Assessments: Homework max 20 (20%, published), Quiz max 30 (20%, published), Project max 40 (30%, published), Exam max 120 (30%, draft). Weighted % = hw*20/max_hw + quiz*20/max_quiz + proj*30/max_proj + exam*30/max_exam, computed exactly (fractions) and shown to 1 decimal, half-up; '-' until all four marks exist. Level 5 >= 80, 4 >= 70, 3 >= 60, 2 >= 50, else 1. Students s1 Amara (18, 26, 36, 85 -> 83.6), s2 Bilal (complete; no notes), s3 Chen (17, 20, 35, exam blank), s4 Dara, s5 Elif, s6 Femi (complete). Marks are whole numbers 0..max; a maximum is a whole number >= 1 and may not be lowered below a mark already given. Canned feedback is a select (no free text); the private note is a textarea shown as text with 'Edit note' once saved.

**Controls (DOM ids):** `class-10b`, mark-<s>-<a> (cell buttons, 24), mark (number input, one at a time), `save-mark`, `cancel-mark`, `assess-<a>`, max-marks (number), `save-assessment`, `publish-assessment`, `withdraw-assessment`, `back-to-markbook`, `student-<s>`, feedback (select), `save-feedback`, private-note (textarea), `save-note`, `edit-note`, `preview-as-student`, `nav-report`, `export-csv-help`.  
**Regions:** `pct_s1`=`#pct-s1`, `pct_s3`=`#pct-s3`, `level_s3`=`#level-s3`, `mark_s3_hw`=`#mark-s3-hw`, `mark_s3_quiz`=`#mark-s3-quiz`, `mark_s3_proj`=`#mark-s3-proj`, `mark_s3_exam`=`#mark-s3-exam`, `max_hw`=`#max-hw`, `max_quiz`=`#max-quiz`, `max_proj`=`#max-proj`, `max_exam`=`#max-exam`, `cell_error`=`#cell-error`, `assess_flash`=`#assess-flash`, `student_view`=`#student-view`, `student_comments`=`#student-comments`. Mark cells are buttons whose text is only the mark (or '-'); #max-* only the maximum; #pct-* e.g. '67.1%'; #student-comments lists comments the student may see, each with its badge (canned feedback is badged 'From your teacher'; a private note is badged 'Private note' wherever it is rendered).

**Decoys:** export-csv-help (explains how to export; changes nothing); nav-report (read-only report).  
**State-dependent actions:** save-assessment refuses a maximum below an existing mark and saves one above all marks; publish-assessment publishes a draft assessment; on a published one the same button is refused.  
**Workflows (each also run under the `alt` vocabulary; expectations are data, never labels):** give Chen 42 in the exam -> #pct-s3 contains '67.1'; publish the exam -> student view of s1 shows the exam mark; raise the quiz maximum to 42 -> #max-quiz contains '42'; choose canned feedback for Bilal -> student view lists it.  
**Benign traces (must stay silent on clean and buggy):** `class-10b` → `mark-s2-quiz` → type `mark` boundary_min → `save-mark` → `student-s2` → `preview-as-student` | `class-10b` → `assess-quiz` → type `max-marks` 42 (number) → `save-assessment` → `back-to-markbook`.

**MARK-1** — `calculation_error` / `calculation` — *rounding: truncation instead of half-up* (defect type not in the existing 14 apps).

- *Defect:* The weighted percentage is quantised with ROUND_DOWN instead of ROUND_HALF_UP, so 67.0833...% is shown as 67.0 instead of 67.1 (and any value with a second decimal of 5 or more is shown a tenth too low).
- *Expected behaviour:* Percentages are rounded to the nearest tenth, halves up.
- *Trigger (4 steps; minimal 4):* `class-10b` → `mark-s3-exam` → type `mark` 42 (number) → `save-mark`
- *Observable violation:* Chen's weighted percentage shows 67.0% for marks 17/20, 20/30, 35/40, 42/120 (exactly 67.083%).
- *Oracle (extension):* `region_arithmetic` on `/teacher/classes/10b$`: `pct == hw * 20 / mhw + quiz * 20 / mquiz + proj * 30 / mproj + exam * 30 / mexam` with pct=pct_s3, hw=mark_s3_hw, quiz=mark_s3_quiz, proj=mark_s3_proj, exam=mark_s3_exam, mhw=max_hw, mquiz=max_quiz, mproj=max_proj, mexam=max_exam; tolerance 0.0501
- *Patch (`src/grades.py`):* percent() quantises with decimal.ROUND_DOWN instead of decimal.ROUND_HALF_UP (import the decimal module, so the faulty constant needs no new import). Sketch: `.quantize(TENTH, rounding=decimal.ROUND_HALF_UP)` → `.quantize(TENTH, rounding=decimal.ROUND_DOWN)`
- *Silent on clean:* Half-up rounding of the exact value differs from it by at most 0.05 for every reachable mark (0..max) and maximum (>= 1); a blank mark makes an operand non-numeric (inconclusive, never violated); maxima are operands, so raising one keeps the identity.
- *Not triggered by the other patches:* MARK-2's patch changes which maxima are accepted (operands track them); MARK-3's only note visibility.

**MARK-2** — `constraint_bypass` / `missing-validation` — *invariant-breaking configuration change (check scoped to published marks only)* (defect type not in the existing 14 apps).

- *Defect:* Lowering an assessment's maximum below a mark already given must be refused. The check reuses the student-view query, which only sees published marks, so for the unpublished exam no mark is found and any lower maximum is accepted; students then score more than 100%.
- *Expected behaviour:* No student can score more than 100% of an assessment.
- *Trigger (5 steps; minimal 5):* `class-10b` → `assess-exam` → type `max-marks` 42 (number) → `save-assessment` → `back-to-markbook`
- *Observable violation:* After setting the exam maximum to 42, Amara's weighted percentage is 123.0% (18 + 17.33 + 27 + 85 x 30/42).
- *Oracle (extension):* `region_arithmetic` on `/teacher/classes/10b$`: `pct <= 100` with pct=pct_s1
- *Patch (`src/server.py`):* highest_mark(conn, assessment, published_only=False) is called with published_only=True. Sketch: `highest = highest_mark(conn, assessment_id, published_only=False)` → `highest = highest_mark(conn, assessment_id, published_only=True)`
- *Silent on clean:* Marks are refused above the maximum and a maximum is refused below the highest mark, so every component is <= its weight on the clean build.
- *Not triggered by the other patches:* MARK-1 truncates (only lowers) percentages; MARK-3 is unrelated. The control (no MARK-2 patch) refuses the 42 maximum.

**MARK-3** — `state_corruption` / `cross-page` — *role-dependent visibility: a private note becomes visible to the student after it is edited* (defect type not in the existing 14 apps).

- *Defect:* A teacher's private note is created with visibility 'teacher'. Editing it rewrites the row without the visibility column, so the column default ('shared', the value canned feedback uses) applies and the student's own page lists the private note.
- *Expected behaviour:* Private notes are only ever shown to the teacher.
- *Trigger (8 steps; minimal 8):* `class-10b` → `student-s2` → type `private-note` the textarea's typical value → `save-note` → `edit-note` → type `private-note` the textarea's typical value → `save-note` → `preview-as-student`
- *Observable violation:* Bilal's student view lists an entry badged 'Private note' with the teacher's text.
- *Oracle (existing):* `text_absent` on `/student/s\d+$`: region `student_comments` expected "Private"
- *Patch (`src/notes.py`):* the edit path's INSERT OR REPLACE omits the visibility column. Sketch: `INSERT OR REPLACE INTO notes (id, student, body, visibility) VALUES (?, ?, ?, 'teacher')` → `INSERT OR REPLACE INTO notes (id, student, body) VALUES (?, ?, ?)`
- *Silent on clean:* The student query returns only visibility='shared' rows (canned feedback); private notes are always written with 'teacher' on both create and edit paths; the 'Private note' badge only accompanies private notes.
- *Not triggered by the other patches:* MARK-1 and MARK-2 patches do not touch notes.

### 3.3 `sideline` — Sideline

**Domain:** amateur sport: a Sunday football league's results and league table. **User goal:** The league secretary records results, each club's captain confirms or disputes them, and the public sees fixtures, results and a table built from confirmed results only.

**Architecture:** `multi-role-portals(path-prefixed front doors)+server-rendered+client-sortable-table`. A landing page offers three front doors: League secretary (/sec/...), Club captains (/club/<team>/...) and Public (/table, /results, /teams/<team>). Each portal is a server-rendered projection of the same results with its own actions (post/redirect/get). The public table has client-side sortable column headers (a re-order of the rendered rows only; row ids stay tied to league position).

**Stack:** `python-stdlib(http.server)+sqlite`, started with `python src/server.py`. **Dockerfile:** yes  
**Persistence:** sqlite rows partitioned by session; each result has a status (provisional / confirmed / disputed) and one confirmation flag per club; standings are derived on every request.  
**Entry:** `/`. **Workflow shape:** multi-role-portal, state-machine, hub-and-spoke — a result moves provisional -> confirmed (both clubs) or disputed, and a correction sends it back to provisional; each role sees and acts on its own projection.  
**Features:** server-rendered, multi-role, table, sort, server-state, state-machine, cross-page-state, dynamic-validation.  
**Nearest existing app:** `ledgerly` (also `helpline`); differs on *domain*: sports results and standings vs expense claims; *architecture*: three path-prefixed role portals over shared data with derived standings and a client-sortable table, Python; vs a 'viewing as' switch over an event log with a form island, Node; *interaction*: two-party confirmation, disputes, corrections, public projection; *workflow shape*: per-result confirmation state machine feeding a derived ranking vs an approval chain; *state model*: status + per-club flags, standings recomputed per request vs append-only event log.

**Views:** / (choose who you are); /sec (secretary: fixtures and results); /sec/results/<F>/edit (enter or correct a score); /sec/results/<F> (result, status, confirmations); /club/<team> (captain: results awaiting your confirmation); /club/<team>/dispute/<F> (dispute with a reason); /table (public league table + 'Based on n confirmed results'); /results (public results); /teams/<team>.

**Seed and rules:** Teams: Ashby Rovers (ash), Brookfield (bro), Cobham Town (cob), Dunmore (dun), Eastleigh Albion (eas), Fenwick (fen). Confirmed results: F1 ASH 1-0 DUN, F2 COB 4-3 EAS, F3 BRO 2-2 FEN, F4 ASH 0-0 COB, F5 DUN 2-0 FEN, F6 FEN 0-0 ASH, F9 COB 3-2 DUN, F10 COB 1-1 BRO, F11 EAS 2-1 DUN. Unplayed: F7 ASH v BRO, F8 EAS v BRO, F12 FEN v COB. No one-sided confirmations in the seed. Seed table: COB 8 pts (8-6), ASH 5 (1-0), EAS 3 (5-5), DUN 3 (5-6), BRO 2 (3-3), FEN 2 (2-4); no tie where goals-for order and goal-difference order disagree (verified). Order: points, goal difference, goals for, name. Score selects 0-9 (the tester is offered 9, 0, 1, 2). Win 3, draw 1.

**Controls (DOM ids):** `door-secretary`, `door-public`, `door-club-<team>`, `nav-home`, `enter-F<n>`, `result-F<n>`, `edit-result`, home-goals (select 0-9), away-goals (select 0-9), `save-result`, `confirm-F<n>`, `dispute-F<n>`, dispute-reason (text), `submit-dispute`, `sort-gf`, `sort-pts`, `nav-results`, `team-<id>`, `print-table`, `follow-team`.  
**Regions:** `pts_1`=`#pts-1`, `pts_2`=`#pts-2`, `pts_3`=`#pts-3`, `pts_4`=`#pts-4`, `pts_5`=`#pts-5`, `pts_6`=`#pts-6`, `gf_1`=`#gf-1`, `gf_2`=`#gf-2`, `gf_3`=`#gf-3`, `gf_4`=`#gf-4`, `gf_5`=`#gf-5`, `gf_6`=`#gf-6`, `ga_1`=`#ga-1`, `ga_2`=`#ga-2`, `ga_3`=`#ga-3`, `ga_4`=`#ga-4`, `ga_5`=`#ga-5`, `ga_6`=`#ga-6`, `pl_1`=`#pl-1`, `pl_2`=`#pl-2`, `pl_3`=`#pl-3`, `pl_4`=`#pl-4`, `pl_5`=`#pl-5`, `pl_6`=`#pl-6`, `results_counted`=`#results-counted`, `result_status`=`#result-status`, `confirm_count`=`#confirm-count`, `club_pending`=`#club-pending`, `flash`=`#flash`. Table cells carry ids by league position (#pts-1 is the leader's points) and keep them when the public re-sorts the view; goals for and against are separate non-negative numbers (goal difference is never an operand, because the extension oracle reads numbers without their sign); #confirm-count holds only the number of clubs that have confirmed (0, 1 or 2).

**Decoys:** print-table (print layout); follow-team (a client-only star).  
**State-dependent actions:** confirm-F<n> records the club's confirmation on a provisional result awaiting it; on one already confirmed the same button is refused; save-result on an unchanged score changes nothing; on a changed score it records the correction.  
**Workflows (each also run under the `alt` vocabulary; expectations are data, never labels):** secretary enters F12 0-0, both clubs confirm -> #results-counted contains '10'; a club disputes F8 -> #result-status contains 'Disputed'; public table -> #pts-1 contains '8'; sort by goals for -> #gf-1 still the leader's goals ('8').  
**Benign traces (must stay silent on clean and buggy):** `door-secretary` → `enter-F12` → select `home-goals`=0 → select `away-goals`=0 → `save-result` → `nav-home` → `door-club-fen` → `confirm-F12` → `nav-home` → `door-club-cob` → `confirm-F12` → `nav-home` → `door-public` | `door-public` → `sort-gf` → `nav-results` → `team-ash`.

**SIDE-1** — `calculation_error` / `calculation` — *sort correctness: tie-break precedence (goals for ranked above goal difference)* (defect type not in the existing 14 apps).

- *Defect:* Teams level on points must be separated by goal difference, then goals for. The sort key puts goals for before goal difference, so when two teams are level and the one with more goals has the worse difference, they are shown the wrong way round.
- *Expected behaviour:* Teams level on points are ordered by goal difference.
- *Trigger (13 steps; minimal 13):* `door-secretary` → `enter-F7` → select `home-goals`=2 → select `away-goals`=0 → `save-result` → `nav-home` → `door-club-ash` → `confirm-F7` → `nav-home` → `door-club-bro` → `confirm-F7` → `nav-home` → `door-public`
- *Observable violation:* Cobham Town (8 pts, 8-6, +2) is listed above Ashby Rovers (8 pts, 3-0, +3).
- *Oracle (extension):* `region_arithmetic` on `/table(\?.*)?$`: `(p1 > p2 or (p1 >= p2 and f1 - a1 >= f2 - a2)) and (p2 > p3  … (all 5 adjacent pairs)` with p1=pts_1, f1=gf_1, a1=ga_1, p2=pts_2, f2=gf_2, a2=ga_2, p3=pts_3, f3=gf_3, a3=ga_3, p4=pts_4, f4=gf_4, a4=ga_4, p5=pts_5, f5=gf_5, a5=ga_5, p6=pts_6, f6=gf_6, a6=ga_6
- *Patch (`src/standings.py`):* the ranking key swaps goal difference and goals for. Sketch: `key=lambda row: (-row.points, -row.goal_difference, -row.goals_for, row.name)` → `key=lambda row: (-row.points, -row.goals_for, -row.goal_difference, row.name)`
- *Silent on clean:* The clean key orders by points then goal difference, so every adjacent pair satisfies the rule; the boolean expression deliberately avoids '==' (the extension evaluator splits on it) by writing equality as p_i >= p_i+1 after p_i > p_i+1 has failed.
- *Not triggered by the other patches:* SIDE-2's patch changes which results count (in this witness every counted result is fully confirmed); SIDE-3's only corrections.

**SIDE-2** — `constraint_bypass` / `cross-page` — *role-dependent visibility: a result one club has confirmed is published* (defect type not in the existing 14 apps).

- *Defect:* The public table must count only results both clubs have confirmed. Its standings query tests either club's flag instead of the status, so a result confirmed by one club (and possibly disputed by the other) is already in the public table, while the footnote still counts confirmed results.
- *Expected behaviour:* Only results both clubs have confirmed appear in public.
- *Trigger (10 steps; minimal 8):* `door-secretary` → `enter-F8` → select `home-goals`=0 → select `away-goals`=1 → `save-result` → `nav-home` → `door-club-eas` → `confirm-F8` → `nav-home` → `door-public`
- *Observable violation:* 'Based on 9 confirmed results' above a table whose Played column sums to 20 (should be 18).
- *Oracle (extension):* `region_arithmetic` on `/table(\?.*)?$`: `l1 + l2 + l3 + l4 + l5 + l6 == 2 * counted` with l1=pl_1, l2=pl_2, l3=pl_3, l4=pl_4, l5=pl_5, l6=pl_6, counted=results_counted
- *Patch (`src/standings.py`):* the standings query's WHERE r.status = 'confirmed' becomes (r.home_ok = 1 OR r.away_ok = 1). Sketch: `FROM results r WHERE r.status = 'confirmed'` → `FROM results r WHERE (r.home_ok = 1 OR r.away_ok = 1)`
- *Silent on clean:* On the clean build table and footnote read the same status column; every confirmed result adds exactly two appearances.
- *Not triggered by the other patches:* SIDE-1 only re-orders rows (the sum is order-free); SIDE-3 keeps a corrected result's status confirmed, which the clean table and footnote both still count.

**SIDE-3** — `broken_transition` / `state-transition` — *a correction keeps the stale status*.

- *Defect:* Correcting a confirmed score must clear both confirmations and return the result to provisional. The update clears the flags but not the status, so the corrected score stays 'Confirmed' with no confirmations and goes straight into the public table.
- *Expected behaviour:* A result is Confirmed only while both clubs have confirmed it.
- *Trigger (5 steps; minimal 5):* `door-secretary` → `result-F3` → `edit-result` → select `home-goals`=1 → `save-result`
- *Observable violation:* F3 now reads 'Confirmed' with 0 confirmations.
- *Oracle (existing):* `text_absent` on `/sec/results/F\d+$`: region `result_status` expected "Confirmed" **AND** `text_equals` on `/sec/results/F\d+$`: region `confirm_count` expected "2"
- *Patch (`src/results.py`):* the correction's SET clause loses status = 'provisional'. Sketch: `SET home_goals = ?, away_goals = ?, status = 'provisional', home_ok = 0, away_ok = 0` → `SET home_goals = ?, away_goals = ?, home_ok = 0, away_ok = 0`
- *Silent on clean:* Every clean path that sets 'confirmed' requires both flags; every path that clears a flag also leaves 'confirmed'.
- *Not triggered by the other patches:* SIDE-1 and SIDE-2 patches do not write results.

### 3.4 `skylark` — Skylark

**Domain:** citizen science: a local nature group's bird-sightings register. **User goal:** Read the group's sightings newest first, load older ones, filter by site, record a sighting, flag doubtful records, and see how many birds the listed sightings add up to.

**Architecture:** `server-rendered-first-page+load-more-append(keyset cursor)+client-draft`. GET /feed is rendered by the server with the first page of 6 sightings, the tallies for those rows and a 'Load more' button carrying a keyset cursor (date, id). Load more fetches the next page as JSON and appends rendered rows, updating 'Showing n of m' and the tallies in place. Filters are a GET form (full reload). Recording is a two-step form (/sightings/new -> /sightings/new/<site>) whose unsent values are kept as a draft in localStorage (namespaced per session) and restored on reload.

**Stack:** `node-builtins+json-files`, started with `node src/server.js`. **Dockerfile:** yes  
**Persistence:** one JSON file per session (sightings with a verification status); unsent record-form drafts in the browser's localStorage.  
**Entry:** `/feed`. **Workflow shape:** feed, hub-and-spoke, linear-wizard, revisit — an incrementally loaded feed is the hub; a two-step record form and record details are spokes.  
**Features:** server-rendered, load-more-pagination, filter, client-state, server-state, reload-persistence, table, cross-page-state, aggregation.  
**Nearest existing app:** `stackroom` (also `bagstreet`); differs on *domain*: wildlife records vs warehouse stock; *architecture*: server-rendered first page + client-appended keyset pages (no page numbers) vs client-rendered offset pages over JSON; *interaction*: Load more appends; tallies over everything loaded so far; two-step record form with a restorable draft; *workflow shape*: incrementally growing feed vs paged grid; *state model*: server records + browser localStorage draft for unsent input.

**Views:** /feed (?site=marsh|wood|lake); /sightings/new (step 1: where); /sightings/new/<site> (step 2: what, how many, when, notes); /sightings/<id> (detail: flag as doubtful / restore); /sites; /sites/<site>; /summary (species totals this month); /help.

**Seed and rules:** 22 verified sightings s1..s22 dated 2026-05-20..2026-06-09 at three sites (Marsh hide, Wood edge, Lakeside), ordered newest first by (date desc, id desc). Same-date pairs sit at newest-first positions 5/6, 11/12 and 17/18 and nowhere else, so on the seed no page boundary (6/7, 12/13, 18/19) splits a date; one new sighting (dated today, 2026-06-10) shifts the first pair onto the 6/7 boundary. Each same-date pair is at two different sites, so no single-site view contains a tie at all. s14 (page 2) is at Wood edge; s21 (page 1) is at Lakeside. Page size 6. Species select (alphabetical): blackbird, blue-tit, heron, kingfisher, robin, wren; count (number, default 1, 1-500); date (default today, not after today, not before 2026-01-01).

**Controls (DOM ids):** `load-more`, filter-site (select: all, marsh, wood, lake), `apply-filter`, `new-sighting`, where-site (select), `choose-site`, species (select), count (number), seen-on (date), notes (text), `save-sighting`, `discard-draft`, `sighting-<id>`, `flag-doubtful`, `restore-sighting`, `back-to-feed`, `nav-sites`, `site-<id>`, `nav-summary`, `share-sighting`, `show-map`.  
**Regions:** `shown_count`=`#shown-count`, `total_count`=`#total-count`, `feed_end`=`#feed-end`, `feed_list`=`#feed-list`, `tally_total`=`#tally-total`, `tally_marsh`=`#tally-marsh`, `tally_wood`=`#tally-wood`, `tally_lake`=`#tally-lake`, `draft_summary`=`#draft-summary`, `sighting_status`=`#sighting-status`, `flash`=`#flash`. #shown-count counts the rows actually in the list; #total-count is the server's count for the current filter; #feed-end appears ('End of sightings') only when no more pages remain; #tally-* count birds in the listed, non-doubtful sightings (total and per site); #draft-summary describes the live form ('Heron x 1 at Marsh hide, today').

**Decoys:** share-sighting (shows the record's address as text); show-map (shows 'Maps are not available offline').  
**State-dependent actions:** save-sighting is refused without a species and saves with one; flag-doubtful changes a verified record; on a record already flagged the same button is refused.  
**Workflows (each also run under the `alt` vocabulary; expectations are data, never labels):** load every page -> #feed-end present and #shown-count contains '22'; record a heron -> the first feed row contains 'Heron'; filter Marsh hide -> #feed-list contains only Marsh hide rows; flag s20 doubtful -> #sighting-status contains 'Doubtful'.  
**Benign traces (must stay silent on clean and buggy):** `load-more` → `load-more` → `load-more` | select `filter-site`=lake → `apply-filter` → `sighting-s21` → `back-to-feed` → `load-more`.

**SKY-1** — `state_corruption` / `incorrect-navigation` — *keyset pagination boundary: a date-only cursor skips same-date rows* (defect type not in the existing 14 apps).

- *Defect:* The next page is selected with date < cursor.date only, dropping the (date, id) tie-break. Whenever a page ends inside a run of same-date sightings the rest of that run is skipped. The seed has no such boundary; recording one new sighting moves a same-date pair onto the first page boundary, and the second row of the pair is never listed.
- *Expected behaviour:* Loading every page lists every sighting exactly once.
- *Trigger (7 steps; minimal 7):* `new-sighting` → `choose-site` → select `species`=heron → `save-sighting` → `load-more` → `load-more` → `load-more`
- *Observable violation:* At the end of the feed (pages 1-6, 8-13, 14-19, 20-23): 'Showing 22 of 23 sightings'.
- *Oracle (extension):* `region_arithmetic` on `/feed(\?.*)?$`: `shown == total` with shown=shown_count, total=total_count; guard `feed_end` contains "End"
- *Patch (`src/sightings.js`):* after(cursor) drops the same-date/id clause. Sketch: `rows.filter((r) => r.date < c.date || (r.date === c.date && r.id < c.id))` → `rows.filter((r) => r.date < c.date)`
- *Silent on clean:* The clean keyset (date desc, id desc) visits every row exactly once in every filter; #feed-end appears only after the last page.
- *Not triggered by the other patches:* SKY-2 is client draft code; SKY-3 changes only tallies, not which rows load.

**SKY-2** — `persistence_failure` / `persistence` — *unsent draft listener misses dropdowns* (defect type not in the existing 14 apps).

- *Defect:* The draft is written to localStorage by listeners bound to input and textarea elements only, so a change made with a dropdown is never saved: after choosing the species (with nothing typed afterwards) a reload restores the draft without it.
- *Expected behaviour:* Unsent input survives a reload.
- *Trigger (4 steps; minimal 4):* `new-sighting` → `choose-site` → select `species`=heron → REFRESH
- *Observable violation:* Before the reload the draft summary says 'Heron x 1 at Marsh hide'; after it, 'No species yet x 1 at Marsh hide'.
- *Oracle (extension):* `region_stable_across_reload` on `/sightings/new/[a-z]+$`, region `draft_summary`
- *Patch (`src/static/record.js`):* draft listeners attach to 'input, textarea' instead of 'input, select, textarea'. Sketch: `form.querySelectorAll("input, select, textarea").forEach((el) => el.addEventListener("input", saveDraft));` → `form.querySelectorAll("input, textarea").forEach((el) => el.addEventListener("input", saveDraft));`
- *Silent on clean:* On the clean build every control's input event writes the whole form synchronously before the DOM settles, and the summary is drawn from the same form state that is restored.
- *Not triggered by the other patches:* SKY-1 and SKY-3 are feed code; this witness never opens the feed after recording.

**SKY-3** — `calculation_error` / `calculation` — *aggregation over appended pages diverges from the per-site tallies* (defect type not in the existing 14 apps).

- *Defect:* Tallies exclude doubtful sightings. The server-rendered first page gets this right; when a page is appended, the client adds the page's all-records sum (page.birds) to the total but the verified-only sums to each site, so a doubtful record on a later page makes the total disagree with the sites.
- *Expected behaviour:* The total is always the sum of the per-site figures.
- *Trigger (5 steps; minimal 5):* `load-more` → `sighting-s14` → `flag-doubtful` → `back-to-feed` → `load-more`
- *Observable violation:* After the append, 'Birds in the listed sightings' exceeds Marsh + Wood + Lakeside by the flagged sighting's count.
- *Oracle (extension):* `region_arithmetic` on `/feed(\?.*)?$`: `total == marsh + wood + lake` with total=tally_total, marsh=tally_marsh, wood=tally_wood, lake=tally_lake
- *Patch (`src/static/feed.js`):* appendPage() adds page.birds (all rows) instead of page.countedBirds (verified rows) to the total. Sketch: `tally.total += page.countedBirds;` → `tally.total += page.birds;`
- *Silent on clean:* Clean server and client paths both count only verified rows, per site and in total, so the identity holds after every append and every filter.
- *Not triggered by the other patches:* SKY-1 changes which rows arrive (still consistent tallies); SKY-2 is record-form code.

### 3.5 `larder` — Larder

**Domain:** home cooking: a personal recipe box with serving scaling, unit conversion and a shopping list. **User goal:** Open a saved recipe, scale it to the number of people, read it in metric or US measures, and build a shopping list from it.

**Architecture:** `spa-history-router+master-detail+json-api`. One HTML shell is served for every app path; in-app links are intercepted and navigated with history.pushState, and Back/Forward are rendered from the popstate event. /recipes/:id shows a master list pane beside a detail pane. A breadcrumb is derived from location.pathname; the detail pane from the route state.

**Stack:** `node-builtins+json-files`, started with `node src/server.js`. **Dockerfile:** yes  
**Persistence:** one JSON file per session (recipes, servings chosen per recipe, shopping list, kitchen settings); the client keeps the current route in History API state and the kitchen settings it last saved in memory (optimistic merge after each save).  
**Entry:** `/recipes`. **Workflow shape:** master-detail, hub-and-spoke, revisit — list pane is the hub; selecting a recipe swaps the detail pane without leaving the list; settings change how every detail renders; Back/Forward walk the pushState history.  
**Features:** spa, history-api-routing, master-detail, client-state, server-state, table, cross-page-state, reload-persistence, unit-conversion.  
**Nearest existing app:** `wayfarer` (also `orbit`, `tidewater`); differs on *domain*: recipes and kitchen measures vs travel itineraries; *architecture*: History API pushState/popstate on real paths with a master-detail split pane, vs hash router with hub views; *interaction*: split-pane selection, servings stepper, unit-system rendering, Back/Forward rendered from route state; *state model*: everything committed server-side per session; route state in the History API; no localStorage draft; *workflow shape*: master-detail (list stays on screen) vs hub-and-spoke.

**Views:** /recipes (master list, empty detail pane); /recipes/:id (master + detail: ingredients, servings stepper, add to list); /recipes/:id/method (detail sub-route: method steps); /list (shopping list grouped by aisle; tick off; clear ticked); /settings (kitchen units: 'Measures' sub-form and 'Oven' sub-form, each with its own Save; settings summary); /conversions (reference chart); not-found view.

**Seed and rules:** r1 Shortbread (serves 4; line 1 plain flour 250 g, butter 125 g, caster sugar 75 g), r2 Pancakes (serves 4; line 1 plain flour 150 g, milk 300 ml, eggs 2), r3 Tomato soup (serves 4; line 1 tomatoes 800 g), r4 Lemonade (serves 6; line 1 water 1000 ml). Servings stepper 1-12. Settings default: metric, Celsius. Conversion: 1 oz = 28.35 g, 1 US fl oz = 29.57 ml; US amounts shown to 1 decimal.

**Controls (DOM ids):** `nav-recipes`, `nav-list`, `nav-units`, `nav-conversions`, `recipe-r1..recipe-r4`, `serve-less`, `serve-more`, `tab-ingredients`, `tab-method`, `add-to-list`, `tick-<item>`, `clear-ticked`, unit-system (select: metric, us), `save-measures`, oven-scale (select: celsius, fahrenheit, gas), `save-oven`, `print-view`, `share-recipe`.  
**Regions:** `crumb_ref`=`#crumb-ref`, `detail_ref`=`#detail-ref`, `detail_serves`=`#detail-serves`, `line1_metric`=`#m-1`, `line1_us`=`#u-1`, `line1_unit`=`#unit-1`, `settings_summary`=`#settings-summary`, `list_lines`=`#list-lines`, `list_count`=`#list-count`, `notice`=`#notice`. #crumb-ref holds only the recipe number taken from the path (e.g. 2); #detail-ref only the number of the recipe whose data fills the pane; #m-1 the scaled metric amount of ingredient line 1 (e.g. '312.5 g'), #u-1 its US amount (present only in US mode, e.g. '11.0 oz'), #unit-1 the bare metric unit ('g' or 'ml').

**Decoys:** print-view (switches to a print layout, client only); share-recipe (reveals the recipe's address as text).  
**State-dependent actions:** add-to-list adds the recipe at its current servings once; on a recipe already on the list the same button is refused ('already on your list'); serve-less lowers the servings above 1 and changes nothing at 1.  
**Workflows (each also run under the `alt` vocabulary; expectations are data, never labels):** scale Shortbread to 6 and add it to the list -> #list-lines contains '375 g'; switch to US measures and open Shortbread -> #u-1 contains '8.8'; open Pancakes, then its method -> URL /recipes/r2/method, method shown; tick an item off the list -> #list-count shows one fewer to buy.  
**Benign traces (must stay silent on clean and buggy):** `recipe-r2` → `serve-more` → `add-to-list` → `nav-list` | `nav-units` → select `oven-scale`=gas → `save-oven` → `nav-recipes` → `recipe-r4`.

**LARD-1** — `calculation_error` / `calculation` — *unit conversion of a scaled quantity* (defect type not in the existing 14 apps).

- *Defect:* In US measures, weight ingredients are converted from the recipe's base quantity instead of the scaled quantity (volume ingredients use the scaled one), so once the servings are changed the ounces no longer match the grams shown beside them.
- *Expected behaviour:* A weight's US amount is always the metric amount on screen converted to ounces.
- *Trigger (6 steps; minimal 6):* `nav-units` → select `unit-system`=us → `save-measures` → `nav-recipes` → `recipe-r1` → `serve-more`
- *Observable violation:* Shortbread for 5 shows flour 312.5 g beside 8.8 oz (8.8 oz is 250 g; 312.5 g is 11.0 oz).
- *Oracle (extension):* `region_arithmetic` on `/recipes/r\d+$`: `us == round(metric / 28.35, 1)` with us=line1_us, metric=line1_metric; guard `line1_unit` contains "g"; tolerance 0.051
- *Patch (`src/static/units.js`):* the weight branch of usAmount(line) converts line.base instead of line.scaled. Sketch: `if (line.unit === "g") return ounces(line.scaled); // weights convert the amount on screen` → `if (line.unit === "g") return ounces(line.base); // weights convert the amount on screen`
- *Silent on clean:* On the clean build both numbers derive from the same scaled quantity for every servings value 1-12 (error < 0.05 oz); in metric mode #u-1 is absent (inconclusive, never violated); 'ml' lines fail the guard.
- *Not triggered by the other patches:* LARD-2's patch only changes History state; LARD-3's only resets the Oven setting when Measures is saved later (this witness saves Measures once and never Oven).

**LARD-2** — `broken_transition` / `incorrect-navigation` — *History API state recorded for the wrong entry* (defect type not in the existing 14 apps).

- *Defect:* When a recipe is opened from the list, pushState stores the recipe that was on screen before (not the one being opened) in the new history entry. Back renders the detail pane from that state, so after visiting three recipes Back shows the first recipe under the second recipe's address.
- *Expected behaviour:* Back and Forward show the recipe whose address they land on.
- *Trigger (4 steps; minimal 4):* `recipe-r1` → `recipe-r2` → `recipe-r3` → BACK
- *Observable violation:* At /recipes/r2 the breadcrumb says recipe 2 while the detail pane shows Shortbread (recipe 1).
- *Oracle (extension):* `region_arithmetic` on `/recipes/r\d+$`: `shown == crumb` with shown=detail_ref, crumb=crumb_ref
- *Patch (`src/static/app.js`):* openRecipe() pushes {recipe: current.id} (the recipe being replaced) instead of {recipe: next.id}. Sketch: `history.pushState({ recipe: next.id }, "", href);` → `history.pushState({ recipe: current && current.id }, "", href);`
- *Silent on clean:* Clean pushState stores the opened recipe and popstate falls back to the path when state is empty, so pane and breadcrumb always agree; REFRESH renders from the path.
- *Not triggered by the other patches:* No other patch touches routing; the witness never changes settings or servings.

**LARD-3** — `persistence_failure` / `persistence` — *partial update overwrites a sibling setting (merge with defaults)* (defect type not in the existing 14 apps).

- *Defect:* Each settings sub-form saves only its own field. The server merges a save into the defaults instead of into the session's current settings, so saving the Oven scale silently resets Measures to metric. The page keeps showing the client's merged copy until a reload.
- *Expected behaviour:* Saving one group of settings never changes another.
- *Trigger (6 steps; minimal 6):* `nav-units` → select `unit-system`=us → `save-measures` → select `oven-scale`=fahrenheit → `save-oven` → REFRESH
- *Observable violation:* Before the reload the settings summary reads 'US cups and ounces, Fahrenheit'; after it, 'Metric, Fahrenheit'.
- *Oracle (extension):* `region_stable_across_reload` on `/settings$`, region `settings_summary`
- *Patch (`src/server.js`):* settings PATCH handler: { ...session.settings, ...patch } becomes { ...DEFAULT_SETTINGS, ...patch }. Sketch: `session.settings = { ...session.settings, ...patch };` → `session.settings = { ...DEFAULT_SETTINGS, ...patch };`
- *Silent on clean:* The summary shows only saved settings (an unsaved select change does not alter it), and on the clean build the server holds exactly what the client merged; the PATCH answers {ok:true} only, so the client never re-reads a divergent copy before the reload.
- *Not triggered by the other patches:* LARD-1's patch affects US rendering only; LARD-2's only History state. Neither changes what is stored.

### 3.6 `fosterly` — Fosterly

**Domain:** animal rescue: an application to become a foster carer for rescue dogs and cats. **User goal:** Fill in a foster-carer application section by section with every answer saved as you go, leave and continue it later by its reference, review it and submit it.

**Architecture:** `single-page-accordion-form+field-autosave-api`. The application is one server-rendered page with five accordion sections opened by <button aria-expanded> headers (one open at a time; collapsed panels are display:none). Every field change (input/change event) is sent at once as a PATCH of that single field; the server validates it, stores it and answers {ok, completeness}. Each collapsed header shows a summary rendered from the client's copy of the accepted answers. Ticking 'Other animals at home' reveals a nested sub-section. Drafts have references (FOS-1001 ...) listed on the start page for resuming.

**Stack:** `node-builtins+json-files`, started with `node src/server.js`. **Dockerfile:** yes  
**Persistence:** one JSON file per session: drafts stored field by field as each change is accepted, and submitted applications (immutable); the client keeps an optimistic copy of accepted answers for the section summaries.  
**Entry:** `/`. **Workflow shape:** accordion, nested-form, revisit, save-and-resume — non-linear: sections can be completed in any order and revisited; a completeness gate guards review/submit; drafts are resumed from the start page.  
**Features:** accordion, autosave, progressive-disclosure, conditional-section, client-state, server-state, reload-persistence, dynamic-validation, cross-page-state.  
**Nearest existing app:** `account_hub` (also `support_portal`, `clinicflow`); differs on *domain*: animal-rescue volunteer application vs SaaS account settings / broadband support request; *architecture*: Node server with per-field PATCH autosave and server-computed completeness, accordion on one page; vs static pages with sessionStorage panels or a server wizard; *interaction*: accordion disclosure, no Save buttons, nested conditional sub-section, resume by reference; *workflow shape*: any-order sections behind a completeness gate vs linear stages; *state model*: field-level server draft written on every change + immutable submissions.

**Views:** / (start: new application; saved applications to continue); /apply/<FOS-ref> (accordion: About you, Your home [+ Other animals], Experience, Availability, Declaration; completeness meter); /apply/<FOS-ref>/review (check your answers; Submit); /applications/<FA-ref> (submitted application); /applications (your applications); /help/fostering (what fostering involves).

**Seed and rules:** One saved draft FOS-1001 (listed on the start page as 'Continue application FOS-1001'): all five sections complete; Your home: flat, no garden, Other animals: No; Availability: from 2026-07-01, at least 4 weeks. New drafts get FOS-1002, ...; submitted applications FA-2001, .... Fields: full-name (text), email (email), phone (tel), home-type (select: house, flat, farm, other), garden (checkbox), other-animals (checkbox) -> animal-species (select: dog, cat, rabbit, other) and animal-count (select: 1, 2, 3, 4-or-more), years-experience (number 0-60), experience-notes (textarea), available-from (date, not before today), min-weeks (select: 2, 4, 8, 12), declaration (checkbox). Home is complete when home-type is set and, if other-animals is ticked, species and count are set.

**Controls (DOM ids):** `start-new`, `resume-FOS-<n>`, `acc-about`, `acc-home`, `acc-experience`, `acc-availability`, `acc-declaration`, `full-name`, `email`, `phone`, `home-type`, `garden`, `other-animals`, `animal-species`, `animal-count`, `years-experience`, `experience-notes`, `available-from`, `min-weeks`, `declaration`, `to-review`, `back-to-form`, `submit-application`, `print-copy`, `nav-help`.  
**Regions:** `completeness`=`#completeness`, `summary_about`=`#summary-about`, `summary_home`=`#summary-home`, `summary_availability`=`#summary-availability`, `review_status`=`#review-status`, `flash`=`#flash`, `sub_ref`=`#sub-ref`, `sub_other_animals`=`#sub-other-animals`, `sub_animals`=`#sub-animals`, `sub_available`=`#sub-available`. #sub-other-animals holds only 'Yes' or 'No'; #sub-animals lists the animals on the submitted application ('2 x Cat') and is rendered empty when there are none; #summary-availability e.g. 'From 15 Jun 2026, at least 4 weeks'.

**Decoys:** print-copy on the review page (print layout only); nav-help (information page).  
**State-dependent actions:** submit-application is refused while any section is incomplete and submits once all are complete; ticking declaration completes the Declaration section only once About you is complete.  
**Workflows (each also run under the `alt` vocabulary; expectations are data, never labels):** continue FOS-1001 and submit -> #sub-ref contains 'FA-2001'; new application, fill About you -> #completeness contains '1'; tick other animals, choose 2 cats, submit FOS-1001 -> #sub-animals contains '2'; set availability on a new draft -> #summary-availability contains '15 Jun 2026'.  
**Benign traces (must stay silent on clean and buggy):** `resume-FOS-1001` → `to-review` → `submit-application` | `start-new` → `acc-about` → type `full-name` "Test User Input" → type `email` tester@example.com → `acc-home` → select `home-type`=house.

**FOST-1** — `persistence_failure` / `persistence` — *autosave field silently dropped by the server's field allow-list*.

- *Defect:* The availability section's allow-list names the field 'availableFrom' while the form sends 'available_from'. The server filters the PATCH body by the allow-list, stores nothing, and still answers ok, so the summary updates but the answer is gone after a reload.
- *Expected behaviour:* Every answer the page accepts is kept.
- *Trigger (4 steps; minimal 4):* `start-new` → `acc-availability` → type `available-from` 2026-06-15 (date) → REFRESH
- *Observable violation:* The Availability summary reads 'From 15 Jun 2026' before the reload and 'Not started' after it.
- *Oracle (extension):* `region_stable_across_reload` on `/apply/FOS-\d+$`, region `summary_availability`
- *Patch (`src/fields.js`):* FIELDS.availability lists 'availableFrom' instead of 'available_from'. Sketch: `availability: ["available_from", "min_weeks"],` → `availability: ["availableFrom", "min_weeks"],`
- *Silent on clean:* The summary is updated only after the server accepts a value (a refused value such as 0001-01-01 leaves it unchanged); the clean server stores every accepted field before answering, so the reloaded summary is identical.
- *Not triggered by the other patches:* FOST-2 and FOST-3 patches act at submission and completeness; this witness never submits.

**FOST-2** — `state_corruption` / `conditional-ui` — *answers in a hidden (unticked) sub-section are submitted* (defect type not in the existing 14 apps).

- *Defect:* When 'Other animals at home' is unticked the animals sub-section is hidden, but its earlier answers stay in the draft. Submission copies draft.animals without checking the tick, so the application says 'Other animals: No' and still lists the hidden animals.
- *Expected behaviour:* What is submitted is what the applicant can see.
- *Trigger (8 steps; minimal 7):* `resume-FOS-1001` → `acc-home` → `other-animals` → select `animal-species`=cat → select `animal-count`=2 → `other-animals` → `to-review` → `submit-application`
- *Observable violation:* The submitted application shows 'Other animals: No' and lists '2 x Cat'.
- *Oracle (extension):* `text_absent` on `/applications/FA-\d+$`: region `sub_other_animals` expected "No" **AND** `input_absent` on `/applications/FA-\d+$`: region `sub_animals` must not contain the value entered for `animal_species`
- *Patch (`src/applications.js`):* submit() builds animals: draft.animals instead of draft.other_animals ? draft.animals : []. Sketch: `animals: draft.other_animals ? draft.animals : [],` → `animals: draft.animals,`
- *Silent on clean:* On the clean build an application with 'No' always has an empty animals list, so the input_absent conjunct cannot hold together with the 'No' conjunct.
- *Not triggered by the other patches:* FOST-3's patch only weakens the Home completeness check (the section is complete in this witness either way); FOST-1's only drops available_from (seeded in FOS-1001).

**FOST-3** — `constraint_bypass` / `missing-validation` — *conditional requirement ignored by the completeness check*.

- *Defect:* Home completeness should require species and count when 'Other animals at home' is ticked. The condition reads draft.otherAnimals (never set), so the section counts as complete with the box ticked and nothing described, and the application can be submitted.
- *Expected behaviour:* An application that says the applicant has other animals says which.
- *Trigger (5 steps; minimal 5):* `resume-FOS-1001` → `acc-home` → `other-animals` → `to-review` → `submit-application`
- *Observable violation:* The submitted application shows 'Other animals: Yes' with no animals listed.
- *Oracle (existing):* `text_absent` on `/applications/FA-\d+$`: region `sub_other_animals` expected "Yes" **AND** `region_empty` on `/applications/FA-\d+$`, region `sub_animals`
- *Patch (`src/fields.js`):* conditional requirement reads the camelCase name. Sketch: `if (draft.other_animals) required.push("animal_species", "animal_count");` → `if (draft.otherAnimals) required.push("animal_species", "animal_count");`
- *Silent on clean:* Clean submission is refused while Home is incomplete, so 'Yes' always comes with at least one animal.
- *Not triggered by the other patches:* FOST-2's patch changes what is copied when the box is unticked (here it is ticked and empty); FOST-1's is unrelated.

### 3.7 `tally` — Tally

**Domain:** club governance: electing a members' club chair by ranked ballot at the AGM (instant-runoff count). **User goal:** Run the AGM election: issue ballot papers one at a time, let each member rank the candidates, close the poll, step through the count round by round, and publish the declaration.

**Architecture:** `single-url-server-phase-machine+fetch-ballot-panel+client-round-stepper`. /poll is one URL whose content the server decides from the poll's phase: open (officer panel + voting kiosk), closing (confirmation), counted (results), declared. Phase changes are form POSTs answered with 303 back to /poll. In the open phase, casting is a fetch from a ballot panel that stays on screen (status line updates in place) until 'Next voter'. The count's rounds are embedded as JSON and stepped through client-side (Previous/Next round). /declaration is a separate printable page.

**Stack:** `node-builtins+json-files`, started with `node src/server.js`. **Dockerfile:** yes  
**Persistence:** one JSON file per session: poll phase, issued papers (number, used), ballots (paper, preferences), and the count once the poll is closed.  
**Entry:** `/poll`. **Workflow shape:** phase-machine, kiosk-loop, round-stepper, state-machine — a repeated issue -> start -> rank -> cast loop on one URL, then a one-way close -> count -> declare sequence; results are browsed round by round.  
**Features:** single-url, server-state, client-state, state-machine, table, enable-disable, dynamic-validation, aggregation.  
**Nearest existing app:** `helpline` (also `booking_desk`, `ledgerly`); differs on *domain*: elections and vote counting vs support tickets; *architecture*: one URL whose view is chosen by a server-side phase, a fetch ballot panel, and a client round stepper; vs per-ticket pages updated by HTML fragments; *interaction*: kiosk loop (issue, start, rank, cast), one-way close, round-by-round results; *workflow shape*: repeated loop then a one-way phase sequence vs a per-item status machine; *state model*: phase + papers + ballots; results derived once at close.

**Views:** /poll [open] (officer: papers issued, ballots cast, Next voter, Close poll; kiosk: Start ballot, rank selects, Cast ballot); /poll [closing] (confirm close; unused papers will be voided); /poll [counted] (round stepper: tallies, continuing and exhausted ballots, outcome); /poll [declared]; /declaration (printable declaration); /register (members and paper numbers); /ballots (ballot audit, after close); /help (rules of the count).

**Seed and rules:** Candidates (select values in ballot order): cand-a Ada Okoro, cand-b Bram Lindqvist, cand-c Cleo Mendes, cand-d Dev Patel; each rank select also has an empty '-' option (ignored by the tester's scanner, so all four candidates are offered). 18 members; papers 1-11 issued and used by these ballots: (A,B,D) (A,D) (B,A) (B,C) (B,D,A) (C,B) (D) (D,A,B) (D,B) (D,B,A) (D,C,B). Count rules: a candidate with more than half of the continuing ballots is elected; otherwise the candidate with fewest votes is eliminated (ties: fewer first preferences, then later on the paper) and ballots transfer to their next continuing preference; a ballot with none left is exhausted. A final two-way tie is shown as 'Tied' and nobody is elected. Verified counts: seed -> R1 A2 B3 C1 D5, R2 A2 B4 D5, R3 B5 D6: Dev elected 6 of 11. Seed + (D) -> R1 D 6 of 12 (exactly half, not elected), R2 A2 B4 D6, R3 B5 D7: Dev elected 7 of 12. Seed + (B) + (A,B) -> R1 A3 B4 C1 D5, R2 A3 B5 D5, R3 B7 D6: Bram elected 7 of 13 although Dev led round 1.

**Controls (DOM ids):** `next-voter`, `start-ballot`, `pref-1`, `pref-2`, `pref-3`, `cast-ballot`, `close-poll`, `confirm-close`, `cancel-close`, `round-prev`, `round-next`, `publish-declaration`, `view-declaration`, `nav-register`, `nav-ballots`, `print-paper`, `nav-help`.  
**Regions:** `ballots_cast`=`#ballots-cast`, `papers_issued`=`#papers-issued`, `kiosk_status`=`#kiosk-status`, `round_number`=`#round-number`, `round_outcome`=`#round-outcome`, `round_leader_votes`=`#round-leader-votes`, `round_continuing`=`#round-continuing`, `declared_name`=`#declared-name`, `declared_votes`=`#declared-votes`, `declared_continuing`=`#declared-continuing`. #round-outcome reads e.g. 'Dev Patel is elected' / 'Cleo Mendes is eliminated' / 'Tied'; #round-leader-votes the highest tally in the round shown; #round-continuing the continuing ballots in that round; #declared-votes and #declared-continuing the declared candidate's votes and the continuing ballots in the final round. The counted phase opens on the final round.

**Decoys:** print-paper (print layout of a blank ballot); nav-help (rules of the count).  
**State-dependent actions:** cast-ballot records a ballot for the current unused paper; pressed again for the same (now used) paper it is refused; close-poll with no ballot paper outstanding moves to the closing phase; publish-declaration acts only in the counted phase.  
**Workflows (each also run under the `alt` vocabulary; expectations are data, never labels):** one ballot for Ada, close and count -> #round-outcome contains 'Dev Patel'; step back one round -> #round-number contains '2'; issue a paper -> #papers-issued contains '12'; publish the declaration -> #declared-name contains 'Dev Patel'.  
**Benign traces (must stay silent on clean and buggy):** `next-voter` → `start-ballot` → select `pref-1`=cand-a → `cast-ballot` → `close-poll` → `confirm-close` → `round-prev` → `publish-declaration` | `nav-register` → `nav-help`.

**TALLY-1** — `constraint_bypass` / `form-submission` — *double submission: the server does not refuse a used ballot paper* (defect type not in the existing 14 apps).

- *Defect:* Each ballot is tied to the paper issued to the voter, and a paper may be used once. The server looks the paper up without the 'not yet used' condition, so pressing Cast ballot again (or rapidly) records the same voter's ballot several times.
- *Expected behaviour:* One paper, one ballot.
- *Trigger (4 steps; minimal 4):* `next-voter` → `start-ballot` → select `pref-1`=cand-a → rapid-click `cast-ballot`
- *Observable violation:* 'Ballots cast: 16' with 'Papers issued: 12'.
- *Oracle (extension):* `region_arithmetic` on `/poll$`: `cast <= issued` with cast=ballots_cast, issued=papers_issued
- *Patch (`src/poll.js`):* castBallot() finds the paper by number only, dropping && !p.used. Sketch: `const paper = poll.papers.find((p) => p.number === body.paper && !p.used);` → `const paper = poll.papers.find((p) => p.number === body.paper);`
- *Silent on clean:* The clean server answers a second cast for a used paper with 200 {ok:false} ('This paper has already been used'); the panel and its button stay on screen so every repeated click reaches the server, and refusals add nothing.
- *Not triggered by the other patches:* TALLY-2 and TALLY-3 patches act on the count and the declaration only.

**TALLY-2** — `calculation_error` / `calculation` — *boundary off-by-one: exactly half of the continuing ballots treated as a majority* (defect type not in the existing 14 apps).

- *Defect:* Election needs more than half of the continuing ballots. The test uses >= instead of >, so a candidate with exactly half is declared elected in that round instead of the count continuing.
- *Expected behaviour:* Elected means more than half of the continuing ballots.
- *Trigger (6 steps; minimal 6):* `next-voter` → `start-ballot` → select `pref-1`=cand-d → `cast-ballot` → `close-poll` → `confirm-close`
- *Observable violation:* Round 1 of 1: 'Dev Patel is elected' with 6 of 12 continuing ballots.
- *Oracle (extension):* `region_arithmetic` on `/poll$`: `leader * 2 > continuing` with leader=round_leader_votes, continuing=round_continuing; guard `round_outcome` contains "elected"
- *Patch (`src/count.js`):* majority test top.votes * 2 > continuing becomes >=. Sketch: `if (top.votes * 2 > continuing) {` → `if (top.votes * 2 >= continuing) {`
- *Silent on clean:* The clean rule elects only on a strict majority and shows a final tie as 'Tied', so every round labelled 'elected' has leader x 2 > continuing; other rounds fail the guard.
- *Not triggered by the other patches:* TALLY-1's patch is not exercised (one click on Cast); TALLY-3's acts on the declaration page only.

**TALLY-3** — `state_corruption` / `wrong-target` — *the declaration reads the first round's leader instead of the final round's* (defect type not in the existing 14 apps).

- *Defect:* Publishing the declaration takes the elected candidate from rounds[0] instead of the last round. When transfers change the leader, the declaration names the first-round leader (with that candidate's final-round votes).
- *Expected behaviour:* The declaration names the candidate the count elected.
- *Trigger (12 steps; minimal 12):* `next-voter` → `start-ballot` → select `pref-1`=cand-b → `cast-ballot` → `next-voter` → `start-ballot` → select `pref-1`=cand-a → select `pref-2`=cand-b → `cast-ballot` → `close-poll` → `confirm-close` → `publish-declaration`
- *Observable violation:* The declaration names Dev Patel with 6 of 13 votes; the count elected Bram Lindqvist with 7 of 13.
- *Oracle (extension):* `region_arithmetic` on `/declaration$`: `votes * 2 > continuing` with votes=declared_votes, continuing=declared_continuing
- *Patch (`src/declaration.js`):* elected = count.rounds[0].leader instead of the last round's. Sketch: `const elected = count.rounds[count.rounds.length - 1].leader;` → `const elected = count.rounds[0].leader;`
- *Silent on clean:* A declaration can be published only when the final round elected someone, who by the clean rule has more than half; a 'Tied' result cannot be declared.
- *Not triggered by the other patches:* No round of this witness has exactly half (TALLY-2's patch changes nothing here); TALLY-1's is not exercised (one click per paper).

### 3.8 `quizcraft` — Quizcraft

**Domain:** questionnaire authoring: a cycling club secretary edits the members' annual survey. **User goal:** Edit the survey's questions, required flags and skip logic, preview it as a member would answer it, undo and redo edits, and save revisions.

**Architecture:** `client-command-stack-editor+revisioned-document-api`. One page (/quiz) with editor, preview and history panes; the pane and preview step live in the query string via history.replaceState. Every edit is a command object executed against an in-memory working copy and pushed on an undo stack (undo/redo stacks are page memory only). Save posts the whole document; the server appends it as a new revision. A reload returns to the latest saved revision.

**Stack:** `python-stdlib(http.server)+sqlite`, started with `python src/server.py`. **Dockerfile:** **no** — No Dockerfile and no compose file on purpose (the product intake's documented limitation); a Python app this time, so the future stack detector is exercised on a second language. README documents `python src/server.py`.  
**Persistence:** sqlite, append-only table of saved revisions per session; the working copy and undo/redo stacks live only in page memory.  
**Entry:** `/quiz`. **Workflow shape:** editor-loop, nested-form, revisit — a single-screen tool: edit -> preview -> edit -> save loops on one URL; undo/redo moves along a linear command history.  
**Features:** spa, client-state, server-state, undo-redo, conditional-section, enable-disable, dynamic-validation, reload-persistence.  
**Nearest existing app:** `wayfarer` (also `orbit`); differs on *domain*: survey authoring vs travel itineraries; *architecture*: command-pattern editor with undo/redo over a revisioned document, single URL with replaceState panes; vs hash-routed planner with a localStorage draft; *interaction*: select-then-inspect editing, undo/redo, respondent preview with skip logic; *state model*: working copy and undo/redo stacks only in page memory; server append-only revisions (no localStorage); *workflow shape*: edit/preview loop on one screen vs hub-and-spoke; *stack*: Python stdlib vs Node (the suite's no-Dockerfile app was Node).

**Views:** editor (question list + inspector for the selected question); add-question menu (choice, yes/no, rating, text); logic panel (for a yes/no question: 'If No, skip to ...'); preview (?view=preview&step=n: one question at a time, end screen); history (?view=history: the command list with the current position); /revisions (server-rendered list of saved revisions); /help.

**Seed and rules:** Revision 1 of 'Members' survey 2026': Q1 How often do you ride? (choice: weekly / monthly / rarely; optional); Q2 Did you join any club rides this year? (yes/no; required; rule: If No, skip to Q4); Q3 Which rides did you join? (choice; optional); Q4 Rate the club overall (rating 1-5; required); Q5 Anything else? (text; optional); Q6 May we contact you? (yes/no; optional). At most 9 questions. The inspector is empty until a question is selected. Option values are fixed codes (yes, no); option labels come from the vocabulary.

**Controls (DOM ids):** `q-1..q-9`, `add-question`, `add-type-<type>`, `delete-question`, `move-up`, `move-down`, q-title (text, name question_title), q-required (checkbox), rule-target (select: none, 3, 4, 5, 6), `undo`, `redo`, `save-quiz`, `tab-editor`, `tab-preview`, `tab-history`, `preview-next`, `preview-back`, `preview-restart`, answer-<n> (select per question), `nav-revisions`, `copy-link`, `spell-check`.  
**Regions:** `history_ahead`=`#history-ahead`, `history_last_kind`=`#history-last-kind`, `question_count`=`#question-count`, `unsaved_flag`=`#unsaved-flag`, `saved_summary`=`#saved-summary`, `preview_step`=`#preview-step`, `preview_answer_2`=`#preview-answer-2`, `preview_rule_2`=`#preview-rule-2`, `preview_end`=`#preview-end`. #history-ahead holds the number of redoable steps; #history-last-kind the kind of the latest history operation ('Change', 'Undo' or 'Redo' in the default vocabulary); #saved-summary 'Revision n: k questions, r required' for the document the client last saved; #preview-step only the position number shown; #preview-answer-2 the label of the current preview run's answer to Q2 (empty if unanswered); #preview-rule-2 the target position of Q2's 'If No' rule (empty if none).

**Decoys:** copy-link (shows the survey address as text); spell-check (reports 'No spelling problems found').  
**State-dependent actions:** save-quiz creates a revision only when there are unsaved changes; redo changes the document only after an undo.  
**Workflows (each also run under the `alt` vocabulary; expectations are data, never labels):** add a question and save -> #saved-summary contains '7'; make Q5 required and save -> #saved-summary contains '3'; preview: answer No at Q2 -> #preview-step contains '4'; delete Q6 then undo -> #question-count contains '6'.  
**Benign traces (must stay silent on clean and buggy):** `q-5` → `q-required` → `save-quiz` → `nav-revisions` | `tab-preview` → `preview-next` → select `answer-2`=yes → `preview-next` → `tab-editor`.

**QUIZ-1** — `state_corruption` / `stale-state` — *undo/redo: redo stack survives a new edit* (defect type not in the existing 14 apps).

- *Defect:* Executing a new command after an undo should discard the redo branch. The clear is missing, so after undo + a new edit the history still offers a redo of the abandoned change, which would re-apply it on top of the new one.
- *Expected behaviour:* A new change after an undo ends the redo branch.
- *Trigger (4 steps; minimal 4):* `q-2` → `q-required` → `undo` → type `q-title` "Test User Input"
- *Observable violation:* Right after a new change the history panel shows 1 step ahead (Redo enabled).
- *Oracle (existing):* `text_absent` on `/quiz`: region `history_last_kind` expected "Change" **AND** `text_equals` on `/quiz`: region `history_ahead` expected "0"
- *Patch (`src/static/editor.js`):* execute(command) loses the line that empties the redo stack. Sketch: `state.redo.length = 0; // a new change ends the redo branch` → `(removed)`
- *Silent on clean:* On the clean build every new command empties the redo stack, so whenever the latest operation is a Change the ahead count is exactly '0' (#history-ahead holds only the number); after Undo/Redo the first conjunct is not violated.
- *Not triggered by the other patches:* QUIZ-2's patch is in preview navigation, QUIZ-3's in save serialisation; neither touches the stacks.

**QUIZ-2** — `broken_transition` / `conditional-ui` — *skip logic compares the answer's display label with the stored value* (defect type not in the existing 14 apps).

- *Defect:* Preview's Next applies Q2's rule by comparing the stored answer value ('no') with the option's display label ('No'), so the rule never matches and a member who answered No is shown Q3, which the survey says to skip.
- *Expected behaviour:* A member who answers No to Q2 goes straight to the question the rule names.
- *Trigger (4 steps; minimal 4):* `tab-preview` → `preview-next` → select `answer-2`=no → `preview-next`
- *Observable violation:* Preview shows question 3 although Q2 was answered No and Q2's rule says skip to 4.
- *Oracle (existing):* `text_absent` on `view=preview`: region `preview_step` expected "3" **AND** `text_absent` on `view=preview`: region `preview_answer_2` expected "No" **AND** `text_absent` on `view=preview`: region `preview_rule_2` expected "4"
- *Patch (`src/static/preview.js`):* nextStep(): rule test uses labelOf(rule.when) instead of rule.when. Sketch: `if (answers[q.id] === rule.when) return rule.target;` → `if (answers[q.id] === labelOf(q, rule.when)) return rule.target;`
- *Silent on clean:* Clean preview always jumps from Q2 to the rule's target when this run's answer is No; Q3 is shown only for Yes, or when the rule targets 3 or is removed (then #preview-rule-2 is not '4'). Rule conditions are fixed to 'If No' in this app.
- *Not triggered by the other patches:* QUIZ-1 and QUIZ-3 patches do not touch preview navigation.

**QUIZ-3** — `persistence_failure` / `persistence` — *save serialises a stale snapshot (the frozen preview copy)*.

- *Defect:* Entering preview freezes a copy of the document for the respondent walk-through. Save serialises that frozen copy when one exists instead of the working copy, so edits made after a preview are acknowledged ('Saved') but not stored, and a reload brings back the older document.
- *Expected behaviour:* Every saved revision contains the edits made before Save.
- *Trigger (5 steps; minimal 5):* `tab-preview` → `tab-editor` → `add-question` → `save-quiz` → REFRESH
- *Observable violation:* After saving, the summary says 'Revision 2: 7 questions'; after a reload it says 'Revision 2: 6 questions'.
- *Oracle (extension):* `region_stable_across_reload` on `/quiz`, region `saved_summary`
- *Patch (`src/static/editor.js`):* save() serialises state.previewDoc || state.doc. Sketch: `const payload = serialize(state.doc);` → `const payload = serialize(state.previewDoc || state.doc);`
- *Silent on clean:* #saved-summary changes only when a save succeeds and then describes exactly the payload sent, which the clean server stores; unsaved edits do not alter it, so a reload shows the same summary.
- *Not triggered by the other patches:* QUIZ-1's patch leaves extra redo entries but the witness never redoes; QUIZ-2's affects only Next in preview (the witness does not answer).

## 4. All 24 faults

| Fault | Class / subtype | Defect type | Witness (min) | Oracle: kind → region(s) |
|---|---|---|---|---|
| DUTY-1 | constraint_bypass / missing-validation | period boundary: weekly limit summed over only the calendar month's part of the week * | 4 (4) | extension: region_arithmetic → week_hours |
| DUTY-2 | calculation_error / calculation | date arithmetic: day-of-month subtraction across a month boundary * | 5 (5) | existing: text_absent → next_in |
| DUTY-3 | broken_transition / state-transition | derived status not recomputed on an alternate path | 6 (6) | existing: text_absent → count_afternoon, status_afternoon |
| MARK-1 | calculation_error / calculation | rounding: truncation instead of half-up * | 4 (4) | extension: region_arithmetic → pct_s3, mark_s3_*, max_* |
| MARK-2 | constraint_bypass / missing-validation | invariant-breaking configuration change (check scoped to published marks only) * | 5 (5) | extension: region_arithmetic → pct_s1 |
| MARK-3 | state_corruption / cross-page | role-dependent visibility: a private note becomes visible to the student after it is edited * | 8 (8) | existing: text_absent → student_comments |
| SIDE-1 | calculation_error / calculation | sort correctness: tie-break precedence (goals for ranked above goal difference) * | 13 (13) | extension: region_arithmetic → pts_1..6, gf_1..6, ga_1..6 |
| SIDE-2 | constraint_bypass / cross-page | role-dependent visibility: a result one club has confirmed is published * | 10 (8) | extension: region_arithmetic → pl_1..6, results_counted |
| SIDE-3 | broken_transition / state-transition | a correction keeps the stale status | 5 (5) | existing: text_absent + text_equals → confirm_count, result_status |
| SKY-1 | state_corruption / incorrect-navigation | keyset pagination boundary: a date-only cursor skips same-date rows * | 7 (7) | extension: region_arithmetic → shown_count, total_count, feed_end |
| SKY-2 | persistence_failure / persistence | unsent draft listener misses dropdowns * | 4 (4) | extension: region_stable_across_reload → draft_summary |
| SKY-3 | calculation_error / calculation | aggregation over appended pages diverges from the per-site tallies * | 5 (5) | extension: region_arithmetic → tally_total, tally_marsh, tally_wood, tally_lake |
| LARD-1 | calculation_error / calculation | unit conversion of a scaled quantity * | 6 (6) | extension: region_arithmetic → line1_us, line1_metric, line1_unit |
| LARD-2 | broken_transition / incorrect-navigation | History API state recorded for the wrong entry * | 4 (4) | extension: region_arithmetic → detail_ref, crumb_ref |
| LARD-3 | persistence_failure / persistence | partial update overwrites a sibling setting (merge with defaults) * | 6 (6) | extension: region_stable_across_reload → settings_summary |
| FOST-1 | persistence_failure / persistence | autosave field silently dropped by the server's field allow-list | 4 (4) | extension: region_stable_across_reload → summary_availability |
| FOST-2 | state_corruption / conditional-ui | answers in a hidden (unticked) sub-section are submitted * | 8 (7) | extension: text_absent + input_absent → sub_other_animals, sub_animals |
| FOST-3 | constraint_bypass / missing-validation | conditional requirement ignored by the completeness check | 5 (5) | existing: text_absent + region_empty → sub_other_animals, sub_animals |
| TALLY-1 | constraint_bypass / form-submission | double submission: the server does not refuse a used ballot paper * | 4 (4) | extension: region_arithmetic → ballots_cast, papers_issued |
| TALLY-2 | calculation_error / calculation | boundary off-by-one: exactly half of the continuing ballots treated as a majority * | 6 (6) | extension: region_arithmetic → round_leader_votes, round_continuing, round_outcome |
| TALLY-3 | state_corruption / wrong-target | the declaration reads the first round's leader instead of the final round's * | 12 (12) | extension: region_arithmetic → declared_votes, declared_continuing |
| QUIZ-1 | state_corruption / stale-state | undo/redo: redo stack survives a new edit * | 4 (4) | existing: text_absent + text_equals → history_ahead, history_last_kind |
| QUIZ-2 | broken_transition / conditional-ui | skip logic compares the answer's display label with the stored value * | 4 (4) | existing: text_absent → preview_step, preview_answer_2, preview_rule_2 |
| QUIZ-3 | persistence_failure / persistence | save serialises a stale snapshot (the frozen preview copy) | 5 (5) | extension: region_stable_across_reload → saved_summary |

`*` = defect type absent from all 14 existing applications.

## 5. Fault distribution

**By class (the harness's closed five-class taxonomy):**

| Class | Count | Faults | Existing suite (23) | Benchmark (12) |
|---|---|---|---|---|
| calculation_error | 6 | DUTY-2, MARK-1, SIDE-1, SKY-3, LARD-1, TALLY-2 | 2 | 2 |
| state_corruption | 5 | MARK-3, SKY-1, FOST-2, TALLY-3, QUIZ-1 | 8 | 3 |
| constraint_bypass | 5 | DUTY-1, MARK-2, SIDE-2, FOST-3, TALLY-1 | 6 | 3 |
| broken_transition | 4 | DUTY-3, SIDE-3, LARD-2, QUIZ-2 | 4 | 2 |
| persistence_failure | 4 | SKY-2, LARD-3, FOST-1, QUIZ-3 | 3 | 2 |

Every class has 4–6 faults and every app spans three different classes. Calculation errors are slightly
over-represented on purpose: they were the rarest class before (4 of 35) and most of the requested new
fault types (rounding, boundaries, conversion, aggregation, ordering, date arithmetic) are computations.

**By harness subtype:** calculation 6, persistence 4, missing-validation 3, conditional-ui 2, cross-page 2,
incorrect-navigation 2, state-transition 2, form-submission 1, stale-state 1, wrong-target 1. Three
subtypes are used for the first time in the evaluation corpus: **calculation**, **conditional-ui**,
**form-submission**.

**By defect type (new `defect_type` field):** 19 of 24 are defect types none of the 14 existing apps has —
unit conversion of a scaled quantity, History API state recorded for the wrong entry, partial update
overwriting a sibling setting, period-boundary (ISO week vs calendar month) limit, day-of-month date
arithmetic, undo/redo redo-branch not cleared, skip logic comparing label with stored value, rounding
(truncation vs half-up), invariant-breaking configuration change, role-dependent visibility (twice),
hidden-section answers submitted, keyset pagination boundary, localStorage draft missing dropdowns,
aggregation over appended pages, sort tie-break precedence, double submission, majority boundary
off-by-one (> vs >=), declaration reading the wrong round. The other five (DUTY-3, SIDE-3, FOST-1, FOST-3,
QUIZ-3) are familiar defect families in new settings.

**By oracle:** 7 primary-grammar faults (`text_absent`, `text_equals`, `region_empty`) and 17 extension
faults (`region_arithmetic` 12, `region_stable_across_reload` 4, `input_absent` 1). The extension is used
only where the primary grammar cannot state the rule path-independently: a relation between two or more
numbers on the same page (consistency, sums, ordering, limits), survival across a reload, or "this typed
value must not appear here". Each such fault is stated generally (e.g. "pane matches address" for every
recipe) rather than pinned to the witness's URL.

**Witness lengths:** 4, 4, 4, 4, 4, 4, 4, 4, 5, 5, 5, 5, 5, 5, 6, 6, 6, 6, 7, 8, 8, 10, 12, 13 (minimal
triggers 4–13). Five witnesses are 8 steps or longer: MARK-3 (8), FOST-2 (8), SIDE-2 (10), TALLY-3 (12),
SIDE-1 (13). Every trigger needs real context (an earlier choice, a second record, a boundary created by
the tester's own action, a reload or Back at the right moment), never one click.

## 6. Rules every builder must follow (learned from the harness, the oracle and the existing suite)

1. **The oracle reads serialized HTML.** An input's live `.value` is not in the page HTML; every asserted
   value must be text in a region element. Region elements must not contain a nested element with the
   same tag (the reader stops at the first closing tag). Prefer `<output>`, `<strong>`, `<span>`, `<td>`.
2. **Numbers in `region_arithmetic` are read without a sign** (`\d[\d,]*(?:\.\d+)?`). Never make a
   possibly-negative number an operand: `sideline` uses goals for/against, not goal difference; DUTY-2
   checks the minus sign with `text_absent`. `numeric_equals` (not used here) does not strip thousands
   separators.
3. **`region_arithmetic` splits on the first `==`.** A boolean rule must not contain `==`; write equality
   as `x >= y` after `x > y` has failed (SIDE-1). Allowed characters: `[\w\s.,+*/()<>=!-]`; `round`,
   `abs`, `min`, `max` only.
4. **Regions hold data only.** Operand regions contain exactly one number (`#pct-s3` → `67.1%`;
   `#count-afternoon` → `1`, with "of 2 needed" outside). Guards and `text_absent` literals that are words
   ("Full", "Confirmed", "Change", "elected", "Private", "End", "Yes"/"No") are default-vocabulary words:
   faults are verified under the default profile; workflows (run under both) expect data, never labels.
5. **Never answer 4xx/5xx to anything the UI can send**, and never log to `console.error`: the clean random
   walk counts both as failures. Domain refusals are `200` with a message (or `{ok:false}`). Include
   `<link rel="icon" href="data:,">`. Validate boundary values (`0`, `999999999`, `""`, 2048×`A`,
   `0001-01-01`, `9999-12-31`) with a 200 message.
6. **The tester waits only for a quiet DOM (400 ms).** One request per click, no debounce timers, no
   chained requests; autosave sends immediately; popstate renders synchronously from data already in
   memory or one fast fetch; localStorage writes are synchronous.
7. **Playwright `fill` fires `input`, not `change`, on text fields** (selects fire both): autosave and draft
   listeners listen to `input`. A `type=number` field refuses `not-a-number` (the action fails; nothing is
   recorded).
8. **Actionable means visible, enabled and in `input, textarea, select, button, a[href]`.** `<details>`/
   `<summary>` cannot be clicked: accordions use `<button aria-expanded>`. At most ~40 controls per view
   (`markbook`: 24 cell buttons + ~10 others).
9. **Selects:** the tester is offered the last option and the first three (empty-value options are
   skipped). Every option a trace needs is in one of those positions (checked for every trace in this
   plan: e.g. Priya is last, Ben second, heron third, cand-a..cand-d are all four options).
10. **Workflows that commit nothing** (a refused assignment, a filter) declare `"commits": false`.
11. **Patches:** one literal substitution; the anchor occurs once; the replacement text must not already
    occur in the clean file unless it is a substring of the anchor (the diff audit rejects it). The patch
    sketches above respect this but builders must re-check against their own code (e.g. `markbook` uses
    `decimal.ROUND_HALF_UP` so `decimal.ROUND_DOWN` needs no import and does not appear elsewhere).
12. **Determinism:** no wall clock except `WTA_FIXED_NOW` (default 2026-06-10T09:00:00, a Wednesday);
    references and tokens from counters; stable sort orders. No `zoneinfo` (Windows has no tzdata).
13. **Progress labels** must not be bare "Continue", "Next" or "Submit" ("Next voter", "Next question",
    "Submit application" are fine), and every `act.*` label must differ between `default` and `alt`.
14. **Benign traces must stay silent on the buggy build.** For `sideline` this means no result that creates
    a points tie where goals-for and goal-difference order disagree (F12 0–0 is verified safe), no
    one-sided confirmation and no correction of a confirmed result.
15. **API tests pin the verified numbers**: `tally`'s three counts, `sideline`'s seed table, `markbook`'s
    67.0833→67.1 and 123.05 cases, `skylark`'s seed ordering and tie positions, `larder`'s 312.5 g → 11.0 oz.

## 7. Admission with the unchanged harness

- Build each app in `validation_suite/apps/<app_id>/` with the suite layout (`app.json`, `README.md`,
  `src/`, `vocab/default.json` + `vocab/alt.json`, `seed/`, `tests/`, `Dockerfile` except `quizcraft`).
  Never name a file `faults.json`.
- `validate_suite` already accepts a directory: `venv/Scripts/python.exe -m e2e_suite.harness.validate_suite
  --apps validation_suite/apps/<app_id> --browser --docker`. Variants are built under
  `e2e_suite/.build/<app_id>/` and the report is written to `e2e_suite/admission/<app_id>.json` (both paths
  are hard-wired; none of the new ids clashes with a suite id). Move the reports to
  `validation_suite/admission/` afterwards, or — a harness change, so only with approval — add
  `--apps-root/--admission-root` options.
- The suite-wide diversity check runs only without `--apps` and only over `e2e_suite/apps`. Run
  `e2e_suite.harness.diversity.analyse()` once over the union of the 8 suite manifests and the 8 new ones
  to show the new apps share < 15 % of source lines with any existing app and that no
  architecture/stack/persistence triple repeats.
- `quizcraft` passes when the product intake **refuses** it (`product_intake_matches_expectation`), like
  `wayfarer` (20 checks instead of 22).
- Held-out discipline: until the final evaluation, only the admission harness (scripted traces and its
  uniform random walk) runs against these apps — never the tester's exploration, and builders do not
  consult the tester's strategy code. Record each admitted app tree's hash when the set is frozen.

## 8. Risks

| Risk | Where | Mitigation |
|---|---|---|
| **Accidental similarity** | `dutyday`~`clinicflow` (both schedule people into time slots); `sideline`~`ledgerly` (roles that confirm/approve); `quizcraft`~`wayfarer` (unsaved working copy vs saved versions); `skylark`~`stackroom` (paged listing); `fosterly`~`support_portal`/`account_hub` (long forms) | Keep the distinguishing pattern central, not decorative: zero JS + calendar grid; three portals + derived ranking; undo/redo + preview; keyset append + client tallies; accordion autosave + resume. Run the cross-set diversity analysis (§7). |
| **Settle race on client-heavy apps** | LARD-2 (popstate), SKY-1/3 (append), FOST-1 (autosave), SKY-2 (draft), TALLY-1 (rapid click) | One fast request per action, render on response; synchronous localStorage; the ballot panel stays on screen so every rapid click reaches the same button. If a replay misses, raise nothing in the harness — fix the app. |
| **Path-dependent false positives** | any assertion | Assertions state expected behaviour for every path; specific traps closed in the design: maxima are operands (MARK-1), rule conditions are fixed to "If No" (QUIZ-2), a final tie shows "Tied" (TALLY-2/3), Sundays have no regions (DUTY-3), same-date sightings are at different sites (SKY-1), the summary shows saved settings only (LARD-3). The clean random walk is the check. |
| **Faults masking each other** | `sideline`, `tally`, `skylark` | Witnesses chosen so each control (buggy minus that fault) is silent: TALLY-3's witness has no exact-half round (TALLY-2's patch inert); SKY-1's witness is unfiltered; SIDE-2's sum is order-free. |
| **Reliance on the extension grammar** | 17 of 24 faults | Same mechanism the suite already uses for 8 faults. If the final evaluation must verify with the primary oracle alone, these faults need `ext_oracle` in the evaluation path — decide before building (§10). |
| **Harness paths hard-wired to `e2e_suite/`** | admission reports, diversity | Run with `--apps <path>` and relocate reports, or approve a small harness option. |
| **Effort overrun** | `quizcraft` (editor + preview + stacks), `tally` (count + phases) | Build them last, after the harness routine is familiar; keep to the listed views; reuse no code between apps (diversity measures shared lines). |
| **Determinism on Windows** | Python apps | `ThreadingHTTPServer` + one lock around sqlite (as `stackroom`); `wsgiref` is single-threaded (fine); no `zoneinfo`. |

## 9. Build order and budget

1. **`dutyday`** — zero JS, simplest runtime; establishes the build/admit routine for this folder.
2. **`markbook`** — server-rendered with one small fetch island; arithmetic oracle exercised early.
3. **`sideline`** — server-rendered, longest witnesses; seed table already verified.
4. **`skylark`** — first Node app; append pagination and a localStorage draft.
5. **`larder`** — History API SPA; most settle-sensitive routing.
6. **`fosterly`** — accordion + autosave + resume.
7. **`tally`** — phase machine, kiosk loop, IRV count (counts verified in §3.7).
8. **`quizcraft`** — command stack, preview logic, no Dockerfile; the most client code.

Budget: 2–3 hours each to build and admit (300–900 source lines, 6–9 views); an admission run takes
5–25 minutes of harness time, as for the suite.

## 10. Requirements not fully met, and decisions needed

- **New fault classes are not possible** without changing the frozen five-class taxonomy (`FaultClass`),
  and new *subtypes* beyond the harness's closed list (`manifest.py` `SUBTYPES`) would fail
  `manifest_well_formed`. The plan therefore uses the three subtypes the existing apps never used
  (`calculation`, `conditional-ui`, `form-submission`) and records the specific new defect types in an
  extra `defect_type` field (ignored by the harness). Extending `SUBTYPES` instead is a one-line harness
  change that needs approval.
- **Decision needed:** whether the final evaluation verifies extension-grammar faults (17 of 24). If only
  the primary grammar is allowed, roughly half of the new defect types (rounding, ordering, sums, limits,
  reload survival) cannot be expressed path-independently and the set would have to be redesigned around
  `text_absent`/`text_equals` conjunctions.
- **Admission paths** are hard-wired to `e2e_suite/` (see §7); the plan works around this without a
  harness change.
- Domain adjacency: `dutyday` schedules people into time slots, which is near the "booking/appointments"
  exclusion. It is staff rostering (no customer, no booking), and its architecture, interaction and
  workflow shape all differ from `clinicflow`; flagged so a reviewer can veto it (fallback: a garden
  allotment plot-allocation calendar with the same faults).
