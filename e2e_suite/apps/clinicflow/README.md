# Clinicflow — Harbourside Health appointments

A clinic's booking site for GP, physiotherapy and practice-nurse appointments, built as a
**server-rendered branching wizard** (Python standard library: `wsgiref` + `sqlite3`).
The booking in progress is a server-side **draft addressed in the URL**
(`/book/<draft>/<step>`), and each branch walks its own route of steps. A small script,
`src/assets/booking.js`, shows and hides parts of a page without a reload: the insurer
fields, the "Next available" highlight, the print-friendly view and the cancel button that
needs a ticked box.

**Run:** `PORT=8080 python src/server.py`, then open http://127.0.0.1:8080/.
**Docker:** `docker build -t clinicflow . && docker run -p 8080:8080 clinicflow`.
Options follow the suite conventions: `WTA_VOCAB` (`default`/`alt`), `WTA_FIXED_NOW`
(default `2026-06-10T09:00:00`; the slot table shows the next five weekdays), `WTA_DATA_DIR`
and `WTA_TEST_HOOKS=1` (enables `GET /__wta/state`).
**Tests:** `E2E_BASE_URL=http://127.0.0.1:8080/ python -m unittest discover -s tests`.

## The journey

1. **Service** (`/book`): GP appointment, Physiotherapy or Practice nurse. Choosing one opens
   a new draft. Only one draft is open at a time, so starting another closes the old one and
   gives back any time it held.
2. **Branch** (`/book/<d>/who`): *New patient* → **registration** (`details`: full name,
   date of birth (not in the future), email, "How will you pay?") or *Returning patient* →
   **lookup** (`lookup`: the email on record; the seeded patient is Alex Morgan,
   `tester@example.com`, plus how they will pay for this visit). Choosing *Insurer* reveals the
   insurer name and member number fields. The script copies the select into the hidden
   `payment_mode` field, which is the value the server books with. Without the script, the
   server falls back to the select itself.
3. **Slot table** (`slots`): one practitioner's week as a grid. Free times have a "Hold slot"
   button. Booked times show "Booked" and keep the button, which is refused there. Above the
   grid are three figures: **Taken**, **Taken by other patients** and **Yours** (times this
   session holds or has booked with that practitioner). "Next available" only highlights the
   first free time.
4. **Pre-visit questions** (`questions`, physiotherapy only): the injury area and a
   description. "Save answers" goes on to the check. "Book visit" here books in one step,
   but only with complete answers.
5. **Check** (`confirm`): the summary, a "Pre-visit questions" status (*Answered*,
   *Not needed* or *Not answered*), "Print summary" (layout only) and "Book visit".
6. **Confirmation** (`/book/<d>/booked`): the reference (`CF-1001`, …). A banner appears only
   on the first view after booking.
7. **Manage** (`/bookings/<ref>`): *Reschedule* reopens the slot table for the same
   practitioner, then the check. Confirming moves the appointment and frees its old time.
   *Cancel visit* needs the confirmation box ticked. All appointments are listed under
   "Your appointments".

The draft owes the first step on its route that is not yet done. A draft may revisit earlier
steps but never jump ahead, and it can be booked only when it owes the check. Registration
is done once per draft.

## Seeded faults (see `app.json`)

| id | kind | what goes wrong |
|---|---|---|
| CLIN-1 | stale-state (extension oracle: `region_arithmetic`) | a reschedule reads the appointment's time after moving it, so the old time is never released and Taken ≠ others + yours |
| CLIN-2 | displayed-vs-submitted | switching the payment select from self-pay to insurer leaves `payment_mode` at `self`, so the visit is booked as self-pay |
| CLIN-3 | missing-validation | the returning-patient route skips the physiotherapy questions and reaches the check unanswered |
