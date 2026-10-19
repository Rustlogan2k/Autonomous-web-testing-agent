"""Bagstreet's pages, rendered on the server. Every label comes from the vocabulary."""

from __future__ import annotations

from html import escape
from urllib.parse import urlencode

VOCAB: dict = {}
DEFAULT_METHOD = "standard"


def t(key: str) -> str:
    return VOCAB.get(key, key)


def e(value) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def money(pence: int) -> str:
    return f"£{pence / 100:,.2f}"


def _options(choices, current) -> str:
    return "".join(f'<option value="{e(value)}"{" selected" if str(value) == str(current) else ""}>{e(label)}</option>'
                   for value, label in choices)


# -- the frame -------------------------------------------------------------------------------


def layout(title: str, body: str, *, who: dict | None = None, bag_count: int = 0, notice: str = "",
           dialogs: str = "") -> bytes:
    """A whole page. `who` is the signed-in user (None on the sign-in pages)."""
    banner = f'<p class="notice" id="notice" role="status">{e(notice)}</p>' if notice else ""
    if who is None:
        header = f'<header class="masthead"><span class="logo">{e(t("shop.name"))}</span></header>'
    else:
        staff = (f'<a id="nav-staff" href="/staff/orders">{e(t("nav.staff"))}</a>\n    '
                 if "staff" in who["roles"] else "")
        header = f"""<header class="masthead">
  <a class="logo" id="nav-home" href="/">{e(t("shop.name"))}</a>
  <form class="search" action="/search" method="get" role="search">
    <label class="sr" for="search-q">{e(t("field.search"))}</label>
    <input id="search-q" name="q" type="search">
    <button id="search-go" type="submit">{e(t("act.search"))}</button>
  </form>
  <nav aria-label="{e(t("nav.label"))}">
    <a id="express-checkout" href="/express">{e(t("act.express"))}</a>
    {staff}<a id="nav-orders" href="/orders">{e(t("nav.orders"))}</a>
    <a id="nav-bag" href="/bag">{e(t("nav.bag"))} ({bag_count})</a>
    <a id="nav-account" href="/account">{e(t("nav.account"))}</a>
  </nav>
  <p class="who"><span id="signed-in-as">{e(who["name"])} · {e(t("role." + who["roles"][0]))}</span></p>
</header>"""
    return f"""<!doctype html>
<html lang="en-GB"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)} · {e(t("shop.name"))}</title>
<link rel="stylesheet" href="/static/site.css"></head>
<body>
{header}
<main id="page">{banner}{body}</main>
{dialogs}
<script src="/static/enhance.js"></script>
</body></html>""".encode("utf-8")


def _dialog(dialog_id: str, title: str, text: str, action: str, confirm_id: str, cancel_id: str) -> str:
    """A confirmation dialog, rendered after <main> and opened by enhance.js."""
    return f"""<div class="overlay" id="{dialog_id}" role="dialog" aria-modal="true" aria-labelledby="{dialog_id}-title" hidden>
  <div class="dialog-box"><h2 id="{dialog_id}-title">{e(title)}</h2><p>{e(text)}</p>
  <form method="post" action="{e(action)}">
    <button id="{confirm_id}" class="danger" type="submit">{e(t("act.delete"))}</button>
    <button id="{cancel_id}" type="button" data-closes>{e(t("act.keep"))}</button>
  </form></div>
</div>"""


# -- signing in ------------------------------------------------------------------------------


def sign_in(next_path: str, error: str = "") -> str:
    return f"""<section class="signin"><h1>{e(t("signin.title"))}</h1>
<form method="post" action="/login">
  <input type="hidden" name="next" value="{e(next_path)}">
  <label for="login-username">{e(t("field.username"))}</label>
  <input id="login-username" name="username" type="text" autocomplete="username">
  <label for="login-password">{e(t("field.password"))}</label>
  <input id="login-password" name="password" type="password" autocomplete="current-password">
  <p class="error" id="login-error" role="alert">{e(error)}</p>
  <button id="login-submit" class="primary" type="submit">{e(t("act.sign_in"))}</button>
</form>
<p><a id="forgot-password" href="/login/forgot">{e(t("act.forgot"))}</a></p></section>"""


