// Ledgerly's client-side behaviour on top of its server-rendered pages. Each part looks for
// the markup it enhances and does nothing on a page without it; every page still works as
// plain links and forms where a part is missing.
//
//   - the Account menu (Sign out)
//   - My claims: filter by status and sort by total or number, in the browser
//   - a claim's tabs (Lines / History), following the address's #fragment
//   - confirmation dialogs for anything that deletes
//   - the approval queue's "select all" and selection count
//   - list filters that apply as soon as they change
//   - click-to-rename on cost centres (saved over JSON; the server has the last word)

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

// -- the Account menu -------------------------------------------------------------------------

const accountButton = $("#nav-account");
if (accountButton) {
  accountButton.addEventListener("click", () => {
    const menu = $("#account-menu");
    menu.hidden = !menu.hidden;
    accountButton.setAttribute("aria-expanded", String(!menu.hidden));
  });
}

// -- My claims: status filter and sorting ----------------------------------------------------

const claimsTable = $("#my-claims");
const claimsTools = $("[data-claims-tools]");
if (claimsTable && claimsTools && $$("tbody tr[data-status]", claimsTable).length) {
  const TOTAL_COLUMN = 2;
  const body = $("tbody", claimsTable);
  const rows = $$("tr[data-status]", body);
  const words = JSON.parse(claimsTable.dataset.words ?? "{}");
  const state = { filter: "all", sort: null, direction: 1 };

  const sortKeys = {
    total: (row) => Number(row.cells[TOTAL_COLUMN].dataset.pence),
    number: (row) => Number(row.dataset.number),
  };

  function render() {
    const ordered = rows.slice();
    if (state.sort) {
      const key = sortKeys[state.sort];
      ordered.sort((a, b) => (key(a) - key(b)) * state.direction);
    }
    let shown = 0;
    ordered.forEach((row, index) => {
      body.append(row);
      row.cells[TOTAL_COLUMN].id = `row-${index + 1}-total`; // ids follow the order on screen
      row.hidden = state.filter !== "all" && row.dataset.status !== state.filter;
      if (!row.hidden) shown += 1;
    });
    $("#claims-shown").textContent = `${shown} ${words.of} ${rows.length}`;
    $("#claims-sort").textContent = state.sort ? words[`${state.sort}${state.direction > 0 ? "Up" : "Down"}`] : words.none;
    for (const button of $$("[data-filter]", claimsTools)) {
      button.setAttribute("aria-pressed", String(button.dataset.filter === state.filter));
    }
  }

  claimsTools.hidden = false;
  claimsTools.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.dataset.filter) {
      state.filter = button.dataset.filter;
    } else if (button.dataset.sort) {
      // The first click sorts lowest first; another click on the same button reverses it.
      state.direction = state.sort === button.dataset.sort ? -state.direction : 1;
      state.sort = button.dataset.sort;
    }
    render();
  });
}

// -- a claim's tabs ----------------------------------------------------------------------------

const tabs = $("[data-tabs]");
if (tabs) {
  const show = () => {
    const wanted = location.hash === "#history" ? "history" : "lines";
    for (const panel of $$("[data-panel]")) panel.hidden = panel.dataset.panel !== wanted;
    for (const tab of $$("[data-tab]", tabs)) tab.setAttribute("aria-current", String(tab.dataset.tab === wanted));
  };
  window.addEventListener("hashchange", show);
  show();
}

// -- confirmation dialogs ----------------------------------------------------------------------

const dialogRoot = $("#dialog-root");
const main = $("main");

function closeDialog() {
  dialogRoot.innerHTML = "";
  main.hidden = false;
}

document.addEventListener("click", (event) => {
  const opener = event.target.closest("[data-dialog]");
  if (opener && dialogRoot) {
    const template = document.getElementById(opener.dataset.dialog);
    if (!template) return;
    dialogRoot.innerHTML = `<div class="backdrop"><div class="dialog" role="dialog" aria-modal="true" aria-labelledby="dialog-title"></div></div>`;
    $(".dialog", dialogRoot).append(template.content.cloneNode(true));
    main.hidden = true; // the dialog has the page to itself until it is answered
    return;
  }
  if (event.target.closest("[data-close]")) closeDialog();
});

// -- the approval queue's selection -----------------------------------------------------------

const bulk = $("form[data-bulk]");
if (bulk) {
  const picks = $$("[data-pick]", bulk);
  const count = $("#picked-count");
  const all = $("[data-pick-all]", bulk);
  const update = () => {
    const chosen = picks.filter((box) => box.checked).length;
    if (count) count.textContent = count.textContent.replace(/^\d+/, String(chosen));
    if (all) all.checked = chosen > 0 && chosen === picks.length;
  };
  if (all) all.addEventListener("change", () => { for (const box of picks) box.checked = all.checked; update(); });
  for (const box of picks) box.addEventListener("change", update);
  update();
}

// -- filters that apply at once -----------------------------------------------------------------

for (const select of $$("select[data-autosubmit]")) {
  select.addEventListener("change", () => select.form.requestSubmit());
}

// -- click-to-rename (cost centres) -------------------------------------------------------------

const centres = $("table[data-renames]");
if (centres) {
  const message = $("#cc-message");
  const say = (text, isError) => {
    message.textContent = text;
    message.hidden = !text;
    message.className = isError ? "error" : "notice";
  };

  function editor(code, current) {
    const cell = $(`tr[data-centre="${code}"] [data-name-cell]`, centres);
    cell.innerHTML = `<label for="cc-input-${code}" class="small">${centres.dataset.renameLabel ?? ""}</label>
      <input id="cc-input-${code}" name="cc_name" type="text">
      <button id="cc-save-${code}" type="button" data-save="${code}">${centres.dataset.saveLabel ?? "Save"}</button>
      <button id="cc-cancel-${code}" type="button" data-cancel="${code}">${centres.dataset.cancelLabel ?? "Cancel"}</button>`;
    const field = $(`#cc-input-${code}`);
    field.value = current;
    field.focus();
    cell.dataset.before = current;
  }

  function label(code, text) {
    const cell = $(`tr[data-centre="${code}"] [data-name-cell]`, centres);
    cell.innerHTML = "";
    const button = document.createElement("button");
    button.type = "button";
    button.id = `cc-name-${code}`;
    button.className = "link";
    button.dataset.rename = code;
    button.textContent = text;
    cell.append(button);
    return cell;
  }

  async function save(code) {
    const cell = $(`tr[data-centre="${code}"] [data-name-cell]`, centres);
    const before = cell.dataset.before;
    const typed = $(`#cc-input-${code}`).value;
    label(code, typed); // shown at once; the server's answer settles it
    let reply = { ok: false, error: "" };
    try {
      const response = await fetch(`${centres.dataset.renames}/${code}/rename`, {
        method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: typed }),
      });
      reply = await response.json();
    } catch {
      reply = { ok: false, error: centres.dataset.failed ?? "" };
    }
    if (reply.reason === "signed-out") {
      location.assign("/login");
      return;
    }
    const shown = reply.ok ? reply.name : before; // a refused rename puts the old name back
    label(code, shown);
    say(reply.ok ? (centres.dataset.savedLabel ?? "") : reply.error, !reply.ok);
  }

  centres.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.dataset.rename) editor(button.dataset.rename, button.textContent);
    else if (button.dataset.save) save(button.dataset.save);
    else if (button.dataset.cancel) {
      const code = button.dataset.cancel;
      label(code, $(`tr[data-centre="${code}"] [data-name-cell]`, centres).dataset.before);
    }
  });
}
