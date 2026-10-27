// Inkwell Stationers: a small online stationery shop.
//
// One Node.js process, no dependencies. Each browser gets its own session (a cookie), with
// its own cart and order history held in memory, so visitors never see each other's data.

"use strict";

const http = require("http");
const crypto = require("crypto");

const PORT = Number(process.env.PORT || 8080);

const PRODUCTS = [
  { id: 1, name: "Fineline Gel Pen", category: "pens", price: 3.5, blurb: "A 0.5 mm gel pen with quick-drying black ink." },
  { id: 2, name: "Brass Fountain Pen", category: "pens", price: 8.75, blurb: "Solid brass body, fine steel nib, takes standard cartridges." },
  { id: 3, name: "Twin-tip Highlighters", category: "pens", price: 4.25, blurb: "Four pastel highlighters with a chisel and a fine tip." },
  { id: 4, name: "Graphite Pencil Set", category: "pens", price: 6.0, blurb: "Six pencils from 2H to 6B in a tin." },
  { id: 5, name: "A5 Dot-grid Notebook", category: "paper", price: 12.0, blurb: "192 pages of 100 gsm paper, lay-flat binding." },
  { id: 6, name: "Recycled Letter Paper", category: "paper", price: 9.5, blurb: "One hundred A4 sheets of recycled writing paper." },
  { id: 7, name: "Linen Journal", category: "paper", price: 22.0, blurb: "Linen-bound journal with a ribbon marker." },
  { id: 8, name: "Watercolour Pad", category: "paper", price: 15.75, blurb: "Twenty sheets of cold-pressed 300 gsm paper." },
  { id: 9, name: "Walnut Desk Organiser", category: "desk", price: 32.5, blurb: "Three compartments in oiled walnut." },
  { id: 10, name: "Felt Desk Mat", category: "desk", price: 18.0, blurb: "A 70 x 30 cm wool felt mat." },
];
const CATEGORIES = { all: "All products", pens: "Pens & pencils", paper: "Paper & notebooks", desk: "Desk" };
const DELIVERY = {
  standard: { label: "Standard (3-5 days)", fee: 4.95 },
  express: { label: "Express (next day)", fee: 9.95 },
};
const DISCOUNT_THRESHOLD = 50;
const DISCOUNT_RATE = 0.1;

// -- sessions -------------------------------------------------------------------------------

const sessions = new Map();

function sessionFor(req, res) {
  const cookie = req.headers.cookie || "";
  const match = cookie.match(/(?:^|;\s*)sid=([a-f0-9]{32})/);
  let sid = match && match[1];
  if (!sid || !sessions.has(sid)) {
    sid = crypto.randomBytes(16).toString("hex");
    sessions.set(sid, { cart: new Map(), orders: [], nextOrder: 1001, notice: "" });
    res.setHeader("Set-Cookie", `sid=${sid}; Path=/; HttpOnly; SameSite=Lax`);
  }
  return sessions.get(sid);
}

// -- money ----------------------------------------------------------------------------------

const round2 = (n) => Math.round(n * 100) / 100;
const money = (n) => `$${n.toFixed(2)}`;

function cartLines(cart) {
  return [...cart.entries()].map(([id, qty]) => {
    const product = PRODUCTS.find((p) => p.id === id);
    return { product, qty, lineTotal: round2(product.price * qty) };
  });
}

function itemCount(cart) {
  return [...cart.values()].reduce((sum, qty) => sum + qty, 0);
}

function badgeCount(cart) {
  return cart.size;
}

function totals(cart, delivery = "standard") {
  const lines = cartLines(cart);
  const subtotal = round2(lines.reduce((sum, line) => sum + line.lineTotal, 0));
  const discount = subtotal >= DISCOUNT_THRESHOLD ? round2(subtotal * DISCOUNT_RATE) : 0;
  const fee = lines.length ? DELIVERY[delivery].fee : 0;
  const afterDiscount = subtotal - discount;
  const total = round2(afterDiscount - discount + fee);
  return { subtotal, discount, fee, total, beforeDelivery: round2(afterDiscount - discount) };
}

// -- rendering ------------------------------------------------------------------------------

