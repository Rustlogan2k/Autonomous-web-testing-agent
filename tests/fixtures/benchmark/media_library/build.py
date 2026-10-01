"""Seeded faults for Reelhouse. Run to regenerate buggy/; --check verifies it is in sync."""
from web_testing_agent.benchmark.builder import Patch, main

PATCHES = (
    Patch(
        fault_id="MEDIA-01",
        filename="assets/library.js",
        # Applying an edit looks the recording up by its position in the grid as it was
        # arranged when the dialog was opened, and resolves that position against the
        # shelf's own order. In shelf order with no filter the two agree, so the plain
        # walk is correct; once the grid is re-arranged, filtered or searched, the change
        # lands on whichever recording holds that shelf position, and the recording the
        # user edited keeps its old values. Silent: the receipt re-reads the edited
        # recording and shows it unchanged.
        find="var target = Library.byId(dlg.id);",
        replace="var target = Library.all()[dlg.slot];",
        why="state corruption: an edited title lands on another recording when the grid is re-arranged",
    ),
    Patch(
        fault_id="MEDIA-02",
        filename="assets/store.js",
        # The write to sessionStorage is gated on the same test that decides whether the
        # search text needs rebuilding, so a save that changes only fields outside the
        # search index (clearance, front shelf, partner stations) is applied in memory
        # and never written. Everything on screen agrees until the page is reloaded,
        # when the stored, unchanged record comes back.
        find="if (touched.length > 0) { write(); }",
        replace="if (touched.some(isIndexed)) { write(); }",
        why="persistence failure: a change of clearance alone is not written to storage",
    ),
)

if __name__ == "__main__":
    raise SystemExit(main(__file__, PATCHES))
