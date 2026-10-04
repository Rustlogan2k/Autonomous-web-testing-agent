"""Bagstreet's pages, rendered on the server. Every label comes from the vocabulary."""

from __future__ import annotations

from html import escape

VOCAB: dict = {}
DEFAULT_METHOD = "standard"


def t(key: str) -> str:
    return VOCAB.get(key, key)


def e(value) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def money(cents: int) -> str:
    return f"£{cents / 100:,.2f}"


def layout(title: str, body: str, bag_count: int, notice: str = "") -> bytes:
    banner = f'<p class="notice" id="notice" role="status">{e(notice)}</p>' if notice else ""
    return f"""<!doctype html>
<html lang="en-GB"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)} · {e(t("shop.name"))}</title>
<link rel="stylesheet" href="/static/site.css"></head>
<body>
<header class="masthead">
  <a class="logo" id="nav-home" href="/">{e(t("shop.name"))}</a>
  <form class="search" action="/search" method="get" role="search">
    <label class="sr" for="search-q">{e(t("field.search"))}</label>
    <input id="search-q" name="q" type="search">
    <button id="search-go" type="submit">{e(t("act.search"))}</button>
  </form>
  <nav aria-label="{e(t("nav.label"))}">
    <a id="express-checkout" href="/express">{e(t("act.express"))}</a>
    <a id="nav-orders" href="/orders">{e(t("nav.orders"))}</a>
    <a id="nav-bag" href="/bag">{e(t("nav.bag"))} ({bag_count})</a>
  </nav>
</header>
<main>{banner}{body}</main>
<script src="/static/enhance.js"></script>
</body></html>""".encode("utf-8")


def home(catalog: dict) -> str:
    cards = "".join(
        f'<li><a id="cat-{e(c["id"])}" href="/search?category={e(c["id"])}">{e(c["name"])}</a></li>'
        for c in catalog["categories"])
    return f"""<h1>{e(t("home.title"))}</h1>
<p class="lede">{e(t("home.lede"))}</p>
<h2>{e(t("home.browse"))}</h2><ul class="categories">{cards}</ul>"""


def results(catalog: dict, items: list[dict], query: str, category: str, sort: str, exact: bool) -> str:
    categories = [("all", t("filter.all"))] + [(c["id"], c["name"]) for c in catalog["categories"]]
    sorts = [("relevance", t("sort.relevance")), ("price-asc", t("sort.price_asc")),
             ("price-desc", t("sort.price_desc")), ("name", t("sort.name"))]
    cat_options = "".join(f'<option value="{e(v)}"{" selected" if v == category else ""}>{e(n)}</option>'
                          for v, n in categories)
    sort_options = "".join(f'<option value="{e(v)}"{" selected" if v == sort else ""}>{e(n)}</option>'
                           for v, n in sorts)
    note = "" if exact or not query else f'<p class="muted" id="no-exact">{e(t("msg.no_exact"))} “{e(query)}”.</p>'
    rows = "".join(
        f'<li class="result"><a id="product-{e(p["slug"])}" href="/p/{e(p["slug"])}">{e(p["name"])}</a>'
        f' <span class="price">{e(t("label.from_price"))} {money(min(v["price"] for v in p["variants"]))}</span></li>'
        for p in items)
    return f"""<h1>{e(t("results.title"))}</h1>{note}
<form class="filters" action="/search" method="get">
  <input type="hidden" name="q" value="{e(query)}">
  <label for="filter-category">{e(t("field.category"))}</label>
  <select id="filter-category" name="category">{cat_options}</select>
  <label for="sort">{e(t("field.sort"))}</label>
  <select id="sort" name="sort">{sort_options}</select>
  <button id="apply-filters" type="submit">{e(t("act.apply_filters"))}</button>
</form>
<ol id="results-list" class="results">{rows}</ol>"""


