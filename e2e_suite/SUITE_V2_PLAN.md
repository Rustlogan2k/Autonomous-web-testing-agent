# Suite v2 plan: 16 apps with a database server, a login and richer data models

## 0. Summary

- **Engine split.** Each set gets 4 PostgreSQL apps and 4 MongoDB apps, and every engine × language cell is filled in both sets. No app changes language.
  - Development: PostgreSQL = clinicflow (Py), stackroom (Py), tidewater (Py), ledgerly (Node). MongoDB = bagstreet (Py), helpline (Py), orbit (Node), wayfarer (Node).
  - Validation: PostgreSQL = dutyday (Py), markbook (Py), sideline (Py), tally (Node). MongoDB = quizcraft (Py), fosterly, larder, skylark (Node).
- **Faults.** All 47 keep their ID, class, subtype, trigger and observable violation.
  - 46 change at most their anchor (and, for the validation apps, nothing in their traces).
  - One needs a small data-model adaptation: **TALLY-1**. Its fault cannot exist if the database enforces one ballot per paper, so ballots become secret: they carry no paper number. The only visible effect is that the `/ballots` audit lists ballot numbers instead of paper numbers.
  - Several other faults need a constraint to be **deliberately absent**. These are listed per app and declared in the manifest.
- **Drivers: verified feasible.**
  - pymongo 4.18.2 has **no pure-Python wheel**. Taking its `.py` files from any wheel and dropping the two compiled modules works. I verified imports and a BSON round trip, with `has_c()` returning False, on Windows CPython 3.11.8, using the sources of both the Windows cp311 wheel and the **Linux cp312 manylinux** wheel.
  - The Node `mongodb@7.7.0` tree is 12 packages, all pure JS, with no install scripts, gyp files or binaries.
  - Still to be run once: an offline import inside `python:3.12-slim` and `node:22-slim` (checkpoint C0).
- **Main cost of the login.** It is the sign-in macro in the browser (3 steps, about 1.5–3 s per episode or replay), not database seeding (about 0.05–0.2 s per new session).

---

## 1. Per-app table

### 1a. Summary

(★ = the user the sign-in macro signs in as. Effort = builder-agent hours, including 2–3 admission iterations and the Docker check.)

| app | set | lang | engine, and why | users and roles | effort |
|---|---|---|---|---|---|
| bagstreet | dev | Py | **Mongo**: catalog items with variant arrays and category-specific attributes; the bag is one document with embedded lines and voucher; orders are immutable snapshots | ★Jo (customer), Sam (customer), Priya (staff) | 7 h |
| clinicflow | dev | Py | **PG**: a slot grid where each (practitioner, time) has one holder (composite PK/FK); patients, services, bookings and questionnaires all reference each other; reschedules are atomic multi-row updates | ★Rhiannon (receptionist), Tom (receptionist), Dr Okafor (practitioner), Grace (manager) | 10 h |
| helpline | dev | Py | **Mongo**: a ticket is one aggregate (polymorphic conversation entries, history, links) read on one page; the queue reads a denormalised index | ★Sam (agent), Kai (agent, on call), Morgan (lead) | 9 h |
| ledgerly | dev | Node | **PG**: money, rates and limits, an append-only event ledger with per-claim sequence keys, delegation between users | ★Pat (expenses administrator, delegate), Ana, Raj, Lena (employees), Ben (manager), Finance desk | 9 h |
| orbit | dev | Node | **Mongo**: Trello-shaped aggregates (cards embed checklist, comments, activity; projects embed columns and members) | ★Mei (member of both projects), Rosa (owner), Ola (viewer) | 8 h (pilot) |
| stackroom | dev | Py | **PG**: inventory (items × categories × locations × suppliers) with server-side filter, sort and OFFSET paging, plus an FK'd audit journal | ★Dev (stock controller), Lee (viewer) | 7 h (pilot) |
| tidewater | dev | Py | **PG**: accounts, ledger entries, transfers, payees; balances are SUMs | ★Alex (account holder), Jamie (view-only access) | 6 h |
| wayfarer | dev | Node | **Mongo**: write-once nested itinerary versions (days → stops); trips embed members and share | ★Noor (owner), Sam (owner of a shared trip), Omar (not a member) | 6 h |
| dutyday | val | Py | **PG**: a rota (shifts × volunteers), capacity, weekly-hours SUM over date ranges, UNIQUE(day, slot) | ★Sam Okoye (coordinator), 6 volunteers | 6 h |
| markbook | val | Py | **PG**: a students × assessments matrix (composite PK), range checks, visibility as a column DEFAULT (MARK-3) | ★Ms Adeyemi (teacher), Mr Grant (head of department), 6 students | 7 h |
| sideline | val | Py | **PG**: clubs, fixtures and results with FKs; standings are an aggregate over confirmed results | ★Morgan Hale (secretary + league administrator), 6 captains, anonymous public | 9 h |
| tally | val | Node | **PG**: an election (members, papers, ranked preferences with UNIQUE(ballot, rank/candidate), counts per round) | ★Ruth (returning officer), scrutineer, member | 8 h |
| quizcraft | val | Py | **Mongo**: append-only revisions of a nested, polymorphic survey document | ★Lin (editor), Ravi (viewer) | 6 h |
| skylark | val | Node | **Mongo**: heterogeneous observation records, embedded review history, a keyset feed over a compound index | ★Jess (recorder: member + verifier), Tom (member) | 7 h |
| larder | val | Node | **Mongo**: recipes embed lines of different kinds and method steps; per-cook settings sub-document (partial-update semantics, LARD-3); the list is one document | ★Robin (cook), Kit (cook), no roles | 6 h |
| fosterly | val | Node | **Mongo**: a field-by-field autosaved draft with nested conditional sections, and immutable submitted snapshots | ★Sam Okafor (applicant), Jo (applicant), Kim (staff) | 7 h |

**Balance.**

| set | PG-Py | PG-Node | Mongo-Py | Mongo-Node | Py / Node |
|---|---|---|---|---|---|
| dev | 3 | 1 | 2 | 2 | 5 / 3 |
| val | 3 | 1 | 1 | 3 | 4 / 4 |

- In the validation set, Mongo-Py is 1 because its Python apps are rota, marks and standings, which are naturally relational. Every cell is non-empty and the two sets differ by at most 1 per cell.
- Both apps without a Dockerfile (wayfarer, quizcraft) are on Mongo. The engine is irrelevant to the intake refusal they exist to exercise.

### 1b. What every app gains

- **New views:**
  - `/login`: username, password, Sign in, and an informational "Forgot password?" link.
  - A header `#signed-in-as` showing the name and role.
  - An Account control (a `<button aria-expanded>` menu, or an `/account` link in zero-JS apps) whose "Sign out" is a POST.
  - An access-notice view (HTTP 200).
- **New regions:** `signed_in_as`, `login_error`, `access_notice`.
- **Unchanged:** every existing workflow, state-dependent action, decoy, benign trace and trigger trace. They all start right after the macro, on the same entry page as before.

### 1c. Per-app cards

Notation: `→` is an FK or reference; UQ = unique; IDX = secondary index. PG tables live in one schema per session, Mongo collections in one database per session. "Seed cautions" are the seed facts that faults or workflows pin.

#### bagstreet (dev · Py · MongoDB)