const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const STYLE = `
  :root { --ink:#1d2433; --muted:#5d6678; --line:#e3e6ec; --accent:#2f5d8a; --bg:#f7f6f2; --card:#fff; --warn:#a33a2b; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: Georgia, "Times New Roman", serif; background:var(--bg); color:var(--ink); }
  header { background:var(--ink); color:#fff; padding:14px 32px; display:flex; align-items:center; gap:28px; }
  header .brand { font-size:22px; letter-spacing:.5px; font-weight:bold; color:#fff; text-decoration:none; }
  header nav { display:flex; gap:20px; font-family: Arial, sans-serif; font-size:15px; }
  header nav a { color:#dfe6f0; text-decoration:none; }
  header nav a.active { color:#fff; border-bottom:2px solid #f0c674; }
  .badge { background:#f0c674; color:var(--ink); border-radius:10px; padding:1px 8px; font-weight:bold; margin-left:4px; }
  main { max-width:980px; margin:28px auto; padding:0 24px; font-family: Arial, sans-serif; }
  h1 { font-family: Georgia, serif; font-weight:normal; font-size:32px; margin:0 0 18px; }
  .notice { background:#eef4ea; border:1px solid #cfe0c5; padding:10px 14px; border-radius:6px; }
  .filters { display:flex; gap:24px; align-items:center; margin:12px 0 20px; }
  .filters label { color:var(--muted); margin-right:6px; }
  select, input[type=text], input[type=email] { font-size:15px; padding:7px 9px; border:1px solid #c9ced8; border-radius:5px; }
  #products { list-style:none; padding:0; display:grid; grid-template-columns:repeat(auto-fill,minmax(210px,1fr)); gap:16px; }
  .product { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:16px; display:flex; flex-direction:column; gap:10px; }
  .product .product-name { color:var(--ink); font-weight:bold; text-decoration:none; }
  .price { font-size:18px; color:var(--accent); }
  button, .button { background:var(--accent); color:#fff; border:0; border-radius:5px; padding:8px 14px; font-size:14px; cursor:pointer; text-decoration:none; display:inline-block; }
  button.secondary { background:#e8ebf0; color:var(--ink); }
  table { width:100%; border-collapse:collapse; background:var(--card); }
  th, td { text-align:left; padding:10px 12px; border-bottom:1px solid var(--line); }
  td.num, th.num { text-align:right; }
  .qty-form { display:flex; align-items:center; gap:8px; }
  .summary { margin:20px 0 0 auto; width:340px; background:var(--card); border:1px solid var(--line); border-radius:8px; padding:14px 18px; }
  .summary div { display:flex; justify-content:space-between; padding:5px 0; }
  .summary .grand { border-top:1px solid var(--line); margin-top:6px; padding-top:10px; font-weight:bold; font-size:18px; }
  .field { margin-bottom:14px; display:flex; flex-direction:column; gap:5px; max-width:420px; }
  .error { color:var(--warn); margin:0; font-size:14px; }
  .columns { display:grid; grid-template-columns: 1fr 340px; gap:32px; align-items:start; }
  .panel { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:18px 22px; }
  .muted { color:var(--muted); }
`;

function layout(session, { title, active, body, script = "" }) {
  const link = (href, id, label, extra = "") =>
    `<a href="${href}" id="${id}"${active === id ? ' class="active"' : ""}>${label}${extra}</a>`;
  const notice = session.notice ? `<p class="notice" id="notice">${escapeHtml(session.notice)}</p>` : "";
  session.notice = "";
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escapeHtml(title)} | Inkwell Stationers</title><style>${STYLE}</style></head>
<body>
<header>
  <a class="brand" href="/shop">Inkwell Stationers</a>
  <nav>
    ${link("/shop", "nav-shop", "Shop")}
    ${link("/cart", "nav-cart", "Cart", ` <span id="cart-count" class="badge">${badgeCount(session.cart)}</span>`)}
    ${link("/orders", "nav-orders", "Orders")}
    ${link("/help", "nav-help", "Delivery &amp; discounts")}
  </nav>
