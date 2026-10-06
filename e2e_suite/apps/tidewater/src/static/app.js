/* Tidewater — single-page banking client. Hash routes, JSON API, no dependencies. */
"use strict";

let VOCAB = {};
const t = (key) => VOCAB[key] || key;

const esc = (value) => String(value ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, options = {}) {
  const response = await fetch(path, {
    method: options.method || "GET",
    headers: options.body ? { "Content-Type": "application/json" } : {},
    body: options.body ? JSON.stringify(options.body) : undefined,
    credentials: "same-origin",
  });
  const data = await response.json();
  return { ok: response.ok, status: response.status, data };
}

/* The transfer form reads payees from this cache so moving between wizard steps does not
   refetch them; anything that changes a payee must drop it. */
let payeeCache = null;
async function getPayees() {
  if (!payeeCache) {
    payeeCache = (await api("/api/payees")).data;
  }
  return payeeCache;
}

let wizard = null;
function freshWizard() {
  wizard = { draftId: null, from: "everyday", to: "payee:landlord", amount: "", reference: "",
             when: "now", remind: false, quote: null, result: null, error: "" };
}
freshWizard();

const view = () => document.getElementById("view");
const go = (hash) => { if (location.hash === hash) { route(); } else { location.hash = hash; } };

function renderNav() {
  document.getElementById("main-nav").innerHTML = `
    <a id="nav-accounts" href="#/">${esc(t("nav.accounts"))}</a>
    <button id="nav-move" type="button">${esc(t("act.move"))}</button>
    <a id="nav-payees" href="#/payees">${esc(t("nav.payees"))}</a>
    <a id="nav-statements" href="#/statements">${esc(t("nav.statements"))}</a>`;
  document.getElementById("nav-move").addEventListener("click", () => { freshWizard(); go("#/move"); });
}

function steps(current) {
  const names = [["accounts", t("step.accounts")], ["amount", t("step.amount")], ["review", t("step.review")]];
  return `<ol class="steps">${names.map(([key, label], i) =>
    `<li${key === current ? ' aria-current="step"' : ""}>${i + 1}. ${esc(label)}</li>`).join("")}</ol>`;
}

// -- dashboard ------------------------------------------------------------------------------

async function showDashboard() {
  const { data } = await api("/api/overview");
  const cards = data.accounts.map((a) => `
    <li class="card">
      <h2>${esc(a.name)}</h2>
      <span class="muted">${esc(a.number)}</span>
      <strong class="balance" id="balance-${esc(a.id)}">${esc(a.balance_label)}</strong>
      <span class="muted" id="scheduled-${esc(a.id)}">${esc(t("label.scheduled_out"))}: ${esc(a.scheduled_label)}</span>
    </li>`).join("");
  const rows = data.scheduled.length ? data.scheduled.map((s) => `
    <tr><td>${esc(s.date)}</td><td>${esc(s.from)}</td><td>${esc(s.to)}</td><td>${esc(s.amount)}</td>
      <td><button id="cancel-${s.id}" type="button" data-id="${s.id}">${esc(t("act.cancel_scheduled"))}</button></td></tr>`).join("")
    : `<tr><td colspan="5" class="muted">${esc(t("msg.no_scheduled"))}</td></tr>`;
  view().innerHTML = `
    <h1>${esc(t("title.accounts"))}</h1>
    <p class="muted">${esc(t("label.today"))} ${esc(data.today)}</p>
    <ul class="cards" id="account-list">${cards}</ul>
    <div class="actions"><button id="refresh-balances" type="button">${esc(t("act.refresh"))}</button></div>
    <section class="panel" id="scheduled-panel">
      <h2>${esc(t("title.scheduled"))}</h2>
      <table id="scheduled-table"><thead><tr><th>${esc(t("col.date"))}</th><th>${esc(t("col.from"))}</th>
        <th>${esc(t("col.to"))}</th><th>${esc(t("col.amount"))}</th><th></th></tr></thead><tbody>${rows}</tbody></table>
    </section>
    <aside class="panel promo">
      <h2>${esc(t("promo.title"))}</h2><p>${esc(t("promo.body"))}</p>
      <button id="promo-continue" type="button">${esc(t("act.promo"))}</button>
    </aside>`;
  document.getElementById("refresh-balances").addEventListener("click", () => showDashboard());
  document.getElementById("promo-continue").addEventListener("click", () => go("#/offers"));
  view().querySelectorAll("button[data-id]").forEach((button) => button.addEventListener("click", async () => {
    await api(`/api/transfers/${button.dataset.id}/cancel`, { method: "POST" });
    showDashboard();
  }));
}