- **Data model**
  - `users` {_id, username UQ, name, roles [customer|staff], pw{salt, hash, iter}, addresses[{key, label, name, line1, town, postcode}]}
  - `categories` {_id, name, position}
  - `products` {_id: slug, category_id→categories, name, blurb, attributes oneOf by category (lighting{bulb, lumens} | kitchen{material, capacity_l} | outdoor{weatherproof, max_load_kg}), variants[{code, label, price_pence ≥1, stock ≥0, sku}], reviews[{n, author, stars 1–5, text}]}
    - IDX {category_id, position}; multikey UQ {variants.sku}
  - `vouchers` {_id: code, kind enum percent|free_delivery|amount_off, plus per-kind fields (oneOf)}
  - `stores` {_id, address, hours[]}
  - `bags` {_id→users, lines[{n, product_id→products, variant, qty, unit_price_pence}], voucher{code→vouchers, discount_pence}|null, method, full_name, address, store_id→stores}
  - `orders` {_id "BS-…", customer_id→users, n, method, full_name, address (may be ""), store_id, lines[snapshot], totals{subtotal, discount, delivery, total}, status enum, events[]}
    - IDX {customer_id, n:-1}
- **Roles**
  - Customers (★Jo, Sam): shop, bag, checkout, own orders, read-only saved addresses.
  - Staff (Priya): `/staff/orders`, "Mark dispatched".
  - Restricted check: Sam → `/staff/orders`.
- **Changes**
  - `/account`, `/account/addresses`, `/staff/orders`.
  - Checkout gains a "Use a saved address" select [— (empty), Home, Work] that fills the fields **only when chosen**, never prefilled. A prefilled address would mask BAG-3.
  - The product page shows attributes, stock and reviews (no new controls).
- **Faults**
  - BAG-1: delete `refresh_voucher(db, bag_id)  # the discount depends …` from update-bag; refresh does the `$set` of `voucher.discount_pence`. ✓
  - BAG-2: hidden input in `pages.py`, unchanged. ✓
  - BAG-3: condition in `server.py`, unchanged. The `orders` validator must not require an address for delivery. ✓
- **Seed cautions**
  - Product names, prices and variant order unchanged (the search/sort workflow).
  - Seeded past orders use `BS-09xx` and **every one has a non-empty confirmation address** (BAG-3 is a single `region_empty`).
  - Jo's bag starts empty.

#### clinicflow (dev · Py · PostgreSQL)

- **Data model**
  - `users`(role CHECK IN (receptionist, practitioner, manager), practitioner_id→ NULL, CHECK practitioner role ⇒ id set)
  - `services`(minutes CHECK > 0)
  - `practitioners`(service_id→services, room)
  - `slots`(practitioner_id→, at, PK(practitioner_id, at)): 100 rows
  - `patients`(dob CHECK ≥ 1900, email NOT NULL **not unique**, IDX lower(email))
  - `drafts`(kind / state CHECK, service_id→, patient_id→, …)
  - `injury_areas`
  - `questionnaires`(draft_id PK→, area_id→)
  - `bookings`(ref PK, patient_id→, service_id→, (practitioner_id, slot_at)→slots, pay_mode CHECK IN (self, insurer), CHECK (pay_mode = 'insurer' OR insurer IS NULL), state CHECK, booked_by→users; IDX patient_id)
  - `occupancy`(PK(practitioner_id, at)→slots, holder_kind CHECK, draft_id→ NULL, booking_ref→ NULL, IDX booking_ref)
  - `booking_events`(ref→, seq, kind CHECK, by_user→)
- **Deliberately absent**
  - No UQ on `occupancy.booking_ref` (CLIN-1; domain reason: a long appointment holds two slots).
  - No CHECK requiring a questionnaire for physio (CLIN-3).
  - No UQ on `patients.email`: the new-patient workflow registers `tester@example.com`, which is Alex Morgan's email.
- **Roles**
  - Receptionists (★Rhiannon, Tom): register or look up patients, book, reschedule, cancel. "Your appointments" lists the bookings made by the signed-in receptionist.
  - Practitioner: `/my/day`, read-only.
  - Manager: `/admin/utilisation`, read-only.
  - Restricted check: Dr Okafor → `/book`.
- **Faults**
  - CLIN-1: the read-before-update swap in `store.py`, now pg8000 SQL. ✓
  - CLIN-2: `booking.js`. ✓
  - CLIN-3: route tuple in `wizard.py`. ✓
- **Seed cautions**
  - The 6 "taken by others" per practitioner become 30 seeded bookings of 8 other patients. Their refs are `CF-09xx` and they were booked by Tom, so the next ref is still `CF-1001` and Rhiannon's list starts empty.
  - "Yours" stays derived from the draft patient's bookings and holds; "others" from occupancy held for other patients. This keeps "Taken 8 = 6 + 1 + phantom".

#### helpline (dev · Py · MongoDB)

- **Data model**
  - `agents` {_id, username UQ, roles [agent|lead], on_call}
  - `customers` {_id, name, email UQ, phone, plan{name, speed_mbps, since}, address{}}
  - `tickets` {_id: int, subject, customer_id→customers, channel / priority / status enums, assignee_id→agents|null, merged_into, linked[int→tickets], tags[], opened, entries[oneOf customer{via email|phone} | agent{author_id→agents} | system{event}], history[{n, at, actor, event, detail}]}
    - IDX {status, opened}, {customer_id}, multikey {linked}
  - `queue_index` {_id→tickets, assignee, status}, IDX {assignee, status}: the denormalised index HELP-3 needs
  - `sla_policies` {_id: priority, first_response_min, resolve_hours}
- **Roles**
  - Agents (★Sam, Kai on call): queue, reply, phone reply, status, hand off, merge.
  - Lead (Morgan): adds `/reports` (SLA breaches).
  - Restricted check: Kai → `/reports`.
- **Changes:** the Details tab shows the customer's plan and SLA due time (from the app clock).
- **Faults**
  - HELP-1: Python condition in `desk.py`. ✓
  - HELP-2: `$addToSet` over both `(owner, other)` pairs; the patch keeps one. ✓
  - HELP-3: the `refresh_assignee_index(db, [...] if row["assignee"]])` filter over `queue_index`. ✓
- **Seed cautions**
  - Exactly 10 tickets with the same statuses and assignees (the `count_*` regions).
  - Conversations extended to 2–4 entries, **but each ticket keeps its latest entry's role** (HELP-1).
  - Ticket 108's history keeps `2026-06-04 09:05`.

#### ledgerly (dev · Node · PostgreSQL)

- **Data model**
  - `users`(role CHECK IN (employee, manager, finance, administrator), manager_id→users, cost_centre→, approval_limit_pence NULL CHECK ≥ 0)
  - `delegations`(delegate_id→, principal_id→, PK both, CHECK delegate ≠ principal)
  - `cost_centres`(name UQ)
  - `currencies`(code CHECK `^[A-Z]{3}$`, rate numeric(8,4) CHECK > 0)
  - `categories`
  - `expense_policies`
  - `claims`(id PK, owner_id→; IDX owner_id)
  - `claim_lines`(claim_id→, revision CHECK ≥ 1, line_no CHECK 1–6, amount_pence CHECK 1–1000000, category→, currency→, PK(claim, revision, line_no)): append-only
  - `claim_events`(claim_id→, seq, kind CHECK, actor_id→, acting_for→ NULL, note, revision, PK(claim, seq); IDX actor_id)
  - There is **no status column**: status is the JS fold of events (LED-2).
