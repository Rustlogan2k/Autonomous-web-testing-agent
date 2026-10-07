# Quizcraft — a survey editor with undo, redo and a respondent preview

The secretary of a cycling club (Riverside Wheelers) edits the members' annual survey: the
questions, which ones must be answered and the skip logic ("If No, skip to ..."), previews
it one question at a time as a member would answer it, undoes and redoes edits, and saves
revisions. Every save appends a revision; earlier revisions never change.

Python standard library only (`http.server`, `sqlite3`, `json`); nothing to install.

## Run

```sh
PORT=8080 python src/server.py
```

Then open http://127.0.0.1:8080/ (it redirects to the editor, `/quiz`).

**No Dockerfile and no compose file, on purpose.** This repository deliberately ships without
a container definition (like the suite's `wayfarer`, but a Python app): the product's intake
is expected to refuse it, and a future stack detector has to work out `python src/server.py`
from this README. It runs on any Python 3.11+ with no packages.

| variable | meaning |
|---|---|
| `PORT` | port to listen on (127.0.0.1; `HOST=0.0.0.0` listens on all interfaces) |
| `WTA_VOCAB` | `default` or `alt`: the same app with different wording |
| `WTA_FIXED_NOW` | the app's "now" (default `2026-06-10T09:00:00`), stamped on saved revisions |
| `WTA_DATA_DIR` | where the SQLite database is written (default: a new temp directory) |
| `WTA_TEST_HOOKS=1` | enables `GET /__wta/state` (the session's revisions and their sha256) |

**Tests:** `python -m unittest discover -s tests -v` runs the unit tests of the document rules
(`tests/test_survey.py`) and the HTTP tests (`tests/test_api.py`), which need a running server
started with `WTA_TEST_HOOKS=1` and `E2E_BASE_URL=http://127.0.0.1:8080/`.

## Views

| where | what it shows |
|---|---|
| `/quiz` (`?view=editor`) | **editor**: the question list (`q-1` ... `q-9`), the add-question menu (type buttons `add-type-choice`, `-yesno`, `-rating`, `-text`, then **Add question**), and the inspector for the selected question (wording, *Answer required*, *Move up/down*, *Delete question*; for a yes/no question the logic panel *If No, skip to ...*) — the inspector is empty until a question is selected |
| `/quiz?view=preview&step=n` | **preview**: one question at a time with its answer control (`answer-<n>`), *Previous question*, *Next question*, *Start again*, then the end screen |
| `/quiz?view=history` | **history**: the command list with the current position ("You are here") and the undone steps after it |
| `/revisions` | every revision saved in this session, newest first |
| `/help` | how the editor works |

The toolbar on `/quiz` always shows the panes' tabs, **Undo**, **Redo**, **Save revision**, the
number of questions, whether there are unsaved changes, the last saved revision ("Revision 2:
7 questions, 2 required"), the kind of the latest history step (Change / Undo / Redo) and the
number of steps that can be redone.

## How it works

- **Command stack.** Every edit — add, delete, move, reword, required on/off, skip rule — is a
  command object with `apply()` and `revert()` (`static/editor.js`). `execute()` applies it to
  the in-memory working copy, pushes it on the undo stack and empties the redo stack (a new
  change ends the redo branch); *Undo* reverts the top command onto the redo stack, *Redo*
  re-applies it. Consecutive rewordings of one question are one command. Both stacks live
  only in the page's memory: a reload starts again from the latest saved revision.
- **Skip logic.** A rule is always "If No, skip to <a later question>" on a yes/no question,
  stored by question id and shown by position. Deleting the target removes the rule, and a
  move that would make a rule point backwards drops it; undo restores both.
- **Preview.** Entering the preview freezes a copy of the working document for the
  walk-through and starts a new run. Answers are stored as option value codes (`yes`, `no`,
  `weekly`, ...); their labels come from the vocabulary or the survey. *Next question* refuses
  to leave an unanswered required question, applies the rule of the question it leaves, and
  records the path, so *Previous question* goes back along the questions actually shown. The
  preview also states this run's answer to question 2 and where question 2's rule skips to.
- **Single URL, replaceState.** The pane and the preview step are written into the address
  with `history.replaceState`, so a reload returns to the same pane and step over the latest
  saved revision (with a fresh, empty preview run).
- **Saving.** *Save revision* posts the whole working copy (`{"base": n, "document": ...}`)
  to `/api/revisions`, only when there are unsaved changes and never twice at once. The
  server checks the document (1-9 questions, titles 1-200 characters, choice options, rules
  pointing forward), refuses a save whose base is not the latest revision, and otherwise
  appends it as revision n+1. Refusals are HTTP 200 with a message. The page then says
  "Revision n+1: k questions, r required" for the document it sent.
- **Persistence.** One SQLite database, rows partitioned by the `wta_sid` session cookie
  (HttpOnly); a new session starts with revision 1, the seeded survey. Every request runs in
  one transaction under one lock.
- *Copy survey link* shows the survey's address to copy; *Check spelling* reports that no
  spelling problems were found. Neither changes anything.

## Seed

Revision 1 of "Members' survey 2026": Q1 How often do you ride? (choice: Weekly / Monthly /
Rarely; optional); Q2 Did you join any club rides this year? (yes/no; required; If No, skip to
Q4); Q3 Which rides did you join? (choice; optional); Q4 Rate the club overall (rating 1-5;
required); Q5 Anything else? (text; optional); Q6 May we contact you? (yes/no; optional).

## Workflow graph

`editor ⇄ inspector (⇄ logic panel)`, `editor → add menu → inspector`; `editor ⇄ preview →
preview end`; `editor ⇄ history`; `editor ⇄ revisions`, `editor ⇄ help`.

## Seeded faults

| fault | where | what goes wrong |
|---|---|---|
| QUIZ-1 | `src/static/editor.js` | `execute()` no longer empties the redo stack, so a new change after an undo still offers to redo the abandoned one |
| QUIZ-2 | `src/static/preview.js` | the skip rule compares the stored answer (`no`) with the option's label (`No`), so it never fires |
| QUIZ-3 | `src/static/editor.js` | save serialises the frozen preview copy when one exists, so edits made after a preview are acknowledged but not stored |

## Deviations from the plan

None in the faults, triggers, assertions, regions or patches. Details:

- **State-dependent actions:** only *Save revision* is declared to the harness. The plan's
  second one, "redo changes the document only after an undo", is implemented (Redo is
  disabled and does nothing until something has been undone) but cannot be declared: the
  working copy lives only in page memory by design, so redo never changes committed
  (server-side) state, which is what the harness's check measures.
- `answer-<n>` is a dropdown for choice, yes/no and rating questions and a text box for the
  free-text question (Q5); a dropdown cannot take a free-text answer.
- Revision saves carry the revision they were based on and the server refuses a stale base;
  this guards two tabs against overwriting each other and does not affect any fault.
- One extra workflow walks the whole preview to its end screen.
