"""The inferred-specification rules, checked on synthetic records (no browser, no LLM)."""

from __future__ import annotations

from web_testing_agent.specinfer.pages import snapshot
from web_testing_agent.specinfer.rules import evaluate, parse_rules, violations


def rec(url, html, action=None, entered=None):  # noqa: ANN001, ANN201
    return {"url": url, "html": html, "action": action, "entered": entered or {}}


BAG = """<div id="items"><span class="price">$10.00</span><span class="price">$5.50</span></div>
<span id="subtotal">Item total: $15.50</span><span id="tax">Tax: $1.24</span><span id="total">Total: $16.74</span>"""


def _one(raw):  # noqa: ANN001, ANN202
    rules, dropped = parse_rules([raw])
    assert not dropped, dropped
    return rules[0]


def test_arithmetic_holds_and_breaks():
    rule = _one({"id": "A", "kind": "arithmetic", "page": "/cart", "expression": "total == round(subtotal + tax, 2) and subtotal == items",
                 "operands": {"total": {"selector": "#total"}, "subtotal": {"selector": "#subtotal"},
                              "tax": {"selector": "#tax"}, "items": {"selector": ".price", "agg": "sum"}}})
    assert evaluate(rule, [rec("https://x/cart", BAG)], 0).holds
    broken = BAG.replace("$16.74", "$15.50")
    assert not evaluate(rule, [rec("https://x/cart", broken)], 0).holds
    assert evaluate(rule, [rec("https://x/other", BAG)], 0) is None


def test_echo_needs_a_later_page_after_a_click():
    rule = _one({"id": "E", "kind": "echo", "field": "last-name", "page": "/overview"})
    typed = rec("https://x/info", "<input id='last-name'>", {"type": "TYPE", "id": "last-name", "params": {"value": "Lovelace"}},
                {"Last Name": "Lovelace"})
    click = {"type": "CLICK", "text": "Continue"}
    ok = [rec("https://x/info", "<p/>"), typed, rec("https://x/overview", "<p>Ship to Ada Lovelace</p>", click)]
    bad = ok[:2] + [rec("https://x/overview", "<p>Ship to Ada</p>", click)]
    assert evaluate(rule, ok, 2).holds
    assert not evaluate(rule, bad, 2).holds


def test_sorted_after_choosing_an_option():
    rule = _one({"id": "S", "kind": "sorted", "page": "/inventory", "control": "product_sort_container", "option": "lohi",
                 "items": ".inventory_item_price", "key": "number", "order": "asc"})
    html = lambda prices: "".join(f"<div class='inventory_item_price'>${p}</div>" for p in prices)  # noqa: E731
    step = {"type": "SELECT", "selector": "[data-test=\"product_sort_container\"]", "params": {"option": "lohi"}}
    good = [rec("https://x/inventory", html([1, 2])), rec("https://x/inventory", html([7.99, 9.99, 29.99]), step, {"sort": "lohi"})]
    bad = [good[0], rec("https://x/inventory", html([29.99, 9.99, 15.99]), step, {"sort": "lohi"})]
    assert evaluate(rule, good, 1).holds
    assert not evaluate(rule, bad, 1).holds


def test_counter_and_blocked():
    counter = _one({"id": "C", "kind": "counter", "page": "/inventory", "action": "add to cart",
                    "counter": ".shopping_cart_badge", "delta": 1})
    before = rec("https://x/inventory", "<a class='shopping_cart_link'></a>")
    after_ok = rec("https://x/inventory", "<span class='shopping_cart_badge'>1</span>", {"type": "CLICK", "text": "Add to cart"})
    after_bad = rec("https://x/inventory", "<a></a>", {"type": "CLICK", "text": "Add to cart"})
    assert evaluate(counter, [before, after_ok], 1).holds
    assert not evaluate(counter, [before, after_bad], 1).holds

    blocked = _one({"id": "B", "kind": "blocked", "form_page": "/checkout-step-one", "field": "postal-code",
                    "submit": "continue", "forbidden_page": "/checkout-step-two"})
    form = rec("https://x/checkout-step-one.html", "<input id='postal-code'>")
    through = rec("https://x/checkout-step-two.html", "<p/>", {"type": "CLICK", "text": "Continue"})
    assert not evaluate(blocked, [form, through], 1).holds
    filled = rec("https://x/checkout-step-one.html", "<input id='postal-code'>", {"type": "TYPE", "id": "postal-code"}, {"Zip": "600001"})
    assert evaluate(blocked, [form, filled, through], 2) is None


