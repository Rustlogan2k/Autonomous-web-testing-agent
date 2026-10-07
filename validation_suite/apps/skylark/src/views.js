// Skylark's pages, rendered on the server. Every visible label comes from the vocabulary
// through t(); species, sites, verification categories, dates and counts are data.
//
// Feed rows are drawn here for the first page and by static/feed.js for appended pages; both
// produce the same markup from the same fields (see rowFields in server.js).

const vocabulary = {};
let register = { sites: [], species: [], statuses: {} };

export function useVocabulary(table, seed) {
  Object.assign(vocabulary, table);
  register = seed;
}

/** A label, with {name} placeholders filled from `params`. */
export function t(key, params = {}) {
  const text = Object.prototype.hasOwnProperty.call(vocabulary, key) ? vocabulary[key] : key;
  return Object.entries(params).reduce((out, [name, value]) => out.split(`{${name}}`).join(String(value)), text);
}

/** Escape text for HTML element content and quoted attribute values. */
export function h(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[ch]);
}

/** JSON safe to embed in a <script type="application/json"> element. */
function embedded(value) {
  return JSON.stringify(value).replace(/</g, "\\u003c").replace(/>/g, "\\u003e").replace(/&/g, "\\u0026");
}

function labelsFor(keys) {
  return embedded(Object.fromEntries(keys.map((key) => [key, t(key)])));
}

const siteName = (id) => register.sites.find((site) => site.id === id)?.name ?? id;

// -- frame -----------------------------------------------------------------------------------

export function page({ title, body, head = "", scripts = [] }) {
  return `<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${h(title)} | ${h(t("app.name"))}</title>
<link rel="icon" href="data:,">
<link rel="stylesheet" href="/static/skylark.css">
${head}
</head>
<body>
<header class="masthead">
  <a class="brand" id="nav-feed" href="/feed">${h(t("app.name"))}</a>
  <nav aria-label="${h(t("nav.label"))}">
    <a id="new-sighting" class="cta" href="/sightings/new">${h(t("act.new_sighting"))}</a>
    <a id="nav-sites" href="/sites">${h(t("nav.sites"))}</a>
    <a id="nav-summary" href="/summary">${h(t("nav.summary"))}</a>
    <a id="nav-help" href="/help">${h(t("nav.help"))}</a>
  </nav>
</header>
<main>
${body}
</main>
${scripts.map((name) => `<script src="/static/${name}" defer></script>`).join("\n")}
</body>
</html>`;
}

/** The one-time message left by the previous request, if it was meant for this page. */
function flashLine(flash) {
  if (!flash) return "";
  const tone = flash.tone === "error" ? "error" : "notice";
  return `<p id="flash" class="${tone}" role="${tone === "error" ? "alert" : "status"}">${h(t(flash.key, flash.params))}</p>`;
}

const backToFeed = () => `<p class="back"><a id="back-to-feed" href="/feed">${h(t("act.back_to_feed"))}</a></p>`;

// -- the feed --------------------------------------------------------------------------------

function feedRow(row) {
  return `<tr data-ref="${h(row.ref)}" class="${h(row.status)}">
  <td>${h(row.date)}</td>
  <td><a id="sighting-${h(row.ref)}" href="/sightings/${h(row.ref)}">${h(row.speciesName)}</a></td>
  <td class="num">${h(row.count)}</td>
  <td>${h(row.siteName)}</td>
  <td><span class="badge">${h(row.statusName)}</span></td>
</tr>`;
}

export function feedPage({ site, rows, total, more, next, tally, flash, dropDraft }) {
  const choices = register.sites.map((s) =>
    `<option value="${h(s.id)}"${s.id === site ? " selected" : ""}>${h(s.name)}</option>`).join("");
  const bySite = register.sites.map((s) =>
    `<li>${h(s.name)} <span id="tally-${h(s.id)}" data-tally="${h(s.id)}">${tally.bySite[s.id]}</span></li>`).join("\n    ");
  const foot = more
    ? `<button id="load-more" type="button" data-site="${h(site)}" data-after="${h(next)}">${h(t("act.load_more"))}</button>`
    : `<p id="feed-end">${h(t("feed.end"))}</p>`;
  const body = `<h1>${h(t("feed.title"))}</h1>
${flashLine(flash)}
<form class="filter" method="get" action="/feed" autocomplete="off">
  <label for="filter-site">${h(t("feed.filter_label"))}</label>
  <select id="filter-site" name="site">
    <option value="all"${site === "all" ? " selected" : ""}>${h(t("feed.all_sites"))}</option>${choices}
  </select>
  <button id="apply-filter" type="submit">${h(t("act.apply_filter"))}</button>
</form>
<section class="tallies" aria-label="${h(t("tally.label"))}">
  <p>${h(t("tally.total"))} <strong id="tally-total">${tally.countedBirds}</strong></p>
  <ul class="by-site">
    ${bySite}
  </ul>
  <p class="muted">${h(t("tally.note"))}</p>
</section>
<p class="shown">${h(t("feed.showing"))} <strong id="shown-count">${rows.length}</strong> ${h(t("feed.of"))} <strong id="total-count">${total}</strong> ${h(t("feed.sightings"))}</p>
<table class="feed">
<thead><tr><th>${h(t("col.date"))}</th><th>${h(t("col.species"))}</th><th class="num">${h(t("col.count"))}</th><th>${h(t("col.site"))}</th><th>${h(t("col.status"))}</th></tr></thead>
<tbody id="feed-list">
${rows.map(feedRow).join("\n")}
</tbody>
</table>
<div class="feed-foot">
${foot}
</div>
<p id="feed-status" class="muted" role="status" aria-live="polite"></p>
<script type="application/json" id="feed-labels">${labelsFor(["feed.end", "feed.loaded", "feed.load_failed"])}</script>`;
  const head = dropDraft ? `<meta name="skylark-sent-draft" content="${h(dropDraft)}">` : "";
  return page({ title: t("feed.title"), body, head, scripts: ["feed.js"] });
}