def forgot() -> str:
    return f"""<section class="signin"><h1>{e(t("forgot.title"))}</h1><p>{e(t("forgot.body"))}</p>
<p><a id="back-to-sign-in" href="/login">{e(t("act.back_to_sign_in"))}</a></p></section>"""


def access_notice() -> str:
    return f"""<h1>{e(t("access.title"))}</h1><p id="access-notice">{e(t("access.staff_only"))}</p>
<p><a id="access-home" href="/">{e(t("act.keep_shopping"))}</a></p>"""


# -- browsing --------------------------------------------------------------------------------


def home(categories: list[dict]) -> str:
    cards = "".join(
        f'<li><a id="cat-{e(c["_id"])}" href="/search?category={e(c["_id"])}">{e(c["name"])}</a></li>'
        for c in categories)
    return f"""<h1>{e(t("home.title"))}</h1>
<p class="lede">{e(t("home.lede"))}</p>
<h2>{e(t("home.browse"))}</h2><ul class="categories">{cards}
<li><a id="cat-all" href="/search?category=all">{e(t("filter.all"))}</a></li></ul>"""


def results(categories: list[dict], items: list[dict], query: str, category: str, sort: str, exact: bool,
            page: int, pages: int, from_price) -> str:
    choices = [("all", t("filter.all"))] + [(c["_id"], c["name"]) for c in categories]
    sorts = [("relevance", t("sort.relevance")), ("price-asc", t("sort.price_asc")),
             ("price-desc", t("sort.price_desc")), ("name", t("sort.name"))]
    note = "" if exact or not query else f'<p class="muted" id="no-exact">{e(t("msg.no_exact"))} “{e(query)}”.</p>'
    rows = "".join(
        f'<li class="result"><a id="product-{e(p["_id"])}" href="/p/{e(p["_id"])}">{e(p["name"])}</a>'
        f' <span class="price">{e(t("label.from_price"))} <b id="result-{i}-price">{money(from_price(p))}</b></span></li>'
        for i, p in enumerate(items, start=1))

    def to(number: int) -> str:
        return "/search?" + urlencode({"q": query, "category": category, "sort": sort, "page": number})

    pager = ""
    if page > 1:
        pager += f'<a id="results-prev" href="{e(to(page - 1))}">{e(t("act.previous"))}</a>'
    if page < pages:
        pager += f'<a id="results-next" href="{e(to(page + 1))}">{e(t("act.next"))}</a>'
    return f"""<h1>{e(t("results.title"))}</h1>{note}
<form class="filters" action="/search" method="get">
  <input type="hidden" name="q" value="{e(query)}">
  <input type="hidden" name="page" value="1">
  <label for="filter-category">{e(t("field.category"))}</label>
  <select id="filter-category" name="category">{_options(choices, category)}</select>
  <label for="sort">{e(t("field.sort"))}</label>
  <select id="sort" name="sort">{_options(sorts, sort)}</select>
  <button id="apply-filters" type="submit">{e(t("act.apply_filters"))}</button>
</form>
<p class="muted">{e(t("label.page"))} <strong id="results-page">{page}</strong> {e(t("label.of"))} <strong id="results-pages">{pages}</strong></p>
<ol id="results-list" class="results">{rows}</ol>
<nav class="pager" aria-label="{e(t("label.pages"))}">{pager}</nav>"""


def _attributes(attributes: dict) -> str:
    kind = attributes["kind"]
    if kind == "lighting":
        rows = [("attr.bulb", attributes["bulb"]), ("attr.lumens", attributes["lumens"])]
    elif kind == "kitchen":
        rows = [("attr.material", attributes["material"]),
                ("attr.capacity", f'{attributes["capacity_l"]:g} {t("unit.litres")}')]
    else:
        rows = [("attr.weatherproof", t("label.yes") if attributes["weatherproof"] else t("label.no")),
                ("attr.max_load", f'{attributes["max_load_kg"]} kg')]
    return "".join(f"<dt>{e(t(key))}</dt><dd>{e(value)}</dd>" for key, value in rows)