def product(p: dict, error: str = "") -> str:
    variants = p["variants"]
    if len(variants) > 1:
        options = "".join(f'<option value="{e(v["id"])}">{e(v["name"])} — {money(v["price"])}</option>'
                          for v in variants)
        chooser = f"""<label for="variant">{e(t("field.variant"))}</label>
  <select id="variant" name="variant">{options}</select>"""
    else:
        chooser = f'<input type="hidden" name="variant" value="{e(variants[0]["id"])}">' \
                  f'<p class="muted">{e(variants[0]["name"])}</p>'
    return f"""<h1>{e(p["name"])}</h1>
<p class="price big" id="product-price">{money(variants[0]["price"])}</p>
<p>{e(p["blurb"])}</p>
<form method="post" action="/bag/add">
  <input type="hidden" name="slug" value="{e(p["slug"])}">
  {chooser}
  <label for="qty">{e(t("field.quantity"))}</label>
  <input id="qty" name="qty" type="number" min="1" max="99" value="1">
  <p class="error" id="product-error">{e(error)}</p>
  <button id="add-to-bag" class="primary" type="submit">{e(t("act.add"))}</button>
</form>
<p><a id="back-results" href="/search?category={e(p["category"])}">{e(t("act.more_like_this"))}</a></p>"""


def bag(lines: list[dict], totals: dict, vouchers: list[tuple[str, str]], current: str, error: str = "") -> str:
    if not lines:
        return f"""<h1>{e(t("bag.title"))}</h1><p id="bag-empty">{e(t("bag.empty"))}</p>
<p><a id="keep-shopping" href="/">{e(t("act.keep_shopping"))}</a></p>"""
    rows = "".join(f"""<tr>
  <td>{e(line["name"])}<br><span class="muted">{e(line["variant_name"])}</span></td>
  <td>{money(line["price"])}</td>
  <td><label class="sr" for="qty-{line["line"]}">{e(t("field.quantity"))}</label>
      <input id="qty-{line["line"]}" name="qty_{line["line"]}" type="number" min="0" max="99" value="{line["qty"]}" form="bag-form"></td>
  <td>{money(line["price"] * line["qty"])}</td>
  <td><form method="post" action="/bag/remove/{line["line"]}"><button id="remove-{line["line"]}" type="submit">{e(t("act.remove"))}</button></form></td>
</tr>""" for line in lines)
    voucher_options = "".join(f'<option value="{e(v)}"{" selected" if v == current else ""}>{e(n)}</option>'
                              for v, n in vouchers)
    return f"""<h1>{e(t("bag.title"))}</h1>
<form id="bag-form" method="post" action="/bag/update"></form>
<table class="bag"><thead><tr><th>{e(t("col.item"))}</th><th>{e(t("col.price"))}</th>
  <th>{e(t("col.qty"))}</th><th>{e(t("col.line_total"))}</th><th></th></tr></thead>
<tbody>{rows}</tbody></table>
<p class="error" id="bag-error">{e(error)}</p>
<p><button id="update-bag" type="submit" form="bag-form">{e(t("act.update_bag"))}</button></p>
<form class="voucher" method="post" action="/bag/voucher">
  <label for="voucher">{e(t("field.voucher"))}</label>
  <select id="voucher" name="voucher">{voucher_options}</select>
  <button id="apply-voucher" type="submit">{e(t("act.apply_voucher"))}</button>
</form>
<dl class="totals">
  <dt>{e(t("total.subtotal"))}</dt><dd><span id="bag-subtotal">{money(totals["subtotal"])}</span></dd>
  <dt>{e(t("total.voucher"))}</dt><dd><span id="bag-voucher">{e(totals["voucher_label"])}</span></dd>
  <dt>{e(t("total.discount"))}</dt><dd><span id="bag-discount">−{money(totals["discount"])}</span></dd>
  <dt>{e(t("total.before_delivery"))}</dt><dd><strong id="bag-total">{money(totals["before_delivery"])}</strong></dd>
</dl>
<p class="actions"><a id="gift-options" href="/gifts">{e(t("act.gift_options"))}</a>
  <a id="go-checkout" class="primary button" href="/checkout/delivery">{e(t("act.checkout"))}</a></p>"""