</header>
<main>${notice}${body}</main>
${script}
</body></html>`;
}

function productCard(p) {
  return `<li class="product" data-category="${p.category}">
    <a class="product-name" id="product-${p.id}" href="/product/${p.id}">${escapeHtml(p.name)}</a>
    <span class="price">${money(p.price)}</span>
    <form method="post" action="/cart/add"><input type="hidden" name="id" value="${p.id}"><input type="hidden" name="back" value="/shop">
      <button type="submit" id="add-${p.id}">Add to cart</button></form>
  </li>`;
}

function shopPage(session) {
  const options = (pairs) => pairs.map(([v, l]) => `<option value="${v}">${l}</option>`).join("");
  const catalogue = PRODUCTS.map((p) => ({ id: p.id, name: p.name, category: p.category, price: p.price.toFixed(2) }));
  const body = `<h1>Shop</h1>
  <div class="filters">
    <div><label for="category">Category</label><select id="category" name="category">${options(Object.entries(CATEGORIES))}</select></div>
    <div><label for="sort">Sort by</label><select id="sort" name="sort">${options([
      ["featured", "Featured"], ["price-asc", "Price: low to high"], ["price-desc", "Price: high to low"], ["name", "Name: A to Z"]])}</select></div>
  </div>
  <ul id="products">${PRODUCTS.map(productCard).join("")}</ul>`;
  const script = `<script id="catalogue" type="application/json">${JSON.stringify(catalogue)}</script>
<script>
  const catalogue = JSON.parse(document.getElementById("catalogue").textContent);
  const list = document.getElementById("products");
  const card = (p) => '<li class="product" data-category="' + p.category + '">' +
    '<a class="product-name" id="product-' + p.id + '" href="/product/' + p.id + '">' + p.name + '</a>' +
    '<span class="price">$' + p.price + '</span>' +
    '<form method="post" action="/cart/add"><input type="hidden" name="id" value="' + p.id + '"><input type="hidden" name="back" value="/shop">' +
    '<button type="submit" id="add-' + p.id + '">Add to cart</button></form></li>';
  function render() {
    const category = document.getElementById("category").value;
    const sort = document.getElementById("sort").value;
    const items = catalogue.filter((p) => category === "all" || p.category === category);
    if (sort === "price-asc") items.sort((a, b) => a.price.localeCompare(b.price));
    if (sort === "price-desc") items.sort((a, b) => Number(b.price) - Number(a.price));
    if (sort === "name") items.sort((a, b) => a.name.localeCompare(b.name));
    list.innerHTML = items.map(card).join("");
  }
  document.getElementById("category").addEventListener("change", render);
  document.getElementById("sort").addEventListener("change", render);