- **Deliberately absent:** any DB check on the £50 limit (LED-3).
- **Roles**
  - ★Pat is an expenses administrator holding delegations for Ana, Ben and Finance. She uses the existing select (`viewing-as` + `switch-role`, options unchanged: ana, ben, finance).
  - Actions are recorded as the principal, with "via Pat Reid" outside `#claim-approver`.
  - Ana, Raj, Lena: employees. Ben: manager (£50). Finance desk.
  - Restricted check: Ana → `/approvals`.
- **Faults**
  - LED-1: `claim-editor.js`. ✓
  - LED-2: transition table in `claims.js`. ✓
  - LED-3: limit computation in `claims.js`. ✓
- **Seed cautions:** exactly CL-101..103 (the LED-3 witness opens CL-104). Raj and Lena have no claims.

#### orbit (dev · Node · MongoDB) — pilot

- **Data model**
  - `users`
  - `workspaces` {member_ids[→users]}
  - `projects` {workspace_id→, columns[{key enum, name, wip, position}], members[{user_id→users, role enum owner|member|viewer}], labels[]}
  - `cards` {_id: int, project_id→projects, column enum, position, checklist[{n, text, done}], blocked_by[→cards], labels[], assignee_ids[→users], comments[{n, author_id→users, body, at}], activity[oneOf moved | shipped | parked | comment | checked]}
    - IDX {project_id, column, position}, multikey {blocked_by}
- **Roles**
  - ★Mei (member of both): move, ship, park, pull in, checklist, comments.
  - Rosa (owner of web): adds `#/board/web/settings` (WIP limits), which the macro user cannot reach.
  - Ola (viewer): read-only board.
  - Restricted check: Ola → `/#/board/ops` (not a member).
- **Faults**
  - ORB-1, ORB-2: `board.js`. ✓
  - ORB-3: the route-table flag `persist: true` → `false`. The router's unit of work writes the dirty card documents back (bulkWrite of replaceOne) only when `persist` is set. ✓
- **Seed cautions**
  - WIP limits, the 14 cards' columns, checklists and blocked_by unchanged.
  - No seeded comment contains the tester's textarea text.

#### stackroom (dev · Py · PostgreSQL) — pilot

- **Data model**
  - `users`(role CHECK IN (controller, viewer))
  - `categories`(name UQ)
  - `locations`(aisle CHECK 1–4)
  - `suppliers`(name UQ, lead_days CHECK ≥ 0)
  - `items`(sku PK CHECK `^SKU-[0-9]{3}$`, category→, location→, supplier_id→, stock CHECK 0–999999999, reorder_level CHECK ≥ 0, status CHECK IN (active, archived); IDX (category, sku), (stock, sku), (name COLLATE "C"))
  - `journal`(seq PK, sku→items, kind / origin CHECK, user_id→users; IDX (sku, seq DESC))
- **Deliberately absent:** an audit trigger (STK-3).
- **Roles**
  - ★Dev (controller): everything.
  - Lee (viewer): grid without pick boxes, Set fields or Adjust.
  - Restricted check: Lee → `/items/SKU-101/adjust`.
- **Faults**
  - STK-1: client. ✓
  - STK-2: positional lookup via `ORDER BY sku`. ✓
  - STK-3: the `_journal(...)` call. ✓
- **Seed cautions:** the 40 items unchanged. All ORDER BY clauses get a unique tie-break plus `COLLATE "C"`.

#### tidewater (dev · Py · PostgreSQL)

- **Data model**
  - `customers`
  - `account_products`(kind CHECK, overdraft_limit CHECK ≥ 0)
  - `accounts`(customer_id→, product→, number UQ, opening_balance_pence)
  - `account_access`(customer_id→, account_id→, level CHECK IN (owner, view))
  - `banks`(name UQ)
  - `payees`(customer_id→, bank_id→; IDX (customer_id, position))
  - `ledger_entries`(account_id→, posted_on, amount CHECK ≠ 0, kind CHECK, transfer_id→ NULL; IDX (account_id, posted_on))
  - `direct_debits`(day CHECK 1–28)
  - `transfer_drafts`
  - `quotes`(run_when CHECK, amount CHECK > 0)
  - `transfers`(quote_id→ **UQ**, status CHECK)
- **Deliberately absent:** a balance CHECK. Balances are derived (opening + SUM), so the "can be authorised" half of TIDE-1 stays true.
- **Roles**
  - ★Alex (holder): move money, payees, statements, cancel.
  - Jamie (view access): balances and statements only.
  - Restricted check: Jamie → `/#/move`.
- **Faults**
  - TIDE-1, TIDE-2: `server.py`. ✓
  - TIDE-3: `app.js`. ✓
- **Seed cautions:** the 8 statement rows unchanged (the Saver filter expects `1 `). Direct debits are mandates, not ledger rows.

#### wayfarer (dev · Node · MongoDB, no Dockerfile)

- **Data model**
  - `users`
  - `regions` {outlook[{month, summary}]}
  - `trips` {owner_id→users, region_id→regions, members[{user_id→users, role enum}], latest, share{token, issued_seq}|null}, multikey IDX {members.user_id}
  - `versions` {_id "t1:v1", trip_id→trips, n, sealed_by→users, days[{day, stops[{id, name, length enum, kind enum}]}]}, UQ {trip_id, n}
- **Roles**
  - ★Noor owns t1.
  - Sam owns `t90`, shared with Noor as a read-only viewer (no Pin or Seal).
  - Share links stay public.
  - Restricted check: Omar → `/#/trips/t1`.
- **Faults:** WAY-1, WAY-2 are client-side and unchanged. ✓
- **Seed cautions:** t1 v1 unchanged. The seeded shared trip uses an id outside the new-trip counter.

#### dutyday (val · Py · PostgreSQL)

- **Data model**
  - `users`(role CHECK)
  - `volunteers`(weekly_limit_min CHECK > 0)
  - `skills`, `volunteer_skills` (informational only, never enforced)
  - `slots`(minutes CHECK > 0, CHECK ends > starts)
  - `shifts`(day, slot→, needed CHECK 1–5, status CHECK IN (open, needs, full), UQ (day, slot), IDX day)
  - `assignments`(id 'a14', shift_id→, volunteer_id→, assigned_by→users, UQ (shift, volunteer), IDX volunteer_id)
  - `withdrawals`(audit)
  - `closures`
  - About 300 seed rows.
- **Deliberately absent:** a status trigger or generated column (DUTY-3), and any weekly-limit check in the DB (DUTY-1).
- **Roles**
  - ★Coordinator: assigns, removes, sees every volunteer's week, withdraws on a volunteer's behalf. The DUTY-3 trace uses this unchanged.
  - Volunteers: their own week, withdraw their own future shifts, a read-only rota.
  - Restricted check: Ana → `/volunteers/ben`.
- **Changes**
  - Stays zero-JS: Account is a link to `/account` with a Sign-out form.
  - Withdrawal history is rendered **outside** `#vol-shifts`.
- **Faults**
  - DUTY-1: `rota.week_window`, unchanged; SQL sums over its window. ✓
  - DUTY-2: `rota.days_until`. ✓
  - DUTY-3: `rota.refresh_status(db, shift_id)  # a withdrawal can reopen the shift`. ✓

#### markbook (val · Py · PostgreSQL)