def delivery(methods: list[tuple[str, str, int]], saved: dict, store: str, error: str = "") -> str:
    options = "".join(f'<option value="{e(v)}"{" selected" if v == saved["method"] else ""}>{e(n)}'
                       f'{" — " + money(fee) if fee else ""}</option>' for v, n, fee in methods)
    if saved["method"] == "collect":
        where = f'<p id="store-details">{e(t("delivery.collect_from"))} {e(store)}.</p>'
    else:
        where = f"""<fieldset id="address-section"><legend>{e(t("delivery.address_legend"))}</legend>
  <label for="full-name">{e(t("field.full_name"))}</label>
  <input id="full-name" name="full_name" type="text" value="{e(saved["full_name"])}">
  <label for="address">{e(t("field.address"))}</label>
  <textarea id="address" name="address" rows="3">{e(saved["address"])}</textarea>
</fieldset>"""
    return f"""<h1>{e(t("delivery.title"))}</h1>
<form method="post" action="/checkout/delivery">
  <label for="method">{e(t("field.method"))}</label>
  <select id="method" name="method" data-autosubmit>{options}</select>
  {where}
  <p class="error" id="delivery-error" role="alert">{e(error)}</p>
  <p class="actions"><a id="back-to-bag" href="/bag">{e(t("act.edit_bag"))}</a>
    <button id="review-order" class="primary" type="submit" name="action" value="review">{e(t("act.review_order"))}</button></p>
</form>"""


def review(lines: list[dict], totals: dict, saved: dict, method_label: str, store: str) -> str:
    rows = "".join(f'<li>{line["qty"]} × {e(line["name"])} ({e(line["variant_name"])}) — '
                   f'{money(line["price"] * line["qty"])}</li>' for line in lines)
    where = (f'{e(t("delivery.collect_from"))} {e(store)}' if saved["method"] == "collect"
             else f'{e(saved["full_name"])}<br>{e(saved["address"])}')
    return f"""<h1>{e(t("review.title"))}</h1>
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


def confirmation(order: dict, lines: list[dict], method_label: str, store: str) -> str:
    rows = "".join(f'<li>{line["qty"]} × {e(line["name"])} ({e(line["variant_name"])})</li>' for line in lines)
    where = (f'{e(t("delivery.collect_from"))} {e(store)}' if order["method"] == "collect"
             else f'{e(order["full_name"])}<br>{e(order["address"])}')
    return f"""<h1>{e(t("confirm.title"))}</h1>
<p>{e(t("confirm.number"))} <strong id="order-number">{e(order["number"])}</strong></p>
<ul id="confirm-lines">{rows}</ul>
<p>{e(t("review.delivery"))}: <span id="confirm-method">{e(method_label)}</span></p>
<address id="confirm-address">{where}</address>
<p>{e(t("total.paid"))} <strong id="confirm-total">{money(order["total"])}</strong></p>
<p><a id="continue-shopping" href="/">{e(t("act.keep_shopping"))}</a> · <a id="all-orders" href="/orders">{e(t("nav.orders"))}</a></p>"""


def orders(rows: list[dict], labels: dict) -> str:
    if not rows:
        return f'<h1>{e(t("nav.orders"))}</h1><p id="no-orders">{e(t("orders.none"))}</p>'
    body = "".join(f'<tr><td><a id="order-{e(o["number"])}" href="/orders/{e(o["number"])}">{e(o["number"])}</a></td>'
                   f'<td>{e(labels.get(o["method"], o["method"]))}</td><td>{money(o["total"])}</td></tr>' for o in rows)
    return f"""<h1>{e(t("nav.orders"))}</h1>
<table id="order-history"><thead><tr><th>{e(t("col.order"))}</th><th>{e(t("col.delivery"))}</th>
<th>{e(t("col.total"))}</th></tr></thead><tbody>{body}</tbody></table>"""


def info(title_key: str, body_key: str) -> str:
    return f"""<h1>{e(t(title_key))}</h1><p>{e(t(body_key))}</p>
<p><a id="info-back" href="/">{e(t("act.keep_shopping"))}</a></p>"""
