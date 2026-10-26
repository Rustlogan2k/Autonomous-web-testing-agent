"""Guards against the silent-bug finder learning a bug as intended behaviour."""

from __future__ import annotations

from pathlib import Path

from web_testing_agent.specinfer.intent import collect_intent, quote_supported
from web_testing_agent.specinfer.monitor import verdict_status
from web_testing_agent.specinfer.pages import snapshot

REPO = Path(__file__).resolve().parents[2]


def test_intent_reads_docs_and_tests_but_never_an_answer_key(tmp_path):
    (tmp_path / "README.md").write_text("Adding an item raises the cart count by one.", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "checkout.md").write_text("A postcode is required to continue.", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_cart.py").write_text("def test_total(): assert total == subtotal + tax", encoding="utf-8")
    (tmp_path / "tests" / "test_seeded.py").write_text("# checks fault HELP-2 fires", encoding="utf-8")
    (tmp_path / "faults.json").write_text('{"faults": []}', encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("total = subtotal  # the code itself is never read", encoding="utf-8")
    text, files = collect_intent(tmp_path)
    assert files == ["README.md", "docs/checkout.md", "tests/test_cart.py"]
    assert "never read" not in text and "HELP-2" not in text


def test_the_bundled_suite_apps_leak_no_fault_ids():
    for app in sorted((REPO / "e2e_suite" / "apps").iterdir()):
        if not app.is_dir():
            continue
        text, files = collect_intent(app)
        assert "app.json" not in files
        for manifest_fault in __import__("json").loads((app / "app.json").read_text(encoding="utf-8"))["faults"]:
            assert manifest_fault["fault_id"] not in text, (app.name, manifest_fault["fault_id"])


def test_the_rule_writer_sees_where_numbers_are_but_not_their_values():
    html = "<span id='total'>Total: $16.74</span><select id='sort'><option value='lohi'>Price (low to high)</option></select>"
    snap = snapshot("https://shop.test/cart", html)
    def samples(s):  # noqa: ANN001, ANN202
        return next(e["samples"] for e in s["elements"] if e["selector"] == "#total")

    assert samples(snap) == ["Total: $##.##"] and "16.74" not in snap["text"]
    assert snap["inputs"][0]["options"] == [{"value": "lohi", "text": "Price (low to high)"}]
    assert samples(snapshot("https://shop.test/cart", html, mask=False)) == ["Total: $16.74"]


def test_intended_must_quote_its_evidence():
    page = "Free delivery on all orders. Delivery fee: $0.00"
    assert verdict_status({"verdict": "bug"}, page, "") == "verified"
    assert verdict_status({"verdict": "rule_error"}, page, "") == "rejected"
    assert verdict_status({"verdict": "intended", "evidence_quote": "Free delivery on all orders"}, page, "") == "rejected"
    unsupported = {"verdict": "intended", "evidence_quote": "discounts are applied at checkout"}
    assert verdict_status(unsupported, page, "") == "needs_review" and "note" in unsupported
    assert verdict_status({"verdict": "intended"}, page, "") == "needs_review"
    assert verdict_status({"verdict": "intended", "evidence_quote": "Totals exclude tax by design"}, page,
                          "README: Totals exclude tax by design.") == "rejected"
    assert not quote_supported("ok", page)