// -- recording a sighting --------------------------------------------------------------------

export function whereStep({ flash }) {
  const options = register.sites.map((s) => `<option value="${h(s.id)}">${h(s.name)}</option>`).join("");
  const body = `<h1>${h(t("record.title"))}</h1>
<p class="step">${h(t("record.step1"))}</p>
${flashLine(flash)}
<form class="record" method="post" action="/sightings/new" autocomplete="off">
  <div class="field">
    <label for="where-site">${h(t("record.where"))}</label>
    <select id="where-site" name="site">${options}</select>
  </div>
  <p class="actions"><button id="choose-site" type="submit">${h(t("act.choose_site"))}</button></p>
</form>
${backToFeed()}`;
  return page({ title: t("record.title"), body });
}

/** The draft line as the form's default values read: what static/record.js would draw. */
export function defaultDraftLine(site) {
  return t("draft.summary", { species: t("draft.no_species"), count: "1", site: site.name, when: t("draft.today") });
}

export function whatStep({ site, today, draftKey, flash }) {
  const species = register.species.map((kind) => `<option value="${h(kind.id)}">${h(kind.name)}</option>`).join("");
  const body = `<h1>${h(t("record.title"))}</h1>
<p class="step">${h(t("record.step2"))} <strong>${h(site.name)}</strong> <a id="change-site" href="/sightings/new">${h(t("act.change_site"))}</a></p>
${flashLine(flash)}
<form id="record-form" class="record" method="post" action="/sightings/new/${h(site.id)}" autocomplete="off" novalidate
      data-draft-key="${h(draftKey)}" data-today="${h(today)}" data-site-name="${h(site.name)}">
  <div class="field">
    <label for="species">${h(t("record.species"))}</label>
    <select id="species" name="species"><option value="">${h(t("record.choose_species"))}</option>${species}</select>
  </div>
  <div class="field">
    <label for="count">${h(t("record.count"))}</label>
    <input id="count" name="count" type="number" min="1" max="500" step="1" value="1" inputmode="numeric">
  </div>
  <div class="field">
    <label for="seen-on">${h(t("record.seen_on"))}</label>
    <input id="seen-on" name="seen_on" type="date" min="2026-01-01" max="${h(today)}" value="${h(today)}">
  </div>
  <div class="field wide">
    <label for="notes">${h(t("record.notes"))}</label>
    <input id="notes" name="notes" type="text">
  </div>
  <p class="draft">${h(t("record.draft_label"))} <output id="draft-summary" for="species count seen-on">${h(defaultDraftLine(site))}</output></p>
  <p class="muted">${h(t("record.draft_hint"))}</p>
  <p class="actions">
    <button id="save-sighting" type="submit">${h(t("act.save_sighting"))}</button>
    <button id="discard-draft" type="button" class="quiet">${h(t("act.discard_draft"))}</button>
  </p>
</form>
<script type="application/json" id="record-labels">${labelsFor(["draft.summary", "draft.no_species", "draft.today", "draft.no_date", "draft.no_count"])}</script>
${backToFeed()}`;
  return page({ title: t("record.title"), body, scripts: ["record.js"] });
}

// -- one sighting ----------------------------------------------------------------------------

