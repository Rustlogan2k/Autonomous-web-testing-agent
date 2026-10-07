// Server-rendered pages. Every visible word comes from the vocabulary; names of the club,
// the candidates and the members are poll data.

let vocabulary = {};
let seed = null;

export function useVocabulary(table, pollSeed) {
  vocabulary = table;
  seed = pollSeed;
}

export function t(key, params = {}) {
  const text = Object.prototype.hasOwnProperty.call(vocabulary, key) ? vocabulary[key] : key;
  return Object.entries(params).reduce((out, [name, value]) => out.split(`{${name}}`).join(String(value)), text);
}

export function h(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[ch]);
}

/** JSON safe to embed in a <script type="application/json"> element. */
const embeddable = (value) => JSON.stringify(value).replace(/</g, "\\u003c").replace(/>/g, "\\u003e").replace(/&/g, "\\u0026");

const candidateName = (id) => seed.candidates.find((c) => c.id === id)?.name ?? id;
const memberName = (id) => seed.members.find((m) => m.id === id)?.name ?? id;

function layout(titleKey, body, { active = "", flash = null, script = "" } = {}) {
  const link = (id, href, key) =>
    `<a id="${id}" href="${href}"${active === id ? ' aria-current="page"' : ""}>${h(t(key))}</a>`;
  const message = flash ? `<p id="flash" class="flash ${flash.ok ? "notice" : "error"}" role="status">${h(t(...flash.message))}</p>` : "";
  return `<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${h(t(titleKey))} · ${h(t("app.name"))}</title>
<link rel="icon" href="data:,">
<link rel="stylesheet" href="/static/tally.css">
</head>
<body>
<header class="masthead">
<span class="wordmark">${h(t("app.name"))}</span>
<span class="election">${h(t("app.election", { office: seed.office, club: seed.club }))}</span>
<nav aria-label="${h(t("nav.label"))}">${link("nav-poll", "/poll", "nav.poll")}${link("nav-register", "/register", "nav.register")}${link("nav-ballots", "/ballots", "nav.ballots")}${link("view-declaration", "/declaration", "nav.declaration")}${link("nav-help", "/help", "nav.help")}</nav>
</header>
<main>
${message}
${body}
</main>
${script}
</body>
</html>`;
}

const postButton = (action, id, key, extra = "") =>
  `<form method="post" action="${action}" class="inline"><button type="submit" id="${id}"${extra}>${h(t(key))}</button></form>`;

function officerPanel(poll) {
  const actions = poll.phase === "open"
    ? `<div class="actions">${postButton("/poll/issue", "next-voter", "act.next_voter")}${postButton("/poll/close", "close-poll", "act.close_poll")}</div>`
    : "";
  return `<section class="officer" aria-labelledby="officer-title">
<h2 id="officer-title">${h(t("officer.title"))}</h2>
<p>${h(t("officer.phase"))} <strong id="poll-phase">${h(t(`phase.${poll.phase}`))}</strong></p>
<p class="figures"><span>${h(t("officer.issued"))} <strong id="papers-issued">${poll.papers.length}</strong> <span class="muted">${h(t("officer.of_members", { members: seed.members.length }))}</span></span>
<span>${h(t("officer.cast"))} <strong id="ballots-cast">${poll.ballots.length}</strong></span></p>
${actions}
</section>`;
}

function rankSelect(rank) {
  const options = seed.candidates.map((c) => `<option value="${c.id}">${h(c.name)}</option>`).join("");
  return `<p><label for="pref-${rank}">${h(t(`kiosk.rank_${rank}`))}</label>
<select id="pref-${rank}" name="pref_${rank}"><option value="">-</option>${options}</select></p>`;
}

function kioskPanel(paper, status) {
  const ranks = Array.from({ length: seed.ranks }, (_, i) => rankSelect(i + 1)).join("\n");
  let intro;
  if (!paper) intro = `<p>${h(t("kiosk.waiting"))}</p>`;
  else intro = `<p>${h(t("kiosk.paper"))} <strong id="kiosk-paper">${paper.number}</strong></p>`;
  const start = paper && !paper.started
    ? `<p><button type="button" id="start-ballot" data-paper="${paper.number}">${h(t("act.start_ballot"))}</button></p>` : "";
  const panel = paper
    ? `<div id="ballot-panel" data-paper="${paper.number}"${paper.started ? "" : " hidden"}>
<fieldset><legend>${h(t("kiosk.instructions"))}</legend>
${ranks}
</fieldset>
<p><button type="button" id="cast-ballot">${h(t("act.cast_ballot"))}</button></p>
</div>` : "";
  const blank = seed.candidates.map((c) => `<li><span class="box"></span> ${h(c.name)}</li>`).join("");
  return `<section class="kiosk" aria-labelledby="kiosk-title">
<h2 id="kiosk-title">${h(t("kiosk.title"))}</h2>
${intro}
${start}
${panel}
<p id="kiosk-status" role="status" data-offline="${h(t("kiosk.offline"))}">${h(status)}</p>
<p><button type="button" id="print-paper">${h(t("act.print_paper"))}</button></p>
<div id="blank-paper" class="blank-paper" hidden><h3>${h(t("paper.title", { office: seed.office }))}</h3><p>${h(t("paper.instructions"))}</p><ol>${blank}</ol></div>
</section>`;
}

