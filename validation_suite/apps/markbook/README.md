# Markbook: a teacher's markbook for one class

A school markbook for class 10B Mathematics (six students, four assessments), with each
student's own view of the marks that have been published to them.

**Architecture.** A WSGI application on the standard library's `wsgiref` server, with one
SQLite file per browser session copied from a template that is built from
`seed/markbook.json` at start-up. Two route trees: the teacher's pages under `/teacher/...`
and the student's own page under `/student/<s>`. The markbook (`/teacher/classes/10b`) is a
server-rendered students x assessments matrix. Every mark is a button; selecting it swaps
in a single number box with *Save mark* / *Cancel* (one editor at a time). Saving sends one
`PATCH` and the server answers with the student's recomputed row (all four marks, weighted
percentage, level) and the assessment's column (class average), which replace those cells
in place (`src/static/markbook.js`, the app's only script). Every other page is plain HTML
forms with post/redirect/get.

**Run:** `PORT=8080 python src/server.py`, then open http://127.0.0.1:8080/ (redirects to
`/teacher`). **Docker:** `docker build -t markbook . && docker run -p 8080:8080 markbook`.
Options as in the suite conventions: `WTA_VOCAB=alt` (alternative wording),
`WTA_FIXED_NOW` (the date on the levels report; default 2026-06-10T09:00:00),
`WTA_DATA_DIR` (where the template and the session files live; default a temporary
directory), `WTA_TEST_HOOKS=1` (enables `GET /__wta/state`).

**Tests:** with the server running,
`E2E_BASE_URL=http://127.0.0.1:8080/ python -m unittest discover -s tests`.

## Views

| path | what it is |
|---|---|
| `/teacher` | the teacher's classes |
| `/teacher/classes/10b` | the markbook matrix: marks, weighted %, level, class averages; *Exporting marks* help |
| `/teacher/classes/10b/assessments/<a>` | one assessment: maximum marks, publish / withdraw |
| `/teacher/classes/10b/students/<s>` | one student's record: marks, feedback from the comment bank, the private note; *Preview as student* |
| `/student/<s>` | the student's own page: published marks, highest in class, provisional %, comments |
| `/teacher/classes/10b/report` | how many students are at each level |
| `/help` | how marks, rounding, levels, publishing and notes work |

## Rules

- Assessments: Homework out of 20 (weight 20%), Quiz out of 30 (20%), Project out of 40
  (30%), all published; Exam out of 120 (30%), a draft.
- Weighted % = hw x 20/max_hw + quiz x 20/max_quiz + proj x 30/max_proj + exam x 30/max_exam,
  computed exactly (fractions) and shown to one decimal, halves up; '-' until all four
  marks exist. Level 5 from 80, 4 from 70, 3 from 60, 2 from 50, else 1 (read from the
  percentage as shown). Seed: Amara 18, 26, 36, 85 (83.6%); Chen 17, 20, 35 and no exam mark.
- Marks are whole numbers from 0 to the maximum; an emptied box clears the mark. A maximum
  is a whole number from 1 to 1000 and cannot be set below a mark already given.
- A cell edit carries the maxima its page was drawn with; if a maximum has changed since,
  the edit is refused with a request to reload, so a page never mixes figures worked out
  against different maxima.
- Publishing makes an assessment's marks visible on student pages; *Publish* on a published
  assessment is refused, *Withdraw* is disabled on a draft.
- Each student has at most one comment from the comment bank (shared with the student) and
  one private note (teacher only), shown as text with *Edit note* once saved. Both are rows
  of the `notes` table; `visibility` decides which pages may list a row, and a row's badge
  ("From your teacher" / "Private note") is decided by whether it came from the bank.

Refusals are answered `200` with a message (the cell editor gets `{ok: false, error}`).
All data belongs to the browser session (`wta_sid` cookie).

## Deviations from the plan

- **MARK-3 witness minimality.** The plan's 8-step witness is used unchanged, but the
  plan's minimal length of 8 assumes the edit form starts empty. Here *Edit note* opens the
  textarea pre-filled with the saved note (what an edit form normally does), so saving it
  again without retyping already goes through the faulty edit path; the second TYPE is
  redundant and the minimizer may shorten the witness to 7 steps (`shortest_path` is 7).
  The fault class, subtype, defect, trigger, patch and assertion are as designed.
- **Server threading.** The plan notes `wsgiref` is single-threaded; the server mixes in
  `socketserver.ThreadingMixIn` (still `wsgiref`, still standard library) so a browser
  connection that is opened but not yet used cannot stall other requests. Requests are
  still handled one at a time behind a lock.
- **Workflow "raise the quiz maximum to 42"** ends with *Back to markbook*, because
  `#max-quiz` is a cell of the markbook matrix rather than of the assessment page.
