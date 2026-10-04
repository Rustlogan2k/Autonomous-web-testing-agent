"use strict";
/*
 * Orbit client. Hash routes:
 *   #/projects                    the projects hub
 *   #/board/<project>             a board
 *   #/board/<project>/card/<n>    a card, as a modal over its board (a reload reopens it)
 * Every route render and every action is one JSON request; the page is always redrawn from
 * the server's answer, never from what the client hoped would happen. The only state kept
 * purely on the client is UI state: the open tab, open menus, drafts, and "watching".
 */

const VOCAB = JSON.parse(document.getElementById("vocab-data").textContent);

function t(key, params) {
  let text = Object.prototype.hasOwnProperty.call(VOCAB, key) ? VOCAB[key] : key;
  for (const [name, value] of Object.entries(params || {})) {
    text = text.split(`{${name}}`).join(String(value));
  }
  return text;
}

const esc = (value) => String(value ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const ui = {
  projects: null,     // last /api/projects answer
  board: null,        // last board the server sent
  boardMessage: "",
  openMenu: null,     // the column whose options menu is open
  draftCard: null,    // {title} while the new-card form is open
  modal: null,        // {n, tab, target, message, linkCopied, comment} while a card is open
  watching: new Set(), // cards this browser tab watches; never sent to the server
  busy: false,
};
let current = null;
let routeToken = 0;

async function request(path, body) {
  const options = body === undefined
    ? { credentials: "same-origin" }
    : { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body) };
  const response = await fetch(path, options);
  return response.json();
}

/** One server action at a time; the answer replaces the board the page is drawn from. */
async function perform(path, body, where) {
  if (ui.busy) {
    return null;
  }
  ui.busy = true;
  try {
    const result = await request(path, body);
    if (result.board) {
      ui.board = result.board;
    }
    const message = result.message || result.error || "";
    if (where === "modal" && ui.modal) {
      ui.modal.message = message;
    } else if (where === "board") {
      ui.boardMessage = message;
    }
    return result;
  } finally {
    ui.busy = false;
  }
}

// -- routing ---------------------------------------------------------------------------------

function parseRoute(hash) {
  let m = /^#\/board\/([a-z]+)\/card\/(\d+)$/.exec(hash);
  if (m) {
    return { name: "card", pid: m[1], n: Number(m[2]) };
  }
  m = /^#\/board\/([a-z]+)$/.exec(hash);
  if (m) {
    return { name: "board", pid: m[1], n: null };
  }
  return hash === "#/projects" ? { name: "projects", pid: null, n: null } : null;
}

async function route() {
  const next = parseRoute(location.hash);
  if (!next) {
    location.replace("#/projects");
    return;
  }
  const token = ++routeToken;
  const previous = current;
  current = next;
  if (!previous || previous.name !== next.name || previous.pid !== next.pid || previous.n !== next.n) {
    ui.boardMessage = "";
    ui.openMenu = null;
  }
  if (!previous || previous.pid !== next.pid) {
    ui.draftCard = null;
  }
  ui.modal = next.name !== "card" ? null
    : ui.modal && ui.modal.n === next.n ? ui.modal
      : { n: next.n, tab: "details", target: null, message: "", linkCopied: false, comment: "" };
  paint(); // at once, from what is already known; then from the server
  if (next.name === "projects") {
    const data = await request("/api/projects");
    if (token === routeToken) {
      ui.projects = data.projects;
      paint();
    }
    return;
  }
  const data = await request(`/api/boards/${next.pid}`);
  if (token === routeToken && data.board) {
    ui.board = data.board;
    paint();
  }
}

function paint() {
  const view = document.getElementById("view");
  const modalRoot = document.getElementById("modal-root");
  if (!current) {
    return;
  }
  document.body.classList.toggle("modal-open", current.name === "card");
  if (current.name === "projects") {
    modalRoot.innerHTML = "";
    view.innerHTML = projectsHtml();
    return;
  }
  const data = ui.board && ui.board.project.id === current.pid ? ui.board : null;
  if (!data) {
    modalRoot.innerHTML = "";
    view.innerHTML = `<p class="loading">${esc(t("label.loading"))}</p>`;
    return;
  }
  const modalOpen = current.name === "card";
  view.innerHTML = boardHtml(data, !modalOpen);
  modalRoot.innerHTML = modalOpen ? modalHtml(data) : "";
  if (modalOpen) {
    bindModal(data);
  } else {
    bindBoard(data);
  }
}

// -- projects hub ------------------------------------------------------------------------------

