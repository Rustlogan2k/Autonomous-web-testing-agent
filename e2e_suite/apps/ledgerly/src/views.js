// Ledgerly's pages, rendered on the server as template literals. Every visible label comes
// from the vocabulary through t(); names, amounts and claim numbers are data. Pages work as
// plain forms; static/ui.js adds sorting and filtering, tabs, dialogs and click-to-rename on
// top, and static/claim-editor.js runs the claim editor's repeatable lines.

import { CURRENCIES, money, roundToPence, sterlingHundredths } from "./static/money.js";
import { claimTotalPence, currencySubtotal, editorActions, roleOf, summarise, waitingOn } from "./claims.js";

export const VOCAB = {};

export function t(key) {
  return VOCAB[key] ?? key;
}

export function e(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-06-10T09:00:00" -> "10 Jun 2026, 09:00", read from the string, never a Date. */
export function when(at) {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(at ?? "");
  if (!match) return "";
  const [, year, month, day, hour, minute] = match;
  return `${Number(day)} ${MONTHS[Number(month) - 1]} ${year}, ${hour}:${minute}`;
}

function personName(cfg, id) {
  return cfg.people[id]?.name ?? id;
}

function personWithRole(cfg, id) {
  return `${personName(cfg, id)} (${t(`role.${roleOf(cfg, id)}`)})`;
}

/** "via Pat Reid" when someone acted on another person's behalf. */
function via(cfg, id) {
  return id ? ` ${t("label.via")} ${personName(cfg, id)}` : "";
}

/** Whole units for an editable amount field: 1840 -> "18.40". */
function plainAmount(minor) {
  return `${Math.floor(minor / 100)}.${String(minor % 100).padStart(2, "0")}`;
}

// -- the frame ------------------------------------------------------------------------------

/**
 * The page around `body`. `who` is {user, principal, principals}: the person signed in, the
 * person they act as, and everyone they may act as (a delegate's "Viewing as" choice).
 */
export function layout(cfg, who, title, body, { island = false } = {}) {
  const role = roleOf(cfg, who.principal);
  const links = [
    role === "employee" && ["nav-claims", "/claims", "nav.claims"],
    role === "manager" && ["nav-approvals", "/approvals", "nav.approvals"],
    role === "finance" && ["nav-finance", "/finance", "nav.finance"],
    ["nav-merchants", "/merchants", "nav.merchants"],
    ["nav-cost-centres", "/cost-centres", "nav.cost_centres"],
    ["nav-reports", "/reports", "nav.reports"],
  ].filter(Boolean).map(([id, href, key]) => `<a id="${id}" href="${href}">${e(t(key))}</a>`).join("\n    ");
  const viewing = who.principals.length ? `<form class="viewer" method="post" action="/viewer">
    <label for="viewing-as">${e(t("field.viewing_as"))}</label>
    <select id="viewing-as" name="viewer">${who.principals.map((id) =>
      `<option value="${e(id)}"${id === who.principal ? " selected" : ""}>${e(personName(cfg, id))} — ${e(t(`role.${roleOf(cfg, id)}`))}</option>`).join("")}</select>
    <button id="switch-role" type="submit">${e(t("act.switch_role"))}</button>
  </form>` : "";
  return `<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${e(title)} · ${e(t("app.name"))}</title>
<link rel="icon" href="data:,">
<link rel="stylesheet" href="/static/ledgerly.css">
</head>
<body>
<header class="bar">
  <a class="brand" id="nav-home" href="/">${e(t("app.name"))}</a>
  <nav class="links" aria-label="${e(t("label.sections"))}">
    ${links}
  </nav>
  ${viewing}
  <div class="account">
    <span id="signed-in-as">${e(personName(cfg, who.user))} · ${e(t(`role.${roleOf(cfg, who.user)}`))}</span>
    <button id="nav-account" type="button" aria-haspopup="menu" aria-expanded="false" aria-controls="account-menu">${e(t("nav.account"))}</button>
    <div id="account-menu" class="menu" hidden>
      <form method="post" action="/logout"><button id="sign-out" type="submit">${e(t("act.sign_out"))}</button></form>
    </div>
  </div>
</header>
<main>
${body}
</main>
<div id="dialog-root"></div>
<script type="module" src="/static/ui.js"></script>
${island ? '<script type="module" src="/static/claim-editor.js"></script>' : ""}
</body>
</html>`;
}

/** A dialog's content, shown by ui.js when the control naming it (data-dialog) is used. */
function dialogTemplate(id, titleKey, message, action, confirmId, confirmKey, cancelId) {
  return `<template id="${e(id)}">
  <h2 id="dialog-title">${e(t(titleKey))}</h2>
  <p>${e(message)}</p>
  <form method="post" action="${e(action)}" class="actions">
    <button id="${e(confirmId)}" class="primary" type="submit">${e(t(confirmKey))}</button>
    <button id="${e(cancelId)}" type="button" data-close>${e(t("act.cancel"))}</button>
  </form>
</template>`;
}

function flashBlock(flash, prefix) {
  return `${flash?.notice ? `<p class="notice" id="${prefix}-notice" role="status">${e(t(flash.notice))}${flash.detail ? ` ${e(flash.detail)}` : ""}</p>` : ""}
${flash?.error ? `<p class="error" id="${prefix}-error" role="alert">${e(t(flash.error))}</p>` : ""}`;
}

// -- claims lists ----------------------------------------------------------------------------

function latestAt(claim, types) {
  const entry = [...claim.history].reverse().find((h) => types.includes(h.type));
  return entry ? when(entry.at) : "";
}

const STATUS_FILTERS = ["all", "draft", "awaiting", "sent_back", "approved"];

/** An employee's own claims; ui.js sorts them by total and filters them by status. */
export function myClaims(cfg, principal, claims, flash) {
  const mine = claims.filter((claim) => claim.owner === principal);
  const rows = mine.map((claim, index) => {
    const pence = claimTotalPence(claim.lines, cfg.rates);
    return `<tr data-status="${e(claim.status)}" data-number="${e(claim.id.slice(3))}">
  <td><a id="claim-${e(claim.id)}" href="/claims/${e(claim.id)}">${e(claim.id)}</a></td>
  <td>${e(claim.lines[0]?.description ?? "")}</td>
  <td class="num" id="row-${index + 1}-total" data-pence="${pence}">${money("GBP", pence)}</td>
  <td>${e(t(`status.${claim.status}`))}</td>
</tr>`;
  }).join("\n");
  const filters = STATUS_FILTERS.map((status) =>
    `<button id="filter-${status}" type="button" class="quiet" data-filter="${status}" aria-pressed="${status === "all"}">${e(t(`filter.${status}`))}</button>`).join("\n  ");
  return `<h1>${e(t("home.employee_title"))}</h1>
${flashBlock(flash, "claims")}
<p><a id="new-claim" class="button primary" href="/claims/new">${e(t("act.new_claim"))}</a></p>
<div class="toolbar" data-claims-tools hidden>
  ${filters}
  <button id="sort-total" type="button" class="quiet" data-sort="total">${e(t("act.sort_total"))}</button>
  <button id="sort-number" type="button" class="quiet" data-sort="number">${e(t("act.sort_number"))}</button>
</div>
<p class="muted"><output id="claims-shown">${mine.length} ${e(t("label.of"))} ${mine.length}</output> · <output id="claims-sort">${e(t("sort.newest_last"))}</output></p>
<table id="my-claims" class="grid" data-words="${e(JSON.stringify({ of: t("label.of"), totalUp: t("sort.total_up"), totalDown: t("sort.total_down"), numberUp: t("sort.number_up"), numberDown: t("sort.number_down"), none: t("sort.newest_last") }))}">
<thead><tr><th>${e(t("col.claim"))}</th><th>${e(t("col.first_line"))}</th><th class="num">${e(t("col.total"))}</th><th>${e(t("col.status"))}</th></tr></thead>
<tbody>
${rows || `<tr><td colspan="4">${e(t("home.no_claims"))}</td></tr>`}
</tbody>
</table>`;
}

function queueRows(cfg, claims, pickable) {
  return claims.map((claim) => `<tr>
  ${pickable ? `<td><input type="checkbox" id="pick-${e(claim.id)}" name="ids" value="${e(claim.id)}" data-pick><label for="pick-${e(claim.id)}" class="small">${e(t("field.pick"))}</label></td>` : ""}
  <td><a id="open-${e(claim.id)}" href="/claims/${e(claim.id)}">${e(claim.id)}</a></td>
  <td>${e(personName(cfg, claim.owner))}</td>
  <td>${e(claim.lines[0]?.description ?? "")}</td>
  <td class="num">${money("GBP", claimTotalPence(claim.lines, cfg.rates))}</td>
  <td>${e(latestAt(claim, ["lodged", "resubmitted", "escalated"]))}</td>
</tr>`).join("\n");
}

function queueHead(pickable) {
  return `<thead><tr>${pickable ? `<th><input type="checkbox" id="pick-all" data-pick-all><label for="pick-all" class="small">${e(t("field.pick_all"))}</label></th>` : ""}<th>${e(t("col.claim"))}</th><th>${e(t("col.claimant"))}</th><th>${e(t("col.first_line"))}</th><th class="num">${e(t("col.total"))}</th><th>${e(t("col.since"))}</th></tr></thead>`;
}

export function approvalsPage(cfg, claims, flash) {
  const waiting = claims.filter((claim) => claim.status === "awaiting");
  return `<h1>${e(t("home.manager_title"))}</h1>
${flashBlock(flash, "queue")}
<p class="muted">${e(t("home.manager_limit"))} ${money("GBP", cfg.managerLimit)}</p>
<form method="post" action="/approvals" data-bulk>
<table id="approval-queue" class="grid">
${queueHead(waiting.length > 0)}
<tbody>
${queueRows(cfg, waiting, true) || `<tr><td colspan="5">${e(t("home.queue_empty"))}</td></tr>`}
</tbody>
</table>
${waiting.length ? `<p class="actions"><button id="approve-selected" class="primary" type="submit" name="action" value="approve">${e(t("act.approve_selected"))}</button>
<output id="picked-count">0 ${e(t("label.selected"))}</output></p>` : ""}
</form>`;
}

export function financePage(cfg, claims) {
  const referred = claims.filter((claim) => claim.status === "with_finance");
  return `<h1>${e(t("home.finance_title"))}</h1>
<p class="muted">${e(t("home.finance_lede"))}</p>
<table id="finance-queue" class="grid">
${queueHead(false)}
<tbody>
${queueRows(cfg, referred, false) || `<tr><td colspan="5">${e(t("home.queue_empty"))}</td></tr>`}
</tbody>
</table>`;
}

// -- one claim ------------------------------------------------------------------------------

function historyEntry(cfg, entry) {
  return `${t(`event.${entry.type}`)} — ${personName(cfg, entry.by)}${via(cfg, entry.via)}${entry.note ? `: ${entry.note}` : ""}`;
}

function decisionForm(claim, actions) {
  const buttons = {
    approve: `<button id="approve-claim" class="primary" type="submit" name="action" value="approve">${e(t("act.approve"))}</button>`,
    send_back: `<button id="send-back" type="submit" name="action" value="send_back">${e(t("act.send_back"))}</button>`,
    escalate: `<button id="escalate" type="submit" name="action" value="escalate">${e(t("act.escalate"))}</button>`,
    reject: `<button id="reject-claim" type="submit" name="action" value="reject">${e(t("act.reject"))}</button>`,
  };
  return `<form class="decide" method="post" action="/claims/${e(claim.id)}">
  <label for="decision-note">${e(t("field.decision_note"))}</label>
  <input id="decision-note" name="note" type="text" autocomplete="off">
  <p class="actions">${actions.map((action) => buttons[action]).join("\n    ")}</p>
</form>`;
}

export function claimPage(cfg, principal, claim, flash) {
  const role = roleOf(cfg, principal);
  const lines = claim.lines.map((line) => `<tr>
  <td>${e(line.description)}</td>
  <td>${e(t(`category.${line.category}`))}</td>
  <td class="num">${money(line.currency, line.minor)}</td>
  <td class="num">${money("GBP", roundToPence(sterlingHundredths(line.minor, line.currency, cfg.rates)))}</td>
</tr>`).join("\n");
  const byCurrency = CURRENCIES.filter((code) => claim.lines.some((line) => line.currency === code))
    .map((code) => money(code, currencySubtotal(claim.lines, code))).join(" · ");
  const latest = claim.history[claim.history.length - 1];
  const history = claim.history.map((entry) =>
    `<li>${e(historyEntry(cfg, entry))} <span class="muted">${e(when(entry.at))}</span></li>`).join("\n");
  const next = waitingOn(cfg, claim);
  let actions = "";
  if (role === "employee" && claim.owner === principal && editorActions(claim).length) {
    actions = `<p class="actions"><a id="edit-claim" class="button primary" href="/claims/${e(claim.id)}/edit">${e(t("act.edit_claim"))}</a>
${claim.status === "draft" ? `<button id="discard-claim" type="button" data-dialog="discard-dialog">${e(t("act.discard_claim"))}</button>` : ""}</p>
${claim.status === "draft" ? dialogTemplate("discard-dialog", "dialog.discard_title", `${claim.id}: ${t("dialog.discard_body")}`,
    `/claims/${claim.id}/discard`, "confirm-discard", "act.confirm_discard", "cancel-discard") : ""}`;
  } else if (role === "manager" && claim.status === "awaiting") {
    actions = `<p class="muted">${e(t("home.manager_limit"))} ${money("GBP", cfg.managerLimit)}</p>
${decisionForm(claim, ["approve", "send_back", "escalate"])}`;
  } else if (role === "finance" && claim.status === "with_finance") {
    actions = decisionForm(claim, ["approve", "reject"]);
  }
  return `<h1>${e(t("claim.title"))} ${e(claim.id)}</h1>
${flashBlock(flash, "claim")}
<dl class="facts">
  <dt>${e(t("claim.claimant"))}</dt><dd>${e(personName(cfg, claim.owner))}</dd>
  <dt>${e(t("claim.status"))}</dt><dd><strong id="claim-status">${e(t(`status.${claim.status}`))}</strong></dd>
  <dt>${e(t("claim.waiting_on"))}</dt><dd>${next ? e(personWithRole(cfg, next)) : "—"}</dd>
  <dt>${e(t("claim.approved_by"))}</dt><dd><span id="claim-approver">${claim.approved_by ? e(personWithRole(cfg, claim.approved_by)) : e(t("claim.not_approved"))}</span>${e(via(cfg, claim.approved_via))}</dd>
  <dt>${e(t("claim.total"))}</dt><dd><strong id="claim-total">${money("GBP", claimTotalPence(claim.lines, cfg.rates))}</strong></dd>
  <dt>${e(t("claim.latest"))}</dt><dd><span id="claim-latest">${latest ? e(historyEntry(cfg, latest)) : "—"}</span></dd>
</dl>
${actions}
<nav class="tabs" data-tabs aria-label="${e(t("label.claim_sections"))}">
  <a id="tab-lines" href="#lines" data-tab="lines">${e(t("tab.lines"))}</a>
  <a id="tab-history" href="#history" data-tab="history">${e(t("tab.history"))}</a>
</nav>
<section id="panel-lines" data-panel="lines">
<table id="claim-lines" class="grid">
<thead><tr><th>${e(t("col.description"))}</th><th>${e(t("col.category"))}</th><th class="num">${e(t("col.amount"))}</th><th class="num">${e(t("col.in_sterling"))}</th></tr></thead>
<tbody>
${lines || `<tr><td colspan="4">—</td></tr>`}
</tbody>
</table>
<p class="muted">${e(t("claim.by_currency"))} ${byCurrency || "—"}</p>
</section>
<section id="panel-history" data-panel="history">
<h2>${e(t("claim.history"))}</h2>
<ol id="claim-history" class="history">
${history}
</ol>
</section>
<p><a id="back-to-claims" href="/">${e(t("act.back_to_claims"))}</a></p>`;
}

// -- the editor (the page the client-side island enhances) -----------------------------------

export function rowsFromLines(lines) {
  return lines.map((line, index) => ({
    n: index + 1, description: line.description, category: line.category,
    amount: plainAmount(line.minor), currency: line.currency,
  }));
}

export const BLANK_ROW = Object.freeze({ n: 1, description: "", category: "travel", amount: "", currency: "GBP" });

function lineFieldset(cfg, row, invalid) {
  const n = e(row.n);
  const categories = cfg.categories.map((code) =>
    `<option value="${e(code)}"${code === row.category ? " selected" : ""}>${e(t(`category.${code}`))}</option>`).join("");
  const currencies = CURRENCIES.map((code) =>
    `<option value="${e(code)}"${code === row.currency ? " selected" : ""}>${e(code)}</option>`).join("");
  return `<fieldset class="line" data-line="${n}">
  <legend>${e(t("editor.line"))} <span data-position>${n}</span></legend>
  <div class="field wide"><label for="description-${n}">${e(t("field.description"))}</label>
    <input id="description-${n}" name="description_${n}" type="text" value="${e(row.description)}"></div>
  <div class="field"><label for="category-${n}">${e(t("field.category"))}</label>
    <select id="category-${n}" name="category_${n}">${categories}</select></div>
  <div class="field"><label for="amount-${n}">${e(t("field.amount"))}</label>
    <input id="amount-${n}" name="amount_${n}" type="number" min="0" step="0.01" inputmode="decimal" value="${e(row.amount)}" data-amount${invalid ? ' aria-invalid="true"' : ""}></div>
  <div class="field"><label for="currency-${n}">${e(t("field.currency"))}</label>
    <select id="currency-${n}" name="currency_${n}" data-currency>${currencies}</select></div>
  <small class="hint" data-hint${invalid ? "" : " hidden"}>${e(t("editor.amount_hint"))}</small>
  <button id="remove-line-${n}" class="quiet" type="button" data-remove>${e(t("act.remove_line"))}</button>
</fieldset>`;
}

export function editorPage(cfg, claim, rows, flash) {
  const summary = summarise(rows, cfg.rates);
  const invalidLines = new Set((flash?.errors ?? []).filter((err) => err.key === "msg.line_amount").map((err) => err.line));
  const filledPositions = new Map();
  rows.filter((row) => row.description.trim() || row.amount.trim()).forEach((row, index) => filledPositions.set(row.n, index + 1));
  const fieldsets = rows.map((row) => lineFieldset(cfg, row, invalidLines.has(filledPositions.get(row.n)))).join("\n");
  const errors = (flash?.errors ?? []).map((err) =>
    `<li>${err.line ? `${e(t("editor.line"))} ${err.line}: ` : ""}${e(t(err.key))}</li>`).join("");
  const action = claim ? `/claims/${e(claim.id)}/edit` : "/claims/new";
  const progress = editorActions(claim).filter((name) => name !== "save")[0];
  const progressButton = progress === "resubmit"
    ? `<button id="resubmit-claim" class="primary" type="submit" name="action" value="resubmit">${e(t("act.resubmit"))}</button>`
    : `<button id="lodge-claim" class="primary" type="submit" name="action" value="lodge">${e(t("act.lodge"))}</button>`;
  const saveLabel = claim?.status === "sent_back" ? t("act.save_changes") : t("act.save_draft");
  const nextLine = rows.reduce((max, row) => Math.max(max, row.n), 0) + 1;
  const rates = JSON.stringify(cfg.rates);
  return `<h1>${claim ? `${e(t("editor.edit_title"))} ${e(claim.id)}` : e(t("editor.new_title"))}</h1>
${flash?.notice ? `<p class="notice" id="editor-notice" role="status">${e(t(flash.notice))}</p>` : ""}
${errors ? `<ul class="error" id="editor-errors" role="alert">${errors}</ul>` : ""}
<form id="claim-editor" method="post" action="${action}" autocomplete="off" novalidate
      data-rates="${e(rates)}" data-max-lines="${e(cfg.maxLines)}" data-line-limit="${e(cfg.lineLimit)}" data-next-line="${e(nextLine)}">
<div id="lines">
${fieldsets}
</div>
<p><button id="add-line" class="quiet" type="button">${e(t("act.add_line"))}</button></p>
<section class="summary" aria-live="polite">
  <h2>${e(t("summary.title"))}</h2>
  <dl>
    <dt>${e(t("summary.gbp"))}</dt><dd><output id="sum-gbp">${money("GBP", summary.GBP)}</output></dd>
    <dt>${e(t("summary.eur"))}</dt><dd><output id="sum-eur">${money("EUR", summary.EUR)}</output></dd>
    <dt>${e(t("summary.usd"))}</dt><dd><output id="sum-usd">${money("USD", summary.USD)}</output></dd>
    <dt>${e(t("summary.total"))}</dt><dd><output id="sum-total">${money("GBP", summary.total)}</output></dd>
  </dl>
  <p class="muted">${e(t("summary.rates"))} ${CURRENCIES.filter((code) => code !== "GBP")
    .map((code) => `${money(code, 100)} = ${money("GBP", cfg.rates[code])}`).join(" · ")}</p>
</section>
<p class="actions">
  <button id="save-draft" type="submit" name="action" value="save">${e(saveLabel)}</button>
  ${progressButton}
  <button id="save-template" class="quiet" type="submit" name="action" value="template">${e(t("act.save_template"))}</button>
  <button id="attach-receipt" class="quiet" type="button" aria-controls="receipt-panel" aria-expanded="false">${e(t("act.attach_receipt"))}</button>
</p>
<aside id="receipt-panel" class="panel" hidden>
  <h2>${e(t("receipt.title"))}</h2>
  <p>${e(t("receipt.body"))}</p>
</aside>
</form>
<template id="line-template">
${lineFieldset(cfg, { ...BLANK_ROW, n: "__N__" }, false)}
</template>
<p><a id="back-to-claims" href="/">${e(t("act.back_to_claims"))}</a></p>`;
}

// -- merchants ------------------------------------------------------------------------------

const MERCHANT_SORTS = ["name", "name-desc", "city"];

/** The address of a merchants list page: the query is in the URL, so Back and Forward work. */
export function merchantsHref(query, page) {
  const params = new URLSearchParams();
  if (query.q) params.set("q", query.q);
  if (query.category) params.set("category", query.category);
  if (query.sort !== "name") params.set("sort", query.sort);
  if (page > 1) params.set("page", String(page));
  const text = params.toString();
  return text ? `/merchants?${text}` : "/merchants";
}

export function merchantsPage(cfg, query, result, admin, flash) {
  const rows = result.rows.map((m) => `<tr>
  <td><a id="merchant-${e(m.id)}" href="/merchants/${e(m.id)}">${e(m.name)}</a></td>
  <td>${e(m.city)}</td>
  <td>${e(t(`category.${m.category}`))}</td>
  <td>${m.preferred ? e(t("label.preferred")) : ""}</td>
</tr>`).join("\n");
  const categories = ["", ...cfg.categories].map((code) =>
    `<option value="${e(code)}"${code === query.category ? " selected" : ""}>${e(code ? t(`category.${code}`) : t("label.all_categories"))}</option>`).join("");
  const sorts = MERCHANT_SORTS.map((code) =>
    `<option value="${code}"${code === query.sort ? " selected" : ""}>${e(t(`sort.${code.replace("-", "_")}`))}</option>`).join("");
  return `<h1>${e(t("merchants.title"))}</h1>
${flashBlock(flash, "merchants")}
${admin ? `<p><a id="new-merchant" class="button primary" href="/merchants/new">${e(t("act.new_merchant"))}</a></p>` : ""}
<form class="filters" method="get" action="/merchants" role="search">
  <div class="field"><label for="merchants-q">${e(t("field.search"))}</label>
    <input id="merchants-q" name="q" type="search" value="${e(query.q)}"></div>
  <button id="merchants-search" type="submit">${e(t("act.search"))}</button>
  <div class="field"><label for="merchants-category">${e(t("field.category"))}</label>
    <select id="merchants-category" name="category" data-autosubmit>${categories}</select></div>
  <div class="field"><label for="merchants-sort">${e(t("field.sort"))}</label>
    <select id="merchants-sort" name="sort" data-autosubmit>${sorts}</select></div>
</form>
<p class="muted"><output id="merchants-total">${result.total} ${e(t("label.merchants"))}</output></p>
<table id="merchants-list" class="grid">
<thead><tr><th>${e(t("col.merchant"))}</th><th>${e(t("col.city"))}</th><th>${e(t("col.category"))}</th><th></th></tr></thead>
<tbody>
${rows || `<tr><td colspan="4">${e(t("merchants.none"))}</td></tr>`}
</tbody>
</table>
<nav class="pager" aria-label="${e(t("label.pages"))}">
  ${query.page > 1 ? `<a id="merchants-prev" href="${e(merchantsHref(query, query.page - 1))}">${e(t("act.previous"))}</a>` : ""}
  <span>${e(t("label.page"))} <output id="merchants-page">${query.page}</output> ${e(t("label.of"))} <output id="merchants-pages">${result.pages}</output></span>
  ${query.page < result.pages ? `<a id="merchants-next" href="${e(merchantsHref(query, query.page + 1))}">${e(t("act.next"))}</a>` : ""}
</nav>`;
}

export function merchantPage(cfg, merchant, admin, flash) {
  return `<h1 id="merchant-name">${e(merchant.name)}</h1>
${flashBlock(flash, "merchant")}
<dl class="facts">
  <dt>${e(t("col.city"))}</dt><dd><span id="merchant-city">${e(merchant.city)}</span></dd>
  <dt>${e(t("col.category"))}</dt><dd>${e(t(`category.${merchant.category}`))}</dd>
  <dt>${e(t("label.preferred"))}</dt><dd>${e(merchant.preferred ? t("label.yes") : t("label.no"))}</dd>
</dl>
<p class="actions">
  ${admin ? `<a id="edit-merchant" class="button primary" href="/merchants/${e(merchant.id)}/edit">${e(t("act.edit_merchant"))}</a>` : ""}
  <a id="back-to-merchants" href="/merchants">${e(t("act.back_to_merchants"))}</a>
</p>`;
}

export function merchantForm(cfg, merchant, values, problems) {
  const categories = cfg.categories.map((code) =>
    `<option value="${e(code)}"${code === values.category ? " selected" : ""}>${e(t(`category.${code}`))}</option>`).join("");
  const action = merchant ? `/merchants/${merchant.id}/edit` : "/merchants/new";
  return `<h1>${e(merchant ? `${t("merchants.edit_title")} ${merchant.name}` : t("merchants.new_title"))}</h1>
${problems.length ? `<ul class="error" id="merchant-errors" role="alert">${problems.map((key) => `<li>${e(t(key))}</li>`).join("")}</ul>` : ""}
<form class="stack" method="post" action="${e(action)}">
  <label for="merchant-name-field">${e(t("field.merchant_name"))}</label>
  <input id="merchant-name-field" name="name" type="text" value="${e(values.name)}">
  <label for="merchant-city-field">${e(t("field.city"))}</label>
  <input id="merchant-city-field" name="city" type="text" value="${e(values.city)}">
  <label for="merchant-category-field">${e(t("field.category"))}</label>
  <select id="merchant-category-field" name="category">${categories}</select>
  <p><input id="merchant-preferred-field" name="preferred" type="checkbox" value="yes"${values.preferred ? " checked" : ""}>
    <label for="merchant-preferred-field">${e(t("field.preferred"))}</label></p>
  <p class="actions"><button id="save-merchant" class="primary" type="submit">${e(t("act.save_merchant"))}</button>
  ${merchant ? `<button id="delete-merchant" type="button" data-dialog="delete-merchant-dialog">${e(t("act.delete_merchant"))}</button>` : ""}</p>
</form>
${merchant ? dialogTemplate("delete-merchant-dialog", "dialog.delete_title", merchant.name, `/merchants/${merchant.id}/delete`,
    "confirm-delete", "act.confirm_delete", "cancel-delete") : ""}
<p><a id="back-to-merchants" href="/merchants">${e(t("act.back_to_merchants"))}</a></p>`;
}

// -- cost centres ---------------------------------------------------------------------------

export function costCentresPage(cfg, centres, spentBy, admin, flash) {
  const rows = centres.map((centre) => {
    const name = admin
      ? `<button id="cc-name-${e(centre.code)}" type="button" class="link" data-rename="${e(centre.code)}">${e(centre.name)}</button>`
      : e(centre.name);
    const spent = spentBy.get(centre.code) ?? 0;
    return `<tr data-centre="${e(centre.code)}">
  <td>${e(centre.code)}</td>
  <td data-name-cell>${name}</td>
  <td class="num">${money("GBP", centre.budget_pence)}</td>
  <td class="num">${money("GBP", spent)}</td>
  ${admin ? `<td><button id="delete-${e(centre.code)}" type="button" class="quiet" data-dialog="delete-${e(centre.code)}-dialog">${e(t("act.delete_centre"))}</button></td>` : ""}
</tr>`;
  }).join("\n");
  const add = admin ? `<h2>${e(t("centres.add_title"))}</h2>
<form class="inline-form" method="post" action="/cost-centres">
  <div class="field"><label for="cc-new-name">${e(t("field.centre_name"))}</label><input id="cc-new-name" name="name" type="text"></div>
  <div class="field"><label for="cc-new-budget">${e(t("field.budget"))}</label><input id="cc-new-budget" name="budget" type="number" min="0" step="0.01"></div>
  <button id="add-cost-centre" type="submit">${e(t("act.add_centre"))}</button>
</form>` : "";
  return `<h1>${e(t("centres.title"))}</h1>
${flashBlock(flash, "centres")}
<p class="notice" id="cc-message" role="status" hidden></p>
<table id="cost-centre-list" class="grid" data-renames="/cost-centres" data-rename-label="${e(t("field.rename"))}"
  data-save-label="${e(t("act.save_name"))}" data-cancel-label="${e(t("act.cancel"))}" data-saved-label="${e(t("msg.centre_renamed"))}" data-failed="${e(t("msg.try_again"))}">
<thead><tr><th>${e(t("col.code"))}</th><th>${e(t("col.name"))}</th><th class="num">${e(t("col.budget"))}</th><th class="num">${e(t("col.spent"))}</th>${admin ? "<th></th>" : ""}</tr></thead>
<tbody>
${rows}
</tbody>
</table>
${admin ? centres.map((centre) => dialogTemplate(`delete-${centre.code}-dialog`, "dialog.delete_title",
    `${centre.code} ${centre.name}`, `/cost-centres/${centre.code}/delete`, "confirm-delete", "act.confirm_delete",
    "cancel-delete")).join("\n") : ""}
${add}`;
}

// -- reports --------------------------------------------------------------------------------

export function reportsPage(cfg, byCategory, byCentre) {
  const categoryRows = cfg.categories.map((code) => `<tr><td>${e(t(`category.${code}`))}</td>
  <td class="num">${money("GBP", byCategory.get(code) ?? 0)}</td></tr>`).join("\n");
  const centreRows = byCentre.map((row) => `<tr><td>${e(row.name)}</td>
  <td class="num">${money("GBP", row.budget)}</td><td class="num">${money("GBP", row.spent)}</td>
  <td class="num">${money("GBP", row.budget - row.spent)}</td></tr>`).join("\n");
  return `<h1>${e(t("reports.title"))}</h1>
<p class="muted">${e(t("reports.lede"))}</p>
<h2>${e(t("reports.by_category"))}</h2>
<table id="report-categories" class="grid">
<thead><tr><th>${e(t("col.category"))}</th><th class="num">${e(t("col.approved"))}</th></tr></thead>
<tbody>
${categoryRows}
</tbody>
</table>
<h2>${e(t("reports.by_centre"))}</h2>
<table id="report-centres" class="grid">
<thead><tr><th>${e(t("col.name"))}</th><th class="num">${e(t("col.budget"))}</th><th class="num">${e(t("col.approved"))}</th><th class="num">${e(t("col.remaining"))}</th></tr></thead>
<tbody>
${centreRows}
</tbody>
</table>`;
}

// -- small pages ----------------------------------------------------------------------------

export function infoPage(titleKey, bodyKey) {
  return `<h1>${e(t(titleKey))}</h1>
<p>${e(t(bodyKey))}</p>
<p><a id="back-to-claims" href="/">${e(t("act.back_to_claims"))}</a></p>`;
}

/** A page this person may not open: an ordinary page (HTTP 200) that says so. */
export function accessNotice() {
  return `<h1>${e(t("notice.title"))}</h1>
<p id="access-notice">${e(t("notice.body"))}</p>
<p><a id="notice-home" href="/">${e(t("act.back_to_claims"))}</a></p>`;
}