- **Data model**
  - `users`(role CHECK)
  - `classes`(teacher_id→users)
  - `students`(class_id→, upn UQ)
  - `assessments`(class_id→, max_marks CHECK 1–1000, weight CHECK 0–100, UQ (class, position))
  - `marks`(PK (student, assessment), score CHECK ≥ 0, entered_by→; IDX assessment_id)
  - `comment_bank`
  - `notes`(id GENERATED BY DEFAULT AS IDENTITY, student_id→, canned→ NULL, visibility CHECK IN (shared, teacher) **DEFAULT 'shared'**)
  - `grade_boundaries`(min_pct UQ)
  - `mark_changes`(audit)
- **Deliberately absent:** score ≤ max_marks in the DB (MARK-2).
- **Roles**
  - ★Teacher: all `/teacher` pages, and preview of `/student/<s>` (path unchanged; the banner sits outside the regions).
  - Head of department: read-only.
  - Students: their own `/student/<self>`.
  - Restricted check: Amara → `/teacher/classes/10b`.
- **Faults**
  - MARK-1: `grades.py`. ✓
  - MARK-2: call site in `server.py`. ✓
  - MARK-3: the edit path `INSERT INTO notes (id, student_id, body, visibility) VALUES (:id, :student, :body, 'teacher') ON CONFLICT (id) DO UPDATE SET body = EXCLUDED.body, visibility = EXCLUDED.visibility`. The patch drops the column and value, so `EXCLUDED.visibility` takes DEFAULT 'shared': the same mechanism as SQLite's INSERT OR REPLACE. ✓
- **Seed cautions:** recount controls on the matrix page; it is about 35 with the account control.

#### sideline (val · Py · PostgreSQL)

- **Data model**
  - `users`(role CHECK IN (secretary, captain), club_code→ NULL, can_act_for_clubs, CHECK captain ⇒ club)
  - `seasons`
  - `clubs`(name UQ)
  - `players`(club→, shirt CHECK 1–99, UQ (club, shirt))
  - `fixtures`(home→, away→, CHECK home ≠ away, UQ (season, home, away), number UQ)
  - `results`(fixture_id PK→, goals CHECK 0–99, status CHECK, home_ok, away_ok, disputed_by→, reason CHECK length ≤ 300; IDX status)
  - `result_events`(kind CHECK, club→, by_user→)
  - About 135 rows.
- **Deliberately absent:** a CHECK tying status to the flags (SIDE-3). Standings are never stored.
- **Roles**
  - ★Secretary, with league-administrator "act for club" rights: the `/` doors are unchanged.
  - Captains: their own `/club/<code>` only.
  - Public pages are anonymous (`login.public`).
  - Restricted check: captain.ash → `/sec`.
- **Faults**
  - SIDE-1: key in `standings.py`. ✓
  - SIDE-2: `WHERE r.status = 'confirmed'` → `WHERE (r.home_ok OR r.away_ok)`. ✓
  - SIDE-3: SET drops `status = 'provisional', `. ✓
- **Seed cautions:** re-verify the seed table and "no GF/GD tie disagreement" in the API tests.

#### tally (val · Node · PostgreSQL)

- **Data model**
  - `users`(role CHECK IN (officer, scrutineer, member))
  - `members`(membership_no UQ)
  - `elections`(phase CHECK, kiosk_paper)
  - `candidates`(UQ (election, ballot_position))
  - `nominations`(proposer→members, seconder→members, CHECK ≠)
  - `papers`(PK (election, number), member_id→ UQ (election, member), CHECK NOT (used AND void))
  - `ballots`(id, cast_seq UQ): **no paper column**
  - `ballot_preferences`(PK (ballot, rank), rank CHECK 1–3, UQ (ballot, candidate))
  - `count_rounds`, `round_outcomes`(outcome CHECK)
  - `declarations`
- **Deliberately absent:** ballot ↔ paper (TALLY-1) and a majority CHECK (TALLY-3).
- **Roles**
  - ★Officer, who also runs the kiosk.
  - Scrutineer: read-only.
  - Member: `/declaration`.
  - Restricted check: member → `/register`.
- **Faults**
  - **TALLY-1:** `UPDATE papers SET used = true WHERE election_id = $1 AND number = $2 AND started AND NOT void AND NOT used RETURNING number`; the patch drops ` AND NOT used`. Preserved with the **secret-ballot adaptation**: `/ballots` lists ballot numbers. Rapid clicks are serialised by the per-session queue. **Flagged.**
  - TALLY-2: `count.js`. ✓
  - TALLY-3: `declaration.js`. ✓

#### quizcraft (val · Py · MongoDB, no Dockerfile)

- **Data model**
  - `users`
  - `clubs`
  - `members` (respondents)
  - `surveys` {club_id→, owner_id→, editors[], head, status enum}
  - `revisions` {survey_id→, n, base, author_id→, document{questions[oneOf choice{options[]} | yesno{rule{when:"no", target}} | rating{min, max} | text]}}, UQ {survey_id, n} (also the optimistic save guard)
  - `responses` {member_id→, answers[{qid, value}]}
- **Roles**
  - ★Editor.
  - Viewer: can preview and read history; edits are refused with a 200 notice.
  - Restricted check: Ravi → `/quiz` shows `#access-notice`.
- **Faults:** QUIZ-1/2/3 are client-side and unchanged. ✓

#### skylark (val · Node · MongoDB)

- **Data model**
  - `users`
  - `sites` {hides[]}
  - `species` {position UQ, months_present[]}
  - `sightings` {_id "s1", n int UQ, date string, site_id→, species_id→, count 1–500, observer_id→, status enum, evidence oneOf seen | heard | photo, reviews[{by→users, action enum}]}
    - IDX {date:-1, n:-1}, {site_id:1, date:-1, n:-1}
- **Roles**
  - ★Recorder (member + verifier).
  - Member: records only.
  - Restricted check: Tom → `/review` (a new read-only verifier queue).
- **Faults**
  - SKY-1: now a Mongo filter, `{ $or: [{ date: { $lt: c.date } }, { date: c.date, n: { $lt: c.n } }] }` → `{ date: { $lt: c.date } }`. ✓
  - SKY-2, SKY-3: client. ✓
- **Seed cautions:** the 22 sightings' date, site, species, count and status unchanged (tie positions). The record form gets no new controls.

#### larder (val · Node · MongoDB)

- **Data model**
  - `households`
  - `users` {household_id→, settings{unitSystem enum, ovenScale enum}, servings{}}, additionalProperties:false
  - `aisles`
  - `ingredients` {aisle_id→}
  - `recipes` {household_id→, number UQ per household, serves, oven_c, source oneOf book | family, lines[{n, ingredient_id→, amount, unit enum g|ml|count}], method[]}
  - `shopping_lists` {_id→users, items[{id, ingredient_id→, recipe_id→, aisle_id, ticked}]}
- **Roles:** none beyond cooks. `restricted: []`.
- **Faults**
  - LARD-1, LARD-2: client. ✓
  - LARD-3: clean is a dot-path `$set` (`settings.<field>`); buggy is `{ $set: { settings: { ...DEFAULT_SETTINGS, ...patch } } }` (wrong update shape). PATCH still answers `{ok:true}` only. ✓

#### fosterly (val · Node · MongoDB)

- **Data model**
  - `users`
  - `drafts` {_id "FOS-1001", applicant_id→, answers{about{}, home{home_type, garden, other_animals, animal_species, animal_count}, experience{}, availability{}, declaration{}}, field_log[]}
  - `applications` {applicant_id→, answers snapshot, animals[{species, count}], status enum, reviews[{by→users}]}, IDX {applicant_id}, {status}
  - `animals` {needs[]}
  - `reference_lists`