def _stars_select(current) -> str:
    return f"""<label for="review-stars">{e(t("field.stars"))}</label>
  <select id="review-stars" name="stars">{_options([(s, f"{s}/5") for s in (5, 4, 3, 2, 1)], current)}</select>"""


def product(item: dict, *, tab: str, authors: dict, me: dict, error: str = "",
            review_error: str = "") -> tuple[str, str]:
    """The product page and its dialogs."""
    variants = item["variants"]
    if len(variants) > 1:
        options = "".join(f'<option value="{e(v["code"])}">{e(v["label"])} — {money(v["price_pence"])}</option>'
                          for v in variants)
        chooser = f"""<label for="variant">{e(t("field.variant"))}</label>
  <select id="variant" name="variant">{options}</select>"""
    else:
        chooser = f'<input type="hidden" name="variant" value="{e(variants[0]["code"])}">' \
                  f'<p class="muted">{e(variants[0]["label"])}</p>'
    sizes = "".join(f'<tr><td>{e(v["label"])}</td><td>{money(v["price_pence"])}</td><td>{v["stock"]}</td>'
                    f'<td>{v["size"]["height_cm"]} cm, {v["size"]["weight_g"]} g</td><td>{e(v["sku"])}</td></tr>'
                    for v in variants)
    reviews = item["reviews"]
    listed = "".join(f'<li><strong>{r["stars"]}/5</strong> {e(authors.get(r["author_id"], r["author_id"]))}: '
                     f'<q>{e(r["text"])}</q></li>' for r in reviews)
    mine = next((r for r in reviews if r["author_id"] == me["id"]), None)
    slug = e(item["_id"])
    dialogs = ""
    if "customer" not in me["roles"]:
        yours = ""
    elif mine is None:
        yours = f"""<form class="review-form" method="post" action="/p/{slug}/reviews">
  <h3>{e(t("review.write"))}</h3>
  {_stars_select(5)}
  <label for="review-text">{e(t("field.review"))}</label>
  <textarea id="review-text" name="text" rows="4"></textarea>
  <p class="error" id="review-error" role="alert">{e(review_error)}</p>
  <button id="post-review" class="primary" type="submit">{e(t("act.post_review"))}</button>
</form>"""
    else:
        yours = f"""<p class="actions"><a id="edit-review" class="button" href="/p/{slug}/review/edit">{e(t("act.edit_review"))}</a>
  <button id="delete-review" type="button" data-opens="dialog-review">{e(t("act.delete_review"))}</button></p>
  <p class="error" id="review-error" role="alert">{e(review_error)}</p>"""
        dialogs = _dialog("dialog-review", t("review.delete_title"), mine["text"][:80],
                          f"/p/{item['_id']}/review/delete", "confirm-delete-review", "cancel-delete-review")
    selected = {"details": tab != "reviews", "reviews": tab == "reviews"}

    def tab_button(name: str, label: str) -> str:
        return (f'<button id="tab-{name}" type="button" role="tab" aria-controls="panel-{name}" '
                f'aria-selected="{"true" if selected[name] else "false"}">{e(label)}</button>')

    no_reviews = "" if reviews else f'<p class="muted" id="no-reviews">{e(t("review.none"))}</p>'
    body = f"""<h1>{e(item["name"])}</h1>
<p class="price big" id="product-price">{money(variants[0]["price_pence"])}</p>
<p>{e(item["blurb"])}</p>
<form method="post" action="/bag/add">
  <input type="hidden" name="slug" value="{slug}">
  {chooser}
  <label for="qty">{e(t("field.quantity"))}</label>
  <input id="qty" name="qty" type="number" min="1" max="99" value="1">
  <p class="error" id="product-error">{e(error)}</p>
  <button id="add-to-bag" class="primary" type="submit">{e(t("act.add"))}</button>
</form>
<div class="tabs" role="tablist">{tab_button("details", t("tab.details"))}{tab_button("reviews", f'{t("tab.reviews")} ({len(reviews)})')}</div>
<section id="panel-details" class="panel" role="tabpanel" aria-labelledby="tab-details"{"" if selected["details"] else " hidden"}>
  <dl class="facts">{_attributes(item["attributes"])}</dl>
  <table class="sizes"><thead><tr><th>{e(t("col.variant"))}</th><th>{e(t("col.price"))}</th><th>{e(t("col.stock"))}</th>
  <th>{e(t("col.size"))}</th><th>{e(t("col.sku"))}</th></tr></thead><tbody>{sizes}</tbody></table>
</section>
<section id="panel-reviews" class="panel" role="tabpanel" aria-labelledby="tab-reviews"{"" if selected["reviews"] else " hidden"}>
  <ol id="review-list" class="reviews">{listed}</ol>
  {no_reviews}
  {yours}
</section>
<p><a id="back-results" href="/search?category={e(item["category_id"])}">{e(t("act.more_like_this"))}</a></p>"""
    return body, dialogs


