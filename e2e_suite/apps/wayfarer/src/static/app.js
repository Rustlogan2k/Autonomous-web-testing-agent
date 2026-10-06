/*
 * Wayfarer planner — vanilla JS, hash routes:
 *   #/trips            trip list and "plan a new trip"
 *   #/trips/<id>       the planner: the itinerary being edited is a local draft
 *   #/share/<token>    read-only view of the latest sealed version
 *
 * The draft lives in localStorage and is written synchronously on every edit, before the
 * screen changes. Sealing sends the draft to the server, which keeps immutable versions.
 */
"use strict";

const BOOT = JSON.parse(document.getElementById("wayfarer-boot").textContent);
const LABELS = BOOT.vocab;

function t(key) {
  return Object.prototype.hasOwnProperty.call(LABELS, key) ? LABELS[key] : key;
}

const ENTITIES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
function h(value) {
  return String(value === undefined || value === null ? "" : value).replace(/[&<>"']/g, (ch) => ENTITIES[ch]);
}

const $ = (id) => document.getElementById(id);
const stopsWord = (n) => (n === 1 ? t("label.stop") : t("label.stops"));
const daysWord = (n) => (n === 1 ? t("label.day_one") : t("label.days"));
const lengthLabel = (code) => t(`len.${code}`);
const LENGTH_CHOICES = [["30m", t("len.30m")], ["1h", t("len.1h")], ["2h", t("len.2h")], ["half", t("len.half")]];

async function request(method, url, payload) {
  const init = { method, credentials: "same-origin", headers: {} };
  if (payload !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(payload);
  }
  const reply = await fetch(url, init);
  let data = {};
  try { data = await reply.json(); } catch { data = {}; }
  return { ok: reply.ok, status: reply.status, data };
}

/* A response that already carries the next view's data is handed to that view, so one
   click costs one request. Taken once: a later visit to the same route fetches afresh. */
const handoff = {
  items: new Map(),
  put(hash, data) { this.items.set(hash, data); },
  take(hash) {
    const data = this.items.get(hash);
    this.items.delete(hash);
    return data;
  },
};

// -- local storage: drafts and favourites ------------------------------------------------------

const local = {
  key(kind, tripId) { return `wayfarer:${BOOT.workspace}:${kind}:${tripId}`; },
  get(kind, tripId) {
    try {
      const raw = window.localStorage.getItem(this.key(kind, tripId));
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  },
  set(kind, tripId, value) { window.localStorage.setItem(this.key(kind, tripId), JSON.stringify(value)); },
  remove(kind, tripId) { window.localStorage.removeItem(this.key(kind, tripId)); },
};

/* Draft model: { base, next, days: [{ order: [stopId, ...] }], stops: { stopId: { name, length } } } */
function draftFromSealed(sealed, dayCount) {
  const days = sealed ? sealed.days : Array.from({ length: dayCount }, () => []);
  const model = { base: sealed ? sealed.version : 0, next: (sealed && sealed.next_stop) || 1, days: [], stops: {} };
  for (const stops of days) {
    model.days.push({ order: stops.map((stop) => stop.id) });
    for (const stop of stops) { model.stops[stop.id] = { name: stop.name, length: stop.length }; }
  }
  return model;
}

function wellFormedDraft(model, dayCount) {
  return Boolean(model) && Array.isArray(model.days) && model.days.length === dayCount
    && model.stops && typeof model.stops === "object" && Number.isInteger(model.next)
    && model.days.every((day) => Array.isArray(day.order) && day.order.every((id) => model.stops[id]));
}

/** The itinerary to show for a trip: the local draft when one is wanted and exists, else the sealed version. */
function itineraryFor(tripId, sealed, { preferDraft, dayCount = sealed ? sealed.days.length : 0 }) {
  const draft = preferDraft ? local.get("draft", tripId) : null;
  if (draft && wellFormedDraft(draft, dayCount)) { return draft; }
  return draftFromSealed(sealed, dayCount);
}

function daysOf(model) {
  return model.days.map((day) => day.order.map((id) => ({ id, name: model.stops[id].name, length: model.stops[id].length })));
}

function sameItinerary(a, b) {
  const shape = (days) => JSON.stringify(days.map((stops) => stops.map((s) => [s.name, s.length])));
  return shape(a) === shape(b);
}

function favourites(tripId) {
  const list = local.get("favourites", tripId);
  return new Set(Array.isArray(list) ? list : []);
}

// -- the planner (#/trips/<id>) -------------------------------------------------------------

const planner = { trip: null, draft: null, notice: "", sealing: false, sharing: false };

function sealedDays(trip) {
  return trip.latest ? trip.latest.days : Array.from({ length: trip.days }, () => []);
}

function draftDiffers() {
  return !sameItinerary(daysOf(planner.draft), sealedDays(planner.trip));
}

/** Persist the draft. Runs before the screen changes, on every edit. */
function saveDraft() {
  if (draftDiffers()) {
    local.set("draft", planner.trip.id, planner.draft);
  } else {
    local.remove("draft", planner.trip.id); // back to what was sealed: nothing to keep
  }
}

function showDraftStatus() {
  $("draft-status").textContent = draftDiffers() ? t("status.draft") : "";
}

function showNotice(text) {
  planner.notice = text;
  $("trip-notice").textContent = text;
}

function stopRow(id, stop, starred) {
  return `<li class="stop" data-stop="${h(id)}">
      <span class="stop-name">${h(stop.name)}</span>
      <span class="stop-length">${h(lengthLabel(stop.length))}</span>
      <span class="stop-tools">
        <button type="button" id="up-${h(id)}" data-nudge="-1">${h(t("act.nudge_up"))}</button>
        <button type="button" id="down-${h(id)}" data-nudge="1">${h(t("act.nudge_down"))}</button>
        <button type="button" id="strike-${h(id)}" data-strike="1">${h(t("act.strike"))}</button>
        <button type="button" id="fav-${h(id)}" class="fav" data-fav="1" aria-pressed="${starred ? "true" : "false"}">${h(t("act.favourite"))}</button>
      </span>
    </li>`;
}

function renderTrip() {
  const { trip, draft } = planner;
  const latest = trip.latest;
  const forecast = [t("weather.sun"), t("weather.cloud"), t("weather.showers")];
  $("view").innerHTML = `
    <h1 id="trip-title">${h(trip.name)}</h1>
    <p class="muted" id="trip-dates">${trip.days} ${h(daysWord(trip.days))} · ${h(t("label.starts"))} ${h(trip.start_label)}</p>
    <p><output id="sealed-version">${h(latest ? `${t("label.sealed_version")} ${latest.version}` : t("label.not_sealed"))}</output>
       <span class="flag" id="draft-status" role="status"></span></p>
    <div class="toolbar">
      <button type="button" id="seal" class="lead">${h(t("act.seal"))}</button>
      <button type="button" id="share">${h(t("act.share"))}</button>
      <button type="button" id="weather" aria-expanded="false" aria-controls="weather-panel">${h(t("act.weather"))}</button>
      <a id="back-trips" href="#/trips">${h(t("act.all_trips"))}</a>
    </div>
    <p class="notice" id="trip-notice" role="status"></p>
    <aside class="weather" id="weather-panel" hidden>
      <h2>${h(t("title.weather"))}</h2>
      <ul>${trip.day_labels.map((label, i) => `<li>${h(label)}: ${h(forecast[i % forecast.length])}</li>`).join("")}</ul>
      <p class="muted">${h(t("weather.note"))}</p>
    </aside>
    <div id="days"></div>`;
  $("trip-notice").textContent = planner.notice;
  showDraftStatus();

  const starred = favourites(trip.id);
  const holder = $("days");
  draft.days.forEach((day, dayIndex) => {
    const n = dayIndex + 1;
    const dayOrder = day.order; // the order the rows below are drawn in
    const section = document.createElement("section");
    section.className = "day";
    section.innerHTML = `
      <h2 id="day-${n}-title">${h(t("label.day"))} ${n} · ${h(trip.day_labels[dayIndex])}</h2>
      <ol class="stops" id="day-${n}-stops">${dayOrder.map((id) => stopRow(id, draft.stops[id], starred.has(id))).join("")}</ol>
      <p class="muted" id="day-${n}-empty"${dayOrder.length ? " hidden" : ""}>${h(t("msg.empty_day"))}</p>
      <div class="pin" role="group" aria-labelledby="day-${n}-title">
        <span><label for="stop-name-${n}">${h(t("field.stop_name"))}</label>
          <input type="text" id="stop-name-${n}" name="stop_name" autocomplete="off"></span>
        <span><label for="stop-length-${n}">${h(t("field.stop_length"))}</label>
          <select id="stop-length-${n}" name="stop_length">${LENGTH_CHOICES.map(([value, label]) =>
            `<option value="${value}"${value === "1h" ? " selected" : ""}>${h(label)}</option>`).join("")}</select></span>
        <button type="button" id="pin-${n}">${h(t("act.pin"))}</button>
      </div>
      <p class="problem" id="pin-problem-${n}" role="alert"></p>`;
    holder.appendChild(section);

    $(`day-${n}-stops`).addEventListener("click", (event) => {
      const button = event.target.closest("button");
      const row = button && button.closest("li[data-stop]");
      if (!row) { return; }
      if (button.dataset.nudge) {
        const order = planner.draft.days[dayIndex].order; // the day's stops, top to bottom
        nudgeStop(order, row, Number(button.dataset.nudge), button);
      } else if (button.dataset.strike) {
        strikeStop(dayIndex, row);
      } else if (button.dataset.fav) {
        toggleFavourite(row.dataset.stop, button);
      }
    });
    $(`pin-${n}`).addEventListener("click", () => pinStop(dayIndex));
  });

  $("seal").addEventListener("click", sealItinerary);
  $("share").addEventListener("click", shareTrip);
  $("weather").addEventListener("click", (event) => {
    const panel = $("weather-panel");
    panel.hidden = !panel.hidden;
    event.currentTarget.setAttribute("aria-expanded", String(!panel.hidden));
  });
}

function pinStop(dayIndex) {
  const n = dayIndex + 1;
  const field = $(`stop-name-${n}`);
  const name = field.value.trim();
  const problem = $(`pin-problem-${n}`);
  if (!name) { problem.textContent = t("msg.stop_name_needed"); return; }
  if (name.length > BOOT.limits.stopName) { problem.textContent = t("msg.stop_name_long"); return; }
  const { draft } = planner;
  const id = `s${draft.next}`;
  draft.next += 1;
  draft.stops[id] = { name, length: $(`stop-length-${n}`).value };
  draft.days[dayIndex].order.push(id);
  saveDraft();
  planner.notice = "";
  renderTrip();
  $(`stop-name-${n}`).focus();
}

/* Strike and nudge edit the list in place (no full redraw), so keyboard focus stays in the
   list. The draft is changed and saved first; the rows are then moved to match it. */
function strikeStop(dayIndex, row) {
  const day = planner.draft.days[dayIndex];
  const id = row.dataset.stop;
  day.order = day.order.filter((other) => other !== id);
  delete planner.draft.stops[id];
  saveDraft();
  const list = row.parentElement;
  const next = row.nextElementSibling || row.previousElementSibling;
  row.remove();
  $(`day-${dayIndex + 1}-empty`).hidden = list.children.length > 0;
  if (next) { next.querySelector("button").focus(); }
  showDraftStatus();
}

function nudgeStop(order, row, delta, button) {
  const id = row.dataset.stop;
  const from = order.indexOf(id);
  const to = from + delta;
  if (from < 0 || to < 0 || to >= order.length) { return; } // already first (or last): nothing moves
  order.splice(from, 1);
  order.splice(to, 0, id);
  saveDraft();
  const neighbour = delta < 0 ? row.previousElementSibling : row.nextElementSibling;
  if (neighbour) {
    if (delta < 0) { neighbour.before(row); } else { neighbour.after(row); }
  }
  button.focus();
  showDraftStatus();
}

function toggleFavourite(stopId, button) {
  const tripId = planner.trip.id;
  const starred = favourites(tripId);
  if (starred.has(stopId)) { starred.delete(stopId); } else { starred.add(stopId); }
  local.set("favourites", tripId, [...starred].sort());
  button.setAttribute("aria-pressed", starred.has(stopId) ? "true" : "false");
}

async function sealItinerary() {
  if (planner.sealing) { return; }
  const days = daysOf(planner.draft);
  if (!days.some((stops) => stops.length)) { showNotice(t("msg.need_stop")); return; }
  const tripId = planner.trip.id;
  planner.sealing = true;
  const reply = await request("POST", `/api/trips/${tripId}/seal`, { days, next_stop: planner.draft.next });
  planner.sealing = false;
  if (!planner.trip || planner.trip.id !== tripId) { return; } // the planner was left meanwhile
  if (!reply.ok) { showNotice(reply.data.error || t("msg.failed")); return; }
  const trip = reply.data.trip;
  planner.trip = trip;
  if (reply.data.created) {
    local.remove("draft", tripId);
    planner.draft = draftFromSealed(trip.latest, trip.days);
    planner.notice = `${t("msg.sealed")} ${trip.latest.version}`;
  } else {
    planner.notice = `${t("msg.nothing_new")} ${trip.latest.version}`;
  }
  renderTrip();
}

async function shareTrip() {
  if (planner.sharing) { return; }
  if (!planner.trip.latest) { showNotice(t("msg.seal_first")); return; }
  planner.sharing = true;
  const reply = await request("POST", `/api/trips/${planner.trip.id}/share`);
  planner.sharing = false;
  if (!reply.ok) { showNotice(reply.data.error || t("msg.failed")); return; }
  const hash = `#/share/${reply.data.token}`;
  handoff.put(hash, reply.data.share);
  go(hash);
}

async function showTrip(tripId, isCurrent) {
  const hash = `#/trips/${tripId}`;
  let trip = handoff.take(hash);
  if (!trip) {
    const reply = await request("GET", `/api/trips/${tripId}`);
    if (!isCurrent()) { return; }
    if (!reply.ok) { return showMissing(t("title.missing_trip"), t("msg.missing_trip")); }
    trip = reply.data;
  }
  planner.trip = trip;
  planner.draft = itineraryFor(trip.id, trip.latest, { preferDraft: true, dayCount: trip.days });
  planner.notice = "";
  planner.sealing = false;
  planner.sharing = false;
  renderTrip();
}

// -- share view (#/share/<token>) -----------------------------------------------------------

async function showShare(token, isCurrent) {
  let share = handoff.take(`#/share/${token}`);
  if (!share) {
    const reply = await request("GET", `/api/share/${token}`);
    if (!isCurrent()) { return; }
    if (!reply.ok) { return showMissing(t("title.missing_share"), t("msg.missing_share")); }
    share = reply.data;
  }
  const sealed = { version: share.version, next_stop: 0, days: share.days.map((day) => day.stops) };
  const shown = itineraryFor(share.trip, sealed, { preferDraft: false }); // a share link shows what was sealed
  const blocks = daysOf(shown).map((stops, i) => `
      <h3>${h(t("label.day"))} ${i + 1} · ${h(share.days[i].label)}</h3>
      <ul class="share-stops">${stops.map((stop) =>
        `<li>${h(stop.name)} <span class="stop-length">${h(lengthLabel(stop.length))}</span></li>`).join("")}</ul>`);
  $("view").innerHTML = `
    <p class="muted">${h(t("title.share"))} · ${h(t("label.read_only"))}</p>
    <h1 id="share-title">${h(share.trip_name)}</h1>
    <p class="muted">${h(t("label.starts"))} ${h(share.start_label)}</p>
    <p class="share-meta"><strong id="share-version">${h(t("label.sealed_version"))} ${share.version}</strong>
      · <strong id="share-count">${share.stop_count} ${h(stopsWord(share.stop_count))}</strong></p>
    <section id="share-days">${blocks.join("")}</section>
    <p><output id="share-shown"></output></p>
    <p><a id="share-permalink" href="#/share/${h(token)}">${h(t("act.permalink"))}</a>
       · <a id="share-trips" href="#/trips">${h(t("act.all_trips"))}</a></p>`;
  const rendered = $("share-days").querySelectorAll("li").length;
  $("share-shown").textContent = `${t("label.showing")} ${rendered} ${stopsWord(rendered)}`;
}

// -- trip list (#/trips) --------------------------------------------------------------------

function isoDay(offset) {
  const [y, m, d] = BOOT.today.split("-").map(Number);
  const when = new Date(Date.UTC(y, m - 1, d + offset));
  return when.toISOString().slice(0, 10);
}

async function showTrips(isCurrent) {
  const reply = await request("GET", "/api/trips");
  if (!isCurrent()) { return; }
  const trips = reply.ok ? reply.data.trips : [];
  const rows = trips.length ? trips.map((trip) => `
      <tr>
        <td>${h(trip.name)}</td>
        <td>${h(trip.start_label)}</td>
        <td>${trip.days} ${h(daysWord(trip.days))}</td>
        <td>${h(trip.latest_version ? `${t("label.sealed_version")} ${trip.latest_version}` : t("label.not_sealed"))}</td>
        <td><a id="open-${h(trip.id)}" href="#/trips/${h(trip.id)}">${h(t("act.open"))}</a></td>
      </tr>`).join("") : `<tr><td colspan="5" class="muted">${h(t("msg.no_trips"))}</td></tr>`;
  const lengths = [];
  for (let n = BOOT.limits.minDays; n <= BOOT.limits.maxDays; n += 1) {
    lengths.push(`<option value="${n}"${n === 3 ? " selected" : ""}>${n} ${h(daysWord(n))}</option>`);
  }
  $("view").innerHTML = `
    <h1>${h(t("title.trips"))}</h1>
    <table>
      <thead><tr><th>${h(t("col.trip"))}</th><th>${h(t("col.starts"))}</th><th>${h(t("col.length"))}</th>
        <th>${h(t("col.sealed"))}</th><th></th></tr></thead>
      <tbody id="trip-list">${rows}</tbody>
    </table>
    <section class="planner-form" aria-labelledby="new-trip-title">
      <h2 id="new-trip-title">${h(t("title.new_trip"))}</h2>
      <label for="trip-name">${h(t("field.trip_name"))}</label>
      <input type="text" id="trip-name" name="trip_name" autocomplete="off">
      <label for="trip-start">${h(t("field.trip_start"))}</label>
      <input type="date" id="trip-start" name="trip_start" min="${isoDay(0)}" max="${isoDay(BOOT.limits.horizonDays)}">
      <label for="trip-days">${h(t("field.trip_days"))}</label>
      <select id="trip-days" name="trip_days">${lengths.join("")}</select>
      <p class="problem" id="trip-problem" role="alert"></p>
      <button type="button" id="create-trip" class="lead">${h(t("act.create_trip"))}</button>
    </section>`;
  $("create-trip").addEventListener("click", createTrip);
}

let creating = false;
async function createTrip() {
  if (creating) { return; }
  const problem = $("trip-problem");
  const name = $("trip-name").value.trim();
  const start = $("trip-start").value;
  if (!name) { problem.textContent = t("msg.trip_name_needed"); return; }
  if (name.length > BOOT.limits.tripName) { problem.textContent = t("msg.trip_name_long"); return; }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(start)) { problem.textContent = t("msg.start_needed"); return; }
  if (start < isoDay(0)) { problem.textContent = t("msg.start_past"); return; }
  if (start > isoDay(BOOT.limits.horizonDays)) { problem.textContent = t("msg.start_far"); return; }
  creating = true;
  const reply = await request("POST", "/api/trips", { name, start, days: Number($("trip-days").value) });
  creating = false;
  if (!reply.ok) { problem.textContent = reply.data.error || t("msg.failed"); return; }
  const hash = `#/trips/${reply.data.id}`;
  handoff.put(hash, reply.data);
  go(hash);
}

// -- routing --------------------------------------------------------------------------------

function showMissing(title, message) {
  $("view").innerHTML = `<h1>${h(title)}</h1><p>${h(message)}</p>
    <p><a id="missing-trips" href="#/trips">${h(t("act.all_trips"))}</a></p>`;
}

function go(hash) {
  if (location.hash === hash) { route(); } else { location.hash = hash; }
}

let routeGeneration = 0;
async function route() {
  routeGeneration += 1;
  const mine = routeGeneration;
  const isCurrent = () => mine === routeGeneration;
  const hash = location.hash;
  let match;
  if ((match = /^#\/trips\/(t\d+)$/.exec(hash))) {
    await showTrip(match[1], isCurrent);
  } else if ((match = /^#\/share\/([0-9a-f]{16})$/.exec(hash))) {
    planner.trip = null;
    await showShare(match[1], isCurrent);
  } else if (hash === "#/trips") {
    planner.trip = null;
    await showTrips(isCurrent);
  } else {
    history.replaceState(null, "", "#/trips");
    await route();
  }
}

function start() {
  document.title = t("app.title");
  $("tagline").textContent = t("app.tagline");
  $("primary-nav").innerHTML = `<a id="nav-trips" href="#/trips">${h(t("nav.trips"))}</a>`;
  window.addEventListener("hashchange", route);
  route();
}

start();
