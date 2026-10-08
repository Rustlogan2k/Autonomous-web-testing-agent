# Stackroom — stockroom inventory administration

The stock grid of a hardware wholesaler: about forty stock lines (SKU-101 to SKU-140) in
four categories (Fasteners, Lighting, Paint, Tools), each with a location (Aisle 1–4), a
supplier, a reorder level, a stock level and a status (active / archived). Open purchase
orders show what is on its way.

**Architecture.** A data-grid admin page over a JSON API with **server-side filtering,
sorting and paging** (Python standard library + the suite's vendored pg8000 + PostgreSQL),
behind a sign-in. Every page route — `/stock`, `/items/<sku>`, `/items/<sku>/adjust`,
`/log` — serves the same HTML shell, and one client script (`src/static/stackroom.js`)
renders the route named by the path. Pages link to each other with ordinary links (full page
loads, no hash router). The grid's state (category, search, sort, page) lives in its own
query string, kept in step with `history.replaceState`; every change fetches one page of ten
rows from `/api/listing`, a `LIMIT`/`OFFSET` query whose order always ends in the SKU.

**Data.** `src/schema.sql`: users, categories, locations, suppliers, items, the audit
journal, purchase orders and their lines, with foreign keys, checks and indexes. Each
signed-in browser session works on its own copy (a PostgreSQL schema, copied from a seeded
template at sign-in); signing out keeps it, so signing in again in the same browser resumes
it. Nothing is written by the database itself: no triggers, no clock or random defaults.

**Run** (the vendored driver must be importable: run a build from `.build/stackroom/clean`,
or put `e2e_suite/vendor/python` on `PYTHONPATH`):

    DATABASE_URL=postgresql://wta:wta-local-test@127.0.0.1:55432/stackroom PORT=8080 python src/server.py

then open http://127.0.0.1:8080/ and sign in. The server owns that database: it rebuilds its
tables at start. **Docker:** build from a build directory (it carries `vendor/`), then
`docker run -p 8080:8080 -e DATABASE_URL=... stackroom`. Options: `WTA_VOCAB=alt`
(alternative wording), `WTA_FIXED_NOW` (the clock that stamps audit entries, default
2026-06-10T09:00), `WTA_SESSION_CAP` (sessions kept before the least recently used is
dropped, default 32), `WTA_TEST_HOOKS=1` (enables `GET /__wta/state`).

**Demo accounts** (password `Stackroom-aisle-26` for both):

| username | name | role |
|---|---|---|
| `dev.malik` | Dev Malik | stock controller: everything below |
| `lee.ward` | Lee Ward | viewer: the grid, item pages and the audit log, read-only |

**Tests:** with the server running, `E2E_BASE_URL=http://127.0.0.1:8080/ python -m unittest
discover -s tests`.

## What you can do

- **Sign in** at `/login` (*Forgot password?* explains who resets it). The header shows who
  is signed in; *Account* opens the menu with *Sign out*.
- **Stock list** (the hub): filter by category and search text, then *Apply*; sort by SKU,
  name or stock with the column headers (again to reverse); page through ten rows at a
  time (*Previous page* / *Next page*, "Page n of m"); see how many items match.
  *Sync* refetches the current page; *Columns* shows or hides the category, location and
  status columns (in this browser tab only).
- **Bulk actions**: tick rows (or *Select all on this page*), choose *Archive selected* or
  *Restock +10*, and press the bulk *Apply*. With nothing ticked it changes nothing.
- **Inline stock**: type a level into a row's *Set* field; it is saved as you type and
  marked *Saved*.
- **Adjust** a row: enter a *New stock level* (required) and *Commit changes* — you land on
  the item's page with a one-time "Saved SKU-…" notice — or *Discard* to return to the
  grid page you came from.
- **Item page**: category, location, status, stock, reorder level, supplier and lead time,
  units on order, and the item's latest changes.
- **Audit log** (`/log`): every committed change — bulk archive and restock, adjustments
  and every inline save (even one that leaves the level unchanged), newest first, with who
  made it, e.g. "SKU-104 stock 7 → 42 (inline edit, Dev Malik)".

A viewer sees the grid without pick boxes, *Set* fields or *Adjust* links; opening an
adjust page shows a notice instead, and the API refuses a viewer's changes. Stock levels
are whole numbers from 0 to 999999999; anything else is refused.

## HTTP

| method | path | purpose |
|---|---|---|
| GET, POST | `/login` (form: `username`, `password`, `next`) | sign-in page; 303 to `next` and the `wta_sid` cookie, or the page again with a message |
| POST | `/logout` | sign out (the session's data is kept); 303 to `/login` |
| GET | `/api/listing?category=&q=&sort=sku\|name\|stock&dir=asc\|desc&page=` | one page of the grid |
| POST | `/api/bulk` `{action: archive\|restock, skus, view}` | bulk action; returns the refreshed page |
| POST | `/api/items/<sku>/stock` `{stock}` | inline save (audited) |
| GET | `/api/items/<sku>[?notice=take]` | one item with its details (and the one-time saved notice) |
| POST | `/api/adjustments` `{sku, at, view, new_stock}` | commit an adjustment |
| GET | `/api/log` | the audit log |

Pages answer 303 to `/login?next=…` when signed out; the API answers 200
`{ok: false, reason: "signed-out" | "forbidden"}`. Nothing answers 401 or 403.
