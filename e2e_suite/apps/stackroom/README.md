# Stackroom — stockroom inventory administration

The stock grid of a hardware wholesaler: about forty stock lines (SKU-101 to SKU-140) in
four categories (Fasteners, Lighting, Paint, Tools), each with a location (Aisle 1–4), a
stock level and a status (active / archived).

**Architecture.** A data-grid admin page over a JSON API with **server-side filtering,
sorting and paging** (Python standard library + SQLite). Every page route — `/stock`,
`/items/<sku>`, `/items/<sku>/adjust`, `/log` — serves the same HTML shell, and one client
script (`src/static/stackroom.js`) renders the route named by the path. Pages link to each
other with ordinary links (full page loads, no hash router). The grid's state (category,
search, sort, page) lives in its own query string, kept in step with
`history.replaceState`; every change fetches one page of ten rows from `/api/listing`.

**Run:** `PORT=8080 python src/server.py`, then open http://127.0.0.1:8080/ (redirects to
`/stock`). **Docker:** `docker build -t stackroom . && docker run -p 8080:8080 stackroom`.
Options: `WTA_VOCAB=alt` (alternative wording), `WTA_FIXED_NOW` (the clock used to stamp
audit entries, default 2026-06-10T09:00), `WTA_DATA_DIR` (where the SQLite file lives),
`WTA_TEST_HOOKS=1` (enables `GET /__wta/state`).

**Tests:** with the server running, `E2E_BASE_URL=http://127.0.0.1:8080/ python -m unittest
discover -s tests`.

## What you can do

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
- **Audit log** (`/log`): every committed change — bulk archive and restock, adjustments
  and every inline save (even one that leaves the level unchanged), newest first, e.g.
  "SKU-104 stock 7 → 42 (inline edit)".

Stock levels are whole numbers from 0 to 999999999; anything else is refused.
All data belongs to your browser session (the `wta_sid` cookie) and starts from
`seed/stockroom.json`.

## API

| method | path | purpose |
|---|---|---|
| GET | `/api/listing?category=&q=&sort=sku\|name\|stock&dir=asc\|desc&page=` | one page of the grid |
| POST | `/api/bulk` `{action: archive\|restock, skus, view}` | bulk action; returns the refreshed page |
| POST | `/api/items/<sku>/stock` `{stock}` | inline save (audited) |
| GET | `/api/items/<sku>[?notice=take]` | one item (and the one-time saved notice) |
| POST | `/api/adjustments` `{sku, at, view, new_stock}` | commit an adjustment |
| GET | `/api/log` | the audit log |
