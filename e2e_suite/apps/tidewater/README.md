# Tidewater — personal banking

A single-page app (hash router, vanilla JS) over a JSON API, behind a sign-in (Python
standard library + the suite's vendored pg8000 + PostgreSQL).

**Data** (`src/schema.sql`): customers, account products, accounts and who may see them
(owner or view access), banks, payees, direct-debit mandates, transfer drafts, quotes,
transfers and the ledger. Balances are never stored: an account's balance is its opening
balance plus its ledger entries. Each signed-in browser session works on its own copy (a
PostgreSQL schema cloned from a seeded template at sign-in); signing out keeps it, so signing
in again in the same browser resumes it. No triggers, no clock or random defaults.

**Run** (the vendored driver must be importable: run a build from `.build/tidewater/clean`,
or put `e2e_suite/vendor/python` on `PYTHONPATH`):

    DATABASE_URL=postgresql://wta:wta-local-test@127.0.0.1:55432/tidewater PORT=8080 python src/server.py

then open http://127.0.0.1:8080/ and sign in. The server owns that database and rebuilds it
at start. **Docker:** build from a build directory (it carries `vendor/`), then
`docker run -p 8080:8080 -e DATABASE_URL=... tidewater`. Options: `WTA_VOCAB=alt`
(alternate wording), `WTA_FIXED_NOW` (the app's "today", default 2026-06-10),
`WTA_SESSION_CAP` (sessions kept, default 32), `WTA_TEST_HOOKS=1` (enables
`GET /__wta/state` for the test harness).

**Demo customers** (password `Tidewater-quay-26` for both):

| username | name | access |
|---|---|---|
| `alex.carter` | Alex Carter | holder of Everyday, Saver and Bills: everything below |
| `jamie.carter` | Jamie Carter | view-only access to Everyday and Bills: balances and statements |

## What you can do

- **Sign in** (*Forgot password?* explains how to get a new one). The header shows who is
  signed in; *Account* opens the menu with *Sign out*.
- **Accounts** (the hub): balances, scheduled transfers (cancellable), active direct debits,
  a promotion (with the bank's accounts and their rates).
- **Move money** — a three-step wizard: choose accounts → amount, reference and timing
  (today / next Friday / end of month; a reminder option appears for future dates) →
  review → authorise. You can step back at any point; your entries are kept.
- **Payees**: add a payee through a two-stage modal (details, then confirmation); rename a
  payee (saved as you type).
- **Statements**: filter by account and direction.

With view-only access the navigation offers Accounts and Statements only; the transfer
wizard and payees show a notice instead, and the API refuses with
`{ok: false, reason: "forbidden"}`. Pages answer 303 to the sign-in page when signed out;
the API answers `{ok: false, reason: "signed-out"}`. Nothing answers 401 or 403.
