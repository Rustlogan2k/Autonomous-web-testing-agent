-- Clinicflow's records for one signed-in browser session (partitions.py creates them in a
-- schema of the session's own, with search_path set, after building the seeded template the
-- same way). Tables are listed so that each comes after the tables it refers to; the one
-- reference that points forward (a draft's booking) is added at the end.
--
-- Deliberately absent, so that no database rule pre-empts a seeded fault:
--   * no UNIQUE on occupancy.booking_ref: a long appointment may hold two consecutive times
--     (CLIN-1 leaves an old time held by the booking it no longer belongs to);
--   * no CHECK that a physiotherapy booking has a questionnaire (CLIN-3 books without one).
-- Also absent by design: UNIQUE on patients.email (people share family email addresses).
-- No column default reads the database clock; times come from the app's clock.

CREATE TABLE counters (
    name  TEXT PRIMARY KEY,
    value INTEGER NOT NULL CHECK (value >= 0)
);

CREATE TABLE flashes (
    key     TEXT PRIMARY KEY,
    message TEXT NOT NULL
);

CREATE TABLE services (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    minutes         INTEGER NOT NULL CHECK (minutes > 0),
    needs_questions BOOLEAN NOT NULL,
    position        INTEGER NOT NULL UNIQUE
);

CREATE TABLE practitioners (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    service_id TEXT NOT NULL REFERENCES services (id),
    room       TEXT NOT NULL,
    position   INTEGER NOT NULL UNIQUE
);
CREATE INDEX practitioners_service_idx ON practitioners (service_id);

CREATE TABLE users (
    id              TEXT PRIMARY KEY,
    username        TEXT NOT NULL UNIQUE,
    full_name       TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('receptionist', 'practitioner', 'manager')),
    practitioner_id TEXT REFERENCES practitioners (id),
    CONSTRAINT practitioner_has_diary CHECK (role <> 'practitioner' OR practitioner_id IS NOT NULL)
);
CREATE INDEX users_practitioner_idx ON users (practitioner_id);

-- The bookable grid: every practitioner's times over the days the clinic shows.
CREATE TABLE slots (
    practitioner_id TEXT NOT NULL REFERENCES practitioners (id),
    at              TIMESTAMP(0) NOT NULL,
    PRIMARY KEY (practitioner_id, at)
);

CREATE TABLE patients (
    id        INTEGER PRIMARY KEY,
    full_name TEXT NOT NULL,
    dob       DATE NOT NULL,
    email     TEXT NOT NULL,
    phone     TEXT CHECK (phone ~ '^[0-9 +()-]{6,20}$'),
    origin    TEXT NOT NULL CHECK (origin IN ('record', 'registered'))
);
CREATE INDEX patients_email_idx ON patients (lower(email), id);
CREATE INDEX patients_name_idx ON patients (lower(full_name) COLLATE "C", id);

CREATE TABLE patient_notes (
    id         INTEGER PRIMARY KEY,
    patient_id INTEGER NOT NULL REFERENCES patients (id),
    body       TEXT NOT NULL CHECK (length(body) BETWEEN 1 AND 5000),
    written_on DATE NOT NULL,
    written_by TEXT NOT NULL REFERENCES users (id)
);
CREATE INDEX patient_notes_patient_idx ON patient_notes (patient_id, id);
CREATE INDEX patient_notes_author_idx ON patient_notes (written_by);

CREATE TABLE injury_areas (
    id       TEXT PRIMARY KEY,
    name     TEXT NOT NULL UNIQUE,
    position INTEGER NOT NULL
);

CREATE TABLE drafts (
    id              INTEGER PRIMARY KEY,
    kind            TEXT NOT NULL CHECK (kind IN ('pending', 'new', 'returning', 'reschedule')),
    state           TEXT NOT NULL CHECK (state IN ('open', 'booked', 'done', 'replaced')),
    service_id      TEXT NOT NULL REFERENCES services (id),
    patient_id      INTEGER REFERENCES patients (id),
    practitioner_id TEXT,
    slot_at         TIMESTAMP(0),
    pay_mode        TEXT CHECK (pay_mode IN ('self', 'insurer')),
    insurer         TEXT,
    member          TEXT,
    booking_ref     TEXT,
    created_by      TEXT NOT NULL REFERENCES users (id),
    FOREIGN KEY (practitioner_id, slot_at) REFERENCES slots (practitioner_id, at),
    CONSTRAINT draft_insurer_only_when_insured CHECK (pay_mode = 'insurer' OR insurer IS NULL)
);
CREATE INDEX drafts_service_idx ON drafts (service_id);
CREATE INDEX drafts_patient_idx ON drafts (patient_id);
CREATE INDEX drafts_slot_idx ON drafts (practitioner_id, slot_at);
CREATE INDEX drafts_creator_idx ON drafts (created_by, state);
CREATE INDEX drafts_booking_idx ON drafts (booking_ref);

