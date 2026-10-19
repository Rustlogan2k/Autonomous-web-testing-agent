# Helpline — a broadband provider's care desk

A support desk for a team of agents. Full pages are rendered by a Python server (standard
library + the suite's vendored pure-Python pymongo + MongoDB); interactions inside a page
(replies, status changes, hand-offs, the merge dialog, tabs, queue filters) are sent with
`fetch` by a small script (`src/static/desk.js`) and answered with **HTML fragments**: each
top-level element of a response replaces the element with the same id. An `X-Desk-Url`
response header becomes the page address via `history.replaceState`
(`/queue?status=open&assignee=none`, `/tickets/104?tab=history`). Without the script every
form still works through ordinary posts and redirects.

**Data** (`src/schema.py`: one `$jsonSchema` validator per collection, strict, plus indexes):

| collection | holds |
|---|---|
| `agents` | the team, roles (agent, lead), who is on call, password hashes |
| `customers` | contact details, plan (with its router), address, authorised contacts |
| `tickets` | one document per ticket: conversation entries (customer, agent, system), history, links, tags |
| `queue_index` | denormalised assignee and status per ticket, read by the queue's assignee filters |
| `sla_policies` | first-response and resolution times per priority |
| `replies` | saved replies, in order |

Each signed-in browser works on its own MongoDB database (`<home>_s000123`) created from the
seed at sign-in; signing out keeps it.

**Run** (the vendored driver must be importable: run a build from `.build/helpline/clean`,
or put `e2e_suite/vendor/python` on `PYTHONPATH`):

    MONGODB_URL="mongodb://wta:wta-local-test@127.0.0.1:57017/helpline?authSource=admin&directConnection=true" PORT=8080 python src/server.py

then open http://127.0.0.1:8080/ and sign in. The server owns that home database and resets it
at start. **Docker:** build from a build directory, then `docker run -p 8080:8080 -e
MONGODB_URL=... helpline`. Options: `WTA_VOCAB`, `WTA_FIXED_NOW`, `WTA_SESSION_CAP`,
`WTA_TEST_HOOKS=1`. **Tests:** with the server running,
`E2E_BASE_URL=http://127.0.0.1:8080/ python -m unittest discover -s tests`.

**Agents** (password `Helpline-desk-26` for all): `sam.lewis` (Sam Lewis), `kai.moreno` (Kai
Moreno, on call), `noor.haddad` (Noor Haddad), `morgan.blake` (Morgan Blake, team lead: also
sees the SLA report).

## What an agent can do

* **Queue** (`/queue`): filter by status and assignee (including *Unassigned*), *Apply*,
  *Refresh queue*, open a ticket, or *Hand off* a row to Kai, the colleague on call.
* **Ticket** (`/tickets/<n>`): tabs *Conversation* / *Details* (the customer, plan, router and
  SLA times) / *History*; *Send reply* (optionally starting from a saved reply); *Log customer
  reply*; *Start triage*, *Start work*, *Resolve*, *Close*; *Hand off to Kai*; *Merge…* (a
  dialog); *Delete ticket* (after confirming).
* **New ticket** (`/tickets/new`): a three-step wizard — choose the customer, enter the
  subject, channel, priority and the customer's message, review and create.
* **Customers** (`/customers`): search, order by name, newest or fastest plan, ten to a page;
  add, view, edit, and delete a customer without tickets.
* **Saved replies** (`/replies`): add, rename in place, move up or down, delete.
* **Team** (`/team`); **SLA report** (`/reports`, team lead only).

Status machine: new → triage → in progress → waiting on customer → resolved → closed.
Sending a reply moves a new, triaged or in-progress ticket to *waiting on customer*; a
customer reply reopens a waiting or resolved ticket to *in progress*. *Resolve* is refused
while the customer has the last word; *Close* is refused unless the ticket is resolved.
Closed is final.

## Seeded faults (see `app.json`)

| id | kind | what goes wrong |
|---|---|---|
| HELP-1 | state-transition | a phone reply on a resolved ticket leaves it resolved |
| HELP-2 | state-transition | a merge links the tickets one way only |
| HELP-3 | stale-state | a hand-off from the queue does not re-index an unassigned ticket |
| HELP-4 | incorrect-navigation | switching tabs writes the conversation tab's address, so a reload shows another tab |
| HELP-5 | calculation | "Fastest plan first" orders customers by plan name, not speed |
