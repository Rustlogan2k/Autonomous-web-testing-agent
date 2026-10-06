// Ledgerly's pages, rendered on the server as template literals. Every visible label comes
// from the vocabulary through t(); names, amounts and claim numbers are data.

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

/** Whole units for an editable amount field: 1840 -> "18.40". */
function plainAmount(minor) {
  return `${Math.floor(minor / 100)}.${String(minor % 100).padStart(2, "0")}`;
}

// -- the frame ------------------------------------------------------------------------------

export function layout(cfg, viewer, title, body, { island = false } = {}) {
  const options = Object.keys(cfg.people).map((id) =>
    `<option value="${e(id)}"${id === viewer ? " selected" : ""}>${e(personName(cfg, id))} — ${e(t(`role.${roleOf(cfg, id)}`))}</option>`).join("");
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
  <form class="viewer" method="post" action="/viewer">
    <label for="viewing-as">${e(t("field.viewing_as"))}</label>
    <select id="viewing-as" name="viewer">${options}</select>
    <button id="switch-role" type="submit">${e(t("act.switch_role"))}</button>
  </form>
</header>
<main>
${body}
</main>
${island ? '<script type="module" src="/static/claim-editor.js"></script>' : ""}
</body>
</html>`;
}

// -- home pages, one per role ---------------------------------------------------------------

function latestAt(claim, types) {
  const entry = [...claim.history].reverse().find((h) => types.includes(h.type));
  return entry ? when(entry.at) : "";
}

export function employeeHome(cfg, state) {
  const mine = state.claims.filter((claim) => claim.owner === state.viewer);
  const rows = mine.map((claim) => `<tr>
  <td><a id="claim-${e(claim.id)}" href="/claims/${e(claim.id)}">${e(claim.id)}</a></td>
  <td>${e(claim.lines[0]?.description ?? "")}</td>
  <td class="num">${money("GBP", claimTotalPence(claim.lines, cfg.rates))}</td>
  <td>${e(t(`status.${claim.status}`))}</td>
</tr>`).join("\n");
  return `<h1>${e(t("home.employee_title"))}</h1>
<p><a id="new-claim" class="button primary" href="/claims/new">${e(t("act.new_claim"))}</a></p>
<table id="my-claims" class="grid">
<thead><tr><th>${e(t("col.claim"))}</th><th>${e(t("col.first_line"))}</th><th class="num">${e(t("col.total"))}</th><th>${e(t("col.status"))}</th></tr></thead>
<tbody>
${rows || `<tr><td colspan="4">${e(t("home.no_claims"))}</td></tr>`}
</tbody>
</table>`;
}

function queueTable(cfg, id, claims, emptyKey) {
  const rows = claims.map((claim) => `<tr>
  <td><a id="open-${e(claim.id)}" href="/claims/${e(claim.id)}">${e(claim.id)}</a></td>
  <td>${e(personName(cfg, claim.owner))}</td>
  <td>${e(claim.lines[0]?.description ?? "")}</td>
  <td class="num">${money("GBP", claimTotalPence(claim.lines, cfg.rates))}</td>
  <td>${e(latestAt(claim, ["lodged", "resubmitted", "escalated"]))}</td>
</tr>`).join("\n");
  return `<table id="${e(id)}" class="grid">
<thead><tr><th>${e(t("col.claim"))}</th><th>${e(t("col.claimant"))}</th><th>${e(t("col.first_line"))}</th><th class="num">${e(t("col.total"))}</th><th>${e(t("col.since"))}</th></tr></thead>
<tbody>
${rows || `<tr><td colspan="5">${e(t(emptyKey))}</td></tr>`}
</tbody>
</table>`;
}

export function managerHome(cfg, state) {
  const waiting = state.claims.filter((claim) => claim.status === "awaiting");
  return `<h1>${e(t("home.manager_title"))}</h1>
<p class="muted">${e(t("home.manager_limit"))} ${money("GBP", cfg.managerLimit)}</p>
${queueTable(cfg, "approval-queue", waiting, "home.queue_empty")}`;
}

export function financeHome(cfg, state) {
  const referred = state.claims.filter((claim) => claim.status === "with_finance");
  return `<h1>${e(t("home.finance_title"))}</h1>
<p class="muted">${e(t("home.finance_lede"))}</p>
${queueTable(cfg, "finance-queue", referred, "home.queue_empty")}`;
}

// -- one claim ------------------------------------------------------------------------------

function historyEntry(cfg, entry) {
  return `${t(`event.${entry.type}`)} — ${personName(cfg, entry.by)}${entry.note ? `: ${entry.note}` : ""}`;
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

export function claimPage(cfg, state, claim, flash) {
  const role = roleOf(cfg, state.viewer);
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
  if (role === "employee" && claim.owner === state.viewer && editorActions(claim).length) {
    actions = `<p class="actions"><a id="edit-claim" class="button primary" href="/claims/${e(claim.id)}/edit">${e(t("act.edit_claim"))}</a></p>`;
  } else if (role === "manager" && claim.status === "awaiting") {
    actions = `<p class="muted">${e(t("home.manager_limit"))} ${money("GBP", cfg.managerLimit)}</p>
${decisionForm(claim, ["approve", "send_back", "escalate"])}`;
  } else if (role === "finance" && claim.status === "with_finance") {
    actions = decisionForm(claim, ["approve", "reject"]);
  }
  return `<h1>${e(t("claim.title"))} ${e(claim.id)}</h1>
${flash?.notice ? `<p class="notice" id="claim-notice" role="status">${e(t(flash.notice))}</p>` : ""}
${flash?.error ? `<p class="error" id="claim-error" role="alert">${e(t(flash.error))}</p>` : ""}
<dl class="facts">
  <dt>${e(t("claim.claimant"))}</dt><dd>${e(personName(cfg, claim.owner))}</dd>
  <dt>${e(t("claim.status"))}</dt><dd><strong id="claim-status">${e(t(`status.${claim.status}`))}</strong></dd>
  <dt>${e(t("claim.waiting_on"))}</dt><dd>${next ? e(personWithRole(cfg, next)) : "—"}</dd>
  <dt>${e(t("claim.approved_by"))}</dt><dd><span id="claim-approver">${claim.approved_by ? e(personWithRole(cfg, claim.approved_by)) : e(t("claim.not_approved"))}</span></dd>
  <dt>${e(t("claim.total"))}</dt><dd><strong id="claim-total">${money("GBP", claimTotalPence(claim.lines, cfg.rates))}</strong></dd>
</dl>
<table id="claim-lines" class="grid">
<thead><tr><th>${e(t("col.description"))}</th><th>${e(t("col.category"))}</th><th class="num">${e(t("col.amount"))}</th><th class="num">${e(t("col.in_sterling"))}</th></tr></thead>
<tbody>
${lines || `<tr><td colspan="4">—</td></tr>`}
</tbody>
</table>
<p class="muted">${e(t("claim.by_currency"))} ${byCurrency || "—"}</p>
${actions}
<h2>${e(t("claim.history"))}</h2>
<p>${e(t("claim.latest"))} <span id="claim-latest">${latest ? e(historyEntry(cfg, latest)) : "—"}</span></p>
<ol id="claim-history" class="history">
${history}
</ol>
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

// -- small pages ----------------------------------------------------------------------------

export function infoPage(titleKey, bodyKey) {
  return `<h1>${e(t(titleKey))}</h1>
<p>${e(t(bodyKey))}</p>
<p><a id="back-to-claims" href="/">${e(t("act.back_to_claims"))}</a></p>`;
}
