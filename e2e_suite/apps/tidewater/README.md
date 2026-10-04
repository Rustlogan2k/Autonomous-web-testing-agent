# Tidewater — personal banking

A single-page app (hash router, vanilla JS) over a JSON API (Python standard library + SQLite).

**Run:** `PORT=8080 python src/server.py`, then open http://127.0.0.1:8080/.
**Docker:** `docker build -t tidewater . && docker run -p 8080:8080 tidewater`.
Options: `WTA_VOCAB=alt` (alternate wording), `WTA_FIXED_NOW` (the app's "today",
default 2026-06-10), `WTA_DATA_DIR` (where the SQLite file lives), `WTA_TEST_HOOKS=1`
(enables `GET /__wta/state` for the test harness).

## What you can do

- **Accounts** (the hub): balances, scheduled transfers (cancellable), a promotion.
- **Move money** — a three-step wizard: choose accounts → amount, reference and timing
  (today / next Friday / end of month; a reminder option appears for future dates) →
  review → authorise. You can step back at any point; your entries are kept.
- **Payees**: add a payee through a two-stage modal (details, then confirmation); rename a
  payee (saved as you type).
- **Statements**: filter by account and direction.

All data belongs to your browser session (a cookie) and starts from `seed/seed.json`.