function showOffers() {
  view().innerHTML = `
    <h1>${esc(t("promo.title"))}</h1>
    <p>${esc(t("promo.detail"))}</p>
    <p class="muted">${esc(t("promo.small_print"))}</p>
    <a id="back-to-accounts" href="#/">${esc(t("act.back_accounts"))}</a>`;
}

// -- move money wizard ---------------------------------------------------------------------

async function showMoveAccounts() {
  const { payees } = await getPayees();
  const { data } = await api("/api/overview");
  const fromOptions = data.accounts.map((a) =>
    `<option value="${esc(a.id)}"${a.id === wizard.from ? " selected" : ""}>${esc(a.name)} (${esc(a.balance_label)})</option>`).join("");
  const targets = payees.map((p) => [`payee:${p.id}`, p.nickname])
    .concat(data.accounts.map((a) => [`acct:${a.id}`, `${a.name} — ${t("label.own_account")}`]));
  const toOptions = targets.map(([value, label]) =>
    `<option value="${esc(value)}"${value === wizard.to ? " selected" : ""}>${esc(label)}</option>`).join("");
  view().innerHTML = `
    <h1>${esc(t("act.move"))}</h1>${steps("accounts")}
    <section class="panel">
      <label for="from-account">${esc(t("field.from"))}</label>
      <select id="from-account" name="from_account">${fromOptions}</select>
      <label for="to-target">${esc(t("field.to"))}</label>
      <select id="to-target" name="to_target">${toOptions}</select>
      <p class="error" id="move-error" role="alert">${esc(wizard.error)}</p>
      <div class="actions">
        <button id="to-amount" class="primary" type="button">${esc(t("act.to_amount"))}</button>
        <a id="cancel-move" href="#/">${esc(t("act.cancel_move"))}</a>
      </div>
    </section>`;
  document.getElementById("from-account").addEventListener("change", (e) => { wizard.from = e.target.value; });
  document.getElementById("to-target").addEventListener("change", (e) => { wizard.to = e.target.value; });
  document.getElementById("to-amount").addEventListener("click", async () => {
    const body = { from: wizard.from, to: wizard.to };
    const result = wizard.draftId
      ? await api(`/api/drafts/${wizard.draftId}`, { method: "PUT", body })
      : await api("/api/drafts", { method: "POST", body });
    if (!result.ok) { wizard.error = result.data.error; return showMoveAccounts(); }
    wizard.draftId = result.data.id;
    wizard.error = "";
    go("#/move/amount");
  });
}

async function showMoveAmount() {
  if (!wizard.draftId) { return go("#/move"); }
  const whenOptions = [["now", t("when.now")], ["next-friday", t("when.next_friday")], ["month-end", t("when.month_end")]]
    .map(([value, label]) => `<option value="${value}"${value === wizard.when ? " selected" : ""}>${esc(label)}</option>`).join("");
  const remind = wizard.when !== "now" ? `
    <p class="check" id="remind-row"><input id="remind" name="remind" type="checkbox"${wizard.remind ? " checked" : ""}>
      <label for="remind">${esc(t("field.remind"))}</label></p>` : "";
  view().innerHTML = `
    <h1>${esc(t("act.move"))}</h1>${steps("amount")}
    <section class="panel">
      <label for="amount">${esc(t("field.amount"))}</label>
      <input id="amount" name="amount" type="number" step="0.01" min="0" value="${esc(wizard.amount)}">
      <label for="reference">${esc(t("field.reference"))}</label>
      <input id="reference" name="reference" type="text" value="${esc(wizard.reference)}">
      <label for="when">${esc(t("field.when"))}</label>
      <select id="when" name="when">${whenOptions}</select>
      ${remind}
      <p class="error" id="amount-error" role="alert">${esc(wizard.error)}</p>
      <div class="actions">
        <button id="change-accounts" type="button">${esc(t("act.change_accounts"))}</button>
        <button id="review-transfer" class="primary" type="button">${esc(t("act.review"))}</button>
      </div>
    </section>`;
  document.getElementById("amount").addEventListener("input", (e) => { wizard.amount = e.target.value; });
  document.getElementById("reference").addEventListener("input", (e) => { wizard.reference = e.target.value; });
  document.getElementById("when").addEventListener("change", (e) => { wizard.when = e.target.value; showMoveAmount(); });
  const remindBox = document.getElementById("remind");
  if (remindBox) { remindBox.addEventListener("change", (e) => { wizard.remind = e.target.checked; }); }
  document.getElementById("change-accounts").addEventListener("click", () => { wizard.error = ""; go("#/move"); });
  document.getElementById("review-transfer").addEventListener("click", async () => {
    const result = await api(`/api/drafts/${wizard.draftId}/quote`, {
      method: "POST", body: { amount: wizard.amount, reference: wizard.reference, when: wizard.when } });
    if (!result.ok) { wizard.error = result.data.error; return showMoveAmount(); }
    wizard.quote = result.data;
    wizard.error = "";
    go("#/move/review");
  });
}

