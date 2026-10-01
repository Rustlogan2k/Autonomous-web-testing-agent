"""Seeded faults for the Larkfield Broadband care desk. Run to regenerate buggy/; --check verifies it is in sync."""
from web_testing_agent.benchmark.builder import Patch, main

PATCHES = (
    Patch(
        fault_id="SUPP-01",
        filename="assets/portal.js",
        # The billing branch declares "the amount you are querying must be more than 0".
        # The comparison is off by one boundary: zero passes, so a billing query for
        # nothing reaches the contact stage. Empty, non-numeric and over-limit amounts
        # are still refused, and no other stage or branch is touched.
        find="    } else if (amount <= 0) {",
        replace="    } else if (amount < 0) {",
        why="constraint bypass: the billing stage accepts a zero amount it declares invalid",
    ),
    Patch(
        fault_id="SUPP-02",
        filename="assets/portal.js",
        # The engineer-visit stage belongs to a connection fault reported as a total loss
        # of service. The topic conjunct is dropped, so any other topic reported as "No
        # service at all" is routed from the details stage into the engineer-visit
        # booking instead of its own next stage.
        find="    if (d.topic === 'fault' && d.urgency === 'outage') { list.push('visit'); }",
        replace="    if (d.urgency === 'outage') { list.push('visit'); }",
        why="broken transition: a non-fault request marked as a total outage is routed to the engineer-visit stage",
    ),
)

if __name__ == "__main__":
    raise SystemExit(main(__file__, PATCHES))