def review_editor(item: dict, mine: dict, error: str = "") -> str:
    slug = e(item["_id"])
    return f"""<h1>{e(t("review.edit_title"))}</h1><p class="muted">{e(item["name"])}</p>
<form method="post" action="/p/{slug}/review/edit">
  {_stars_select(mine["stars"])}
  <label for="review-text">{e(t("field.review"))}</label>
  <textarea id="review-text" name="text" rows="4">{e(mine["text"])}</textarea>
  <p class="error" id="review-error" role="alert">{e(error)}</p>
  <p class="actions"><a id="cancel-review" href="/p/{slug}?tab=reviews">{e(t("act.cancel"))}</a>
    <button id="save-review" class="primary" type="submit">{e(t("act.save"))}</button></p>
</form>"""


# -- the bag and checkout --------------------------------------------------------------------


def bag(lines: list[dict], totals: dict, vouchers: list[tuple[str, str]], error: str = "") -> str:
    if not lines:
        return f"""<h1>{e(t("bag.title"))}</h1><p id="bag-empty">{e(t("bag.empty"))}</p>
<p class="error" id="bag-error">{e(error)}</p>
<p><a id="keep-shopping" href="/">{e(t("act.keep_shopping"))}</a></p>"""
    rows = "".join(f"""<tr>
  <td><label class="sr" for="pick-{line["n"]}">{e(t("field.pick"))} {e(line["name"])}</label>
      <input id="pick-{line["n"]}" name="line" type="checkbox" value="{line["n"]}" form="bulk-form"></td>
  <td>{e(line["name"])}<br><span class="muted">{e(line["variant_label"])}</span></td>
  <td>{money(line["unit_price_pence"])}</td>
  <td><label class="sr" for="qty-{line["n"]}">{e(t("field.quantity"))}</label>
      <input id="qty-{line["n"]}" name="qty_{line["n"]}" type="number" min="0" max="99" value="{line["qty"]}" form="bag-form"></td>
  <td>{money(line["unit_price_pence"] * line["qty"])}</td>
  <td><form method="post" action="/bag/remove/{line["n"]}"><button id="remove-{line["n"]}" type="submit">{e(t("act.remove"))}</button></form></td>
</tr>""" for line in lines)
    return f"""<h1>{e(t("bag.title"))}</h1>
<form id="bag-form" method="post" action="/bag/update"></form>
<form id="bulk-form" method="post" action="/bag/remove-selected"></form>
<table class="bag"><thead><tr><th></th><th>{e(t("col.item"))}</th><th>{e(t("col.price"))}</th>
  <th>{e(t("col.qty"))}</th><th>{e(t("col.line_total"))}</th><th></th></tr></thead>
<tbody>{rows}</tbody></table>
<p class="error" id="bag-error">{e(error)}</p>
<p class="actions"><button id="update-bag" type="submit" form="bag-form">{e(t("act.update_bag"))}</button>
  <button id="remove-selected" type="submit" form="bulk-form">{e(t("act.remove_selected"))}</button></p>
<form class="voucher" method="post" action="/bag/voucher">
  <label for="voucher">{e(t("field.voucher"))}</label>
  <select id="voucher" name="voucher">{_options(vouchers, totals["voucher"])}</select>
  <button id="apply-voucher" type="submit">{e(t("act.apply_voucher"))}</button>
</form>
<dl class="totals">
  <dt>{e(t("total.subtotal"))}</dt><dd><span id="bag-subtotal">{money(totals["subtotal"])}</span></dd>
  <dt>{e(t("total.voucher"))}</dt><dd><span id="bag-voucher">{e(t("voucher." + totals["voucher"]))}</span></dd>
  <dt>{e(t("total.discount"))}</dt><dd><span id="bag-discount">−{money(totals["discount"])}</span></dd>
  <dt>{e(t("total.before_delivery"))}</dt><dd><strong id="bag-total">{money(totals["before_delivery"])}</strong></dd>
</dl>
<p class="actions"><a id="gift-options" href="/gifts">{e(t("act.gift_options"))}</a>
  <a id="go-checkout" class="primary button" href="/checkout/delivery">{e(t("act.checkout"))}</a></p>"""


