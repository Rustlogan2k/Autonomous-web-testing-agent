// The claim editor's island: repeatable expense lines added and removed without a reload, a
// live summary per currency and in sterling, and inline hints on amounts. Everything else in
// Ledgerly is a plain server-rendered form; without this script the editor still posts the
// lines the server rendered, and the server validates and totals them itself.

import { money, parseMinor, roundToPence, sterlingHundredths } from "./money.js";

const editor = document.getElementById("claim-editor");
if (editor) start(editor);

function start(form) {
  const rates = JSON.parse(form.dataset.rates);
  const maxLines = Number(form.dataset.maxLines);
  const lineLimit = Number(form.dataset.lineLimit);
  let nextLine = Number(form.dataset.nextLine);
  const list = document.getElementById("lines");
  const template = document.getElementById("line-template");
  const addButton = document.getElementById("add-line");
  const receiptButton = document.getElementById("attach-receipt");
  const receiptPanel = document.getElementById("receipt-panel");
  const out = {
    GBP: document.getElementById("sum-gbp"),
    EUR: document.getElementById("sum-eur"),
    USD: document.getElementById("sum-usd"),
    total: document.getElementById("sum-total"),
  };

  // Each line's value in sterling (hundredths of a penny), keyed by line number, so the
  // total is a sum of cached values rather than a re-conversion of every row.
  const sterling = new Map();

  const rows = () => [...list.querySelectorAll("fieldset[data-line]")];
  const amountOf = (row) => parseMinor(row.querySelector("[data-amount]").value) ?? 0;
  const currencyOf = (row) => row.querySelector("[data-currency]").value;

  function reprice(row) {
    sterling.set(row.dataset.line, sterlingHundredths(amountOf(row), currencyOf(row), rates));
  }

  function renderSummary() {
    const byCurrency = { GBP: 0, EUR: 0, USD: 0 };
    let total = 0;
    for (const row of rows()) {
      byCurrency[currencyOf(row)] += amountOf(row);
      total += sterling.get(row.dataset.line) ?? 0;
    }
    for (const code of ["GBP", "EUR", "USD"]) out[code].textContent = money(code, byCurrency[code]);
    out.total.textContent = money("GBP", roundToPence(total));
  }

  function checkAmount(row) {
    const field = row.querySelector("[data-amount]");
    const minor = parseMinor(field.value);
    const bad = minor === null || minor === 0 || minor > lineLimit;
    row.querySelector("[data-hint]").hidden = !bad;
    if (bad) field.setAttribute("aria-invalid", "true");
    else field.removeAttribute("aria-invalid");
  }

  function refreshControls() {
    const all = rows();
    addButton.disabled = all.length >= maxLines;
    all.forEach((row, index) => {
      row.querySelector("[data-remove]").disabled = all.length <= 1;
      row.querySelector("[data-position]").textContent = String(index + 1);
    });
  }

  function wire(row) {
    row.querySelector("[data-amount]").addEventListener("input", () => {
      reprice(row);
      checkAmount(row);
      renderSummary();
    });
    row.querySelector("[data-currency]").addEventListener("change", () => { reprice(row); renderSummary(); });
    row.querySelector("[data-remove]").addEventListener("click", () => removeLine(row));
  }

  function addLine() {
    if (rows().length >= maxLines) return;
    const n = nextLine;
    nextLine += 1;
    list.insertAdjacentHTML("beforeend", template.innerHTML.replaceAll("__N__", String(n)).trim());
    const row = list.lastElementChild;
    wire(row);
    reprice(row);
    refreshControls();
    renderSummary();
    row.querySelector("input").focus();
  }

  function removeLine(row) {
    if (rows().length <= 1) return;
    sterling.delete(row.dataset.line);
    row.remove();
    refreshControls();
    renderSummary();
    addButton.focus();
  }

  for (const row of rows()) {
    wire(row);
    reprice(row);
  }
  addButton.addEventListener("click", addLine);
  receiptButton.addEventListener("click", () => {
    receiptPanel.hidden = !receiptPanel.hidden;
    receiptButton.setAttribute("aria-expanded", String(!receiptPanel.hidden));
  });
  refreshControls();
  renderSummary();
}
