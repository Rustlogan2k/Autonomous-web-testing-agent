-- Tidewater: the bank as one signed-in browser context sees it.
--
-- vault.py runs this file inside a fresh schema (search_path set to it) to build the seeded
-- template and then for every new session, so nothing here is schema-qualified, and every
-- table comes after the tables it refers to.
--
-- Balances are never stored: an account's balance is its opening balance plus the sum of
-- its ledger entries. Deliberately absent: any rule that refuses a transfer larger than the
-- source account's balance (a trigger, or a stored balance with CHECK (balance >= 0)); the
-- application checks funds when it quotes, and that check is where TIDE-1 lives.
-- No default reads the database clock: dates come from the app's clock (WTA_FIXED_NOW).

create table counters (
    name  text primary key,
    value integer not null constraint counter_not_negative check (value >= 0)
);

create table customers (
    id        text primary key,
    username  text not null constraint customer_username_unique unique,
    full_name text not null,
    email     text not null constraint customer_email_unique unique,
    joined_on date not null
);

create table account_products (
    code                  text primary key,
    title                 text not null,
    kind                  text not null constraint product_kind check (kind in ('current', 'savings')),
    overdraft_limit_pence integer not null constraint overdraft_not_negative check (overdraft_limit_pence >= 0),
    interest_bps          integer not null constraint interest_range check (interest_bps between 0 and 1000)
);

create table accounts (
    id                    text primary key,
    customer_id           text not null references customers (id),
    product_code          text not null references account_products (code),
    name                  text not null,
    number                text not null constraint account_number_unique unique
                          constraint account_number_digits check (number ~ '^[0-9]{8}$'),
    opening_balance_pence integer not null,
    position              integer not null constraint account_position_unique unique
);
create index accounts_by_customer on accounts (customer_id);
create index accounts_by_product on accounts (product_code);

create table account_access (
    customer_id text not null references customers (id),
    account_id  text not null references accounts (id),
    level       text not null constraint access_level check (level in ('owner', 'view')),
    primary key (customer_id, account_id)
);
create index account_access_by_account on account_access (account_id);

create table banks (
    id       text primary key,
    name     text not null constraint bank_name_unique unique,
    position integer not null
);

create table payees (
    id             text primary key,
    customer_id    text not null references customers (id),
    nickname       text not null constraint nickname_length check (length(nickname) between 1 and 5000),
    bank_id        text not null references banks (id),
    account_number text not null constraint payee_number_digits check (account_number ~ '^[0-9]{2,8}$'),
    position       integer not null
);
create index payees_by_customer on payees (customer_id, position);
create index payees_by_bank on payees (bank_id);

-- Mandates only: the debits they have collected are ledger entries of kind direct_debit.
create table direct_debits (
    id             text primary key,
    account_id     text not null references accounts (id),
    originator     text not null,
    reference      text not null,
    collection_day integer not null constraint collection_day_range check (collection_day between 1 and 28),
    status         text not null constraint mandate_status check (status in ('active', 'cancelled'))
);
create index direct_debits_by_account on direct_debits (account_id);

create table transfer_drafts (
    id                       integer primary key,
    customer_id              text not null references customers (id),
    source_id                text not null references accounts (id),
    target_account_id        text references accounts (id),
    target_payee_id          text references payees (id),
    available_snapshot_pence integer,
    constraint draft_one_target check (num_nonnulls(target_account_id, target_payee_id) = 1)
);
create index drafts_by_customer on transfer_drafts (customer_id);
create index drafts_by_source on transfer_drafts (source_id);
create index drafts_by_target_account on transfer_drafts (target_account_id);
create index drafts_by_target_payee on transfer_drafts (target_payee_id);

create table quotes (
    id                integer primary key,
    draft_id          integer not null references transfer_drafts (id),
    source_id         text not null references accounts (id),
    target_account_id text references accounts (id),
    target_payee_id   text references payees (id),
    amount_pence      integer not null constraint quote_amount_positive check (amount_pence > 0),
    reference         text not null constraint quote_reference_length check (length(reference) <= 140),
    run_when          text not null constraint quote_run_when check (run_when in ('now', 'next-friday', 'month-end')),
    used              boolean not null,
    constraint quote_one_target check (num_nonnulls(target_account_id, target_payee_id) = 1)
);
create index quotes_by_draft on quotes (draft_id);
create index quotes_by_source on quotes (source_id);
create index quotes_by_target_account on quotes (target_account_id);
create index quotes_by_target_payee on quotes (target_payee_id);

create table transfers (
    id                integer primary key,
    quote_id          integer not null constraint transfer_quote_unique unique references quotes (id),
    source_id         text not null references accounts (id),
    target_account_id text references accounts (id),
    target_payee_id   text references payees (id),
    amount_pence      integer not null constraint transfer_amount_positive check (amount_pence > 0),
    reference         text not null,
    run_when          text not null constraint transfer_run_when check (run_when in ('now', 'next-friday', 'month-end')),
    run_on            date not null,
    status            text not null constraint transfer_status check (status in ('scheduled', 'completed', 'cancelled')),
    authorised_by     text not null references customers (id),
    constraint transfer_one_target check (num_nonnulls(target_account_id, target_payee_id) = 1)
);
create index transfers_by_source on transfers (source_id, status);
create index transfers_by_target_account on transfers (target_account_id);
create index transfers_by_target_payee on transfers (target_payee_id);
create index transfers_by_authoriser on transfers (authorised_by);

create table ledger_entries (
    id           integer primary key,
    account_id   text not null references accounts (id),
    posted_on    date not null,
    description  text not null,
    amount_pence integer not null constraint entry_not_zero check (amount_pence <> 0),
    kind         text not null constraint entry_kind check (kind in
                     ('salary', 'card', 'payment', 'direct_debit', 'transfer_in', 'transfer_out')),
    transfer_id  integer references transfers (id)
);
create index ledger_by_account on ledger_entries (account_id, posted_on);
create index ledger_by_transfer on ledger_entries (transfer_id);
