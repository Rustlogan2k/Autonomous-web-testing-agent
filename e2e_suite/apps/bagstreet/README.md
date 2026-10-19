# Bagstreet — a small shop

Server-rendered pages and plain HTML forms (Python standard library plus the suite's vendored
MongoDB driver `pymongo`, put on the import path from `vendor/python/`), post/redirect/get
throughout. `src/static/enhance.js` adds client-side behaviour on top: changing the delivery
method saves the form, "Use a saved address" fills the address fields, tabs on the product
page, and confirmation dialogs.

**Persistence.** MongoDB, one database per signed-in browser (`<home>_s000017`, where the home is
the database named by `MONGODB_URL`), created at the first sign-in from `seed/*.json` with the
validators and indexes in `src/validators.py` (`src/store.py`). The home holds the registry of
browser sessions (`visits`) and their one-time messages. Requests are served one at a time.

**Run** (from a build, which carries `vendor/python/`):
`MONGODB_URL=mongodb://wta:wta-local-test@127.0.0.1:57017/bagstreet?authSource=admin&directConnection=true PORT=8080 python src/server.py`.
**Docker:** `docker build -t bagstreet .` in a build directory, then run with `-e MONGODB_URL=...`.
**Tests:** `E2E_BASE_URL=http://127.0.0.1:8080/ python -m unittest discover -s tests` against a
server started with `WTA_TEST_HOOKS=1`. Options: `WTA_VOCAB`, `WTA_FIXED_NOW`, `WTA_SESSION_CAP`,
`WTA_TEST_HOOKS`.

## Demo accounts (password `Bagstreet-shop-26`)

| user | username | role |
|---|---|---|
| Jo Marsh (the tester signs in as Jo) | `jo.marsh` | customer; saved addresses Home and Work; orders BS-0901, 0903, 0905 |
| Sam Adeyemi | `sam.adeyemi` | customer; orders BS-0902, 0904, 0906 |
| Priya Shah | `priya.shah` | staff: the dispatch desk |

A customer who opens the dispatch desk gets an access notice; the navigation never links to it.

## Views (18 declared)

Sign-in and forgotten password; home (departments and "Everything"); **product list** (search,
department filter, sort, pages of eight); **product page** (tabs Details / Reviews; write, edit or
delete your own review, deletion through a dialog); review editor; **bag** (quantities, voucher,
tick several lines and remove them together); the checkout **wizard** (delivery → check your
order → placed); one order; order history; **account** (with Sign out); **address book** (add, edit,
delete through a dialog); new / edit address; the staff **dispatch desk** (filter by status, tick
orders and mark them dispatched); the access notice; express checkout and gift information.

Entities with full create / read / update / delete through the UI: saved addresses, reviews,
bag lines. Patterns: pagination, sort and filter, search, modal dialogs, tabs, bulk selection with
a bulk action, a multi-step wizard.

## Seeded faults

| fault | where | |
|---|---|---|
| BAG-1 | `server.py` (update bag) | changing a quantity keeps the voucher discount of the old quantity |
| BAG-2 | `pages.py` (review page) | the order is placed with the default delivery method, not the one shown |
| BAG-3 | `server.py` (delivery step) | express delivery goes through without a name and address |
| BAG-4 | `shop.py` (sorting) | "price, high to low" sorts by the first option's price, not the "from" price listed |
| BAG-5 | `pages.py` (filter form) | changing the department on page 2 keeps page 2: "page 2 of 1", empty |

## Suite v2 notes (2026-10-19)

- Seed facts kept: product names, prices and option order of the original six (the cheapest two
  are still the tea towel and the path lights); past orders BS-0901..0906, every one with a
  delivery address (BAG-3 reads one region); Jo's bag starts empty; new orders count from BS-1001.
- Deliberately absent: a validator rule that a delivered order carries a name and address (BAG-3),
  declared in the manifest.
- Deviation from the plan card: customers *edit* their saved addresses (the card said read-only),
  because the enlargement needs a third entity with full CRUD. The checkout's "Use a saved
  address" still fills the fields only when one is chosen, never prefilled.
- The address on a confirmation is the order's snapshot; editing the address book later does
  not change past orders.