- **Deliberately absent:** any validator coupling `other_animals` ↔ `animals` (FOST-2/3).
- **Roles**
  - ★Applicant Sam.
  - Jo (applicant).
  - Kim (staff): `/staff/applications`.
  - Restricted check: Jo → `/staff/applications`.
- **Faults**
  - FOST-1: allow-list in `fields.js`; `$set` is built from the allowed fields only. Guard: skip the write if nothing is allowed, and still answer `ok`. ✓
  - FOST-2: `applications.js`. ✓
  - FOST-3: `fields.js`. ✓
- **Seed cautions:** Jo's draft and past applications use FOS-0998 and FA-199x, so the next refs stay FOS-1002 and FA-2001.

### 1d. Faults that need flagging

- **TALLY-1** is the only fault that needs a data-model adaptation (secret ballot). Its witness, observable violation ("Ballots cast 16 / Papers issued 12") and class are unchanged.
- Constraints that must be **declared absent** in the manifest's `database.absent_constraints`: CLIN-1, CLIN-3, TIDE-1, STK-3, LED-3, BAG-3, DUTY-1, DUTY-3, MARK-2, SIDE-3, FOST-2, FOST-3, TALLY-1, TALLY-3.
- **Faults now expressed in the database layer:** SKY-1 (filter), LARD-3 (update operator), TALLY-1 (compare-and-set predicate), MARK-3, SIDE-2 and SIDE-3 (SQL, as before), HELP-2 (`$addToSet` pair).
- All other faults are app or client code with identical anchors, apart from dropping `sid` arguments.

---

## 2. Session isolation and seeding

**Common to both engines**

- The `wta_sid` cookie (counter-derived) maps to a registry row {partition, user_id, touched-counter} in the variant's home schema or database.
- **A partition is created at the first successful sign-in**, from the seed. Anonymous requests create nothing.
- Credentials are checked against the seed users.
- Sign-out clears `user_id` but **keeps the partition**, so signing in again in the same browser context resumes the same data. A new browser context means a new partition.
- An LRU cap `WTA_SESSION_CAP` (default 32) drops the least-recently-used partition when a new one is created. Episodes and replays never reuse a closed context, so this is safe.
- Requests are **serialised per session**: one lock and one connection in Python; a per-sid promise queue in Node. This reproduces today's "no interleaving" and makes RAPID_CLICK deterministic.
- `/__wta/state` covers only the caller's partition, excludes passwords and flash messages, and never includes partition names or ObjectIds.