def _steps(current: int) -> str:
    names = [t("step.delivery"), t("step.review"), t("step.placed")]
    marker = ' aria-current="step"'
    return '<ol class="steps">' + "".join(
        f'<li{marker if i == current else ""}>{e(name)}</li>' for i, name in enumerate(names)) + "</ol>"


def delivery(methods: list[tuple[str, str, int]], saved: dict, store: str, book: list[dict], one_line,
             error: str = "") -> str:
    options = "".join(f'<option value="{e(v)}"{" selected" if v == saved["method"] else ""}>{e(n)}'
                      f'{" — " + money(fee) if fee else ""}</option>' for v, n, fee in methods)
    if saved["method"] == "collect":
        where = f'<p id="store-details">{e(t("delivery.collect_from"))} {e(store)}.</p>'
    else:
        picker = ""
        if book:  # fills the fields only when an address is chosen; nothing is prefilled
            choices = "".join(f'<option value="{e(a["key"])}" data-name="{e(a["name"])}" '
                              f'data-address="{e(one_line(a))}">{e(a["label"])}</option>' for a in book)
            picker = f"""<label for="saved-address">{e(t("field.saved_address"))}</label>
  <select id="saved-address" name="saved_address" data-fills-address><option value="">—</option>{choices}</select>"""
        where = f"""<fieldset id="address-section"><legend>{e(t("delivery.address_legend"))}</legend>
  {picker}
  <label for="full-name">{e(t("field.full_name"))}</label>
  <input id="full-name" name="full_name" type="text" value="{e(saved["full_name"])}">
  <label for="address">{e(t("field.address"))}</label>
  <textarea id="address" name="address" rows="3">{e(saved["address"])}</textarea>
</fieldset>"""
    return f"""<h1>{e(t("delivery.title"))}</h1>
{_steps(0)}
<form method="post" action="/checkout/delivery">
  <label for="method">{e(t("field.method"))}</label>
  <select id="method" name="method" data-autosubmit>{options}</select>
  {where}
  <p class="error" id="delivery-error" role="alert">{e(error)}</p>
  <p class="actions"><a id="back-to-bag" href="/bag">{e(t("act.edit_bag"))}</a>
    <button id="review-order" class="primary" type="submit" name="action" value="review">{e(t("act.review_order"))}</button></p>
</form>"""


def review(lines: list[dict], totals: dict, saved: dict, method_label: str, store: str) -> str:
    rows = "".join(f'<li>{line["qty"]} × {e(line["name"])} ({e(line["variant_label"])}) — '
                   f'{money(line["unit_price_pence"] * line["qty"])}</li>' for line in lines)
    where = (f'{e(t("delivery.collect_from"))} {e(store)}' if saved["method"] == "collect"
             else f'{e(saved["full_name"])}<br>{e(saved["address"])}')
    return f"""<h1>{e(t("review.title"))}</h1>
{_steps(1)}
<ul id="review-lines">{rows}</ul>
<dl class="totals">
  <dt>{e(t("total.before_delivery"))}</dt><dd>{money(totals["before_delivery"])}</dd>
  <dt>{e(t("total.delivery"))}</dt><dd><span id="review-delivery-fee">{money(totals["delivery"])}</span></dd>
  <dt>{e(t("total.to_pay"))}</dt><dd><strong id="review-total">{money(totals["total"])}</strong></dd>
</dl>
<h2>{e(t("review.delivery"))}</h2>
<p><span id="review-method">{e(method_label)}</span></p>
<address id="review-address">{where}</address>
<form method="post" action="/checkout/place">
  <input type="hidden" name="method" value="{e(saved["method"])}">
  <p class="actions"><a id="change-delivery" href="/checkout/delivery">{e(t("act.change_delivery"))}</a>
    <a id="edit-bag" href="/bag">{e(t("act.edit_bag"))}</a>
    <button id="place-order" class="primary" type="submit">{e(t("act.place_order"))}</button></p>
</form>"""