</script>`;
  return layout(session, { title: "Shop", active: "nav-shop", body, script });
}

function productPage(session, product) {
  const body = `<p><a href="/shop" id="back-to-shop">&larr; Back to the shop</a></p>
  <div class="panel">
    <h1 id="product-name">${escapeHtml(product.name)}</h1>
    <p class="muted" id="product-category">${CATEGORIES[product.category]}</p>
    <p id="product-blurb">${escapeHtml(product.blurb)}</p>
    <p class="price" id="product-price">${money(product.price)}</p>
    <form method="post" action="/cart/add"><input type="hidden" name="id" value="${product.id}"><input type="hidden" name="back" value="/product/${product.id}">
      <button type="submit" id="add-to-cart">Add to cart</button></form>
  </div>`;
  return layout(session, { title: product.name, active: "nav-shop", body });
}

function summaryBlock(t, prefix, deliveryLabel) {
  return `<div><span>Subtotal</span><span id="${prefix}subtotal">${money(t.subtotal)}</span></div>
    <div><span>Discount (10% off orders of $50 or more)</span><span id="${prefix}discount">&minus;${money(t.discount)}</span></div>
    <div><span id="${prefix}delivery-label">${deliveryLabel}</span><span id="${prefix}delivery">${money(t.fee)}</span></div>
    <div class="grand"><span>Total</span><span id="${prefix}total">${money(t.total)}</span></div>`;
}

function cartSummary(t) {
  return `<div><span>Subtotal</span><span id="subtotal">${money(t.subtotal)}</span></div>
    <div><span>Discount (10% off orders of $50 or more)</span><span id="discount">&minus;${money(t.discount)}</span></div>
    <div class="grand"><span>Total before delivery</span><span id="total">${money(t.beforeDelivery)}</span></div>
    <p class="muted">Delivery is chosen and added at checkout.</p>`;
}

function cartPage(session) {
  const lines = cartLines(session.cart);
  if (!lines.length) {
    return layout(session, { title: "Your cart", active: "nav-cart",
      body: `<h1>Your cart</h1><p id="empty-cart">Your cart is empty.</p><p><a class="button" href="/shop" id="continue-shopping">Continue shopping</a></p>` });
  }
  const rows = lines.map(({ product: p, qty, lineTotal }) => `<tr class="line">
      <td class="line-name"><a href="/product/${p.id}">${escapeHtml(p.name)}</a></td>
      <td class="num unit-price">${money(p.price)}</td>
      <td><form method="post" action="/cart/update" class="qty-form"><input type="hidden" name="id" value="${p.id}">
        <button type="submit" name="op" value="dec" id="dec-${p.id}" class="secondary" aria-label="One fewer ${escapeHtml(p.name)}">&minus;</button>
        <span class="qty">${qty}</span>
        <button type="submit" name="op" value="inc" id="inc-${p.id}" class="secondary" aria-label="One more ${escapeHtml(p.name)}">+</button></form></td>
      <td class="num line-total">${money(lineTotal)}</td>
      <td><form method="post" action="/cart/remove"><input type="hidden" name="id" value="${p.id}"><button type="submit" id="remove-${p.id}" class="secondary">Remove</button></form></td>
    </tr>`).join("");
  const count = itemCount(session.cart);
  const body = `<h1>Your cart</h1>
  <p id="item-count">${count} ${count === 1 ? "item" : "items"} in your cart</p>
  <table><thead><tr><th>Product</th><th class="num">Price</th><th>Quantity</th><th class="num">Line total</th><th></th></tr></thead>
  <tbody>${rows}</tbody></table>
  <div class="summary" id="cart-summary">${cartSummary(totals(session.cart))}</div>
  <p style="text-align:right;margin-top:18px"><a class="button" href="/checkout" id="checkout">Proceed to checkout</a></p>`;
  return layout(session, { title: "Your cart", active: "nav-cart", body });
}

function checkoutPage(session, form = {}, errors = {}) {
  if (!session.cart.size) {
    return layout(session, { title: "Checkout", active: "nav-cart",
      body: `<h1>Checkout</h1><p>Your cart is empty.</p><p><a class="button" href="/shop" id="continue-shopping">Back to the shop</a></p>` });
  }
  const delivery = DELIVERY[form.delivery] ? form.delivery : "standard";
  const t = totals(session.cart, delivery);
  const field = (name, label, type = "text") => `<div class="field">
      <label for="${name}">${label}</label>
      <input type="${type}" id="${name}" name="${name}" value="${escapeHtml(form[name] || "")}">
      ${errors[name] ? `<p class="error" id="error-${name}">${errors[name]}</p>` : ""}</div>`;
  const options = Object.entries(DELIVERY).map(([k, d]) =>
    `<option value="${k}"${k === delivery ? " selected" : ""}>${d.label} - ${money(d.fee)}</option>`).join("");
  const body = `<h1>Checkout</h1>
  <div class="columns">
    <form method="post" action="/checkout" class="panel" id="checkout-form" novalidate>
      ${field("name", "Full name *")}
      ${field("email", "Email *", "email")}
      ${field("address", "Street address *")}
      ${field("postcode", "Postcode *")}
      <div class="field"><label for="delivery">Delivery *</label><select id="delivery" name="delivery">${options}</select></div>
      <button type="submit" id="place-order">Place order</button>
    </form>
    <div class="summary" id="checkout-summary" data-before="${t.beforeDelivery}"
         data-fees='${JSON.stringify(Object.fromEntries(Object.entries(DELIVERY).map(([k, d]) => [k, d.fee])))}'>
      <div><span>Subtotal</span><span id="checkout-subtotal">${money(t.subtotal)}</span></div>
      <div><span>Discount (10% off orders of $50 or more)</span><span id="checkout-discount">&minus;${money(t.discount)}</span></div>
      <div><span>Delivery</span><span id="checkout-delivery">${money(t.fee)}</span></div>
      <div class="grand"><span>Order total</span><span id="checkout-total">${money(t.total)}</span></div>
    </div>
  </div>`;
  const script = `<script>
  const summary = document.getElementById("checkout-summary");
  const fees = JSON.parse(summary.dataset.fees);
  document.getElementById("delivery").addEventListener("change", (event) => {
    const fee = fees[event.target.value];
    document.getElementById("checkout-delivery").textContent = "$" + fee.toFixed(2);
    document.getElementById("checkout-total").textContent = "$" + (Number(summary.dataset.before) + fee).toFixed(2);
  });