**PostgreSQL: schema per session** (the owner's idea; endorsed)

- At app start: create the `app_meta` registry and counters, and build `tmpl` from `src/schema.sql` plus `seed/*.json`.
- At sign-in, **one simple-query round trip** (pg8000 `run()` without parameters and node-pg `query()` without values both use the simple protocol; I confirmed this in the vendored pg8000):
  `BEGIN; CREATE SCHEMA s_000017; SET LOCAL search_path TO s_000017; <schema.sql>; INSERT INTO t SELECT * FROM tmpl.t …; INSERT INTO app_meta.sessions …; COMMIT;`
  then `DROP SCHEMA <lru> CASCADE` when over the cap.
- Every request runs `BEGIN; SET LOCAL search_path …; …; COMMIT`.
- Use counter-based ids rather than identity columns.
- Cleanup: the harness drops the variant database (it already does).

**MongoDB: database per session** (recommended over a collection prefix)

- The home database is the path of `MONGODB_URL`. A session's database is `<home>_s000017`.
- At sign-in, for each collection: `create` (validator, strict, error), then `createIndexes`, then `insertMany` from the seed held in memory.
- A collection prefix costs the same WiredTiger files with uglier names and harder cleanup, so it is rejected.
- **Fallback, if the pilot measures p95 above 300 ms or file pressure:** shared collections per variant with a `tenant` field and every index prefixed by it, wrapped so app code cannot forget the filter.
- Optional speed-up: pre-create one spare partition after each sign-in. Its contents are identical, so it stays deterministic.
- Cleanup: the harness drops `<home>` and `<home>_s*`.

**Cost per new session** (estimates; the pilot measures them and admission enforces a p95)

| | PostgreSQL | Mongo, database per session | Mongo, tenant fallback |
|---|---|---|---|
| PBKDF2 credential check (20k iterations) | 8–15 ms | 8–15 ms | 8–15 ms |
| create the partition | 25–70 ms (1 round trip) | 60–150 ms (~20 commands; +5–20 ms for pure-Python BSON) | 10–30 ms |
| evict when at the cap | 10–25 ms | 10–30 ms | 5–15 ms |
| **total per sign-in** | **45–110 ms** | **80–220 ms** | 25–60 ms |
| storage per partition (cap 32 → per variant) | 0.2–0.4 MB (≤ 13 MB) | 0.3–0.7 MB (~20 files) (≤ 22 MB, ~640 files) | KBs |

- Per episode (about 40 steps × 0.6–1 s ≈ 25–40 s), seeding is under 1%.
- Per run (≈ 50 episodes + ≈ 300 replays ≈ 350 sessions): PostgreSQL ≈ 15–40 s, Mongo ≈ 30–75 s of database time.
- **The macro costs ≈ 9–18 min per run** and +6–8 min per admission. Plan for it.
- Load at 4 jobs × 5 servers = 20 variants: ≤ 640 partitions per engine, ≈ 13k WiredTiger files, under 0.5 GB of tmpfs. That is fine.

---

## 3. Driver vendoring (verified)

**pymongo 4.18.2**
- PyPI ships only platform wheels plus an sdist (2.7 MB). There is **no `py3-none-any`**.
- From the 4.18.2 sources: `bson/__init__.py` sets `_USE_C = False` on ImportError, `pymongo/message.py` sets `_use_c = False`, and `_setup.py` honours `NO_EXT`.
- Test: I loaded the wheels in memory without writing files, with `bson/_cbson*` and `pymongo/_cmessage*` excluded. On **Windows CPython 3.11.8**:
  - with the cp311 Windows wheel: `has_c() False`, BSON round trip including Decimal128 passes, `MongoClient` builds, and a ping with no server raises `ServerSelectionTimeoutError` as expected;
  - with the **cp312 manylinux wheel's** `.py` files: the same results.
- Pure-source size: pymongo + bson + gridfs = **2.67 MB**.
- Only dependency: `dnspython>=2.7,<3`. dnspython 2.8.0 is pure (`py3-none-any`), **1.08 MB** of `.py`, needs Python ≥ 3.10 (host 3.11 ✓, image 3.12 ✓), and is only imported for `mongodb+srv://`. Vendor it anyway.
- Python total ≈ **3.8 MB**.
- Not yet run: the Linux runtime itself. Python 3.12 is not actually installed on this host (stale registry entry). C0 runs `docker run --rm --network none -v <vendor>:/v:ro python:3.12-slim python -c "import sys; sys.path.insert(0,'/v'); import pymongo, bson; print(pymongo.has_c())"`.
- Fallback: pin an earlier pymongo 4.x, or vendor per-platform compiled extensions (not pure, but works offline).

**Node `mongodb@7.7.0`** (engines `>=20.19`; host Node 22.17.0 ✓; local `node:22-slim` is 22.23.3 ✓, read with one throwaway `--rm --network none` container)
- Tree: bson 7.3.x (2.2 MB), @mongodb-js/saslprep 1.5.x (0.65 MB) → sparse-bitfield → memory-pager; mongodb-connection-string-url 7.0.2 → whatwg-url 14.2 → tr46 5.1.1 → punycode, plus webidl-conversions 7 and @types/whatwg-url.
- **12 packages, ≈ 6.9 MB unpacked; no install, preinstall or postinstall scripts, no gyp, no binaries** (registry metadata).
- Optional peers (kerberos, snappy, zstd, client-encryption, aws, gcp, socks) are not vendored.
- Pruning `src/`, `*.d.ts`, `*.map` and the browser/RN bundles brings it to about 2.5–3 MB.
- Fallback: `mongodb@6.21.0` (node ≥ 16.20.1, bson ^6.10).
- I did not install packages. C0 runs `npm install` into a temporary directory once, then an offline `require` in `node:22-slim`.

**PostgreSQL drivers** (already in the worktree)
- Python: pg8000 1.31.5 + scramp + asn1crypto + python-dateutil + six = 1.4 MB.
- Node: pg 8.23.1 + 12 deps = 0.72 MB.

**Import pattern**
- Python: `sys.path.insert(0, <root>/vendor/python)`.
- Node: `createRequire(import.meta.url)("../vendor/node/node_modules/mongodb")`. ESM ignores `NODE_PATH`, and package `imports` cannot point outside `src/`.
- Copy only the language subtree into builds.
- Licences are Apache-2.0, ISC, MIT and BSD; confirm the LICENSE files when vendoring.
- Add `.gitattributes` with `e2e_suite/vendor/** -text`.

---

## 4. Harness and infrastructure changes, file by file

Worktree `../rlwt-suite-v2`. **Commit the existing uncommitted work first.**

| file | change |
|---|---|
| `harness/postgres.py` | Add `--tmpfs …size=2g`, `--memory 2g`, and `-c TimeZone=UTC`. Extend `describe()` with secondary indexes, unique (non-PK) count, NOT NULL ratio, user triggers/rules, `pg_attrdef` scan for now/random defaults, rows per table. Add `run_probes(db, schema, probes)` (each probe in a rolled-back transaction, expecting its SQLSTATE). Add `drop_prefixed()`. |
| **new** `harness/mongo.py` | `mongo:7.0` (pinned digest), container `wta-mongo`, `127.0.0.1:57017`, root user `wta` / `wta-local-test`, `--tmpfs /data/db:size=3g`, `--wiredTigerCacheSizeGB 0.75`, `--setParameter diagnosticDataCollectionEnabled=false`. **Standalone, no replica set** (see decision D2). Functions: race-safe `ensure_server()`, `home_name(app, port)`, `url_for(home, host)` (adds `authSource=admin&directConnection=true&retryWrites=false`), `drop_home()`, `describe(home)` (validators, levels, `required` sizes, additionalProperties, enum/oneOf counts, nesting depth, indexes including unique and compound, docs + embedded counts, `_id` types), `check_references()`, `run_probes()` (code 121 or 11000). Uses the vendored pymongo. |
| **new** `harness/dbctl.py` | `up / down / reset / status` for both containers, run before parallel jobs to avoid start races. Also creates the `wta-net` network as a fallback. |
| `launcher.py` | Dispatch on engine: postgres → `DATABASE_URL`; mongodb → `MONGODB_URL` (home dropped before start and after stop). Pass `WTA_SESSION_CAP` through. |
| `manifest.py` | `database.engine ∈ {postgres, mongodb}`. Validate the `database` block (isolation, entities, references (Mongo ≥ 4), probes ≥ 3, absent_constraints). Validate `login`: path, protected, `public`, users {username, name, roles, exactly one `macro: true`}, `password_value`, steps containing exactly one TYPE whose `params.value` is the password and one whose value is the macro username, `form`, `signed_in_region`, `error_region`, `sign_out`, `restricted`. **Reject a password or username equal to any `generate_input_value(type, category)`.** |
| `variants.py` | Copy `vendor/<language>` for both engines. |
| `browser_checks.py` | Extend `check_login` as in the table below. Add `check_sign_out`, `check_role_restrictions` (macro with the username substituted; `page.goto`; status 200; access region present; sha unchanged), `check_session_cost` (5 HTTP sign-ins), `check_session_cap` (a server with cap 4, 8 sign-ins). In the random walk, record slow responses. |
| `validate_suite.py` | Wire in the new checks (22 → about 33). |
| `docker_checks.py` | `--add-host host.docker.internal:host-gateway`. DB URL: `…@host.docker.internal:55432/e2e_<app>_docker` (PG) or `mongodb://…@host.docker.internal:57017/e2e_<app>_docker?authSource=admin&directConnection=true` (Mongo). Set `WTA_TEST_HOOKS=1`. The health check passes only with the DB reachable. Then an HTTP sign-in round trip (POST form → 303 + cookie → entry 200), and the container's seed sha must equal the host clean build's. If the host route fails, fall back to `--network wta-net` with `wta-postgres:5432` / `wta-mongo:27017`. Drop the DB afterwards. The Dockerfile must `COPY vendor/`. |
| `static_checks.py` | Add `.sql`. Add patterns for `now()`, `current_date`, `current_timestamp`, `clock_timestamp`, `random()`, `gen_random_uuid`, `$$NOW`, `$currentDate`, `ObjectId(`, `$rand`, `$sample`, `secrets.`, `os.urandom`, `CREATE TRIGGER` / `CREATE RULE`. Flag hard-coded DB URLs: the URL must come from the environment. |
| `diversity.py` | Include `.sql` (the 15% shared-line rule now also covers authentication and DB boilerplate; write each app's own). |
| `harness/tests/` | Tests for manifest login/database validation, name sanitisers, and vendored `has_c() is False`. |
| **new** `scripts/vendor_drivers.py` | Rebuilds the vendor tree with network access: strips binaries and maps, writes `vendor/manifest.json` with versions and sha256. |

**Login and session admission checks**

| check | passes when |
|---|---|
| `login_protected` | Every `login.protected` path lands on `login.path` with no status ≥ 400; every `login.public` path answers 200 without redirect. |
| `login_macro_signs_in` | All steps resolve, the final page is not `login.path`, `signed_in_region` contains the macro user's name, and the sha equals the seed sha. |
| `login_wrong_password_refused` | On `login.path`, all responses < 400, `error_region` non-empty, and `/__wta/state` shows no session. |
| `login_sign_out_sound` | Sign-out lands on `login.path`; the entry is protected again; signing in again in the same context gives an unchanged sha. |
| `role_restrictions_sound` | Each `restricted` entry answers 200 with its access region present and the state unchanged. |
| `session_seed_cost` | p95 ≤ 300 ms (warn above 150 ms). |
| `session_cap_honoured` | Live partitions ≤ cap. |
| `variant_storage_released` | 0 leftovers after the variant stops. |
| `responses_fast` | Clean-walk responses other than sign-in have p95 ≤ 150 ms and max ≤ 1000 ms. |

**Data-model contract (exact thresholds; counted in one freshly seeded partition, excluding counters, flash, notices and sessions)**

- **PostgreSQL**
  - ≥ 8 tables; ≥ 6 FKs, ≥ 80% of them backed by an index; ≥ 5 CHECKs, including ≥ 2 enumerations.
  - ≥ 2 UNIQUE besides PKs; ≥ 3 non-unique secondary indexes; ≥ 60% of columns NOT NULL.
  - **0 user triggers or rules; 0 time/random defaults.**
  - ≥ 40 seed rows across ≥ 5 non-empty tables.
  - ≥ 3 probes (FK, CHECK, UNIQUE) rejected with 23503 / 23514 / 23505.
- **MongoDB**
  - ≥ 4 collections, all with `$jsonSchema`, strict, error, `required` ≥ 2 fields; ≥ 2 with `additionalProperties:false`.
  - ≥ 3 `enum`s; ≥ 1 `oneOf`/`anyOf`; ≥ 2 collections with arrays of objects (seeded non-empty); depth ≥ 3 somewhere.
  - ≥ 2 unique indexes besides `_id`; ≥ 3 secondary indexes, including ≥ 1 compound.
  - ≥ 3 declared references with 0 dangling.
  - **No ObjectId**; ≥ 60 documents + embedded elements across ≥ 4 non-empty collections.
  - ≥ 3 probes (missing required field, bad enum, duplicate key) rejected with 121 / 121 / 11000.
- The volume thresholds are deliberately modest: the fault-pinned seed figures limit breadth, so richness comes from structure.

**Manifest `login` block** (dutyday example)

```json
"login": {"path": "/login", "protected": ["/", "/rota"], "public": ["/help"],
 "users": [{"username": "sam.okoye", "name": "Sam Okoye", "roles": ["coordinator"], "macro": true},
           {"username": "ana.silva", "name": "Ana Silva", "roles": ["volunteer"]}],
 "password_value": "Dutyday-rota-26",
 "steps": [{"type": "TYPE", "id": "login-username", "params": {"value": "sam.okoye"}},
           {"type": "TYPE", "id": "login-password", "params": {"value": "Dutyday-rota-26"}},
           {"type": "CLICK", "id": "login-submit"}],
 "form": {"action": "/login", "username_field": "username", "password_field": "password"},
 "signed_in_region": "signed_in_as", "error_region": "login_error",
 "sign_out": [{"type": "CLICK", "id": "nav-account"}, {"type": "CLICK", "id": "sign-out"}],
 "restricted": [{"user": "ana.silva", "path": "/volunteers/ben", "region": "access_notice"}]}
```

- The `database` block holds `engine`, `isolation`, `entities`, `references`, `probes` and `absent_constraints`.
- Keep the sign-in macro to these 3 steps; it is matched by id against the live action space.
- The evaluation runner must pass `login.steps` as `setup_actions`, exactly as the harness does.

---

## 5. CONVENTIONS v2 outline

1. **Layout:** add `src/schema.sql` or validator modules; `vendor/` arrives at build time; the README lists the demo accounts.
2. **Runtime:**
   - "Zero dependencies" becomes "standard library + the suite's vendored pinned drivers only".
   - New environment variables: `DATABASE_URL`, `MONGODB_URL`, `WTA_SESSION_CAP`. The app fails fast if its URL is missing.
   - `/healthz` keeps its payload, with `ok` only when the DB answers a ping.
   - Python: one connection plus a lock. Node: a pool plus a per-session queue. Request p95 ≤ 100 ms.
3. **State and reset:** partitions per engine (§2), the sign-out-resume rule, eviction, and the snapshot exclusions.
4. **New: sign-in and roles.**
   - Form markup and ids; refusal is 200 plus a generic message; `next` restricted to same-origin paths; a signed-in GET of `/login` redirects (so BACK and the bfcache are safe).
   - Two-step sign-out; `Cache-Control: no-store`.
   - PBKDF2 at 20k iterations with seeded salts; one fixture password per app; neither password nor usernames equal any tester value.
   - **Never 401 or 403.** Pages redirect with 303; APIs answer 200 `{ok:false, reason:"signed-out"|"forbidden"}`; pages show `#access-notice` with 200.
   - Navigation never links a role to a forbidden page. The patterns are act-as / delegate (ledgerly, sideline), preview-as (markbook), and acting on behalf (dutyday).
5. **Manifest:** the `database` and `login` blocks; new `stack` and `persistence` strings.
6. **Markup:** the header adds ≤ 1 control and counts toward the 40-control limit; zero-JS apps use an `/account` link.
7. **Faults:**
   - Faults may be re-expressed in the DB layer.
   - **No DB-side business rule may pre-empt a seeded fault**; declare it in `absent_constraints`.
   - SQL anchors must be unique; the replacement text must not already exist in the clean build.
8. **New lessons:**
   - node-pg returns `int8` and `numeric` as strings, and DATE/TIMESTAMP are converted to JS Dates (a timezone trap): set type parsers 1082, 1114, 20 and 1700. pg8000 returns `Decimal` and `date`.
   - Store business dates as ISO strings in Mongo.
   - Every ORDER BY or sort has a unique tie-break plus `COLLATE "C"`.
   - Never use the database's clock.
   - An empty `$set` is legal on Mongo 5.0+ but guard it anyway.
   - Keep seed facts: enrich depth, not breadth.
9. **New:** the data-model contract thresholds (§4).
10. **New:** driver import snippets and the Dockerfile pattern (`COPY vendor/`, `PYTHONPATH`).
11. **New:** the Docker check environment and the networking fallback.

---

## 6. Schedule

Builder **A** is the Python specialist and owns the harness. Builder **B** is the Node specialist and owns vendoring.

| date | A | B |
|---|---|---|
| Oct 9 | **C0** (both builders): commit the worktree; `dbctl up`; pull `mongo:7.0`; host.docker.internal hairpin test. A: harness skeleton (`mongo.py`, launcher, manifest). | `vendor_drivers.py`; offline import tests in both slim images; **micro-benchmarks for the cells the pilots do not cover (pure-Python pymongo, node-pg)**. |
| Oct 9–10 | **Pilot: stackroom (Py/PG)**, all checks including Docker | **Pilot: orbit (Node/Mongo)**, all checks including Docker |
| Oct 10 EOD | **C1:** both pilots admitted; seeding p95 measured on both engines; decision on Mongo isolation; CONVENTIONS v2 frozen | |
| Oct 11 | tidewater | wayfarer |
| Oct 11–12 | clinicflow | ledgerly |
| Oct 12–13 | helpline | bagstreet |
| Oct 13 EOD | **C2:** all 8 dev apps admitted, so tuning can start Oct 14 | B starts tally |
| Oct 14 | dutyday, markbook | tally, fosterly |
| Oct 15 | sideline | skylark |
| Oct 16 | quizcraft; re-run Docker checks | larder |
| Oct 16 EOD | **C3:** all 16 admitted including Docker (wayfarer and quizcraft as intake refusals); diversity over 16 passes; validation tree hashes frozen; a PLAN v2 addendum records the TALLY-1 adaptation | |
| Oct 17–18 | Buffer, and a full 16-app regression with 4 jobs (≈ 2 h) | |

- Load: A ≈ 72 h, B ≈ 64 h (136 h in total, including 18 h of infrastructure). This needs about 8.5 h per builder per day.
- Honest outlook: Oct 16 is achievable only if C1 lands on Oct 10. Expect a risk of 1–3 apps slipping into the Oct 17–18 buffer.
- If something slips, cut the optional enrichment (review pages, response summaries), never faults.
- Only A edits harness files; B files requests to A.

---

## 7. Risks, mitigations and decisions

**Risks**

1. **Mongo seeding is slow, or WiredTiger runs short of file handles.** Measure at C1 and fall back to the tenant field.
2. **The Docker Desktop hairpin fails** (container → host.docker.internal → a port published on 127.0.0.1). Test at C0; fall back to `wta-net`.
3. **Async races in Node** (rapid click, TALLY-1, orbit). The per-session queue is mandatory, and API tests cover concurrency.
4. **Admission takes longer** because of the macro (about +40%). Run 4 jobs and keep `dbctl` up.
5. **Signing out strands the random walk** on the sign-in page. It stays sound, but coverage drops; the two-step sign-out reduces how often it happens.
6. **Seed enrichment shifts pinned numbers.** Frozen seed facts are listed per app and pinned in API tests.
7. **Database constraints pre-empt faults.** They are declared absent, and the per-fault controls catch any regression.
8. **Shared authentication or DB code raises the diversity overlap** above 15%. Each app writes its own; check at C3.
9. **Pure-Python BSON is slow.** The documents are tiny; benchmark at C0.
10. **Held-out integrity.** Builders run only the harness, and tree hashes are frozen at C3.
11. **Docker Desktop memory.** Allow 8 GB or more in `.wslconfig`; tmpfs is capped at 2 GB and 3 GB.
12. **Product-side gap.** The product's runtime would need to supply `DATABASE_URL` / `MONGODB_URL` (a database sidecar) to run v2 apps. This is out of scope here, but it needs your attention.

**Decisions** (★ = recommended)

- **D1, Mongo isolation:** ★ database per session with an LRU cap (subject to p95 ≤ 300 ms); otherwise a tenant field. A collection prefix is rejected.
- **D2, Mongo topology:** ★ standalone. Per-session serialisation plus single-document aggregates remove the need for transactions, and it avoids replica-set host/`directConnection` traps. A single-node replica set is only for a deliberate transaction showcase.
- **D3, sign-out:** ★ present, in two steps (account → sign out). The alternatives are absent or one click.
- **D4, credentials:** ★ unguessable by the tester's own values. The alternative makes them reproducible, so the explorer could sign back in.
- **D5, multi-role witnesses:** ★ act-as / delegation within one sign-in. The alternative is an account switcher with a longer macro.
- **D6, engine assignment:** ★ as in §1. The alternative is MongoDB for all 16 (D-alt below).
- **D7, languages:** ★ unchanged, no rewrites.
- **D8, TALLY-1 adaptation (secret ballot):** ★ approve.
- **D9, Docker networking:** ★ host.docker.internal, with `wta-net` as the fallback.
- **D10, PBKDF2:** ★ 20k iterations as a documented fixture choice. 600k would add 0.3–0.6 s per session.
- **D11, where v2 lives:** ★ replace v1 in place on `suite-v2`, and tag v1.
- **D12, Mongo version:** ★ `mongo:7.0`. The local `mongo:6` image is end-of-life.

**D-alt: MongoDB for all 16**

- PostgreSQL infrastructure disappears (half a day less harness work), and there is no engine diversity.
- Referential integrity rests only on the reference checks; CHECKs become validators (no cross-collection rules).
- The SQL-heavy Python apps need aggregation-pipeline rewrites (standings, weekly hours, ledgers, paging): about +10% effort.
- Seeding pressure doubles, so the tenant fallback becomes likely.
- **MARK-3 changes mechanism.** Mongo has no column defaults, so it becomes "the edit omits `visibility` and the student query uses `$ne:'teacher'`". The observable is identical, but it is a deeper adaptation.
- SIDE-2, SIDE-3, TALLY-1 and CLIN-1 translate cleanly.
- Every app would carry the 3.8 MB Python or about 3 MB Node Mongo driver.

---

### Critical Files for Implementation
- c:\Users\reach\OneDrive\Code files\SEM7\rlwt-suite-v2\e2e_suite\harness\postgres.py
- c:\Users\reach\OneDrive\Code files\SEM7\rlwt-suite-v2\e2e_suite\harness\launcher.py
- c:\Users\reach\OneDrive\Code files\SEM7\rlwt-suite-v2\e2e_suite\harness\manifest.py
- c:\Users\reach\OneDrive\Code files\SEM7\rlwt-suite-v2\e2e_suite\harness\browser_checks.py
- c:\Users\reach\OneDrive\Code files\SEM7\RL web tester\e2e_suite\CONVENTIONS.md

---

## 8. Addendum 2026-10-17: the five remaining development apps are rebuilt *and enlarged*

Owner's decision, 2026-10-17: finish only the five remaining development apps (clinicflow,
wayfarer, ledgerly, bagstreet, helpline); the eight validation apps stay as evaluated
(their rebuild is future work). The development apps are also made larger and more varied,
so that a tester tuned on them meets the breadth of interaction found in real open-source
web applications.

**Independence rule (extends the existing one).** The enlargement is generic, chosen from
common web-application patterns and the blind spots our own evaluations found (list paging,
non-form interaction). Nobody building or tuning on these apps may look at, fetch, or read
about the subject applications of the external benchmark (WebExplor / WebRLED: dimeshift,
pagekit, splittypie, phoenix-trello, retroboard, petclinic, timeoff, realworld, 4gaBoards,
parabank, gadael, agilefant). The external benchmark is the evaluation of the re-tuned tester.

**Size and breadth, per app (in addition to §1 and CONVENTIONS v2):**
- at least **12 distinct views** (routes or screens), at least **3 entity types** with full
  create / read / update / delete reachable from the UI;
- at least **5 of these interaction patterns**: pagination or "load more"; sort and filter on
  lists; search; modal dialogs; tabs; inline (click-to-edit) editing; a multi-step wizard;
  reordering or moving items between groups (buttons, and drag where natural); bulk selection
  with a bulk action; undo; client-side routing with working Back/Forward;
- client-side behaviour of real size in the Node / SPA apps (the tester's breadth matters most
  there); the ≤ 40-controls-per-view rule still holds;
- the clean build still raises **no console errors or failed requests** (existing check).

**Faults.** Every existing fault keeps its design. Each app gains **two new faults** in
interaction types other than form submission (paging, sorting/filtering, inline edit,
reordering, modal or tab state, client-side routing / Back), with designs from standard fault
classes, admitted under the same rules (witness fires on buggy, silent on clean and on its
control, minimised, benign traces silent).

## 9. Addendum 2026-10-19: crash-type faults in the eight development apps

Owner's decision: every development app also carries **two crash-type faults**, so that
tuning sees the failures web-testing benchmarks count (console errors and failed requests),
not only silent functional faults.

- **Fault designs** from standard classes, chosen per app from its own logic: an uncaught
  exception after a particular state change (e.g. a client handler reading a field that a
  prior edit removed), a server error (HTTP 5xx) from an endpoint on an edge-case input or
  state, or a broken resource / navigation target (HTTP 404) reached through a non-obvious
  path. Each needs at least two contextual steps and must not fire on any benign trace or
  workflow.
- **The clean build stays free of console errors and failed requests** (the clean random walk
  check is unchanged); only the buggy build and that fault's other controls show the error.
- **Oracle:** a new extension assertion kind in the harness (Builder A) that fires when a
  console error or failed request matching the fault's declared pattern occurs on a matching
  URL; admitted under the usual rules (fires on buggy, silent on clean and on its control,
  minimised).
- **Independence rule unchanged:** no reference to the external benchmark's subject apps.