CREATE TABLE questionnaires (
    draft_id INTEGER PRIMARY KEY REFERENCES drafts (id),
    area_id  TEXT NOT NULL REFERENCES injury_areas (id),
    notes    TEXT NOT NULL CHECK (length(notes) BETWEEN 1 AND 10000)
);
CREATE INDEX questionnaires_area_idx ON questionnaires (area_id);

CREATE TABLE bookings (
    ref             TEXT PRIMARY KEY CHECK (ref ~ '^CF-[0-9]{4}$'),
    draft_id        INTEGER REFERENCES drafts (id),
    patient_id      INTEGER NOT NULL REFERENCES patients (id),
    service_id      TEXT NOT NULL REFERENCES services (id),
    practitioner_id TEXT NOT NULL,
    slot_at         TIMESTAMP(0) NOT NULL,
    pay_mode        TEXT NOT NULL CHECK (pay_mode IN ('self', 'insurer')),
    insurer         TEXT,
    member          TEXT,
    state           TEXT NOT NULL CHECK (state IN ('active', 'cancelled')),
    booked_by       TEXT NOT NULL REFERENCES users (id),
    FOREIGN KEY (practitioner_id, slot_at) REFERENCES slots (practitioner_id, at),
    CONSTRAINT booking_insurer_only_when_insured CHECK (pay_mode = 'insurer' OR insurer IS NULL)
);
CREATE INDEX bookings_patient_idx ON bookings (patient_id);
CREATE INDEX bookings_slot_idx ON bookings (practitioner_id, slot_at);
CREATE INDEX bookings_service_idx ON bookings (service_id);
CREATE INDEX bookings_draft_idx ON bookings (draft_id);
CREATE INDEX bookings_booker_idx ON bookings (booked_by, state);

-- Who holds each taken time: an open draft, or a booking.
CREATE TABLE occupancy (
    practitioner_id TEXT NOT NULL,
    slot_at         TIMESTAMP(0) NOT NULL,
    holder_kind     TEXT NOT NULL CHECK (holder_kind IN ('draft', 'booking')),
    draft_id        INTEGER REFERENCES drafts (id),
    booking_ref     TEXT REFERENCES bookings (ref),
    PRIMARY KEY (practitioner_id, slot_at),
    FOREIGN KEY (practitioner_id, slot_at) REFERENCES slots (practitioner_id, at),
    CONSTRAINT occupancy_one_holder CHECK ((holder_kind = 'draft') = (draft_id IS NOT NULL)
                                           AND (holder_kind = 'booking') = (booking_ref IS NOT NULL))
);
CREATE INDEX occupancy_booking_idx ON occupancy (booking_ref);
CREATE INDEX occupancy_draft_idx ON occupancy (draft_id);

-- One queue per service. Removing an entry only marks it removed, so Undo can put it back
-- where it was; positions stay unique within a service (checked at commit, so two entries
-- can swap places in one transaction).
CREATE TABLE waiting_entries (
    id         TEXT PRIMARY KEY CHECK (id ~ '^w[0-9]+$'),
    patient_id INTEGER NOT NULL REFERENCES patients (id),
    service_id TEXT NOT NULL REFERENCES services (id),
    position   INTEGER NOT NULL CHECK (position >= 1),
    note       TEXT NOT NULL CHECK (length(note) <= 5000),
    state      TEXT NOT NULL CHECK (state IN ('waiting', 'removed')),
    added_on   DATE NOT NULL,
    added_by   TEXT NOT NULL REFERENCES users (id),
    CONSTRAINT waiting_position_unique UNIQUE (service_id, position) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX waiting_patient_idx ON waiting_entries (patient_id);
CREATE INDEX waiting_adder_idx ON waiting_entries (added_by);

CREATE TABLE booking_events (
    ref     TEXT NOT NULL REFERENCES bookings (ref),
    seq     INTEGER NOT NULL CHECK (seq >= 1),
    kind    TEXT NOT NULL CHECK (kind IN ('booked', 'moved', 'cancelled')),
    slot_at TIMESTAMP(0) NOT NULL,
    by_user TEXT NOT NULL REFERENCES users (id),
    PRIMARY KEY (ref, seq)
);
CREATE INDEX booking_events_user_idx ON booking_events (by_user);

ALTER TABLE drafts ADD CONSTRAINT drafts_booking_fk FOREIGN KEY (booking_ref) REFERENCES bookings (ref);
