// Fosterly's pages, rendered on the server. Every visible label comes from the vocabulary
// through t(); references, names, dates, home types and species are data.
//
// The application page is one form of five accordion sections. Its section summaries are
// drawn here for the first paint and redrawn by static/apply.js from the browser's copy of
// the accepted answers (the same templates, passed to the page as labels).

import { CHOICES, SECTIONS, animalsOf, completeness } from "./fields.js";

const vocabulary = {};
let catalogue = { home_types: [], species: [], counts: [], min_weeks: [] };

export function useVocabulary(table, seed) {
  Object.assign(vocabulary, table);
  catalogue = seed;
}

/** A label, with {name} placeholders filled from `params`. */
export function t(key, params = {}) {
  const text = Object.prototype.hasOwnProperty.call(vocabulary, key) ? vocabulary[key] : key;
  return Object.entries(params).reduce((out, [name, value]) => out.split(`{${name}}`).join(String(value)), text);
}

/** Escape text for element content and quoted attribute values. */
export function h(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[ch]);
}

/** JSON safe inside <script type="application/json">. */
function embedded(value) {
  return JSON.stringify(value).replace(/</g, "\\u003c").replace(/>/g, "\\u003e").replace(/&/g, "\\u0026");
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-06-15" -> "15 Jun 2026" (dates are data: the same under every vocabulary). */
export function formatDate(iso) {
  const [year, month, day] = String(iso).split("-");
  return `${Number(day)} ${MONTHS[Number(month) - 1]} ${year}`;
}

const nameOf = (list, id) => list.find((item) => item.id === id)?.name ?? id;
const animalLine = (animal) => animal.count
  ? `${nameOf(catalogue.counts, animal.count)} x ${nameOf(catalogue.species, animal.species)}`
  : nameOf(catalogue.species, animal.species);
const yesNo = (flag) => t(flag ? "answer.yes" : "answer.no");

/** "From 15 Jun 2026, at least 4 weeks", or as much of it as has been answered. */
export function availabilityLine(answers) {
  const date = answers.available_from ? formatDate(answers.available_from) : "";
  const weeks = answers.min_weeks;
  if (date && weeks) return t("summary.from_weeks", { date, weeks });
  if (date) return t("summary.from", { date });
  if (weeks) return t("summary.weeks_only", { weeks });
  return t("summary.not_started");
}

/** The one-line summary under each section's header (static/apply.js mirrors this). */
function summaryOf(section, a) {
  switch (section) {
    case "about": {
      const parts = [a.full_name, a.email, a.phone].map((part) => part.trim()).filter(Boolean);
      return parts.length ? parts.join(", ") : t("summary.not_started");
    }
    case "home": {
      if (!a.home_type) return t("summary.not_started");
      const animals = animalsOf(a).map(animalLine).join(", ");
      const others = a.other_animals ? t("summary.animals", { animals: animals || t("summary.animals_unknown") }) : t("summary.no_animals");
      return [nameOf(catalogue.home_types, a.home_type), t(a.garden ? "summary.garden" : "summary.no_garden"), others].join(", ");
    }
    case "experience":
      return a.years_experience ? t("summary.years", { years: a.years_experience }) : t("summary.not_started");
    case "availability":
      return availabilityLine(a);
    default:
      return t(a.declaration ? "summary.signed" : "summary.unsigned");
  }
}

// -- frame -----------------------------------------------------------------------------------

export function page({ title, body, scripts = [] }) {
  return `<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${h(title)} | ${h(t("app.name"))}</title>
<link rel="icon" href="data:,">
<link rel="stylesheet" href="/static/fosterly.css">
</head>
<body>
<header class="masthead">
  <a class="brand" id="nav-home" href="/">${h(t("app.name"))}</a>
  <nav aria-label="${h(t("nav.label"))}">
    <a id="nav-applications" href="/applications">${h(t("nav.applications"))}</a>
    <a id="nav-help" href="/help/fostering">${h(t("nav.help"))}</a>
  </nav>
</header>
<main>
${body}
</main>
${scripts.map((name) => `<script src="/static/${name}" defer></script>`).join("\n")}
</body>
</html>`;
}

function flashLine(flash) {
  if (!flash) return "";
  const tone = flash.tone === "error" ? "error" : "notice";
  return `<p id="flash" class="${tone}" role="${tone === "error" ? "alert" : "status"}">${h(t(flash.key, flash.params))}</p>`;
}

const sectionNames = (sections) => sections.map((section) => t(`section.${section}`)).join(", ");

// -- start -----------------------------------------------------------------------------------

export function startPage({ drafts, flash }) {
  const saved = drafts.map((draft) => {
    const progress = completeness(draft.answers);
    return `<li><a id="resume-${h(draft.ref)}" href="/apply/${h(draft.ref)}">${h(t("act.resume", { ref: draft.ref }))}</a>
      <span class="muted">${h(t("start.progress", { done: progress.done, of: progress.of }))}</span></li>`;
  }).join("\n    ");
  const body = `<h1>${h(t("start.title"))}</h1>
<p>${h(t("start.intro"))}</p>
${flashLine(flash)}
<form method="post" action="/drafts">
  <button id="start-new" type="submit">${h(t("act.start_new"))}</button>
</form>
<h2>${h(t("start.saved_title"))}</h2>
<p class="muted">${h(t("start.saved_hint"))}</p>
${saved ? `<ul class="drafts">
    ${saved}
</ul>` : `<p>${h(t("start.none"))}</p>`}`;
  return page({ title: t("start.title"), body });
}

// -- the application form --------------------------------------------------------------------

/** Each field's DOM id (the field's name is its key in the draft). */
const DOM_IDS = {
  full_name: "full-name", email: "email", phone: "phone", home_type: "home-type", garden: "garden",
  other_animals: "other-animals", animal_species: "animal-species", animal_count: "animal-count",
  years_experience: "years-experience", experience_notes: "experience-notes", available_from: "available-from",
  min_weeks: "min-weeks", declaration: "declaration",
};

const note = (id) => `<span class="note" id="note-${id}" aria-live="polite"></span>`;

function input(section, name, type, value, attrs = "") {
  const id = DOM_IDS[name];
  return `<div class="field">
      <label for="${id}">${h(t(`field.${name}`))}</label>
      <input id="${id}" name="${name}" type="${type}" value="${h(value)}" data-section="${section}" autocomplete="off"${attrs}>
      ${note(id)}
    </div>`;
}

function checkbox(section, name, checked) {
  const id = DOM_IDS[name];
  return `<div class="field check">
      <input id="${id}" name="${name}" type="checkbox" data-section="${section}"${checked ? " checked" : ""}>
      <label for="${id}">${h(t(`field.${name}`))}</label>
      ${note(id)}
    </div>`;
}

function select(section, name, items, value) {
  const id = DOM_IDS[name];
  const options = items.map((item) =>
    `<option value="${h(item.id)}"${item.id === value ? " selected" : ""}>${h(item.name)}</option>`).join("");
  return `<div class="field">
      <label for="${id}">${h(t(`field.${name}`))}</label>
      <select id="${id}" name="${name}" data-section="${section}"><option value="">${h(t("field.choose"))}</option>${options}</select>
      ${note(id)}
    </div>`;
}

function panelFields(section, a, today) {
  const weeks = CHOICES.min_weeks.map((n) => ({ id: n, name: t("field.weeks_option", { weeks: n }) }));
  switch (section) {
    case "about":
      return [input(section, "full_name", "text", a.full_name),
        input(section, "email", "email", a.email),
        input(section, "phone", "tel", a.phone)].join("\n    ");
    case "home":
      return `${select(section, "home_type", catalogue.home_types, a.home_type)}
    ${checkbox(section, "garden", a.garden)}
    ${checkbox(section, "other_animals", a.other_animals)}
    <fieldset id="animals-group" class="nested"${a.other_animals ? "" : " hidden"}>
      <legend>${h(t("animals.title"))}</legend>
      ${select(section, "animal_species", catalogue.species, a.animal_species)}
      ${select(section, "animal_count", catalogue.counts, a.animal_count)}
    </fieldset>`;
    case "experience":
      return `${input(section, "years_experience", "number", a.years_experience, ' min="0" max="60" step="1" inputmode="numeric"')}
    <div class="field wide">
      <label for="experience-notes">${h(t("field.experience_notes"))}</label>
      <textarea id="experience-notes" name="experience_notes" rows="4" data-section="${section}">${h(a.experience_notes)}</textarea>
      ${note("experience-notes")}
    </div>`;
    case "availability":
      return `${input(section, "available_from", "date", a.available_from, ` min="${h(today)}"`)}
    ${select(section, "min_weeks", weeks, a.min_weeks)}`;
    default:
      return `<p>${h(t("declaration.text"))}</p>
    ${checkbox(section, "declaration", a.declaration)}`;
  }
}

/** Labels static/apply.js needs to redraw summaries and notes in the page's vocabulary. */
const CLIENT_KEYS = ["summary.not_started", "summary.from_weeks", "summary.from", "summary.weeks_only",
  "summary.garden", "summary.no_garden", "summary.animals", "summary.animals_unknown", "summary.no_animals",
  "summary.years", "summary.signed", "summary.unsigned", "status.done", "status.todo",
  "note.saving", "note.saved", "note.offline"];

export function applyPage({ draft, today, flash }) {
  const a = draft.answers;
  const progress = completeness(a);
  const sections = SECTIONS.map((section) => `<section class="acc" data-acc-section="${section}">
  <h2 class="acc-head">
    <button id="acc-${section}" type="button" data-acc="${section}" aria-expanded="false" aria-controls="panel-${section}">${h(t(`section.${section}`))}</button>
    <em class="badge" id="status-${section}">${h(t(progress.sections[section] ? "status.done" : "status.todo"))}</em>
  </h2>
  <p class="summary"><span id="summary-${section}">${h(summaryOf(section, a))}</span></p>
  <div class="panel" id="panel-${section}" hidden>
    ${panelFields(section, a, today)}
  </div>
</section>`).join("\n");
  const labels = Object.fromEntries(CLIENT_KEYS.map((key) => [key, t(key)]));
  const names = {
    home_types: Object.fromEntries(catalogue.home_types.map((item) => [item.id, item.name])),
    species: Object.fromEntries(catalogue.species.map((item) => [item.id, item.name])),
    counts: Object.fromEntries(catalogue.counts.map((item) => [item.id, item.name])),
  };
  const body = `<h1>${h(t("apply.title", { ref: draft.ref }))}</h1>
${flashLine(flash)}
<p class="meter"><output id="completeness">${progress.done}</output> ${h(t("apply.meter_of", { of: progress.of }))}</p>
<p class="muted">${h(t("apply.hint"))}</p>
<form id="application" class="application" data-ref="${h(draft.ref)}" autocomplete="off" novalidate>
${sections}
</form>
<p class="actions"><a id="to-review" class="button" href="/apply/${h(draft.ref)}/review">${h(t("act.to_review"))}</a></p>
<script type="application/json" id="answers-data">${embedded(a)}</script>
<script type="application/json" id="apply-labels">${embedded({ labels, names })}</script>`;
  return page({ title: t("apply.title", { ref: draft.ref }), body, scripts: ["apply.js"] });
}

// -- review and submission -------------------------------------------------------------------

function answerRows(a) {
  const weeks = a.min_weeks ? t("field.weeks_option", { weeks: a.min_weeks }) : "";
  const shown = (value) => (value === "" ? t("answer.none") : value);
  const animals = animalsOf(a).map(animalLine).join(", ");
  return {
    about: [["field.full_name", shown(a.full_name)], ["field.email", shown(a.email)], ["field.phone", shown(a.phone)]],
    home: [["field.home_type", a.home_type ? nameOf(catalogue.home_types, a.home_type) : t("answer.none")],
      ["field.garden", yesNo(a.garden)], ["field.other_animals", yesNo(a.other_animals)],
      ...(a.other_animals ? [["animals.title", animals || t("answer.none")]] : [])],
    experience: [["field.years_experience", shown(a.years_experience)], ["field.experience_notes", shown(a.experience_notes)]],
    availability: [["field.available_from", a.available_from ? formatDate(a.available_from) : t("answer.none")],
      ["field.min_weeks", shown(weeks)]],
    declaration: [["review.declared", yesNo(a.declaration)]],
  };
}

export function reviewPage({ draft, flash }) {
  const progress = completeness(draft.answers);
  const rows = answerRows(draft.answers);
  const blocks = SECTIONS.map((section) => `<section class="check">
  <h2>${h(t(`section.${section}`))} <em class="badge">${h(t(progress.sections[section] ? "status.done" : "status.todo"))}</em></h2>
  <dl class="facts">
    ${rows[section].map(([key, value]) => `<dt>${h(t(key))}</dt><dd>${h(value)}</dd>`).join("\n    ")}
  </dl>
</section>`).join("\n");
  const status = progress.done === progress.of
    ? t("review.ready")
    : t("review.not_ready", { done: progress.done, of: progress.of });
  const body = `<h1>${h(t("review.title"))}</h1>
<p>${h(t("review.for", { ref: draft.ref }))}</p>
${flashLine(flash)}
<p class="status">${h(t("review.status"))} <strong id="review-status">${h(status)}</strong></p>
${blocks}
<form class="submit" method="post" action="/apply/${h(draft.ref)}/submit">
  <button id="submit-application" type="submit">${h(t("act.submit"))}</button>
</form>
<p class="actions">
  <a id="back-to-form" href="/apply/${h(draft.ref)}">${h(t("act.back_to_form"))}</a>
  <button id="print-copy" type="button" class="quiet" aria-controls="print-note">${h(t("act.print"))}</button>
</p>
<p id="print-note" class="panel" hidden>${h(t("review.print_note"))}</p>`;
  return page({ title: t("review.title"), body, scripts: ["review.js"] });
}

export function missingSections(missing) {
  return sectionNames(missing);
}

export function applicationPage({ application, flash }) {
  const a = application;
  const weeks = a.min_weeks ? t("field.weeks_option", { weeks: a.min_weeks }) : t("answer.none");
  const body = `<h1>${h(t("application.title"))} <strong id="sub-ref">${h(a.ref)}</strong></h1>
${flashLine(flash)}
<p>${h(t("application.received"))}</p>
<dl class="facts">
  <dt>${h(t("application.submitted_on"))}</dt><dd>${h(formatDate(a.submitted_on))}</dd>
  <dt>${h(t("application.draft"))}</dt><dd>${h(a.from)}</dd>
  <dt>${h(t("field.full_name"))}</dt><dd>${h(a.full_name)}</dd>
  <dt>${h(t("field.email"))}</dt><dd>${h(a.email)}</dd>
  <dt>${h(t("field.phone"))}</dt><dd>${h(a.phone)}</dd>
  <dt>${h(t("field.home_type"))}</dt><dd>${h(nameOf(catalogue.home_types, a.home_type))}</dd>
  <dt>${h(t("field.garden"))}</dt><dd>${h(yesNo(a.garden))}</dd>
  <dt>${h(t("field.other_animals"))}</dt><dd><span id="sub-other-animals">${h(yesNo(a.other_animals))}</span></dd>
  <dt>${h(t("animals.title"))}</dt><dd><span id="sub-animals">${h(a.animals.map(animalLine).join(", "))}</span></dd>
  <dt>${h(t("field.years_experience"))}</dt><dd>${h(a.years_experience)}</dd>
  <dt>${h(t("field.experience_notes"))}</dt><dd>${h(a.experience_notes || t("answer.none"))}</dd>
  <dt>${h(t("application.availability"))}</dt><dd><span id="sub-available">${h(availabilityLine(a))}</span></dd>
  <dt>${h(t("field.min_weeks"))}</dt><dd>${h(weeks)}</dd>
</dl>
<p class="back"><a id="back-to-applications" href="/applications">${h(t("act.all_applications"))}</a></p>`;
  return page({ title: `${t("application.title")} ${a.ref}`, body });
}

export function applicationsPage({ applications, drafts }) {
  const rows = applications.map((a) => `<tr>
  <td><a id="app-${h(a.ref)}" href="/applications/${h(a.ref)}">${h(a.ref)}</a></td>
  <td>${h(formatDate(a.submitted_on))}</td>
  <td>${h(a.full_name)}</td>
</tr>`).join("\n");
  const body = `<h1>${h(t("applications.title"))}</h1>
${applications.length ? `<table class="grid">
<thead><tr><th>${h(t("applications.col_ref"))}</th><th>${h(t("applications.col_date"))}</th><th>${h(t("applications.col_name"))}</th></tr></thead>
<tbody>
${rows}
</tbody>
</table>` : `<p>${h(t("applications.none"))}</p>`}
<p class="muted">${h(t("applications.drafts", { count: drafts.length }))}</p>
<p class="back"><a id="back-home" href="/">${h(t("act.back_home"))}</a></p>`;
  return page({ title: t("applications.title"), body });
}

export function submittedDraftPage({ draftRef, application }) {
  const body = `<h1>${h(t("submitted.title"))}</h1>
<p>${h(t("submitted.body", { draft: draftRef, ref: application.ref }))}</p>
<p><a id="view-application" href="/applications/${h(application.ref)}">${h(t("act.view_application"))}</a></p>`;
  return page({ title: t("submitted.title"), body });
}

export function helpPage() {
  const body = `<h1>${h(t("help.title"))}</h1>
<p>${h(t("help.what"))}</p>
<p>${h(t("help.costs"))}</p>
<p>${h(t("help.form"))}</p>
<p>${h(t("help.after"))}</p>
<p class="back"><a id="back-home" href="/">${h(t("act.back_home"))}</a></p>`;
  return page({ title: t("help.title"), body });
}

export function messagePage(titleKey, bodyKey) {
  const body = `<h1>${h(t(titleKey))}</h1>
<p>${h(t(bodyKey))}</p>
<p class="back"><a id="back-home" href="/">${h(t("act.back_home"))}</a></p>`;
  return page({ title: t(titleKey), body });
}
