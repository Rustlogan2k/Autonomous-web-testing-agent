/* Stackroom client. Every page route is the same document; this script renders the route
   named by location.pathname, and pages link to each other with ordinary hrefs. The stock
   grid's state (category, search, sort, page) lives in its own query string, kept in step
   with history.replaceState, and every change fetches one page of rows from the server. */
"use strict";

const WORDS = JSON.parse(document.getElementById("stackroom-words").textContent);
const t = (key) => (Object.prototype.hasOwnProperty.call(WORDS, key) ? WORDS[key] : key);
const page = document.getElementById("page");
const LEVEL = /^\d{1,9}$/;
const SORTABLE = ["sku", "name", "stock"];
const OPTIONAL_COLUMNS = ["category", "location", "status"];
const REMEMBERED_GRID = "stackroom.grid";

function escapeHtml(value) {
  return String(value === null || value === undefined ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

async function request(method, url, payload) {
  const init = { method, credentials: "same-origin", headers: { Accept: "application/json" } };
  if (payload !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(payload);
  }
  try {
    const response = await fetch(url, init);
    const body = await response.json();
    return { ok: response.ok, body };
  } catch (_) {
    // The page navigated away mid-request, or the server could not be reached.
    return { ok: false, body: { error: t("msg.failed") } };
  }
}

// -- the grid's address -----------------------------------------------------------------------

function gridStateFrom(search) {
  const params = new URLSearchParams(search);
  const asked = Number.parseInt(params.get("page") || "1", 10);
  return {
    category: params.get("category") || "all",
    q: params.get("q") || "",
    sort: SORTABLE.includes(params.get("sort")) ? params.get("sort") : "sku",
    dir: params.get("dir") === "desc" ? "desc" : "asc",
    page: Number.isInteger(asked) && asked > 0 ? asked : 1,
  };
}

function gridSearch(state) {
  return new URLSearchParams({ category: state.category, q: state.q, sort: state.sort,
                               dir: state.dir, page: String(state.page) }).toString();
}

function rememberGrid(search) {
  try { sessionStorage.setItem(REMEMBERED_GRID, search); } catch (_) { /* storage may be off */ }
}

function gridHref() {
  let saved = "";
  try { saved = sessionStorage.getItem(REMEMBERED_GRID) || ""; } catch (_) { saved = ""; }
  return saved ? `/stock?${saved}` : "/stock";
}

function renderNav() {
  document.getElementById("site-nav").innerHTML = `
    <a id="nav-stock" href="${escapeHtml(gridHref())}">${escapeHtml(t("nav.stock"))}</a>
    <a id="nav-log" href="/log">${escapeHtml(t("nav.log"))}</a>`;
}

// -- stock grid ---------------------------------------------------------------------------------

const grid = {
  state: null,      // what the rows on screen were fetched for
  draft: null,      // the filter form as typed, applied only by the filter's Apply
  data: null,       // the last listing the server sent
  picked: new Set(),
  bulkAction: "archive",
  hidden: new Set(),
  menuOpen: false,
  ticket: 0,
  note: "",
};

async function loadGrid(wanted, firstLoad = false) {
  const ticket = ++grid.ticket;
  const { ok, body } = await request("GET", `/api/listing?${gridSearch(wanted)}`);
  if (ticket !== grid.ticket) { return; }            // a newer request has superseded this one
  if (!ok) { grid.note = body.error || t("msg.failed"); return renderGrid(); }
  if (firstLoad && body.total > 0 && body.page > body.pages) {
    return loadGrid({ ...wanted, page: body.pages }); // an out-of-date bookmark: show its last page
  }
  showListing(body);
}

function showListing(listing) {
  const view = listing.view;
  grid.state = { category: view.category, q: view.q, sort: view.sort, dir: view.dir, page: listing.page };
  grid.data = listing;
  grid.picked = new Set();
  if (!grid.draft) { grid.draft = { category: view.category, q: view.q }; }
  const search = gridSearch(grid.state);
  history.replaceState(null, "", `/stock?${search}`);
  rememberGrid(search);
  renderNav();
  renderGrid();
}

function sortButton(key) {
  const active = grid.state.sort === key;
  const sortAttr = active ? ` aria-sort="${grid.state.dir === "asc" ? "ascending" : "descending"}"` : "";
  const arrow = active ? (grid.state.dir === "asc" ? " ▲" : " ▼") : "";
  return `<button id="sort-${key}" type="button" data-sort="${key}"${sortAttr}>${escapeHtml(t(`col.${key}`))}${arrow}</button>`;
}

function adjustHref(sku, at) {
  const s = grid.state;
  const params = new URLSearchParams({ at: String(at), category: s.category, q: s.q, sort: s.sort, dir: s.dir });
  return `/items/${encodeURIComponent(sku)}/adjust?${params.toString()}`;
}

function gridRow(item, at) {
  const sku = escapeHtml(item.sku);
  const archived = item.status === "archived";
  return `<tr${archived ? ' class="archived"' : ""}>
      <td><input id="pick-${sku}" name="pick" type="checkbox" value="${sku}"${grid.picked.has(item.sku) ? " checked" : ""}></td>
      <td><label for="pick-${sku}">${sku}</label></td>
      <td>${escapeHtml(item.name)}</td>
      <td class="c-category">${escapeHtml(item.category)}</td>
      <td class="c-location">${escapeHtml(item.location)}</td>
      <td><output id="qty-${sku}">${escapeHtml(item.stock)}</output></td>
      <td class="c-status">${escapeHtml(t(`status.${item.status}`))}</td>
      <td><label for="stock-${sku}">${escapeHtml(t("field.set_stock"))}</label>
        <input id="stock-${sku}" name="stock_inline" type="number" min="0" max="999999999" step="1"
          inputmode="numeric" autocomplete="off" data-sku="${sku}">
        <span class="tick" id="saved-${sku}" aria-live="polite"></span></td>
      <td><a id="adjust-${sku}" href="${escapeHtml(adjustHref(item.sku, at))}">${escapeHtml(t("act.adjust"))}</a></td>
    </tr>`;
}

function renderGrid() {
  const data = grid.data;
  if (!data) { page.innerHTML = `<p class="error">${escapeHtml(grid.note || t("msg.failed"))}</p>`; return; }
  const categories = [["all", t("filter.all")]].concat(data.categories.map((c) => [c, c]));
  const categoryOptions = categories.map(([value, label]) =>
    `<option value="${escapeHtml(value)}"${value === grid.draft.category ? " selected" : ""}>${escapeHtml(label)}</option>`).join("");
  const bulkOptions = [["archive", t("bulk.archive")], ["restock", t("bulk.restock")]].map(([value, label]) =>
    `<option value="${value}"${value === grid.bulkAction ? " selected" : ""}>${escapeHtml(label)}</option>`).join("");
  const columnChoices = OPTIONAL_COLUMNS.map((column) => `
      <label><input id="col-${column}" name="show_${column}" type="checkbox" data-column="${column}"${grid.hidden.has(column) ? "" : " checked"}>
        ${escapeHtml(t(`col.${column}`))}</label>`).join("");
  const rows = data.rows.map((item, i) => gridRow(item, data.first + i)).join("");
  const everyPicked = data.rows.length > 0 && data.rows.every((item) => grid.picked.has(item.sku));
  const hiddenClasses = [...grid.hidden].map((column) => `hide-${column}`).join(" ");
  page.innerHTML = `
    <h1>${escapeHtml(t("title.grid"))}</h1>
    <div class="toolbar">
      <label for="filter-category">${escapeHtml(t("field.category"))}</label>
      <select id="filter-category" name="category">${categoryOptions}</select>
      <label for="filter-q">${escapeHtml(t("field.search"))}</label>
      <input id="filter-q" name="q" type="search" value="${escapeHtml(grid.draft.q)}" autocomplete="off">
      <button id="filter-apply" class="strong" type="button">${escapeHtml(t("act.apply"))}</button>
      <button id="sync-grid" type="button">${escapeHtml(t("act.sync"))}</button>
      <button id="columns-toggle" type="button" aria-expanded="${grid.menuOpen}" aria-controls="columns-menu">${escapeHtml(t("act.columns"))}</button>
    </div>
    <div id="columns-menu" class="menu" role="group" aria-label="${escapeHtml(t("title.columns"))}"${grid.menuOpen ? "" : " hidden"}>${columnChoices}</div>
    <p class="count"><output id="match-count">${escapeHtml(data.total)}</output> ${escapeHtml(t("label.matching"))}</p>
    <div class="bulkbar">
      <input id="pick-all" name="pick_all" type="checkbox"${everyPicked ? " checked" : ""}>
      <label for="pick-all">${escapeHtml(t("label.select_all"))}</label>
      <label for="bulk-action">${escapeHtml(t("field.bulk"))}</label>
      <select id="bulk-action" name="bulk_action">${bulkOptions}</select>
      <button id="bulk-apply" class="strong" type="button">${escapeHtml(t("act.apply"))}</button>
      <output id="bulk-status">${escapeHtml(grid.note)}</output>
    </div>
    <table class="grid ${hiddenClasses}" id="stock-grid">
      <thead><tr><th></th><th>${sortButton("sku")}</th><th>${sortButton("name")}</th>
        <th class="c-category">${escapeHtml(t("col.category"))}</th><th class="c-location">${escapeHtml(t("col.location"))}</th>
        <th>${sortButton("stock")}</th><th class="c-status">${escapeHtml(t("col.status"))}</th>
        <th>${escapeHtml(t("col.set"))}</th><th></th></tr></thead>
      <tbody id="grid-rows">${rows}</tbody>
    </table>
    ${data.rows.length ? "" : `<p id="grid-empty" class="empty">${escapeHtml(t("msg.empty"))}</p>`}
    <div class="pager">
      <button id="page-prev" type="button"${grid.state.page <= 1 ? " disabled" : ""}>${escapeHtml(t("act.prev"))}</button>
      <span>${escapeHtml(t("label.page"))} <output id="page-number">${grid.state.page}</output> ${escapeHtml(t("label.of"))} <output id="page-count">${data.pages}</output></span>
      <button id="page-next" type="button"${grid.state.page >= data.pages ? " disabled" : ""}>${escapeHtml(t("act.next"))}</button>
    </div>`;
  wireGrid();
}

function wireGrid() {
  const byId = (id) => document.getElementById(id);
  byId("filter-category").addEventListener("change", (e) => { grid.draft.category = e.target.value; });
  byId("filter-q").addEventListener("input", (e) => { grid.draft.q = e.target.value; });
  byId("filter-apply").addEventListener("click", () => {
    grid.note = "";
    // A different filter is a different result set, so it is read from its first page.
    loadGrid({ ...grid.state, category: grid.draft.category, q: grid.draft.q, page: 1 });
  });
  byId("sync-grid").addEventListener("click", () => { grid.note = ""; loadGrid({ ...grid.state }); });
  byId("columns-toggle").addEventListener("click", () => { grid.menuOpen = !grid.menuOpen; renderGrid(); });
  page.querySelectorAll("input[data-column]").forEach((box) => box.addEventListener("change", () => {
    if (box.checked) { grid.hidden.delete(box.dataset.column); } else { grid.hidden.add(box.dataset.column); }
    renderGrid();
  }));
  page.querySelectorAll("button[data-sort]").forEach((button) => button.addEventListener("click", () => {
    const key = button.dataset.sort;
    const dir = grid.state.sort === key && grid.state.dir === "asc" ? "desc" : "asc";
    grid.note = "";
    loadGrid({ ...grid.state, sort: key, dir, page: 1 });
  }));
  byId("page-prev").addEventListener("click", () => {
    loadGrid({ ...grid.state, page: Math.max(1, grid.state.page - 1) });
  });
  byId("page-next").addEventListener("click", () => {
    loadGrid({ ...grid.state, page: Math.min(grid.data.pages, grid.state.page + 1) });
  });
  byId("pick-all").addEventListener("change", (e) => {
    grid.data.rows.forEach((item) => (e.target.checked ? grid.picked.add(item.sku) : grid.picked.delete(item.sku)));
    page.querySelectorAll('input[name="pick"]').forEach((box) => { box.checked = e.target.checked; });
  });
  page.querySelectorAll('input[name="pick"]').forEach((box) => box.addEventListener("change", () => {
    if (box.checked) { grid.picked.add(box.value); } else { grid.picked.delete(box.value); }
    byId("pick-all").checked = grid.data.rows.every((item) => grid.picked.has(item.sku));
  }));
  byId("bulk-action").addEventListener("change", (e) => { grid.bulkAction = e.target.value; });
  byId("bulk-apply").addEventListener("click", runBulk);
  page.querySelectorAll('input[name="stock_inline"]').forEach((input) => input.addEventListener("input", () => saveInline(input)));
}

async function runBulk() {
  const skus = [...grid.picked];
  const ticket = ++grid.ticket;
  const { ok, body } = await request("POST", "/api/bulk", { action: grid.bulkAction, skus, view: grid.state });
  if (ticket !== grid.ticket) { return; }
  if (!ok) { grid.note = body.error || t("msg.failed"); return renderGrid(); }
  grid.note = skus.length ? `${t("msg.bulk_done")} ${body.changed}` : t("msg.bulk_none");
  showListing(body.listing);
}

const inlineTickets = new Map();

async function saveInline(input) {
  const sku = input.dataset.sku;
  const status = document.getElementById(`saved-${sku}`);
  const typed = input.value.trim();
  if (!LEVEL.test(typed)) { status.textContent = t("msg.level_rule"); return; }
  const mine = (inlineTickets.get(sku) || 0) + 1;
  inlineTickets.set(sku, mine);
  status.textContent = t("msg.saving");
  const { ok, body } = await request("POST", `/api/items/${encodeURIComponent(sku)}/stock`, { stock: typed });
  if (inlineTickets.get(sku) !== mine) { return; }   // a later keystroke's save answers instead
  if (!ok) { status.textContent = body.error || t("msg.failed"); return; }
  const row = grid.data.rows.find((item) => item.sku === sku);
  if (row) { row.stock = body.stock; }
  const shown = document.getElementById(`qty-${sku}`);
  if (shown) { shown.textContent = String(body.stock); }
  status.textContent = t("msg.saved");
}

// -- adjust page -----------------------------------------------------------------------------------

async function showAdjust(sku) {
  const params = new URLSearchParams(location.search);
  const at = Number.parseInt(params.get("at") || "0", 10);
  const ref = {
    at: Number.isInteger(at) && at >= 0 ? at : 0,
    view: gridStateFrom(location.search),
  };
  const { ok, body } = await request("GET", `/api/items/${encodeURIComponent(sku)}`);
  if (!ok) { page.innerHTML = `<p class="error">${escapeHtml(t("msg.not_found"))}</p>`; return; }
  const item = body.item;
  const backTo = `/stock?${gridSearch({ ...ref.view, page: Math.floor(ref.at / 10) + 1 })}`;
  page.innerHTML = `
    <h1>${escapeHtml(t("title.adjust"))}</h1>
    <section class="panel">
      <p><strong id="adjust-sku">${escapeHtml(item.sku)}</strong> · <span id="adjust-name">${escapeHtml(item.name)}</span></p>
      <p>${escapeHtml(t("label.current"))}: <output id="adjust-current">${escapeHtml(item.stock)}</output></p>
      <label for="new-stock">${escapeHtml(t("field.new_stock"))}</label>
      <input id="new-stock" name="new_stock" type="number" min="0" max="999999999" step="1" required autocomplete="off">
      <p id="adjust-error" class="error" role="alert"></p>
      <div class="actions">
        <button id="commit-adjust" class="strong" type="button">${escapeHtml(t("act.commit"))}</button>
        <a id="discard-adjust" href="${escapeHtml(backTo)}">${escapeHtml(t("act.discard"))}</a>
      </div>
    </section>`;
  const field = document.getElementById("new-stock");
  const error = document.getElementById("adjust-error");
  let committing = false;
  document.getElementById("commit-adjust").addEventListener("click", async () => {
    if (committing) { return; }                         // one commit per click burst
    const typed = field.value.trim();
    if (!LEVEL.test(typed)) { error.textContent = t("msg.level_needed"); return; }
    committing = true;
    error.textContent = "";
    const result = await request("POST", "/api/adjustments",
      { sku: item.sku, at: ref.at, view: ref.view, new_stock: typed });
    if (!result.ok) { committing = false; error.textContent = result.body.error || t("msg.failed"); return; }
    location.assign(`/items/${encodeURIComponent(item.sku)}`);
  });
}

// -- item page -------------------------------------------------------------------------------------

async function showItem(sku) {
  const { ok, body } = await request("GET", `/api/items/${encodeURIComponent(sku)}?notice=take`);
  if (!ok) { page.innerHTML = `<p class="error">${escapeHtml(t("msg.not_found"))}</p>`; return; }
  const item = body.item;
  const flash = body.notice
    ? `<p id="flash" class="flash" role="status">${escapeHtml(t("msg.saved_item"))} ${escapeHtml(body.notice.sku)}</p>` : "";
  page.innerHTML = `
    ${flash}
    <h1>${escapeHtml(item.name)}</h1>
    <dl class="facts">
      <dt>${escapeHtml(t("col.sku"))}</dt><dd><span id="item-sku">${escapeHtml(item.sku)}</span></dd>
      <dt>${escapeHtml(t("col.category"))}</dt><dd>${escapeHtml(item.category)}</dd>
      <dt>${escapeHtml(t("col.location"))}</dt><dd>${escapeHtml(item.location)}</dd>
      <dt>${escapeHtml(t("col.status"))}</dt><dd><span id="item-status">${escapeHtml(t(`status.${item.status}`))}</span></dd>
      <dt>${escapeHtml(t("col.stock"))}</dt><dd><output id="item-stock">${escapeHtml(item.stock)}</output></dd>
    </dl>
    <p class="actions"><a id="back-to-grid" href="${escapeHtml(gridHref())}">${escapeHtml(t("act.back_grid"))}</a></p>`;
}

// -- audit log -------------------------------------------------------------------------------------

function journalLine(entry) {
  const what = entry.kind === "archive"
    ? t("log.archived")
    : `${t("log.stock")} ${entry.before} → ${entry.after}`;
  return `<li>${escapeHtml(entry.stamp)} — ${escapeHtml(entry.sku)} ${escapeHtml(what)} (${escapeHtml(t(`log.${entry.origin}`))})</li>`;
}

async function showLog() {
  const { ok, body } = await request("GET", "/api/log");
  if (!ok) { page.innerHTML = `<p class="error">${escapeHtml(body.error)}</p>`; return; }
  const lines = body.entries.length
    ? body.entries.map(journalLine).join("")
    : `<li>${escapeHtml(t("log.empty"))}</li>`;
  page.innerHTML = `
    <h1>${escapeHtml(t("title.log"))}</h1>
    <ol id="audit-log" class="journal">${lines}</ol>`;
}

// -- routing -----------------------------------------------------------------------------------------

async function start() {
  renderNav();
  page.innerHTML = `<p class="empty">${escapeHtml(t("msg.loading"))}</p>`;
  const path = location.pathname;
  let found = path.match(/^\/items\/(SKU-\d+)\/adjust$/);
  if (found) { return showAdjust(found[1]); }
  found = path.match(/^\/items\/(SKU-\d+)$/);
  if (found) { return showItem(found[1]); }
  if (path === "/log") { return showLog(); }
  return loadGrid(gridStateFrom(location.search), true);
}

// A page restored from the back/forward cache would show data (and one-time notices) as they
// were when it was left; load it afresh instead.
window.addEventListener("pageshow", (event) => { if (event.persisted) { location.reload(); } });

document.title = t("app.title");
start();