function projectsHtml() {
  if (!ui.projects) {
    return `<p class="loading">${esc(t("label.loading"))}</p>`;
  }
  const items = ui.projects.map((p) => `
    <li class="project">
      <h2>${esc(p.name)}</h2>
      <p class="muted">${esc(p.summary)}</p>
      <span id="summary-${esc(p.id)}" class="summary">${esc(t("label.project_summary", { cards: p.cards, shipped: p.shipped }))}</span>
      <a id="open-project-${esc(p.id)}" class="button primary" href="#/board/${esc(p.id)}">${esc(t("act.open_board"))}</a>
    </li>`).join("");
  return `<h1>${esc(t("title.projects"))}</h1><ul id="project-list" class="projects">${items}</ul>`;
}

// -- board ---------------------------------------------------------------------------------------

function boardHtml(data, interactive) {
  const columns = data.columns.map((column) => columnHtml(data, column, interactive)).join("");
  return `
    <section class="board-head">
      <h1 id="board-title">${esc(data.project.name)}</h1>
      <p class="muted">${esc(data.project.summary)}</p>
      <p id="board-message" class="message" role="status">${interactive ? esc(ui.boardMessage) : ""}</p>
    </section>
    <div class="columns"${interactive ? "" : ' aria-hidden="true"'}>${columns}</div>`;
}

function columnHtml(data, column, interactive) {
  const full = column.wip !== null && column.cards.length >= column.wip;
  const limit = column.wip === null ? "" : ` title="${esc(t("label.wip_limit", { wip: column.wip }))}"`;
  const cards = column.cards.length
    ? column.cards.map((card) => tileHtml(data, card, interactive)).join("")
    : `<li class="empty">${esc(t("label.no_cards"))}</li>`;
  return `
    <section class="column" aria-labelledby="col-title-${esc(column.id)}">
      <header class="column-head">
        <h2 id="col-title-${esc(column.id)}">${esc(column.name)}</h2>
        <output id="wip-${esc(column.id)}" class="load${full ? " full" : ""}"${limit}>${esc(column.load)}</output>
      </header>
      ${interactive ? columnTools(column) : ""}
      <ul id="col-${esc(column.id)}-cards" class="cards">${cards}</ul>
    </section>`;
}

function columnTools(column) {
  if (column.id === "doing") {
    return `<div class="column-tools"><button id="pull-in" type="button">${esc(t("act.pull_in"))}</button></div>`;
  }
  if (column.id !== "backlog" && column.id !== "review") {
    return "";
  }
  const open = ui.openMenu === column.id;
  let menu = "";
  if (open && column.id === "backlog") {
    menu = `<div class="menu" role="menu"><button id="new-card" type="button" role="menuitem">${esc(t("act.new_card"))}</button></div>`;
  } else if (open) {
    menu = `<div class="menu" role="menu"><button id="ship-ready" type="button" role="menuitem">${esc(t("act.ship_ready"))}</button></div>`;
  }
  let form = "";
  if (column.id === "backlog" && ui.draftCard) {
    form = `
      <div class="new-card">
        <label for="new-card-title">${esc(t("field.card_title"))}</label>
        <input id="new-card-title" name="card_title" type="text" value="${esc(ui.draftCard.title)}">
        <div class="row">
          <button id="add-card" class="primary" type="button"${ui.draftCard.title.trim() ? "" : " disabled"}>${esc(t("act.add_card"))}</button>
          <button id="cancel-new-card" type="button">${esc(t("act.cancel_new_card"))}</button>
        </div>
      </div>`;
  }
  return `
    <div class="column-tools">
      <button id="col-menu-${esc(column.id)}" class="menu-toggle" type="button" aria-haspopup="menu"
        aria-expanded="${open}">${esc(t("act.column_menu"))}</button>
      ${menu}
    </div>${form}`;
}

function tileHtml(data, card, interactive) {
  const inner = `<span class="num">#${card.n}</span> <span class="title">${esc(card.title)}</span>
    <span class="meta">${card.done}/${card.total}</span>${card.blocked ? ` <span class="badge">${esc(t("badge.blocked"))}</span>` : ""}`;
  return interactive
    ? `<li><a id="card-${card.n}" class="tile" href="#/board/${esc(data.project.id)}/card/${card.n}">${inner}</a></li>`
    : `<li><span class="tile">${inner}</span></li>`;
}

function bindBoard(data) {
  const pid = data.project.id;
  const on = (id, handler) => {
    const element = document.getElementById(id);
    if (element) {
      element.addEventListener("click", handler);
    }
  };
  for (const column of ["backlog", "review"]) {
    on(`col-menu-${column}`, () => {
      ui.openMenu = ui.openMenu === column ? null : column;
      paint();
    });
  }
  on("new-card", () => {
    ui.openMenu = null;
    ui.draftCard = { title: "" };
    paint();
    document.getElementById("new-card-title").focus();
  });
  const titleInput = document.getElementById("new-card-title");
  if (titleInput) {
    titleInput.addEventListener("input", (event) => {
      ui.draftCard.title = event.target.value;
      document.getElementById("add-card").disabled = !event.target.value.trim();
    });
  }
  on("add-card", async () => {
    const result = await perform(`/api/boards/${pid}/cards`, { title: ui.draftCard.title }, "board");
    if (result && result.ok) {
      ui.draftCard = null;
    }
    paint();
  });
  on("cancel-new-card", () => {
    ui.draftCard = null;
    paint();
  });
  on("pull-in", async () => {
    await perform(`/api/boards/${pid}/pull-in`, {}, "board");
    paint();
  });
  on("ship-ready", async () => {
    ui.openMenu = null;
    await perform(`/api/boards/${pid}/ship-ready`, {}, "board");
    paint();
  });
}