function showMoveReview() {
  const q = wizard.quote;
  if (!q) { return go("#/move"); }
  view().innerHTML = `
    <h1>${esc(t("act.move"))}</h1>${steps("review")}
    <section class="panel">
      <dl class="summary">
        <dt>${esc(t("field.from"))}</dt><dd><strong id="review-from">${esc(q.from)}</strong></dd>
        <dt>${esc(t("field.to"))}</dt><dd><strong id="review-to">${esc(q.to)}</strong></dd>
        <dt>${esc(t("field.amount"))}</dt><dd><strong id="review-amount">${esc(q.amount)}</strong></dd>
        <dt>${esc(t("field.when"))}</dt><dd><strong id="review-when">${esc(q.date)}</strong></dd>
        <dt>${esc(t("field.reference"))}</dt><dd><span id="review-reference">${esc(q.reference || "—")}</span></dd>
        <dt>${esc(t("label.available_after"))}</dt><dd><output id="review-after">${esc(q.available_after)}</output></dd>
      </dl>
      <p class="error" id="review-error" role="alert">${esc(wizard.error)}</p>
      <div class="actions">
        <button id="change-details" type="button">${esc(t("act.change_details"))}</button>
        <button id="authorise" class="primary" type="button">${esc(t("act.authorise"))}</button>
      </div>
    </section>`;
  document.getElementById("change-details").addEventListener("click", () => { wizard.error = ""; go("#/move/amount"); });
  document.getElementById("authorise").addEventListener("click", async () => {
    const result = await api(`/api/quotes/${q.id}/authorise`, { method: "POST" });
    if (!result.ok) { wizard.error = result.data.error; return showMoveReview(); }
    wizard.result = result.data;
    wizard.error = "";
    go(result.data.status === "scheduled" ? "#/move/scheduled" : "#/move/done");
  });
}

function showMoveResult(kind) {
  const r = wizard.result;
  if (!r) { return go("#/"); }
  view().innerHTML = kind === "done" ? `
    <h1 id="done-title">${esc(t("title.sent"))}</h1>
    <p id="done-summary">${esc(r.amount)} ${esc(t("label.to"))} ${esc(r.to)} ${esc(t("label.from"))} ${esc(r.from)}</p>
    <a id="done-accounts" href="#/">${esc(t("act.back_accounts"))}</a>` : `
    <h1 id="scheduled-title">${esc(t("title.scheduled_one"))}</h1>
    <p id="scheduled-summary">${esc(r.amount)} ${esc(t("label.to"))} ${esc(r.to)} ${esc(t("label.on"))} <strong id="scheduled-date">${esc(r.date)}</strong></p>
    <a id="scheduled-accounts" href="#/">${esc(t("act.back_accounts"))}</a>`;
}

// -- payees ----------------------------------------------------------------------------------

