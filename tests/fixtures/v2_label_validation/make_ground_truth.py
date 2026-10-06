"""Ground truth for the labeller validation pages, written by hand before labeller v3 ran.

Run once to expand the rules below into `ground_truth.json`:

    python tests/fixtures/v2_label_validation/make_ground_truth.py

Vocabulary is labeller v3's (save / advance / commit merged into `submit`). Where a page or
control honestly fits more than one word, the accepted set lists each. Radio buttons and
checkboxes are fields, not buttons: they are left out of role scoring. Page kinds are keyed
by page id (from each page's headings, text and open dialogs, read before any labelling).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "src"))

from web_testing_agent.hybrid.label_validation import load_pages  # noqa: E402

NAV = {"navigate"}
ROLES: dict[str, list[tuple[str, str, set[str]]]] = {
    # (match "id" | "label", regex (full match), accepted roles)
    "account_hub": [
        ("id", r"nav-.*", NAV), ("id", r"card-.*", {"navigate", "open", "edit"}),
        ("id", r"plan-review|email-review|plan-apply|email-switch", {"submit"}),
        ("id", r"plan-close|email-back|plan-back|email-discard|tip-dismiss", {"back"}),
        ("id", r"details-edit|name-edit|email-edit|open-plan-editor", {"edit", "open"}),
    ],
    "admin_records": [
        ("id", r"nav-register|add-equipment|go-register", {"add", "open", "navigate"}),
        ("id", r"nav-.*|go-equipment", NAV), ("id", r"back-to-register", {"back", "navigate"}),
        ("id", r"save-changes|create-record", {"submit"}),
        ("id", r"discard-edits|discard-draft", {"back"}),
        ("id", r"apply-filter", {"search_or_filter"}),
        ("id", r"view-\d+|attention-\d+", {"open", "navigate"}),
        ("id", r"edit-\d+|edit-record", {"edit"}), ("id", r"remove-\d+|remove-record", {"remove"}),
    ],
    "booking_desk": [
        ("id", r"nav-help", {"help", "navigate"}), ("id", r"nav-.*|rail-.*", NAV),
        ("id", r"see-reservations|another-room|browse-rooms", {"navigate", "open"}),
        ("id", r"finalise|save-later|to-details|to-extras", {"submit"}),
        ("id", r"amend-.*", {"edit"}), ("id", r"back-session", {"back"}),
        ("id", r"change-room", {"back", "navigate", "edit"}),
        ("id", r"cancel-draft|release-.*", {"remove", "back"}),
        ("id", r"clear-extras|clear-session", {"remove", "other"}),
    ],
    "media_library": [
        ("id", r"nav-help", {"help", "navigate"}), ("id", r"nav-.*", NAV),
        ("id", r"list-.*", {"open", "search_or_filter", "navigate"}),
        ("id", r"edit-review|move-apply|add-file|add-to-options|review-apply", {"submit"}),
        ("id", r"edit-discard|move-cancel|add-back-options|add-abandon|detail-close", {"back"}),
        ("id", r"review-back", {"back", "edit"}), ("id", r"add-recording", {"add", "open"}),
        ("id", r"archive-notes", {"open", "navigate", "other"}), ("id", r"open-item-\d+", {"open"}),
        ("id", r"receipt-finish|added-finish", {"navigate", "back"}),
        ("id", r"detail-edit", {"edit"}), ("id", r"detail-move", {"open", "edit"}),
        ("id", r"detail-remove", {"remove"}), ("id", r"detail-earlier|detail-later", NAV),
    ],
    "shop_catalog": [
        ("id", r"basket-link", {"navigate", "open"}),
        ("id", r"go-to-checkout", {"submit", "navigate"}), ("id", r"empty-the-basket", {"remove"}),
        ("id", r"confirm-order", {"submit"}), ("id", r"add-to-basket", {"add"}),
        ("label", r"FernbrookOutfitters|Store|Shelter|Cooking", NAV),
        ("label", r"Back to the basket|Back to shelter", {"back", "navigate"}),
        ("label", r"Continue shopping", {"navigate", "back"}),
        ("label", r"Delivery and collection times|Returns policy|Returns within 30 days|"
                  r"Stockists and trade enquiries", {"help", "navigate"}),
        ("label", r"Delivery times|Returns|Delivery|Sizing|Stockists|Privacy notice|"
                  r"Sizing and fit guidance", {"help", "navigate"}),
        ("label", r"Ridgeline 2 tent|Fell tarp 3x3|Bivvy Lite bag|View .*", {"open", "navigate"}),
    ],
    "support_portal": [
        ("id", r"nav-help|start-help|foot-.*", {"help", "navigate"}),
        ("id", r"nav-raise|start-request|raise-another", {"open", "navigate"}),
        ("id", r"nav-.*|start-track|track-this", NAV),
        ("id", r"save-.*|check-answers|send-request|use-topic", {"submit"}),
        ("id", r"save-draft", {"submit"}), ("id", r"clear-step", {"remove", "other"}),
        ("id", r"back-step", {"back"}), ("id", r"discard-request", {"back", "remove"}),
        ("id", r"amend-.*", {"edit"}),
    ],
}
FIELDS: dict[str, dict[str, set[str]]] = {
    "account_hub": {"plan-seats": {"number"}, "billing-period": {"choice"}, "language": {"choice"},
                    "time-zone": {"choice"}, "new-email": {"email"}, "repeat-email": {"email"}},
    "admin_records": {"f-asset-name": {"name", "free_text", "other"}, "f-custodian": {"name"},
                      "f-units": {"number"}, "f-notes": {"free_text"}, "f-change-reason": {"free_text"},
                      "f-category": {"choice"}, "f-location": {"choice", "address"},
                      "f-status": {"choice"}, "f-show-status": {"choice"}},
    "booking_desk": {"catering": {"choice", "number"}, "session-date": {"date"}, "guests": {"number"},
                     "arrival": {"choice", "date"}, "hours": {"choice", "number"}, "layout": {"choice"}},
    "media_library": {"item-title": {"name", "free_text", "other"}, "item-summary": {"free_text"},
                      "item-recorded-by": {"name"}, "item-programme": {"choice"},
                      "item-clearance": {"choice"}, "move-to": {"choice"},
                      "find-text": {"free_text", "other"}, "arrange-by": {"choice"},
                      "show-programme": {"choice"}, "new-title": {"name", "free_text", "other"},
                      "new-summary": {"free_text"}, "new-recorded-by": {"name"}},
    "shop_catalog": {"contact": {"email"}, "postcode": {"address"}, "dispatch": {"choice"},
                     "qty": {"number"}},
    "support_portal": {"invoice_ref": {"other", "free_text", "number"},
                       "disputed_amount": {"number", "money"}, "charge_kind": {"choice"},
                       "full_name": {"name"}, "email": {"email"}, "phone": {"phone"},
                       "best_time": {"choice", "date"}, "summary": {"free_text"},
                       "description": {"free_text"}, "urgency": {"choice"},
                       "since": {"choice", "date"}, "router_lights": {"choice"},
                       "devices_affected": {"choice"}, "access_notes": {"free_text"}},
}
FORM, DETAIL, LIST, REVIEW, OUTCOME = {"form"}, {"detail"}, {"list"}, {"review"}, {"outcome"}
PAGE_KINDS: dict[str, set[str]] = {
    # account_hub
    "387f90": FORM | DETAIL, "88924f": {"home"}, "15316c": FORM | DETAIL, "4451a1": OUTCOME | DETAIL,
    "3bcfce": FORM | DETAIL, "56650d": REVIEW, "ce867c": FORM | DETAIL, "67fb9b": OUTCOME | DETAIL,
    "ec235a": FORM, "68b2f6": FORM | DETAIL, "fed4b3": FORM, "697172": FORM | DETAIL,
    # admin_records
    "0d8068": FORM, "05e56e": LIST, "c7e37c": {"home"}, "0dbd32": OUTCOME | DETAIL, "527687": FORM,
    "567f12": FORM, "9200a7": LIST, "282e2a": OUTCOME | DETAIL, "763048": FORM, "b3ec4e": LIST,
    "2b0f29": OUTCOME | DETAIL, "8755f3": FORM,
    # booking_desk
    "0001e6": OUTCOME, "01e101": OUTCOME, "067d0f": REVIEW, "0fa74c": REVIEW, "1701e1": LIST,
    "3c4553": REVIEW, "3cc091": FORM, "5cab0e": FORM, "627f8b": REVIEW, "63b5e4": FORM,
    "737918": REVIEW, "771ae0": OUTCOME,
    # media_library
    "9d0eb3": LIST, "022e4a": FORM, "b88261": LIST, "143ff7": FORM | DETAIL, "1dae5f": LIST,
    "1f7887": REVIEW | FORM, "24ea6c": FORM, "2df1f1": REVIEW, "328e46": OUTCOME, "3a9130": DETAIL,
    "45d893": OUTCOME, "47a433": OUTCOME,
    # shop_catalog
    "599f5c": LIST | REVIEW, "40019a": FORM, "2cac44": LIST, "24ed9c": OUTCOME, "c0ca04": {"home"},
    "fb1c84": DETAIL | FORM, "f0a4a4": LIST | REVIEW, "550996": FORM, "952362": OUTCOME,
    # support_portal
    "217f6a": {"home"}, "2bed7c": FORM, "068adf": FORM, "0c7b03": FORM, "932133": FORM,
    "196c80": FORM, "7e33d8": REVIEW, "2de6db": OUTCOME, "75d0ce": FORM, "7a4587": FORM,
    "609991": FORM, "a19b79": FORM,
}
_TOGGLE = re.compile(r'<input\b[^>]*\btype="(?:radio|checkbox)"[^>]*>', re.I)
_ID = re.compile(r'\bid="([^"]+)"')


def main() -> None:
    out: dict = {"label_version": "labels-v3", "pages": {},
                 "note": "rules in make_ground_truth.py; toggles excluded from role scoring"}
    for page in load_pages():
        app, short = page["app_id"], page["page_id"].split(":")[1][:6]
        toggles = {m.group(1) for tag in _TOGGLE.findall(page["html"]) if (m := _ID.search(tag))}
        controls: dict[str, list[str]] = {}
        for control in page["controls"]:
            identity = control["identity"]
            dom = identity[5:-2] if identity.startswith('[id="') else ""
            if dom in toggles:
                continue
            if control["kind"] == "field":
                truth = FIELDS[app].get(dom)
            else:
                label = control["line"].split('"')[1] if '"' in control["line"] else ""
                truth = next((roles for by, pattern, roles in ROLES[app]
                              if re.fullmatch(pattern, dom if by == "id" else label)), None)
            if truth is None:
                raise SystemExit(f"no ground truth for {app} {identity} {control['line']!r}")
            controls[identity] = sorted(truth)
        out["pages"][page["page_id"]] = {"kind": sorted(PAGE_KINDS[short]), "controls": controls}
    (HERE / "ground_truth.json").write_text(json.dumps(out, indent=1, sort_keys=True) + "\n",
                                            encoding="utf-8")
    print(f"{len(out['pages'])} pages, {sum(len(p['controls']) for p in out['pages'].values())} controls")


if __name__ == "__main__":
    main()