</script>`;
  return layout(session, { title: "Checkout", active: "nav-cart", body, script });
}

function shippingName(name) {
  return name.slice(0, 12);
}

function orderPage(session, order) {
  const rows = order.lines.map((l) => `<tr class="order-line"><td>${escapeHtml(l.name)}</td><td class="num order-qty">${l.qty}</td>
      <td class="num order-line-total">${money(l.lineTotal)}</td></tr>`).join("");
  const body = `<h1>Thank you for your order</h1>
  <p id="order-number">Order number ${order.id}</p>
  <div class="columns">
    <div class="panel">
      <h2>Delivering to</h2>
      <p id="ship-name">${escapeHtml(shippingName(order.name))}</p>
      <p id="ship-address">${escapeHtml(order.address)}, ${escapeHtml(order.postcode)}</p>
      <p id="receipt">A receipt has been sent to <span id="receipt-email">${escapeHtml(order.email)}</span>.</p>
      <table><thead><tr><th>Product</th><th class="num">Quantity</th><th class="num">Line total</th></tr></thead><tbody>${rows}</tbody></table>
    </div>
    <div class="summary" id="order-summary">${summaryBlock(order.totals, "order-", escapeHtml(DELIVERY[order.delivery].label))}</div>
  </div>
  <p><a href="/orders" id="view-orders">View all orders</a> &middot; <a href="/shop" id="keep-shopping">Keep shopping</a></p>`;
  return layout(session, { title: `Order ${order.id}`, active: "nav-orders", body });
}

function ordersPage(session) {
  const rows = session.orders.slice().reverse().map((o) => `<tr class="order-row">
      <td><a href="/order/${o.id}" id="order-${o.id}">Order ${o.id}</a></td>
      <td class="num order-items">${o.lines.reduce((s, l) => s + l.qty, 0)}</td>
      <td>${escapeHtml(DELIVERY[o.delivery].label)}</td>
      <td class="num order-row-total">${money(o.totals.total)}</td></tr>`).join("");
  const body = `<h1>Your orders</h1>${session.orders.length
    ? `<table><thead><tr><th>Order</th><th class="num">Items</th><th>Delivery</th><th class="num">Total</th></tr></thead><tbody>${rows}</tbody></table>`
    : `<p id="no-orders">You have not placed an order yet.</p>`}`;
  return layout(session, { title: "Your orders", active: "nav-orders", body });
}

function helpPage(session) {
  const body = `<h1>Delivery &amp; discounts</h1>
  <div class="panel" id="policy">
    <h2>Discount</h2>
    <p>Orders with an item subtotal of $50 or more get 10% off the item subtotal. The discount is applied once.</p>
    <h2>Delivery</h2>
    <p>Standard delivery (3-5 days) costs $4.95. Express delivery (next day) costs $9.95. The delivery fee is added after the discount.</p>
    <p>We deliver to a street address, and every order needs a postcode, whichever delivery option you choose.</p>
    <h2>Your cart</h2>
    <p>The cart count in the header shows how many items are in your cart: a product added three times counts as three items.</p>
  </div>`;
  return layout(session, { title: "Delivery & discounts", active: "nav-help", body });
}

function notFound(session, res) {
  send(res, 404, layout(session, { title: "Not found", body: `<h1>Page not found</h1><p><a href="/shop" id="home">Back to the shop</a></p>` }));
}

// -- http -----------------------------------------------------------------------------------

function send(res, status, html) {
  res.writeHead(status, { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" });
  res.end(html);
}

function redirect(res, location) {
  res.writeHead(303, { Location: location });
  res.end();
}

function readForm(req) {
  return new Promise((resolve) => {
    let raw = "";
    req.on("data", (chunk) => {
      raw += chunk;
      if (raw.length > 100_000) req.destroy();
    });
    req.on("end", () => resolve(Object.fromEntries(new URLSearchParams(raw))));
  });
}

function productById(value) {
  return PRODUCTS.find((p) => p.id === Number(value));
}

function validate(form) {
  const errors = {};
  if (!(form.name || "").trim()) errors.name = "Enter your full name";
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test((form.email || "").trim())) errors.email = "Enter a valid email address";
  if (!(form.address || "").trim()) errors.address = "Enter a street address";
  if (form.delivery === "express") {
    if (!(form.postcode || "").trim()) errors.postcode = "Enter a postcode";
  }
  if (!DELIVERY[form.delivery]) errors.delivery = "Choose a delivery option";
  return errors;
}

async function handle(req, res) {
  const session = sessionFor(req, res);
  const url = new URL(req.url, "http://localhost");
  const path = url.pathname.replace(/\/+$/, "") || "/";

  if (req.method === "GET") {
    if (path === "/") return redirect(res, "/shop");
    if (path === "/shop") return send(res, 200, shopPage(session));
    if (path === "/cart") return send(res, 200, cartPage(session));
    if (path === "/checkout") return send(res, 200, checkoutPage(session));
    if (path === "/orders") return send(res, 200, ordersPage(session));
    if (path === "/help") return send(res, 200, helpPage(session));
    let match = path.match(/^\/product\/(\d+)$/);
    if (match && productById(match[1])) return send(res, 200, productPage(session, productById(match[1])));
    match = path.match(/^\/order\/(\d+)$/);
    const order = match && session.orders.find((o) => o.id === Number(match[1]));
    if (order) return send(res, 200, orderPage(session, order));
    return notFound(session, res);
  }

  if (req.method === "POST") {
    const form = await readForm(req);
    if (path === "/cart/add") {
      const product = productById(form.id);
      if (!product) return notFound(session, res);
      session.cart.set(product.id, (session.cart.get(product.id) || 0) + 1);
      session.notice = `Added ${product.name} to your cart.`;
      const back = typeof form.back === "string" && /^\/[a-z0-9/]*$/.test(form.back) ? form.back : "/shop";
      return redirect(res, back);
    }
    if (path === "/cart/update") {
      const product = productById(form.id);
      if (product && session.cart.has(product.id)) {
        const qty = session.cart.get(product.id) + (form.op === "inc" ? 1 : -1);
        if (qty > 0) session.cart.set(product.id, qty);
        else session.cart.delete(product.id);
      }
      return redirect(res, "/cart");
    }
    if (path === "/cart/remove") {
      const product = productById(form.id);
      if (product && session.cart.delete(product.id)) session.notice = `Removed ${product.name} from your cart.`;
      return redirect(res, "/cart");
    }
    if (path === "/checkout") {
      if (!session.cart.size) return redirect(res, "/cart");
      const errors = validate(form);
      if (Object.keys(errors).length) return send(res, 200, checkoutPage(session, form, errors));
      const order = {
        id: session.nextOrder++,
        name: form.name.trim(),
        email: form.email.trim(),
        address: form.address.trim(),
        postcode: (form.postcode || "").trim(),
        delivery: form.delivery,
        lines: cartLines(session.cart).map((l) => ({ name: l.product.name, qty: l.qty, lineTotal: l.lineTotal })),
        totals: totals(session.cart),
      };
      session.orders.push(order);
      session.cart.clear();
      return redirect(res, `/order/${order.id}`);
    }
    return notFound(session, res);
  }

  res.writeHead(405, { Allow: "GET, POST" });
  res.end();
}

http.createServer((req, res) => {
  handle(req, res).catch((error) => {
    console.error(error);
    if (!res.headersSent) send(res, 500, "<h1>Something went wrong</h1>");
  });
}).listen(PORT, "0.0.0.0", () => console.log(`Inkwell Stationers listening on port ${PORT}`));
