# Ledgerly — expense claims with multi-role approval

Server-rendered pages built from template literals and plain HTML forms (Node 22 built-ins
only, post/redirect/get throughout), with one client-side island: the claim editor's
repeatable expense lines (`src/static/claim-editor.js`) — add and remove lines without a
reload, a live summary per currency and in sterling, and inline amount hints. The money
arithmetic (`src/static/money.js`) is one ES module shared by the server and the browser.

**Persistence.** Each session (`wta_sid` cookie) has an append-only JSON-lines event log under
`WTA_DATA_DIR/sessions/`. Every request reads its log and folds it over `seed/ledger.json`
(`src/claims.js`); commands validate against the folded state and append the events they
produce. Nothing is ever rewritten, so a claim's history *is* its log.

**Run:** `PORT=8080 node src/server.js`, open http://127.0.0.1:8080/.
**Docker:** `docker build -t ledgerly . && docker run -p 8080:8080 ledgerly`.
**Tests:** `E2E_BASE_URL=http://127.0.0.1:8080/ WTA_TEST_HOOKS=1 node --test tests/` (needs a
server started with `WTA_TEST_HOOKS=1`). Options as in the suite conventions: `WTA_VOCAB`
(`default`/`alt`), `WTA_FIXED_NOW`, `WTA_DATA_DIR`, `WTA_TEST_HOOKS`.

## Roles and workflow

"Viewing as" in the header switches between Ana Ruiz (employee), Ben Okafor (manager, may
approve up to £50) and the Finance desk.

- **Ana** starts a claim, adds lines (description, category, amount, currency GBP/EUR/USD;
  fixed rates €1 = £0.86, $1 = £0.79), saves a draft or **lodges** it. Lines need a
  description and an amount from 0.01 to 10,000.00; a claim holds at most six lines.
- **Ben** sees lodged claims waiting for him: **Approve** (only when the whole claim, converted
  to sterling, is £50 or less), **Send back** (a note is required), or **Escalate** to finance.
- **Finance** sees escalated claims: **Approve** or **Reject** (with a reason).
- A sent-back claim is edited by Ana and **resubmitted**, which puts it back in Ben's queue.

Every claim page shows its status, who it is waiting on, who approved it, its total in
sterling, its lines, and its history with the latest entry first in view.

## Workflow graph

`my-claims → claim-editor ⇄ (add/remove lines) → claim`; `approval-queue → claim`,
`finance-queue → claim`; claim states `draft → awaiting → approved | sent_back | with_finance`,
`sent_back → awaiting`, `with_finance → approved | rejected`.
