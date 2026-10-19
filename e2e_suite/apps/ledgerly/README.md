# Ledgerly — expense claims with multi-role approval

Server-rendered pages built from template literals and plain HTML forms (Node 22 built-ins plus
the suite's vendored PostgreSQL driver `pg`, loaded with `createRequire` from `vendor/node/`),
post/redirect/get throughout. Client-side behaviour sits on top: the claim editor's repeatable
lines with a live multi-currency summary (`src/static/claim-editor.js`, money arithmetic shared
with the server in `src/static/money.js`) and `src/static/ui.js` (sorting and filtering, tabs,
dialogs, bulk selection, click-to-rename, the Account menu).

**Persistence.** PostgreSQL, one schema per signed-in browser (`s_000017` in the home database
named by `DATABASE_URL`), copied from a `tmpl` schema (`src/schema.sql` + `seed/*.json`) in one
round trip at the first sign-in (`src/db.js`). A claim has no status column: its lines are
append-only revisions and its status is the fold of its events (`src/claims.js`).

**Run** (from a build, which carries `vendor/node/`):
`DATABASE_URL=postgresql://wta:wta-local-test@127.0.0.1:55432/ledgerly PORT=8080 node src/server.js`.
**Docker:** `docker build -t ledgerly .` in a build directory, then run with `-e DATABASE_URL=...`.
**Tests:** `E2E_BASE_URL=http://127.0.0.1:8080/ node --test "tests/**/*.test.mjs"` against a server
started with `WTA_TEST_HOOKS=1`. Options: `WTA_VOCAB`, `WTA_FIXED_NOW`, `WTA_SESSION_CAP`, `WTA_TEST_HOOKS`.

## Demo accounts (password `Ledgerly-claims-26`)

| user | username | role |
|---|---|---|
| Pat Reid (the tester signs in as Pat) | `pat.reid` | expenses administrator; acts for Ana, Ben and Finance through "Viewing as" |
| Ana Ruiz | `ana.ruiz` | employee (claims CL-101..103) |
| Raj Patel, Lena Novak | `raj.patel`, `lena.novak` | employees with no claims |
| Ben Okafor | `ben.okafor` | manager, may approve up to £50 |
| Finance desk | `finance.desk` | finance |

What Pat does on someone's behalf is recorded as that person, "via Pat Reid". Merchants and cost
centres are changed by the administrator only; a page a role may not open shows an access notice.

## Views (15)

Sign-in and forgotten password; **My claims** (filter by status, sort by total or number, in the
browser); the **claim editor**; **one claim** (tabs Lines / History; a draft can be discarded through
a dialog); the manager's **approval queue** (select several and approve them together); the
**finance queue**; **merchants** (search, category filter, sort, pages of eight, all in the address);
one merchant; new / edit merchant (delete through a dialog); **cost centres** (add, click a name to
rename it, delete an unused one through a dialog); the **spending report**; the access notice.

Entities with full create / read / update / delete through the UI: claims (discard = delete a
never-lodged draft), merchants, cost centres. Patterns: pagination, sort and filter, search, modal
dialogs, tabs, inline editing, bulk selection with a bulk action.

## Rules

- Lines need a description and an amount from 0.01 to 10,000.00 (GBP, EUR at £0.86, USD at £0.79);
  at most six lines.
- The manager approves only a claim worth £50 or less in sterling (all lines converted), sends it
  back with a note, or escalates it; finance approves or rejects (with a reason). A sent-back
  claim is edited and resubmitted into the manager's queue.

## Seeded faults

| fault | where | |
|---|---|---|
| LED-1 | `claim-editor.js` | changing a line's currency leaves the sterling total at the old rate |
| LED-2 | `claims.js` (transition table) | a resubmitted claim stays "Sent back" |
| LED-3 | `claims.js` (limit) | the £50 limit counts pound lines only |
| LED-4 | `ui.js` (click-to-rename) | a refused cost-centre rename stays on screen; a reload shows the old name |
| LED-5 | `ui.js` (sorting) | "Sort by total" reads the column before the total, so "lowest first" is not ascending |

## Suite v2 notes (2026-10-19)

- Seed facts kept: exactly CL-101..103 (the LED-3 witness opens CL-104), Raj and Lena have no
  claims, the "Viewing as" options are still ana, ben, finance (Pat's delegations).
- `claim_lines.amount_minor` is the plan's `amount_pence`: amounts are in the line's own currency.
- Deliberately absent: any database rule on the £50 limit (LED-3), declared in the manifest.
- The session's "viewing as" and its one-time page message live in the registry
  (`app_meta.sessions`), not in the partition: they are browser state, not business data.