async function showPayees() {
  const { data } = await api("/api/payees");
  const rows = data.payees.map((p) => `
    <tr><td>${esc(p.nickname)}</td><td>${esc(p.bank)}</td><td>${esc(p.account_number)}</td>
      <td><a id="edit-${esc(p.id)}" href="#/payees/${esc(p.id)}">${esc(t("act.edit"))}</a></td></tr>`).join("");
  view().innerHTML = `
    <h1>${esc(t("nav.payees"))}</h1>
    <table id="payee-table"><thead><tr><th>${esc(t("col.name"))}</th><th>${esc(t("col.bank"))}</th>
      <th>${esc(t("col.account"))}</th><th></th></tr></thead><tbody>${rows}</tbody></table>
    <div class="actions"><button id="add-payee" class="primary" type="button">${esc(t("act.add_payee"))}</button></div>`;
  document.getElementById("add-payee").addEventListener("click", () => openPayeeModal(data.banks));
}

function openPayeeModal(banks) {
  const state = { name: "", bank: banks[0], number: "", stage: "form", error: "" };
  const root = document.getElementById("modal-root");
  const close = () => { root.innerHTML = ""; };
  const render = () => {
    const body = state.stage === "form" ? `
      <label for="new-payee-name">${esc(t("field.payee_name"))}</label>
      <input id="new-payee-name" name="payee_name" type="text" value="${esc(state.name)}">
      <label for="new-payee-bank">${esc(t("field.bank"))}</label>
      <select id="new-payee-bank" name="bank">${banks.map((b) =>
        `<option${b === state.bank ? " selected" : ""}>${esc(b)}</option>`).join("")}</select>
      <label for="new-payee-number">${esc(t("field.account_number"))}</label>
      <input id="new-payee-number" name="account_number" type="number" value="${esc(state.number)}">
      <p class="error" id="payee-error" role="alert">${esc(state.error)}</p>
      <div class="actions">
        <button id="close-payee-modal" type="button">${esc(t("act.close"))}</button>
        <button id="check-payee" class="primary" type="button">${esc(t("act.check_payee"))}</button>
      </div>` : `
      <p id="payee-confirm-text">${esc(t("msg.confirm_payee"))} <strong>${esc(state.name)}</strong>,
        ${esc(state.bank)}, ${esc(state.number)}</p>
      <p class="error" id="payee-error" role="alert">${esc(state.error)}</p>
      <div class="actions">
        <button id="edit-payee-details" type="button">${esc(t("act.edit_payee_details"))}</button>
        <button id="confirm-payee" class="primary" type="button">${esc(t("act.confirm_payee"))}</button>
      </div>`;
    root.innerHTML = `<div class="modal-backdrop"><div class="modal" role="dialog" aria-modal="true"
      aria-labelledby="payee-modal-title" id="payee-modal"><h2 id="payee-modal-title">${esc(t("title.new_payee"))}</h2>${body}</div></div>`;
    if (state.stage === "form") {
      document.getElementById("new-payee-name").addEventListener("input", (e) => { state.name = e.target.value; });
      document.getElementById("new-payee-bank").addEventListener("change", (e) => { state.bank = e.target.value; });
      document.getElementById("new-payee-number").addEventListener("input", (e) => { state.number = e.target.value; });
      document.getElementById("close-payee-modal").addEventListener("click", close);
      document.getElementById("check-payee").addEventListener("click", () => {
        if (!state.name.trim()) { state.error = t("msg.payee_name_needed"); return render(); }
        if (!/^\d{2,8}$/.test(state.number.trim())) { state.error = t("msg.account_number_rule"); return render(); }
        state.error = ""; state.stage = "confirm"; render();
      });
    } else {
      document.getElementById("edit-payee-details").addEventListener("click", () => { state.stage = "form"; render(); });
      document.getElementById("confirm-payee").addEventListener("click", async () => {
        const result = await api("/api/payees", { method: "POST",
          body: { nickname: state.name, bank: state.bank, account_number: state.number } });
        if (!result.ok) { state.error = result.data.error; state.stage = "form"; return render(); }
        payeeCache = null;
        close();
        showPayees();
      });
    }
  };
  render();
}

