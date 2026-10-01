"""Seeded faults for Brasswick Asset Register. Run to regenerate buggy/; --check verifies it is in sync."""
from web_testing_agent.benchmark.builder import Patch, main

PATCHES = (
    Patch(
        fault_id="ADMIN-01",
        filename="assets/console.js",
        # The custody ledger becomes insert-only. The first reassignment of a record is
        # stored; every later one is silently dropped, although the save reports success,
        # the change log records it, and the receipt lists the custodian as written. The
        # record page, which re-reads the ledger, keeps showing the first reassignment.
        find="    ledger[String(id)] = holder;",
        replace="    if (!(String(id) in ledger)) { ledger[String(id)] = holder; }",
        why="persistence failure: a second custodian reassignment of the same record is not written back",
    ),
    Patch(
        fault_id="ADMIN-02",
        filename="assets/console.js",
        # The write path locates the record by treating its id as its position in the
        # register. That holds until a record with a lower id is removed; after that an
        # edit's fields are written into the next record down, and the record that was
        # edited keeps its old values. Reads still look records up by id, so every page
        # renders normally. Custody lives in its own ledger keyed by id, so this fault
        # never touches the custodian.
        find="    var slot = positionOf(list, id);",
        replace="    var slot = id - 1;",
        why="state corruption: after a removal, an edit lands in a different record than the one edited",
    ),
)

if __name__ == "__main__":
    raise SystemExit(main(__file__, PATCHES))
