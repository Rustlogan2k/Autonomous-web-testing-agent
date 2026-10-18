# Clinicflow — Harbourside Health front desk

The front desk of a clinic offering GP, physiotherapy and practice-nurse appointments, built
as a **server-rendered branching wizard** with a patient directory and waiting lists (Python
standard library `wsgiref` + the suite's vendored pg8000 + PostgreSQL). The booking in
progress is a server-side **draft addressed in the URL** (`/book/<draft>/<step>`), and each
branch walks its own route of steps. Small scripts show and hide parts of a page without a
reload (the insurer fields, the "Next available" highlight, the print-friendly view, the
cancel button that needs a ticked box), open confirmation dialogs, and save waiting-list
notes in place.

**Data** (`src/schema.sql`): services, practitioners, staff, the slot grid, patients and their
notes, booking drafts with their pre-visit questionnaires, bookings, who holds each time
(`occupancy`), the booking history and one waiting list per service. Each signed-in browser
session works on its own copy (a PostgreSQL schema cloned from a seeded template); signing
out keeps it. Deliberately absent: a unique booking per held time (CLIN-1) and a rule that a
physiotherapy booking has a questionnaire (CLIN-3); patients may share an email address.

**Run** (the vendored driver must be importable: run a build from `.build/clinicflow/clean`,
or put `e2e_suite/vendor/python` on `PYTHONPATH`):

    DATABASE_URL=postgresql://wta:wta-local-test@127.0.0.1:55432/clinicflow PORT=8080 python src/server.py

then open http://127.0.0.1:8080/ and sign in. The server owns that database and rebuilds it at
start. **Docker:** build from a build directory, then `docker run -p 8080:8080 -e
DATABASE_URL=... clinicflow`. Options follow the suite conventions: `WTA_VOCAB`,
`WTA_FIXED_NOW` (default `2026-06-10T09:00:00`; the slot table shows the next five weekdays),
`WTA_SESSION_CAP`, `WTA_TEST_HOOKS=1`.
**Tests:** `E2E_BASE_URL=http://127.0.0.1:8080/ python -m unittest discover -s tests`.

**Staff accounts** (password `Harbourside-clinic-26` for all):

| username | name | role |
|---|---|---|
| `rhiannon.price` | Rhiannon Price | receptionist: booking, patients, waiting lists |
| `tom.avery` | Tom Avery | receptionist (booked the appointments already in the diary) |
| `amara.okafor` | Dr Amara Okafor | practitioner: *My day* |
| `grace.holloway` | Grace Holloway | practice manager: *Utilisation* |

## The booking journey (receptionists)

1. **Service** (`/book`): GP appointment, Physiotherapy or Practice nurse. Choosing one opens a
   new draft; one draft is open at a time per receptionist, so starting another closes the
   old one and gives back any time it held.
2. **Branch** (`/book/<d>/who`): *New patient* → **registration** (`details`) or *Returning
   patient* → **lookup** (`lookup`: the email on record; Alex Morgan is `tester@example.com`),
   plus how they will pay. Choosing *Insurer* reveals the insurer fields; the script copies the
   select into the hidden `payment_mode` field, which is the value the server books with.
3. **Slot table** (`slots`): one practitioner's week. Above the grid: **Taken**, **Taken by
   other patients** and **Yours** (the patient's appointments with that practitioner plus the
   time this draft holds).
4. **Pre-visit questions** (`questions`, physiotherapy only), 5. **Check** (`confirm`),
   6. **Confirmation** (`booked`, reference `CF-1001` onwards; the banner shows once).
7. **Manage** (`/bookings/<ref>`): reschedule, cancel (with the box ticked), and the history.
   *Your appointments* lists what this receptionist booked, filtered by practitioner and
   ordered by time or reference.

## Patients and waiting lists

- **Patient directory** (`/patients`): search by name or email, order by name, date of birth
  or newest, ten to a page with First / Previous / Next / Last and "Showing a–b of n".
- **Patient record** (`/patients/<id>`), in tabs: *Details* (edit, delete after confirming in a
  dialog, add to a waiting list), *Appointments*, *Notes* (add, edit, delete after confirming).
  A patient with appointments cannot be deleted.
- **Waiting lists** (`/waiting`), one tab per service: move people up or down, edit a note in
  place, remove someone (with *Undo* on the next view).

## Seeded faults (see `app.json`)

| id | kind | what goes wrong |
|---|---|---|
| CLIN-1 | stale-state (extension: `region_arithmetic`) | a reschedule reads the appointment's time after moving it, so the old time is never released and Taken ≠ others + yours |
| CLIN-2 | displayed-vs-submitted | switching the payment select from self-pay to insurer leaves `payment_mode` at `self` |
| CLIN-3 | missing-validation | the returning-patient route skips the physiotherapy questions |
| CLIN-4 | calculation (extension: `region_arithmetic`) | the directory's range on its last page is computed as a full page: "Showing 41–50 of 45" |
| CLIN-5 | persistence (extension: `region_stable_across_reload`) | the in-place waiting-list note editor never sends an edited note; the edit is gone after a reload |