// -- card modal ----------------------------------------------------------------------------------

const TABS = ["details", "checklist", "activity"];
const TAB_LABEL = { details: () => t("tab.details"), checklist: () => t("tab.checklist"), activity: () => t("tab.activity") };

function modalHtml(data) {
  const m = ui.modal;
  const card = data.cards[m.n];
  const closeHref = `#/board/${esc(data.project.id)}`;
  if (!card) {
    return `<div class="backdrop"><div class="modal" role="dialog" aria-modal="true" aria-labelledby="card-title" id="card-modal">
      <header class="modal-head"><h2 id="card-title">${esc(t("label.not_found"))}</h2>
      <a id="close-card" class="close" href="${closeHref}">${esc(t("act.close"))}</a></header></div></div>`;
  }
  if (!card.moveTargets.some((target) => target.id === m.target)) {
    m.target = card.moveTargets.length ? card.moveTargets[0].id : null;
  }
  const watching = ui.watching.has(card.n);
  const tabs = TABS.map((tab) => `<button id="tab-${tab}" type="button" role="tab"
    aria-selected="${m.tab === tab}" class="tab${m.tab === tab ? " active" : ""}">${esc(TAB_LABEL[tab]())}</button>`).join("");
  const panel = m.tab === "checklist" ? checklistHtml(card) : m.tab === "activity" ? activityHtml(card) : detailsHtml(data, card);
  const options = card.moveTargets.map((target) =>
    `<option value="${esc(target.id)}"${target.id === m.target ? " selected" : ""}>${esc(target.name)}</option>`).join("");
  const workflow = card.final ? `<p class="muted" id="card-final">${esc(t("label.final"))}</p>` : `
      <div class="move">
        <label for="move-target">${esc(t("field.move_to"))}</label>
        <select id="move-target" name="move_target">${options}</select>
        <button id="move-card" type="button">${esc(t("act.move"))}</button>
      </div>
      ${card.canShip ? `<button id="ship-card" class="primary" type="button">${esc(t("act.ship"))}</button>` : ""}
      ${card.canPark ? `<button id="park-card" type="button">${esc(t("act.park"))}</button>` : ""}`;
  return `
    <div class="backdrop">
      <div class="modal" role="dialog" aria-modal="true" aria-labelledby="card-title" id="card-modal">
        <header class="modal-head">
          <h2 id="card-title"><span class="num">#${card.n}</span> ${esc(card.title)}</h2>
          <a id="close-card" class="close" href="${closeHref}">${esc(t("act.close"))}</a>
          <p class="card-facts">
            <span id="card-column" class="pill">${esc(card.columnName)}</span>
            ${card.blocked ? `<span id="card-blocked" class="badge">${esc(t("badge.blocked"))}</span>` : ""}
            <output id="card-progress">${esc(t("label.progress", { done: card.done, total: card.total }))}</output>
            ${watching ? `<span id="watch-state" class="pill watch">${esc(t("label.watching"))}</span>` : ""}
          </p>
        </header>
        <div class="tabs" role="tablist">${tabs}</div>
        <section class="panel" role="tabpanel" id="panel-${m.tab}" aria-labelledby="tab-${m.tab}">${panel}</section>
        <footer class="modal-actions">
          <div class="row">${workflow}</div>
          <div class="row secondary">
            <button id="watch-card" type="button" aria-pressed="${watching}">${esc(watching ? t("act.unwatch") : t("act.watch"))}</button>
            <button id="copy-link" type="button">${esc(t("act.copy_link"))}</button>
            <output id="link-status">${m.linkCopied ? esc(t("label.link_copied")) : ""}</output>
          </div>
          <p id="card-message" class="message" role="status">${esc(m.message)}</p>
        </footer>
      </div>
    </div>`;
}