function closingPanel(poll, unused) {
  return `<section class="closing" aria-labelledby="closing-title">
<h2 id="closing-title">${h(t("closing.title"))}</h2>
<p>${h(t("closing.summary", { issued: poll.papers.length, cast: poll.ballots.length, members: seed.members.length }))}</p>
<p>${h(t("closing.void", { unused }))}</p>
<p>${h(t("closing.final"))}</p>
<div class="actions">${postButton("/poll/confirm-close", "confirm-close", "act.confirm_close")}${postButton("/poll/cancel-close", "cancel-close", "act.cancel_close")}</div>
</section>`;
}

function resultsPanel(poll) {
  const data = {
    rounds: poll.count.rounds,
    candidates: seed.candidates,
    labels: Object.fromEntries(Object.entries(vocabulary).filter(([key]) => key.startsWith("round.") || key.startsWith("act.round_"))),
  };
  const declared = poll.phase === "declared"
    ? `<p class="declared">${h(t("results.declared", { name: poll.declaration.name }))}</p>` : "";
  return `<section class="results" aria-labelledby="results-title">
<h2 id="results-title">${h(t("results.title"))}</h2>
${declared}
<div id="round-view"></div>
<script id="count-data" type="application/json">${embeddable(data)}</script>
<div class="actions">${postButton("/poll/declare", "publish-declaration", "act.publish_declaration")}</div>
</section>`;
}

export function pollPage({ poll, kioskPaper, kioskStatus, unused, flash }) {
  let body = officerPanel(poll);
  if (poll.phase === "open") body += kioskPanel(kioskPaper, kioskStatus);
  else if (poll.phase === "closing") body += closingPanel(poll, unused);
  else body += resultsPanel(poll);
  return layout("poll.title", body, { active: "nav-poll", flash, script: '<script src="/static/poll.js"></script>' });
}

export function declarationPage(poll) {
  const d = poll.declaration;
  const body = d
    ? `<article class="declaration">
<h1>${h(t("declaration.title"))}</h1>
<p>${h(t("declaration.intro", { office: seed.office, club: seed.club }))}</p>
<p>${h(t("declaration.counted", { ballots: d.ballots, round: d.round, exhausted: d.exhausted }))}</p>
<p class="elected"><strong id="declared-name">${h(d.name)}</strong> ${h(t("declaration.elected_with", { office: seed.office }))}
<strong id="declared-votes">${d.votes}</strong> ${h(t("declaration.votes_of"))} <strong id="declared-continuing">${d.continuing}</strong> ${h(t("declaration.continuing"))}</p>
<p class="muted">${h(t("declaration.signed"))}</p>
</article>`
    : `<h1>${h(t("declaration.title"))}</h1><p>${h(t("declaration.none"))}</p>`;
  return layout("declaration.title", body, { active: "view-declaration" });
}

export function registerPage(poll) {
  const rows = seed.members.map((m) => {
    const paper = poll.papers.find((p) => p.member === m.id);
    let status = t("register.no_paper");
    if (paper) {
      if (paper.void) status = t("register.void");
      else if (paper.used) status = t("register.voted");
      else status = t("register.issued");
    }
    return `<tr><td>${h(m.name)}</td><td>${paper ? paper.number : "—"}</td><td>${h(status)}</td></tr>`;
  }).join("");
  return layout("register.title", `<h1>${h(t("register.title"))}</h1>
<p>${h(t("register.intro", { members: seed.members.length }))}</p>
<table><thead><tr><th scope="col">${h(t("register.member"))}</th><th scope="col">${h(t("register.paper"))}</th><th scope="col">${h(t("register.status"))}</th></tr></thead>
<tbody>${rows}</tbody></table>`, { active: "nav-register" });
}

export function ballotsPage(poll) {
  if (poll.phase === "open" || poll.phase === "closing") {
    return layout("ballots.title", `<h1>${h(t("ballots.title"))}</h1><p>${h(t("ballots.sealed"))}</p>`, { active: "nav-ballots" });
  }
  const head = Array.from({ length: seed.ranks }, (_, i) => `<th scope="col">${h(t(`kiosk.rank_${i + 1}`))}</th>`).join("");
  const rows = poll.ballots.map((b) => {
    const cells = Array.from({ length: seed.ranks }, (_, i) => `<td>${h(b.preferences[i] ? candidateName(b.preferences[i]) : "—")}</td>`).join("");
    return `<tr><td>${b.paper}</td>${cells}</tr>`;
  }).join("");
  return layout("ballots.title", `<h1>${h(t("ballots.title"))}</h1>
<p>${h(t("ballots.intro", { count: poll.ballots.length }))}</p>
<table><thead><tr><th scope="col">${h(t("register.paper"))}</th>${head}</tr></thead><tbody>${rows}</tbody></table>`, { active: "nav-ballots" });
}

export function helpPage() {
  const rules = ["help.rule_majority", "help.rule_eliminate", "help.rule_ties", "help.rule_exhausted", "help.rule_tied", "help.rule_paper"]
    .map((key) => `<li>${h(t(key))}</li>`).join("");
  return layout("help.title", `<h1>${h(t("help.title"))}</h1><p>${h(t("help.intro"))}</p><ol>${rules}</ol>`, { active: "nav-help" });
}

export function missingPage() {
  return layout("missing.title", `<h1>${h(t("missing.title"))}</h1><p>${h(t("missing.body"))}</p>`);
}

export { memberName };