async function showPayee(id) {
  const { data } = await api("/api/payees");
  const payee = data.payees.find((p) => p.id === id);
  if (!payee) { return go("#/payees"); }
  view().innerHTML = `
    <h1>${esc(payee.nickname)}</h1>
    <section class="panel">
      <p class="muted">${esc(payee.bank)} · ${esc(payee.account_number)}</p>
      <label for="nickname">${esc(t("field.nickname"))}</label>
      <input id="nickname" name="nickname" type="text" value="${esc(payee.nickname)}">
      <span class="saved" id="nickname-status" aria-live="polite"></span>
      <p class="muted">${esc(t("msg.autosave"))}</p>
      <a id="back-to-payees" href="#/payees">${esc(t("act.back_payees"))}</a>
    </section>`;
  const status = document.getElementById("nickname-status");
  document.getElementById("nickname").addEventListener("input", async (e) => {
    const result = await api(`/api/payees/${id}`, { method: "PUT", body: { nickname: e.target.value } });
    status.textContent = result.ok ? t("msg.saved") : result.data.error;
    if (result.ok) {
      payeeCache = null; // the transfer form lists payees by name
    }
  });
}

// -- statements -----------------------------------------------------------------------------

const statementFilter = { account: "all", direction: "all" };

async function showStatements() {
  const { data: overview } = await api("/api/overview");
  const query = `account=${encodeURIComponent(statementFilter.account)}&direction=${encodeURIComponent(statementFilter.direction)}`;
  const { data } = await api(`/api/statements?${query}`);
  const accountOptions = [["all", t("filter.all_accounts")]].concat(overview.accounts.map((a) => [a.id, a.name]))
    .map(([value, label]) => `<option value="${esc(value)}"${value === statementFilter.account ? " selected" : ""}>${esc(label)}</option>`).join("");
  const directionOptions = [["all", t("filter.all")], ["in", t("filter.in")], ["out", t("filter.out")]]
    .map(([value, label]) => `<option value="${value}"${value === statementFilter.direction ? " selected" : ""}>${esc(label)}</option>`).join("");
  const rows = data.rows.map((r) => `<tr><td>${esc(r.date)}</td><td>${esc(r.account)}</td>
    <td>${esc(r.description)}</td><td>${esc(r.amount)}</td></tr>`).join("");
  view().innerHTML = `
    <h1>${esc(t("nav.statements"))}</h1>
    <section class="panel">
      <label for="stmt-account">${esc(t("field.account"))}</label>
      <select id="stmt-account" name="account">${accountOptions}</select>
      <label for="stmt-direction">${esc(t("field.direction"))}</label>
      <select id="stmt-direction" name="direction">${directionOptions}</select>
      <div class="actions"><button id="apply-statement-filter" type="button">${esc(t("act.apply_filter"))}</button></div>
    </section>
    <p><span id="statement-count">${data.count} ${esc(t("label.transactions"))}</span></p>
    <table id="statement-table"><thead><tr><th>${esc(t("col.date"))}</th><th>${esc(t("col.account"))}</th>
      <th>${esc(t("col.description"))}</th><th>${esc(t("col.amount"))}</th></tr></thead><tbody>${rows}</tbody></table>`;
  const pending = { ...statementFilter };
  document.getElementById("stmt-account").addEventListener("change", (e) => { pending.account = e.target.value; });
  document.getElementById("stmt-direction").addEventListener("change", (e) => { pending.direction = e.target.value; });
  document.getElementById("apply-statement-filter").addEventListener("click", () => {
    Object.assign(statementFilter, pending);
    showStatements();
  });
}

// -- router -----------------------------------------------------------------------------------

async function route() {
  const hash = location.hash || "#/";
  const payee = hash.match(/^#\/payees\/([\w-]+)$/);
  if (hash === "#/" || hash === "#") { await showDashboard(); }
  else if (hash === "#/offers") { showOffers(); }
  else if (hash === "#/move") { await showMoveAccounts(); }
  else if (hash === "#/move/amount") { await showMoveAmount(); }
  else if (hash === "#/move/review") { showMoveReview(); }
  else if (hash === "#/move/done") { showMoveResult("done"); }
  else if (hash === "#/move/scheduled") { showMoveResult("scheduled"); }
  else if (hash === "#/payees") { await showPayees(); }
  else if (payee) { await showPayee(payee[1]); }
  else if (hash === "#/statements") { await showStatements(); }
  else { go("#/"); }
}

async function boot() {
  VOCAB = (await api("/api/vocab")).data;
  document.title = t("app.title");
  renderNav();
  window.addEventListener("hashchange", route);
  await route();
}

boot();