function detailsHtml(data, card) {
  const blockers = card.blockers.length ? `
    <h3>${esc(t("label.blocked_by"))}</h3>
    <ul id="card-blockers" class="blockers">${card.blockers.map((b) => `
      <li><a id="dep-${b.n}" href="#/board/${esc(data.project.id)}/card/${b.n}">#${b.n} ${esc(b.title)}</a>
        <span class="muted">${esc(b.columnName)} · ${esc(b.finished ? t("label.dep_shipped") : t("label.dep_open"))}</span></li>`).join("")}
    </ul>` : `<p class="muted" id="card-blockers-none">${esc(t("label.no_blockers"))}</p>`;
  return `
    <h3>${esc(t("title.description"))}</h3>
    <p id="card-description">${esc(card.description || t("label.no_description"))}</p>
    ${blockers}`;
}

function checklistHtml(card) {
  if (!card.checklist.length) {
    return `<p class="muted">${esc(t("label.empty_checklist"))}</p>`;
  }
  const items = card.checklist.map((item) => {
    const id = `check-${card.n}-${item.id}`;
    return `<li><input type="checkbox" id="${id}" name="item-${card.n}-${item.id}" data-item="${item.id}"${item.done ? " checked" : ""}${card.final ? " disabled" : ""}>
      <label for="${id}">${esc(item.text)}</label></li>`;
  }).join("");
  return `<ul id="checklist" class="checklist">${items}</ul>`;
}

function activityHtml(card) {
  const comments = card.comments.length
    ? card.comments.map((c) => `<li><p class="comment-body">${esc(c.body)}</p><span class="muted">${esc(c.at)}</span></li>`).join("")
    : `<li class="muted">${esc(t("label.no_comments"))}</li>`;
  const history = card.activity.length
    ? card.activity.map((a) => `<li><span class="muted">${esc(a.at)}</span> ${esc(a.text)}</li>`).join("")
    : `<li class="muted">${esc(t("label.no_activity"))}</li>`;
  return `
    <h3>${esc(t("title.comments"))}</h3>
    <ul id="comment-list" class="comments">${comments}</ul>
    <label for="comment-body">${esc(t("field.comment"))}</label>
    <textarea id="comment-body" name="comment" rows="3">${esc(ui.modal.comment)}</textarea>
    <div class="row"><button id="add-comment" type="button">${esc(t("act.comment"))}</button></div>
    <h3>${esc(t("title.activity"))}</h3>
    <ol id="activity-log" class="activity">${history}</ol>`;
}

function bindModal(data) {
  const m = ui.modal;
  const card = data.cards[m.n];
  if (!card) {
    return;
  }
  const on = (id, handler) => {
    const element = document.getElementById(id);
    if (element) {
      element.addEventListener("click", handler);
    }
  };
  for (const tab of TABS) {
    on(`tab-${tab}`, () => {
      m.tab = tab;
      paint();
    });
  }
  document.querySelectorAll("#checklist input[type=checkbox]").forEach((box) => {
    box.addEventListener("click", async (event) => {
      event.preventDefault(); // the server decides; the box is redrawn from its answer
      const item = card.checklist.find((entry) => entry.id === Number(box.dataset.item));
      await perform(`/api/cards/${card.n}/checklist/${item.id}`, { done: !item.done }, "modal");
      paint();
    });
  });
  const select = document.getElementById("move-target");
  if (select) {
    select.addEventListener("change", (event) => {
      m.target = event.target.value;
    });
  }
  on("move-card", async () => {
    await perform(`/api/cards/${card.n}/move`, { to: m.target }, "modal");
    paint();
  });
  on("ship-card", async () => {
    await perform(`/api/cards/${card.n}/ship`, {}, "modal");
    paint();
  });
  on("park-card", async () => {
    await perform(`/api/cards/${card.n}/park`, {}, "modal");
    paint();
  });
  on("watch-card", () => {
    if (ui.watching.has(card.n)) {
      ui.watching.delete(card.n);
    } else {
      ui.watching.add(card.n);
    }
    paint();
  });
  on("copy-link", () => {
    m.linkCopied = true;
    paint();
    try {
      navigator.clipboard.writeText(location.href).catch(() => {});
    } catch {
      // no clipboard in this context: the link is still the page's address
    }
  });
  const comment = document.getElementById("comment-body");
  if (comment) {
    comment.addEventListener("input", (event) => {
      m.comment = event.target.value;
    });
  }
  on("add-comment", async () => {
    const result = await perform(`/api/cards/${card.n}/comments`, { body: m.comment }, "modal");
    if (result && result.ok) {
      m.comment = "";
    }
    paint();
  });
}

// -- boot ------------------------------------------------------------------------------------------

function boot() {
  document.title = t("app.title");
  document.getElementById("brand").textContent = t("app.title");
  document.getElementById("main-nav").innerHTML =
    `<a id="nav-projects" href="#/projects">${esc(t("nav.projects"))}</a>`;
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && current && current.name === "card") {
      location.hash = `#/board/${current.pid}`;
    }
  });
  window.addEventListener("hashchange", route);
  route();
}

boot();
