# Fosterly — an application to become a foster carer

An animal rescue's online application for people who want to foster rescued dogs and cats.
The applicant fills in five sections in any order, every answer is saved the moment it is
given, the application can be left and continued later by its reference (FOS-1001, ...),
and a complete application is reviewed and submitted (FA-2001, ...).

Node 22 built-ins only (`node:http`, `node:fs`, `node:crypto`); nothing to install.

## Run

```sh
PORT=8080 node src/server.js
```

Then open http://127.0.0.1:8080/.
**Docker:** `docker build -t fosterly . && docker run -p 8080:8080 fosterly`.

| variable | meaning |
|---|---|
| `PORT` | port to listen on (127.0.0.1; `HOST=0.0.0.0` listens on all interfaces) |
| `WTA_VOCAB` | `default` or `alt`: the same app with different wording |
| `WTA_FIXED_NOW` | the app's "now" (default `2026-06-10T09:00:00`); its date is "today" (availability cannot start earlier) |
| `WTA_DATA_DIR` | where session files are written (default: a new temp directory) |
| `WTA_TEST_HOOKS=1` | enables `GET /__wta/state` (the session's drafts and applications and their sha256) |

**Tests:** `node --test "tests/**/*.test.mjs"` runs the unit tests of the form rules
(`tests/fields.test.mjs`) and the HTTP tests (`tests/api.test.mjs`), which need a running
server started with `WTA_TEST_HOOKS=1` and `E2E_BASE_URL=http://127.0.0.1:8080/`.

## Views

| path | what it shows |
|---|---|
| `/` | start: **Start a new application**, and the saved applications to continue ("Continue application FOS-1001", with how many sections are complete) |
| `/apply/<FOS-ref>` | the application: five accordion sections (About you, Your home [+ Other animals], Experience, Availability, Declaration), each with a status badge and a one-line summary; the completeness meter; **Check your answers** |
| `/apply/<FOS-ref>/review` | every answer by section, the status ("Ready to submit" or "n of 5 sections complete"), **Submit application**, Print a copy |
| `/applications/<FA-ref>` | a submitted application (read-only) |
| `/applications` | your submitted applications |
| `/help/fostering` | what fostering involves |

## How it works

- **Accordion.** Each section header is a `<button aria-expanded>`; one section is open at a
  time, collapsed panels are `hidden` (display: none), and pressing an open section's header
  closes it. Every section starts collapsed when the page loads.
- **Per-field autosave, no Save buttons.** `static/apply.js` sends every change of a field at
  once — `input` events for text, email, phone, number, date and the textarea, `change` for
  boxes and dropdowns — as `PATCH /api/drafts/<ref>/<section>` with that one field. The
  server keeps only the fields that section owns (`FIELDS` in `src/fields.js`), checks each,
  stores the accepted value exactly as sent, and answers `{ok: true, completeness}`; a refused
  value is answered `{ok: false, message}` (always HTTP 200) and nothing is stored. An empty
  string clears an answer.
- **Summaries from the client's copy.** The page keeps a copy of the accepted answers. When a
  value is accepted the copy takes it and the section's summary is redrawn from the copy
  ("From 15 Jun 2026, at least 4 weeks"); the meter and the badges show the server's
  completeness. A refused value leaves the copy and the summary unchanged and the reason is
  shown beside the field.
- **Nested conditional sub-section.** Ticking *Other animals at home* reveals *Which animal*
  and *How many*. Unticking hides them again; their answers stay in the draft (ticking the box
  again brings them back) but are not part of what is submitted.
- **Completeness.** About you: name, email and phone. Your home: type of home and, when Other
  animals is ticked, which animal and how many. Experience: years caring for animals (notes are
  optional). Availability: start date (not before today) and minimum weeks. Declaration: ticked,
  and About you complete — the box can be ticked only once About you is complete (otherwise the
  tick is refused and the box unticks itself).
- **Submission gate.** *Submit application* is refused with a message naming the incomplete
  sections while any section is incomplete. A complete draft becomes application FA-n (the
  answers are copied; the application never changes) and leaves the list of saved drafts.
- **Persistence.** One JSON file per session (`wta_sid` cookie, HttpOnly) under
  `WTA_DATA_DIR/sessions/`, created from `seed/fosterly.json`. A request reads the file,
  changes it and writes it back without awaiting in between, so requests of one session never
  interleave. One-time messages live in the same file but are not committed state.
- **Data, not labels.** References, names, dates, home types, species and counts are data
  (the seed); every other visible word comes from `vocab/<profile>.json`.
- *Print a copy* switches the review page to its print layout and says how to print it; *What
  fostering involves* is an information page. Neither changes anything.

## Seed

One saved draft, **FOS-1001**, complete in all five sections: Sam Okafor, a flat with no
garden and no other animals, three years' experience, available from 1 Jul 2026 for at least
4 weeks, declaration signed. New drafts are FOS-1002, FOS-1003, ...; submitted applications
FA-2001, FA-2002, ... (counters per session).

## Workflow graph

`start → apply → (open a section → fill fields → open another) → review → application`;
`start → apply (continue a saved draft)`; `review → apply` (back to the form);
`start → applications → application`; `start → help`.

## Seeded faults

| fault | where | what goes wrong |
|---|---|---|
| FOST-1 | `src/fields.js` | the Availability allow-list names `availableFrom`, so an accepted start date is not stored (summary changes, reload loses it) |
| FOST-2 | `src/applications.js` | submission copies the hidden Other animals answers even when the box is unticked |
| FOST-3 | `src/fields.js` | the conditional requirement reads `draft.otherAnimals`, so a ticked box with no animal described counts as complete |

## Deviations from the plan

None in the views, controls, regions, faults, triggers, assertions or patches. Details the
plan leaves open, decided here:

- `animal-count`'s fourth option is the code `4-or-more`, displayed as "4+" (data).
- *Check your answers* is always available; the gate is on *Submit application* (the review
  page states "n of 5 sections complete" until every section is complete), as the plan's
  state-dependent action describes.
- One extra workflow, "an incomplete application is not submitted", and a few extra
  expectations on the planned workflows (all data, never labels), e.g. the meter staying at 0
  after an availability date alone.