export function sightingPage({ row, flash }) {
  const body = `<h1>${h(t("detail.title", { species: row.speciesName, site: row.siteName }))}</h1>
${flashLine(flash)}
<dl class="facts">
  <dt>${h(t("detail.ref"))}</dt><dd>${h(row.ref)}</dd>
  <dt>${h(t("detail.seen_on"))}</dt><dd>${h(row.date)}</dd>
  <dt>${h(t("detail.count"))}</dt><dd>${h(row.count)}</dd>
  <dt>${h(t("detail.site"))}</dt><dd><a id="site-${h(row.site)}" href="/sites/${h(row.site)}">${h(row.siteName)}</a></dd>
  <dt>${h(t("detail.status"))}</dt><dd><strong id="sighting-status">${h(row.statusName)}</strong></dd>
  <dt>${h(t("detail.notes"))}</dt><dd>${row.notes ? h(row.notes) : h(t("detail.no_notes"))}</dd>
</dl>
<form class="review" method="post" action="/sightings/${h(row.ref)}/review">
  <button id="flag-doubtful" type="submit" name="action" value="flag">${h(t("act.flag_doubtful"))}</button>
  <button id="restore-sighting" type="submit" name="action" value="restore">${h(t("act.restore"))}</button>
</form>
<p class="actions">
  <button id="share-sighting" type="button" class="quiet" aria-expanded="false" aria-controls="share-panel">${h(t("act.share"))}</button>
  <button id="show-map" type="button" class="quiet" aria-expanded="false" aria-controls="map-panel">${h(t("act.show_map"))}</button>
</p>
<p id="share-panel" class="panel" hidden>${h(t("share.label"))} <code id="share-address"></code></p>
<p id="map-panel" class="panel" hidden>${h(t("map.offline"))}</p>
${backToFeed()}`;
  return page({ title: `${row.speciesName} ${row.ref}`, body, scripts: ["sighting.js"] });
}

// -- sites, the month, help ------------------------------------------------------------------

export function sitesPage({ summaries }) {
  const rows = register.sites.map((s) => `<tr>
  <td><a id="site-${h(s.id)}" href="/sites/${h(s.id)}">${h(s.name)}</a></td>
  <td class="num">${summaries[s.id].sightings}</td>
  <td class="num">${summaries[s.id].birds}</td>
  <td>${h(summaries[s.id].latest ?? "")}</td>
</tr>`).join("\n");
  const body = `<h1>${h(t("sites.title"))}</h1>
<table class="grid">
<thead><tr><th>${h(t("sites.col_site"))}</th><th class="num">${h(t("sites.col_sightings"))}</th><th class="num">${h(t("sites.col_birds"))}</th><th>${h(t("sites.col_latest"))}</th></tr></thead>
<tbody>
${rows}
</tbody>
</table>
${backToFeed()}`;
  return page({ title: t("sites.title"), body });
}

export function sitePage({ site, summary }) {
  const kinds = summary.species.map((kind) => `<li>${h(kind.name)}</li>`).join("");
  const body = `<h1>${h(site.name)}</h1>
<p>${h(site.habitat)}</p>
<dl class="facts">
  <dt>${h(t("site.sightings"))}</dt><dd>${summary.sightings}</dd>
  <dt>${h(t("site.birds"))}</dt><dd>${summary.birds}</dd>
  <dt>${h(t("site.latest"))}</dt><dd>${h(summary.latest ?? "")}</dd>
</dl>
<h2>${h(t("site.species_title"))}</h2>
${kinds ? `<ul class="species">${kinds}</ul>` : `<p>${h(t("site.none"))}</p>`}
<p><a id="site-sightings" href="/feed?site=${h(site.id)}">${h(t("act.site_sightings"))}</a></p>
<p class="back"><a id="back-to-sites" href="/sites">${h(t("act.back_to_sites"))}</a></p>`;
  return page({ title: site.name, body });
}

export function summaryPage({ month, lines }) {
  const rows = lines.map((line) => `<tr><td>${h(line.species.name)}</td><td class="num">${line.sightings}</td><td class="num">${line.birds}</td></tr>`).join("\n");
  const birds = lines.reduce((sum, line) => sum + line.birds, 0);
  const sightings = lines.reduce((sum, line) => sum + line.sightings, 0);
  const body = `<h1>${h(t("summary.title"))}</h1>
<p>${h(t("summary.month"))} <strong>${h(month)}</strong></p>
${lines.length ? `<table class="grid">
<thead><tr><th>${h(t("summary.col_species"))}</th><th class="num">${h(t("summary.col_sightings"))}</th><th class="num">${h(t("summary.col_birds"))}</th></tr></thead>
<tbody>
${rows}
</tbody>
<tfoot><tr><th>${h(t("summary.total"))}</th><td class="num">${sightings}</td><td class="num">${birds}</td></tr></tfoot>
</table>` : `<p>${h(t("summary.none"))}</p>`}
<p class="muted">${h(t("summary.note"))}</p>
${backToFeed()}`;
  return page({ title: t("summary.title"), body });
}

export function helpPage() {
  const body = `<h1>${h(t("help.title"))}</h1>
<p>${h(t("help.feed"))}</p>
<p>${h(t("help.record"))}</p>
<p>${h(t("help.review"))}</p>
<p>${h(t("help.tallies"))}</p>
${backToFeed()}`;
  return page({ title: t("help.title"), body });
}

export function messagePage(titleKey, bodyKey) {
  const body = `<h1>${h(t(titleKey))}</h1>
<p>${h(t(bodyKey))}</p>
${backToFeed()}`;
  return page({ title: t(titleKey), body });
}

export { siteName };