# -- orders ----------------------------------------------------------------------------------


def confirmation(placed: dict, method_label: str, store: str) -> str:
    rows = "".join(f'<li>{line["qty"]} × {e(line["name"])} ({e(line["variant_label"])})</li>'
                   for line in placed["lines"])
    where = (f'{e(t("delivery.collect_from"))} {e(store)}' if placed["method"] == "collect"
             else f'{e(placed["full_name"])}<br>{e(placed["address"])}')
    return f"""<h1>{e(t("confirm.title"))}</h1>
<p>{e(t("confirm.number"))} <strong id="order-number">{e(placed["_id"])}</strong>
  · <span id="order-status">{e(t("status." + placed["status"]))}</span></p>
<ul id="confirm-lines">{rows}</ul>
<p>{e(t("review.delivery"))}: <span id="confirm-method">{e(method_label)}</span></p>
<address id="confirm-address">{where}</address>
<p>{e(t("total.paid"))} <strong id="confirm-total">{money(placed["totals"]["total"])}</strong></p>
<p><a id="continue-shopping" href="/">{e(t("act.keep_shopping"))}</a> · <a id="all-orders" href="/orders">{e(t("nav.orders"))}</a></p>"""


def orders(rows: list[dict], labels: dict) -> str:
    if not rows:
        return f'<h1>{e(t("nav.orders"))}</h1><p id="no-orders">{e(t("orders.none"))}</p>'
    body = "".join(f'<tr><td><a id="order-{e(o["_id"])}" href="/orders/{e(o["_id"])}">{e(o["_id"])}</a></td>'
                   f'<td>{e(labels.get(o["method"], o["method"]))}</td><td>{e(t("status." + o["status"]))}</td>'
                   f'<td>{money(o["totals"]["total"])}</td></tr>' for o in rows)
    return f"""<h1>{e(t("nav.orders"))}</h1>
<table id="order-history"><thead><tr><th>{e(t("col.order"))}</th><th>{e(t("col.delivery"))}</th>
<th>{e(t("col.status"))}</th><th>{e(t("col.total"))}</th></tr></thead><tbody>{body}</tbody></table>"""


def staff_orders(rows: list[dict], status: str, customers: dict, labels: dict, error: str = "") -> str:
    choices = [("all", t("filter.every_order")), ("placed", t("status.placed")),
               ("dispatched", t("status.dispatched"))]

    def pick(o: dict) -> str:
        if o["status"] != "placed":
            return ""
        number = e(o["_id"])
        return (f'<label class="sr" for="pick-order-{number}">{e(t("field.pick"))} {number}</label>'
                f'<input id="pick-order-{number}" name="order" type="checkbox" value="{number}" form="dispatch-form">')

    body = "".join(f'<tr><td>{pick(o)}</td><td><a href="/orders/{e(o["_id"])}">{e(o["_id"])}</a></td>'
                   f'<td>{e(customers.get(o["customer_id"], o["customer_id"]))}</td>'
                   f'<td>{e(labels.get(o["method"], o["method"]))}</td><td>{money(o["totals"]["total"])}</td>'
                   f'<td>{e(t("status." + o["status"]))}</td></tr>' for o in rows)
    return f"""<h1>{e(t("staff.title"))}</h1>
<form class="filters" method="get" action="/staff/orders">
  <label for="status-filter">{e(t("field.status"))}</label>
  <select id="status-filter" name="status">{_options(choices, status)}</select>
  <button id="apply-status" type="submit">{e(t("act.apply_filters"))}</button>
</form>
<form id="dispatch-form" method="post" action="/staff/orders/dispatch"></form>
<table id="staff-orders"><thead><tr><th></th><th>{e(t("col.order"))}</th><th>{e(t("col.customer"))}</th>
<th>{e(t("col.delivery"))}</th><th>{e(t("col.total"))}</th><th>{e(t("col.status"))}</th></tr></thead><tbody>{body}</tbody></table>
<p class="error" id="staff-error" role="alert">{e(error)}</p>
<p class="actions"><button id="mark-dispatched" type="submit" form="dispatch-form">{e(t("act.mark_dispatched"))}</button></p>"""


