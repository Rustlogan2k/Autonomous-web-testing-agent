# Helpline — a broadband provider's care desk

A support-ticketing desk for one agent, Sam. Full pages are rendered by a Python server
(standard library + SQLite); interactions inside a page (replies, status changes, hand-offs,
the merge dialog, tabs, queue filters) are sent with `fetch` by a small script
(`src/static/desk.js`) and answered with **HTML fragments**: each top-level element of a
response replaces the element with the same id. An `X-Desk-Url` response header becomes the
page address via `history.replaceState` (`/queue?status=open&assignee=none`,
`/tickets/104?tab=history`). Without the script every form still works through ordinary
posts and redirects.

**Run:** `PORT=8080 python src/server.py`, then open http://127.0.0.1:8080/.
**Docker:** `docker build -t helpline . && docker run -p 8080:8080 helpline`.
Options as in the suite conventions: `WTA_VOCAB` (`default`/`alt`), `WTA_FIXED_NOW`,
`WTA_DATA_DIR`, `WTA_TEST_HOOKS=1` (enables `GET /__wta/state`).
**Tests:** with the server running, `E2E_BASE_URL=http://127.0.0.1:8080/ python -m unittest discover -s tests`.

## Layout

| file | role |
|---|---|
| `src/server.py` | HTTP plumbing, a route table, and which fragments each action answers with |
| `src/desk.py` | the domain: tickets, conversations, the status machine, hand-off, merge |
| `src/store.py` | one SQLite file per browser session (`wta_sid`), copied from a seeded template |
| `src/views.py` | pages and the fragment functions they are assembled from |
| `src/static/desk.js` | fragment swapping, the merge modal, client-only dismissals |
| `seed/desk.json` | ten tickets with their conversations |

## What an agent can do

* **Queue** (`/queue`): filter by status and assignee (including *Unassigned*), *Apply*,
  *Refresh queue*, open a ticket, or *Hand off* a row to Kai, the colleague on call.
* **Ticket** (`/tickets/<n>`): tabs *Conversation* / *Details* / *History*; *Send reply*;
  *Log customer reply* (what the customer said on the phone); *Start triage*, *Start work*,
  *Resolve*, *Close*; *Hand off to Kai*; *Merge…* opens a dialog to merge another open
  ticket into this one (the other ticket is closed and both list each other as linked).

Status machine: new → triage → in progress → waiting on customer → resolved → closed.
Sending a reply moves a new, triaged or in-progress ticket to *waiting on customer*; a
customer reply reopens a waiting or resolved ticket to *in progress*. *Resolve* is refused
while the customer has the last word; *Close* is refused unless the ticket is resolved.
Closed is final.

## Workflows

Answer and resolve a ticket; hand a ticket to Kai from its page or from the queue; merge a
duplicate billing ticket; show unassigned tickets; log a phone reply on a waiting ticket;
resolve and close; triage and start work; read a ticket's history. See `app.json`.