def test_consistency_and_bad_rules_are_dropped():
    rule = _one({"id": "K", "kind": "consistency", "a": {"page": "/cart", "selector": "#total"},
                 "b": {"page": "/checkout", "selector": "#total"}, "compare": "number"})
    runs = [rec("https://x/cart", "<b id='total'>$20.00</b>"), rec("https://x/checkout", "<b id='total'>$18.00</b>")]
    assert [i for i, _ in violations(rule, runs)] == [1]
    rules, dropped = parse_rules([{"kind": "arithmetic", "expression": "__import__('os')", "operands": {"a": "#a"}},
                                  {"kind": "teleport"}, {"kind": "echo"}])
    assert rules == [] and len(dropped) == 3


def test_consistency_on_one_page_compares_that_page_not_an_earlier_one():
    rule = _one({"id": "K", "kind": "consistency", "a": {"page": "^/recipes/r\\d$", "selector": "#crumb"},
                 "b": {"page": "^/recipes/r\\d$", "selector": "#ref"}, "compare": "text"})
    runs = [rec("https://x/recipes/r1", "<b id='crumb'>1</b><b id='ref'>1</b>"),
            rec("https://x/recipes/r4", "<b id='crumb'>4</b><b id='ref'>4</b>")]
    assert violations(rule, runs) == []          # recipe 1 is never compared with recipe 4
    runs.append(rec("https://x/recipes/r2", "<b id='crumb'>4</b><b id='ref'>2</b>"))
    assert [(i, o.details["a"], o.details["b"]) for i, o in violations(rule, runs)] == [(2, "4", "2")]


def test_consistency_from_a_list_takes_the_row_that_was_opened():
    rule = _one({"id": "H", "kind": "consistency", "a": {"page": "^/volunteers$", "selector": "span.hours"},
                 "b": {"page": "^/volunteers/[^/]+$", "selector": "#week-hours"}, "compare": "number"})
    overview = ("<ul><li><a id='vol-ann'>Ann</a> <span class='hours'>4 h</span></li>"
                "<li><a id='vol-chidi'>Chidi</a> <span class='hours'>6.5 h</span></li></ul>")
    opened = lambda who, hours: [rec("https://x/volunteers", overview),  # noqa: E731
                                 rec(f"https://x/volunteers/{who}", f"<b id='week-hours'>{hours} of 12</b>",
                                     action={"type": "CLICK", "id": f"vol-{who}"})]
    assert violations(rule, opened("chidi", 6.5)) == []      # Chidi's row, not the first row (Ann's 4 h)
    assert [o.details["earlier"] for _, o in violations(rule, opened("chidi", 3))] == ["6.5 h"]
    # No way to tell which row was opened: the rule does not apply rather than guess.
    unknown = [rec("https://x/volunteers", overview), rec("https://x/volunteers/chidi", "<b id='week-hours'>3</b>")]
    assert violations(rule, unknown) == []


def test_parse_rules_drops_malformed_rules():
    rules, dropped = parse_rules([{"kind": "arithmetic", "expression": "__import__('os')", "operands": {"a": "#a"}},
                                  {"kind": "teleport"}, {"kind": "echo"}])
    assert rules == [] and len(dropped) == 3


def test_snapshot_offers_real_selectors():
    snap = snapshot("https://x/cart/42", BAG + "<input id='qty' name='q'><select data-test='sort'><option value='az'>A to Z</option></select>")
    selectors = {e["selector"] for e in snap["elements"]}
    assert "#total" in selectors and ".price" in selectors or "span.price" in selectors
    assert snap["page"] == "/cart/{id}"
    assert any(i["selector"] == '[data-test="sort"]' and i["options"] for i in snap["inputs"])