# -- the account -----------------------------------------------------------------------------


def account(who: dict) -> str:
    return f"""<h1>{e(t("account.title"))}</h1>
<dl class="facts"><dt>{e(t("field.full_name"))}</dt><dd id="account-name">{e(who["name"])}</dd>
<dt>{e(t("field.username"))}</dt><dd>{e(who["username"])}</dd>
<dt>{e(t("field.role"))}</dt><dd>{e(t("role." + who["roles"][0]))}</dd></dl>
<ul class="links"><li><a id="account-addresses" href="/account/addresses">{e(t("addresses.title"))}</a></li>
<li><a id="account-orders" href="/orders">{e(t("nav.orders"))}</a></li></ul>
<form method="post" action="/logout"><button id="sign-out" type="submit">{e(t("act.sign_out"))}</button></form>"""


def address_book(book: list[dict], one_line, error: str = "") -> tuple[str, str]:
    items = "".join(f"""<li id="address-{e(a["key"])}"><b>{e(a["label"])}</b> {e(a["name"])}, {e(one_line(a))}
  <a id="edit-address-{e(a["key"])}" href="/account/addresses/{e(a["key"])}/edit">{e(t("act.edit"))}</a>
  <button id="delete-address-{e(a["key"])}" type="button" data-opens="dialog-address-{e(a["key"])}">{e(t("act.delete"))}</button></li>"""
                    for a in book)
    dialogs = "".join(_dialog(f"dialog-address-{e(a['key'])}", t("addresses.delete_title"),
                              f"{a['label']}: {one_line(a)}", f"/account/addresses/{a['key']}/delete",
                              f"confirm-delete-{e(a['key'])}", f"cancel-delete-{e(a['key'])}") for a in book)
    empty = "" if book else f'<p class="muted" id="no-addresses">{e(t("addresses.none"))}</p>'
    body = f"""<h1>{e(t("addresses.title"))}</h1>
<ul id="address-list" class="cards">{items}</ul>{empty}
<p class="error" id="address-error" role="alert">{e(error)}</p>
<p class="actions"><a id="add-address" class="button" href="/account/addresses/new">{e(t("act.add_address"))}</a>
  <a id="back-to-account" href="/account">{e(t("account.title"))}</a></p>"""
    return body, dialogs


def address_form(action: str, values: dict, error: str = "") -> str:
    def field(name: str, key: str) -> str:
        return (f'<label for="addr-{name}">{e(t(key))}</label>\n  '
                f'<input id="addr-{name}" name="{name}" type="text" value="{e(values.get(name, ""))}">')

    title = t("addresses.edit_title") if values else t("addresses.new_title")
    return f"""<h1>{e(title)}</h1>
<form method="post" action="{e(action)}">
  {field("label", "field.address_label")}
  {field("name", "field.full_name")}
  {field("line1", "field.line1")}
  {field("town", "field.town")}
  {field("postcode", "field.postcode")}
  <p class="error" id="address-form-error" role="alert">{e(error)}</p>
  <p class="actions"><a id="cancel-address" href="/account/addresses">{e(t("act.cancel"))}</a>
    <button id="save-address" class="primary" type="submit">{e(t("act.save"))}</button></p>
</form>"""


def info(title_key: str, body_key: str) -> str:
    return f"""<h1>{e(t(title_key))}</h1><p>{e(t(body_key))}</p>
<p><a id="info-back" href="/">{e(t("act.keep_shopping"))}</a></p>"""
