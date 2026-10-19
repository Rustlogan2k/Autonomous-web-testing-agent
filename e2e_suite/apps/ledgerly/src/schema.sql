-- Ledgerly's tables: one session's partition. The server runs this file once to build the
-- `tmpl` schema (filled from seed/) and again inside every new session schema, then copies
-- tmpl's rows across in the same round trip.
--
-- A claim has no status column: its status is the fold of its events (src/claims.js), and
-- its lines are append-only revisions. Rules that need a claim's whole value in sterling
-- (the manager's approval limit) are the application's: no CHECK can see across rows.

CREATE TABLE cost_centres (
  code          text PRIMARY KEY CHECK (code ~ '^CC-[0-9]{2}$'),
  name          text NOT NULL UNIQUE CHECK (length(name) BETWEEN 1 AND 60),
  budget_pence  integer NOT NULL CHECK (budget_pence BETWEEN 0 AND 100000000)
);

CREATE TABLE users (
  id                    text PRIMARY KEY CHECK (id ~ '^[a-z]+$'),
  username              text NOT NULL UNIQUE,
  name                  text NOT NULL,
  role                  text NOT NULL CHECK (role IN ('employee', 'manager', 'finance', 'administrator')),
  manager_id            text REFERENCES users (id),
  cost_centre           text REFERENCES cost_centres (code),
  approval_limit_pence  integer CHECK (approval_limit_pence >= 0),
  pw_salt               text NOT NULL CHECK (pw_salt ~ '^[0-9a-f]{32}$'),
  pw_hash               text NOT NULL CHECK (pw_hash ~ '^[0-9a-f]{64}$'),
  pw_iterations         integer NOT NULL CHECK (pw_iterations >= 1000)
);
CREATE INDEX users_by_manager ON users (manager_id);
CREATE INDEX users_by_cost_centre ON users (cost_centre);

-- Who may act for whom (an expenses administrator for the people she supports).
CREATE TABLE delegations (
  delegate_id   text NOT NULL REFERENCES users (id),
  principal_id  text NOT NULL REFERENCES users (id),
  PRIMARY KEY (delegate_id, principal_id),
  CHECK (delegate_id <> principal_id)
);
CREATE INDEX delegations_by_principal ON delegations (principal_id);

CREATE TABLE currencies (
  code      text PRIMARY KEY CHECK (code ~ '^[A-Z]{3}$'),
  rate      numeric(8, 4) NOT NULL CHECK (rate > 0),
  position  integer NOT NULL UNIQUE
);

CREATE TABLE categories (
  code      text PRIMARY KEY CHECK (code ~ '^[a-z]+$'),
  position  integer NOT NULL UNIQUE
);

CREATE TABLE expense_policies (
  category            text PRIMARY KEY REFERENCES categories (code),
  receipt_over_pence  integer NOT NULL CHECK (receipt_over_pence >= 0),
  note                text NOT NULL CHECK (length(note) <= 200)
);

CREATE TABLE merchants (
  id         text PRIMARY KEY CHECK (id ~ '^M[0-9]{3}$'),
  name       text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
  city       text NOT NULL CHECK (length(city) BETWEEN 1 AND 60),
  category   text NOT NULL REFERENCES categories (code),
  preferred  boolean NOT NULL,
  UNIQUE (name, city)
);
CREATE INDEX merchants_by_category ON merchants (category, name COLLATE "C");
CREATE INDEX merchants_by_city ON merchants (city COLLATE "C", name COLLATE "C");

CREATE TABLE claims (
  id           text PRIMARY KEY CHECK (id ~ '^CL-[0-9]{3,6}$'),
  owner_id     text NOT NULL REFERENCES users (id),
  cost_centre  text NOT NULL REFERENCES cost_centres (code)
);
CREATE INDEX claims_by_owner ON claims (owner_id);
CREATE INDEX claims_by_cost_centre ON claims (cost_centre);

-- Append-only: an edit adds a revision; nothing is updated in place.
CREATE TABLE claim_lines (
  claim_id      text NOT NULL REFERENCES claims (id) ON DELETE CASCADE,
  revision      integer NOT NULL CHECK (revision >= 1),
  line_no       integer NOT NULL CHECK (line_no BETWEEN 1 AND 6),
  description   text NOT NULL CHECK (length(description) BETWEEN 1 AND 120),
  category      text NOT NULL REFERENCES categories (code),
  currency      text NOT NULL REFERENCES currencies (code),
  amount_minor  integer NOT NULL CHECK (amount_minor BETWEEN 1 AND 1000000),
  PRIMARY KEY (claim_id, revision, line_no)
);
CREATE INDEX claim_lines_by_category ON claim_lines (category);
CREATE INDEX claim_lines_by_currency ON claim_lines (currency);

-- A claim's history, and the only record of its status. `actor_id` clicked; `acting_for` is
-- the person the action counts as when someone acts on their behalf.
CREATE TABLE claim_events (
  claim_id    text NOT NULL REFERENCES claims (id) ON DELETE CASCADE,
  seq         integer NOT NULL CHECK (seq >= 1),
  kind        text NOT NULL CHECK (kind IN ('claim_drafted', 'draft_saved', 'lodged', 'approved',
                                            'sent_back', 'escalated', 'rejected', 'resubmitted')),
  actor_id    text NOT NULL REFERENCES users (id),
  acting_for  text REFERENCES users (id),
  revision    integer NOT NULL CHECK (revision >= 1),
  at          text NOT NULL CHECK (at ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}$'),
  note        text NOT NULL CHECK (length(note) <= 500),
  PRIMARY KEY (claim_id, seq)
);
CREATE INDEX claim_events_by_actor ON claim_events (actor_id);
CREATE INDEX claim_events_by_principal ON claim_events (acting_for);

-- The next claim, merchant and cost-centre numbers (never reused after a deletion).
CREATE TABLE counters (
  name   text PRIMARY KEY,
  value  integer NOT NULL CHECK (value >= 1)
);
