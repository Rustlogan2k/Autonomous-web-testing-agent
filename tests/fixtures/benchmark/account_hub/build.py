"""Seeded faults for Tidepool account hub. Run to regenerate buggy/; --check verifies it is in sync."""
from web_testing_agent.benchmark.builder import Patch, main

PATCHES = (
    Patch(
        fault_id="ACCT-01",
        filename="assets/core.js",
        # The per-seat Priority support add-on is priced for a single month whatever the
        # billing period, so a quarterly or yearly plan with the add-on under-states its
        # charge. Monthly billing (months = 1) is unaffected, which is the default.
        find="var supportCharge = config.seats * SUPPORT_SEAT_RATE * months;",
        replace="var supportCharge = config.seats * SUPPORT_SEAT_RATE;",
        why="calculation error: a per-seat add-on is not scaled by the billing period",
    ),
    Patch(
        fault_id="ACCT-02",
        filename="assets/profile.js",
        # The contact-email change checks that the repeated address was filled in, not that
        # it matches. Format, "already your address" and the access acknowledgement are all
        # still enforced, so only this one declared rule is missing.
        find="var addressesMatch = repeatValue === newValue;",
        replace='var addressesMatch = repeatValue !== "";',
        why="constraint bypass: the repeated email address is never compared with the new one",
    ),
)

if __name__ == "__main__":
    raise SystemExit(main(__file__, PATCHES))
