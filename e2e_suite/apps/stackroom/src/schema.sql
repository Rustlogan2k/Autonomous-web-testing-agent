-- Stackroom's stockroom: one copy of these tables per signed-in browser session.
--
-- The server runs this file inside a schema of the session's own (search_path is set to it),
-- once to build the seed template and once for every new session, so names here are never
-- schema-qualified. Tables are created in dependency order; the partition copy relies on it.
--
-- Deliberately absent: an audit trigger on items. Every journal line is written by the
-- application next to the change it records, which is where a missing one can be found.
-- No column default reads the database clock or a random source: stamps come from the
-- app's clock (WTA_FIXED_NOW) and every id from a counter or the seed.

CREATE TABLE counters (
    name  text PRIMARY KEY,
    value integer NOT NULL CHECK (value >= 0)
);

CREATE TABLE users (
    id       text PRIMARY KEY,
    username text NOT NULL UNIQUE,
    name     text NOT NULL,
    role     text NOT NULL CHECK (role IN ('controller', 'viewer'))
);

CREATE TABLE categories (
    id       text PRIMARY KEY,
    name     text NOT NULL UNIQUE,
    position integer NOT NULL UNIQUE CHECK (position >= 1)
);

CREATE TABLE locations (
    code  text PRIMARY KEY CHECK (code ~ '^A[1-4]$'),
    label text NOT NULL UNIQUE,
    aisle integer NOT NULL CHECK (aisle BETWEEN 1 AND 4)
);

CREATE TABLE suppliers (
    id        text PRIMARY KEY,
    name      text NOT NULL UNIQUE,
    lead_days integer NOT NULL CHECK (lead_days >= 0)
);

CREATE TABLE items (
    sku           text PRIMARY KEY CHECK (sku ~ '^SKU-[0-9]{3}$'),
    name          text NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
    category_id   text NOT NULL REFERENCES categories (id),
    location_code text NOT NULL REFERENCES locations (code),
    supplier_id   text NOT NULL REFERENCES suppliers (id),
    stock         integer NOT NULL CHECK (stock BETWEEN 0 AND 999999999),
    reorder_level integer NOT NULL CHECK (reorder_level >= 0),
    status        text NOT NULL CHECK (status IN ('active', 'archived'))
);
CREATE INDEX items_by_category ON items (category_id, sku);
CREATE INDEX items_by_stock ON items (stock, sku);
CREATE INDEX items_by_name ON items ((lower(name)) COLLATE "C", sku);
CREATE INDEX items_by_location ON items (location_code);
CREATE INDEX items_by_supplier ON items (supplier_id);

CREATE TABLE journal (
    seq     integer PRIMARY KEY CHECK (seq >= 1),
    stamp   timestamp(0) NOT NULL,
    sku     text NOT NULL REFERENCES items (sku),
    kind    text NOT NULL CHECK (kind IN ('stock', 'archive')),
    before  text NOT NULL,
    after   text NOT NULL,
    origin  text NOT NULL CHECK (origin IN ('inline', 'adjust', 'bulk', 'import')),
    user_id text NOT NULL REFERENCES users (id)
);
CREATE INDEX journal_by_item ON journal (sku, seq DESC);
CREATE INDEX journal_by_user ON journal (user_id);

CREATE TABLE purchase_orders (
    number      text PRIMARY KEY CHECK (number ~ '^PO-[0-9]{4}$'),
    supplier_id text NOT NULL REFERENCES suppliers (id),
    raised_on   date NOT NULL,
    due_on      date NOT NULL,
    status      text NOT NULL CHECK (status IN ('open', 'received', 'cancelled')),
    raised_by   text NOT NULL REFERENCES users (id),
    CHECK (due_on >= raised_on)
);
CREATE INDEX purchase_orders_by_supplier ON purchase_orders (supplier_id, number);
CREATE INDEX purchase_orders_by_raiser ON purchase_orders (raised_by);

CREATE TABLE order_lines (
    order_number text NOT NULL REFERENCES purchase_orders (number),
    line_no      integer NOT NULL CHECK (line_no BETWEEN 1 AND 20),
    sku          text NOT NULL REFERENCES items (sku),
    quantity     integer NOT NULL CHECK (quantity >= 1),
    PRIMARY KEY (order_number, line_no),
    UNIQUE (order_number, sku)
);
CREATE INDEX order_lines_by_item ON order_lines (sku);

-- The one-time "Saved SKU-..." notice an adjustment leaves for the item page: page state,
-- not business state (excluded from /__wta/state).
CREATE TABLE notices (
    slot integer PRIMARY KEY CHECK (slot = 1),
    sku  text NOT NULL REFERENCES items (sku)
);
