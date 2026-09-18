"""The frozen vocabulary the label projection is fitted on, and nothing else.

**Why this file exists and why it is committed.** The action representation encodes a
control's label with a frozen sentence embedding, projected from 384 dimensions down to a
width an observation can carry. The projection has to be fitted on *some* corpus, and the
choice of corpus is a generalization decision, not an implementation detail:

* Fitted on the **benchmark applications' own labels**, the projection would be shaped by
  the held-out application, and a leave-one-application-out result measured through it
  would be contaminated before the agent took its first step.
* Fitted **per application**, the representation would not be the same representation
  across applications, and "transfer" would not mean anything.

So it is fitted **once**, on the generic web-UI vocabulary below, and frozen. The matrix
is committed alongside it. No benchmark fixture's DOM was read to build this list.

**The rule this list is written under:** every entry is a phrase that a web application
*in general* might put on a control. Nothing here was harvested from
`tests/fixtures/*/`, and nothing was added because it appeared in a fixture.

`HELD_OUT_PROBE` is deliberately **excluded from the fit** and used to check that the
projection generalises to words it was not fitted on — without it, a projection that
merely memorised this vocabulary would look excellent and transfer nothing.
"""

from __future__ import annotations

#: Generic web-UI control vocabulary, grouped only for readability. Order is fixed:
#: the projection is fitted on this list in this order, so changing it changes the matrix.
FIT_VOCABULARY: tuple[str, ...] = (
    # commit / submit
    "Submit", "Submit form", "Submit application", "Send", "Send message", "Send request",
    "Confirm", "Confirm booking", "Confirm payment", "Place order", "Pay now", "Buy now",
    "Checkout", "Proceed to checkout", "Complete purchase", "Finish", "Finalize",
    "Apply", "Apply changes", "Save", "Save changes", "Save draft", "Update", "Publish",
    "Create", "Create account", "Create new", "Add", "Add item", "Add to cart",
    "Add to basket", "Book now", "Reserve", "Schedule", "Sign up", "Register", "Subscribe",
    "Continue", "Next", "Next step", "Proceed", "Accept", "Agree", "Approve", "Enable",
    # abandon / reverse / destructive
    "Cancel", "Cancel order", "Cancel booking", "Discard", "Discard changes", "Abort",
    "Back", "Go back", "Previous", "Previous step", "Return", "Undo", "Reset", "Clear",
    "Clear filters", "Remove", "Remove item", "Delete", "Delete account", "Decline",
    "Reject", "Disable", "Unsubscribe", "Close", "Dismiss", "Skip", "Exit",
    # session
    "Log in", "Login", "Sign in", "Log out", "Logout", "Sign out", "Forgot password",
    "Reset password", "Change password", "My account", "Profile", "Settings",
    "Preferences", "Manage account", "Switch user",
    # navigation / information
    "Home", "Go home", "Dashboard", "Overview", "Menu", "Browse", "Catalog", "Products",
    "Categories", "Search", "Find", "Filter", "Sort", "View all", "Show more",
    "Load more", "Details", "View details", "Learn more", "Read more", "Help", "Support",
    "Contact us", "About", "About us", "Terms", "Terms of service", "Privacy",
    "Privacy policy", "FAQ", "Documentation", "Status", "Blog", "News",
    # data / list operations
    "Edit", "Modify", "Rename", "Duplicate", "Copy", "Move", "Archive", "Restore",
    "Download", "Upload", "Import", "Export", "Print", "Share", "Refresh", "Reload",
    "Select", "Select all", "Deselect", "Check", "Uncheck", "Expand", "Collapse",
    # cart / commerce
    "Cart", "Shopping cart", "Basket", "View cart", "Empty cart", "Update quantity",
    "Increase quantity", "Decrease quantity", "Apply coupon", "Apply discount",
    "Shipping", "Shipping address", "Billing", "Billing address", "Payment method",
    "Order history", "Track order", "Return item", "Request refund", "Invoice", "Receipt",
    # form field labels
    "Email", "Email address", "Username", "Password", "Confirm password", "First name",
    "Last name", "Full name", "Phone", "Phone number", "Address", "City", "Postcode",
    "Country", "Quantity", "Amount", "Date", "Start date", "End date", "Time",
    "Message", "Comment", "Description", "Title", "Notes", "Search query",
    # generic states
    "Yes", "No", "OK", "Done", "Close dialog", "Try again", "Retry", "Report a problem",
)

#: Held out of the fit on purpose, and used only to check that the projection works on
#: words it has never seen. If the projection's discrimination collapses here while
#: holding up on `FIT_VOCABULARY`, the projection memorised its corpus and must not ship.
HELD_OUT_PROBE: tuple[tuple[str, str, str], ...] = (
    # (label_a, label_b, relation) where relation is "same" or "opposite"
    ("Dispatch the parcel", "Send the parcel", "same"),
    ("Authorise the transfer", "Approve the transfer", "same"),
    ("Enrol in the course", "Register for the course", "same"),
    ("Withdraw the application", "Cancel the application", "same"),
    ("Publish the article", "Release the article", "same"),
    ("Amend the record", "Edit the record", "same"),
    ("Terminate the session", "End the session", "same"),
    ("Acquire the licence", "Purchase the licence", "same"),
    ("Confirm the reservation", "Cancel the reservation", "opposite"),
    ("Activate the account", "Deactivate the account", "opposite"),
    ("Grant access", "Revoke access", "opposite"),
    ("Accept the invitation", "Decline the invitation", "opposite"),
    ("Mount the drive", "Unmount the drive", "opposite"),
    ("Expand the section", "Collapse the section", "opposite"),
)

VOCABULARY_VERSION = "2026-09-18.1"
