"""Seeded faults for Millrace Rooms. Run to regenerate buggy/; --check verifies it is in sync."""
from web_testing_agent.benchmark.builder import Patch, main

PATCHES = (
    Patch(
        fault_id="BOOK-01",
        filename="assets/app.js",
        # The community rate is declared (hire terms, and the checkbox label) as 20% off
        # room hire only. The seeded fault also applies it to the catering line, so a
        # community-rate reservation with catering is undercharged for catering, and the
        # total due is wrong with it. Silent: the review renders normally and logs nothing.
        find="var catering = covers * perHead;",
        replace="var catering = Math.round(covers * perHead * (d.community ? 1 - COMMUNITY_RATE : 1));",
        why="calculation error: the community rate is applied to catering as well as room hire",
    ),
    Patch(
        fault_id="BOOK-02",
        filename="assets/app.js",
        # Finalising should move a reservation from Provisional to Reserved whichever way
        # the customer settles up. The seeded fault leaves an invoiced reservation
        # Provisional, while the page still thanks the customer and issues a reference.
        find="record.status = STATUS.reserved;",
        replace="record.status = record.settle === 'invoice' ? record.status : STATUS.reserved;",
        why="broken transition: finalising an invoiced reservation does not reserve it",
    ),
)

if __name__ == "__main__":
    raise SystemExit(main(__file__, PATCHES))
