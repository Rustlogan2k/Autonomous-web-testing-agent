/*
 * Wayfarer planner — vanilla JS, hash routes (Back and Forward work everywhere):
 *   #/trips                        trip list and "plan a new trip"
 *   #/trips/<id>                   the itinerary: the version being edited is a local draft
 *   #/trips/<id>/packing           the trip's packing list (bulk actions, click-to-rename)
 *   #/trips/<id>/history           sealed versions, newest first
 *   #/trips/<id>/versions/<n>      one sealed version; its owner can start a draft from it
 *   #/trips/<id>/settings          rename, re-date or move the trip; delete it
 *   #/share/<token>                read-only view of the latest sealed version
 *   #/places?q=&kind=&sort=&page=  the places catalogue: search, filter, sort, pages
 *   #/places/<id>                  one place, which can be added to a day of a trip
 * and, served at its own address for anyone with the link, /share/<token> (BOOT.mode "share").
 *
 * The draft lives in localStorage and is written synchronously on every edit, before the
 * screen changes. Sealing sends the draft to the server, which keeps immutable versions.
 * A trip's owner edits it; someone it is shared with as a viewer sees it read-only.
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
const kindLabel = (code) => t(`kind.${code}`);
const LENGTH_CHOICES = [["30m", t("len.30m")], ["1h", t("len.1h")], ["2h", t("len.2h")], ["half", t("len.half")]];
/** Where the trip list is: in this page, or (on a public share page) in the planner. */
const TRIPS_HREF = BOOT.mode === "share" ? "/#/trips" : "#/trips";

async function request(method, url, payload) {
  const init = { method, credentials: "same-origin", headers: {} };
  if (payload !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(payload);
  }
  const reply = await fetch(url, init);
  let data = {};
  try { data = await reply.json(); } catch { data = {}; }
  if (data.reason === "signed-out") {
    window.location.assign("/login");
    return new Promise(() => {}); // the page is being replaced by the sign-in page
  }
  return { ok: reply.ok && data.ok !== false, status: reply.status, data };
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

/** A message for the next view to show once (after a redirecting action). */
let flash = "";
function takeFlash() {
  const text = flash;
  flash = "";
  return text;
}

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

/* Draft model: { base, next, days: [{ order: [stopId, ...] }], stops: { stopId: { name, length, kind } } } */
function draftFromSealed(sealed, dayCount) {
  const days = sealed ? sealed.days : Array.from({ length: dayCount }, () => []);
  const model = { base: sealed ? sealed.version : 0, next: (sealed && sealed.next_stop) || 1, days: [], stops: {} };
  for (const stops of days) {
    model.days.push({ order: stops.map((stop) => stop.id) });
    for (const stop of stops) { model.stops[stop.id] = { name: stop.name, length: stop.length, kind: stop.kind }; }
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
  return model.days.map((day) => day.order.map((id) => ({ id, ...model.stops[id] })));
}

function sameItinerary(a, b) {
  const shape = (days) => JSON.stringify(days.map((stops) => stops.map((s) => [s.name, s.length, s.kind])));
  return shape(a) === shape(b);
}

/** Keep `draft` for trip `tripId` only while it differs from what was sealed. */
function storeDraft(tripId, draft, sealedDaysList) {
  if (sameItinerary(daysOf(draft), sealedDaysList)) {
    local.remove("draft", tripId); // back to what was sealed: nothing to keep
  } else {
    local.set("draft", tripId, draft);
  }
}

function favourites(tripId) {
  const list = local.get("favourites", tripId);
  return new Set(Array.isArray(list) ? list : []);
}

// -- dialogs (a dialog takes over the page until it is closed) ------------------------------------

function openDialog(html) {
  $("view").hidden = true;
  $("dialog-root").innerHTML = `<div class="backdrop"><div class="dialog" role="dialog" aria-modal="true"
    aria-labelledby="dialog-title">${html}</div></div>`;
}

function closeDialog() {
  $("dialog-root").innerHTML = "";
  $("view").hidden = false;
}

// -- the trip pages' shared header and tabs -------------------------------------------------------

const planner = { trip: null, draft: null, notice: "", sealing: false, sharing: false };

function tripHeader(trip, active) {
  const tabs = [["itinerary", `#/trips/${trip.id}`, t("tab.itinerary")],
    ["packing", `#/trips/${trip.id}/packing`, t("tab.packing")],
    ["history", `#/trips/${trip.id}/history`, t("tab.history")]];
  if (trip.can_edit) { tabs.push(["settings", `#/trips/${trip.id}/settings`, t("tab.settings")]); }
  const strip = tabs.map(([key, href, label]) => {
    const badge = key === "packing"
      ? ` <span class="badge" id="packing-badge">${trip.packing.packed}/${trip.packing.total}</span>` : "";
    return key === active
      ? `<span class="tab current" id="tab-${key}" aria-current="page">${h(label)}${badge}</span>`
      : `<a class="tab" id="tab-${key}" href="${href}">${h(label)}${badge}</a>`;
  }).join("");
  return `
    <h1 id="trip-title">${h(trip.name)}</h1>
    <p class="muted" id="trip-dates">${trip.days} ${h(daysWord(trip.days))} · ${h(t("label.starts"))} ${h(trip.start_label)} · ${h(trip.region)}</p>
    ${trip.can_edit ? "" : `<p class="flag" id="read-only-note">${h(t("label.shared_by"))} ${h(trip.owner)} · ${h(t("label.read_only"))}</p>`}
    <nav class="tabs" aria-label="${h(t("label.trip_sections"))}">${strip}</nav>`;
}

/** Fetch a trip page's data; show the access notice or "not found" when it cannot be had. */
async function tripData(url, isCurrent) {
  const reply = await request("GET", url);
  if (!isCurrent()) { return null; }
  if (reply.data.reason === "forbidden") { showAccessNotice(); return null; }
  if (!reply.ok) { showMissing(t("title.missing_trip"), t("msg.missing_trip")); return null; }
  return reply.data;
}

// -- the itinerary (#/trips/<id>) -----------------------------------------------------------------

function sealedDays(trip) {
  return trip.latest ? trip.latest.days : Array.from({ length: trip.days }, () => []);
}

function draftDiffers() {
  return !sameItinerary(daysOf(planner.draft), sealedDays(planner.trip));
}

/** Persist the draft. Runs before the screen changes, on every edit. */
function saveDraft() {
  storeDraft(planner.trip.id, planner.draft, sealedDays(planner.trip));
}

function showDraftStatus() {
  $("draft-status").textContent = draftDiffers() ? t("status.draft") : "";
}

function showNotice(text) {
  planner.notice = text;
  $("trip-notice").textContent = text;
}

function stopRow(id, stop, starred, editable) {
  const edits = editable ? `
        <button type="button" id="up-${h(id)}" data-nudge="-1">${h(t("act.nudge_up"))}</button>
        <button type="button" id="down-${h(id)}" data-nudge="1">${h(t("act.nudge_down"))}</button>
        <button type="button" id="strike-${h(id)}" data-strike="1">${h(t("act.strike"))}</button>` : "";
  const name = editable
    ? `<button type="button" class="stop-name link" id="stop-${h(id)}" data-edit="1">${h(stop.name)}</button>`
    : `<span class="stop-name">${h(stop.name)}</span>`;
  return `<li class="stop" data-stop="${h(id)}">
      ${name}
      <span class="stop-length">${h(lengthLabel(stop.length))}</span>
      <span class="stop-kind">${h(kindLabel(stop.kind))}</span>
      <span class="stop-tools">${edits}
        <button type="button" id="fav-${h(id)}" class="fav" data-fav="1" aria-pressed="${starred ? "true" : "false"}">${h(t("act.favourite"))}</button>
      </span>
    </li>`;
}

function pinForm(n) {
  return `
      <div class="pin" role="group" aria-labelledby="day-${n}-title">
        <span><label for="stop-name-${n}">${h(t("field.stop_name"))}</label>
          <input type="text" id="stop-name-${n}" name="stop_name" autocomplete="off"></span>
        <span><label for="stop-length-${n}">${h(t("field.stop_length"))}</label>
          <select id="stop-length-${n}" name="stop_length">${LENGTH_CHOICES.map(([value, label]) =>
            `<option value="${value}"${value === "1h" ? " selected" : ""}>${h(label)}</option>`).join("")}</select></span>
        <button type="button" id="pin-${n}">${h(t("act.pin"))}</button>
      </div>
      <p class="problem" id="pin-problem-${n}" role="alert"></p>`;
}

function renderTrip() {
  const { trip, draft } = planner;
  const latest = trip.latest;
  const editable = trip.can_edit;
  const ownerTools = editable ? `
      <button type="button" id="seal" class="lead">${h(t("act.seal"))}</button>
      <button type="button" id="share">${h(t("act.share"))}</button>` : "";
  $("view").innerHTML = `${tripHeader(trip, "itinerary")}
    <p><output id="sealed-version">${h(latest ? `${t("label.sealed_version")} ${latest.version}` : t("label.not_sealed"))}</output>
       <span class="flag" id="draft-status" role="status"></span></p>
    <div class="toolbar">${ownerTools}
      <button type="button" id="weather" aria-expanded="false" aria-controls="weather-panel">${h(t("act.weather"))}</button>
      <a id="back-trips" href="#/trips">${h(t("act.all_trips"))}</a>
    </div>
    <p class="notice" id="trip-notice" role="status"></p>
    <aside class="weather" id="weather-panel" hidden>
      <h2>${h(t("title.weather"))}</h2>
      <ul>${trip.outlook.map((day) => `<li>${h(day.label)}: ${h(t(`weather.${day.sky}`))}, ${day.high_c}°C</li>`).join("")}</ul>
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
      <ol class="stops" id="day-${n}-stops">${dayOrder.map((id) => stopRow(id, draft.stops[id], starred.has(id), editable)).join("")}</ol>
      <p class="muted" id="day-${n}-empty"${dayOrder.length ? " hidden" : ""}>${h(t("msg.empty_day"))}</p>${editable ? pinForm(n) : ""}`;
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
      } else if (button.dataset.edit) {
        editStop(row.dataset.stop);
      }
    });
    if (editable) { $(`pin-${n}`).addEventListener("click", () => pinStop(dayIndex)); }
  });

  if (editable) {
    $("seal").addEventListener("click", sealItinerary);
    $("share").addEventListener("click", shareTrip);
  }
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
  draft.stops[id] = { name, length: $(`stop-length-${n}`).value, kind: "other" };
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

/** The stop editor: rename a stop, change its time, or move it to another day. */
function editStop(stopId) {
  const { draft, trip } = planner;
  const stop = draft.stops[stopId];
  const dayIndex = draft.days.findIndex((day) => day.order.includes(stopId));
  openDialog(`
    <h2 id="dialog-title">${h(t("title.edit_stop"))}</h2>
    <label for="stop-edit-name">${h(t("field.stop_name"))}</label>
    <input type="text" id="stop-edit-name" name="stop_edit_name" value="${h(stop.name)}" autocomplete="off">
    <label for="stop-edit-length">${h(t("field.stop_length"))}</label>
    <select id="stop-edit-length" name="stop_edit_length">${LENGTH_CHOICES.map(([value, label]) =>
      `<option value="${value}"${value === stop.length ? " selected" : ""}>${h(label)}</option>`).join("")}</select>
    <label for="stop-edit-day">${h(t("field.stop_day"))}</label>
    <select id="stop-edit-day" name="stop_edit_day">${trip.day_labels.map((label, i) =>
      `<option value="${i}"${i === dayIndex ? " selected" : ""}>${h(t("label.day"))} ${i + 1} · ${h(label)}</option>`).join("")}</select>
    <p class="problem" id="stop-edit-problem" role="alert"></p>
    <div class="toolbar">
      <button type="button" id="stop-save" class="lead">${h(t("act.save_stop"))}</button>
      <button type="button" id="stop-cancel">${h(t("act.cancel"))}</button>
    </div>`);
  $("stop-cancel").addEventListener("click", closeDialog);
  $("stop-save").addEventListener("click", () => {
    const name = $("stop-edit-name").value.trim();
    const problem = $("stop-edit-problem");
    if (!name) { problem.textContent = t("msg.stop_name_needed"); return; }
    if (name.length > BOOT.limits.stopName) { problem.textContent = t("msg.stop_name_long"); return; }
    const target = Number($("stop-edit-day").value);
    draft.stops[stopId] = { ...stop, name, length: $("stop-edit-length").value };
    if (target !== dayIndex) { // moved to another day: it goes to the end of that day
      draft.days[dayIndex].order = draft.days[dayIndex].order.filter((id) => id !== stopId);
      draft.days[target].order.push(stopId);
    }
    saveDraft();
    closeDialog();
    planner.notice = target !== dayIndex ? `${t("msg.moved_to_day")} ${target + 1}` : t("msg.stop_saved");
    renderTrip();
  });
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
    trip = await tripData(`/api/trips/${tripId}`, isCurrent);
    if (!trip) { return; }
  }
  planner.trip = trip;
  planner.draft = itineraryFor(trip.id, trip.latest, { preferDraft: true, dayCount: trip.days });
  planner.notice = takeFlash();
  planner.sealing = false;
  planner.sharing = false;
  renderTrip();
}

// -- the packing list (#/trips/<id>/packing) ------------------------------------------------------

const packing = { items: [], canEdit: false, selected: new Set(), editing: null, notice: "", busy: false };

/** One packing request at a time: a second click while one is on its way does nothing. */
async function packingRequest(path, body) {
  if (packing.busy) { return null; }
  packing.busy = true;
  try {
    return await request("POST", `/api/${path}`, body);
  } finally {
    packing.busy = false;
  }
}

function countPacked(items) {
  return { packed: items.filter((item) => item.packed).length, total: items.length };
}

/** The packed count in the summary line and on the tab's badge. */
function showPackingCounts() {
  const now = countPacked(packing.items);
  $("packing-done").textContent = `${now.packed} ${t("label.of")} ${now.total} ${t("label.packed")}`;
  const badge = countPacked(packing.items); // the tab's badge follows the list as it stands
  $("packing-badge").textContent = `${badge.packed}/${badge.total}`;
}

function itemRow(item) {
  if (!packing.canEdit) {
    return `<li class="item"><span class="item-text">${h(item.text)}</span>
      <span class="flag">${h(item.packed ? t("label.packed_one") : t("label.not_packed"))}</span></li>`;
  }
  const name = packing.editing === item.id ? `
      <label for="item-text-${h(item.id)}">${h(t("field.rename_item"))}</label>
      <input type="text" id="item-text-${h(item.id)}" name="item_rename" value="${h(item.text)}" autocomplete="off">
      <button type="button" id="item-save-${h(item.id)}" data-save="1">${h(t("act.save_item"))}</button>
      <button type="button" id="item-cancel-${h(item.id)}" data-cancel="1">${h(t("act.cancel"))}</button>`
    : `<button type="button" class="item-text link" id="item-name-${h(item.id)}" data-rename="1">${h(item.text)}</button>`;
  return `<li class="item${item.packed ? " packed" : ""}" data-item="${h(item.id)}">
      <input type="checkbox" id="select-${h(item.id)}" name="select_item" data-select="1"${packing.selected.has(item.id) ? " checked" : ""}>
      <label for="select-${h(item.id)}" class="small">${h(t("field.select"))}</label>
      <input type="checkbox" id="pack-${h(item.id)}" name="packed" data-pack="1"${item.packed ? " checked" : ""}>
      <label for="pack-${h(item.id)}" class="small">${h(t("field.packed"))}</label>
      ${name}
      <button type="button" id="item-delete-${h(item.id)}" data-delete="1">${h(t("act.delete_item"))}</button>
    </li>`;
}

function renderPackingList() {
  for (const id of [...packing.selected]) {
    if (!packing.items.some((item) => item.id === id)) { packing.selected.delete(id); }
  }
  $("packing-list").innerHTML = packing.items.length ? packing.items.map(itemRow).join("")
    : `<li class="muted">${h(t("msg.no_items"))}</li>`;
  if (packing.canEdit) {
    const none = packing.selected.size === 0;
    for (const id of ["bulk-pack", "bulk-unpack", "bulk-delete"]) { $(id).disabled = none; }
    $("select-all").checked = packing.items.length > 0 && packing.selected.size === packing.items.length;
    $("selection-count").textContent = `${packing.selected.size} ${t("label.selected")}`;
  }
  $("packing-notice").textContent = packing.notice;
}

/** Apply the server's answer: its list replaces ours, then the rows and counts are redrawn. */
function packingAnswer(reply, notice) {
  if (!reply) { return; } // another request was still on its way
  if (reply.ok && reply.data.gone) {
    packing.items = reply.data.items;
    packing.notice = t("msg.item_gone");
  } else if (!reply.ok) {
    packing.notice = reply.data.error || t("msg.failed");
  } else {
    packing.items = reply.data.items;
    packing.notice = notice;
  }
  renderPackingList();
  showPackingCounts();
}

async function showPacking(tripId, isCurrent) {
  const data = await tripData(`/api/trips/${tripId}/packing`, isCurrent);
  if (!data) { return; }
  planner.trip = data.trip;
  Object.assign(packing, { items: data.items, canEdit: data.can_edit, editing: null, notice: takeFlash() });
  packing.selected = new Set();
  const tools = packing.canEdit ? `
    <div class="pin">
      <span><label for="item-new">${h(t("field.new_item"))}</label>
        <input type="text" id="item-new" name="item_text" autocomplete="off"></span>
      <button type="button" id="item-add">${h(t("act.add_item"))}</button>
    </div>
    <div class="toolbar bulk">
      <input type="checkbox" id="select-all" name="select_all"><label for="select-all">${h(t("field.select_all"))}</label>
      <button type="button" id="bulk-pack">${h(t("act.bulk_pack"))}</button>
      <button type="button" id="bulk-unpack">${h(t("act.bulk_unpack"))}</button>
      <button type="button" id="bulk-delete">${h(t("act.bulk_delete"))}</button>
      <output id="selection-count"></output>
    </div>` : "";
  $("view").innerHTML = `${tripHeader(data.trip, "packing")}
    <p><output id="packing-done"></output></p>${tools}
    <p class="notice" id="packing-notice" role="status"></p>
    <ul class="packing" id="packing-list"></ul>`;
  renderPackingList();
  showPackingCounts();
  if (!packing.canEdit) { return; }
  $("item-add").addEventListener("click", async () => {
    const text = $("item-new").value.trim();
    if (!text) { packing.notice = t("msg.item_needed"); renderPackingList(); return; }
    if (text.length > BOOT.limits.itemText) { packing.notice = t("msg.item_long"); renderPackingList(); return; }
    packingAnswer(await packingRequest(`trips/${tripId}/packing`, { text }), t("msg.item_added"));
    $("item-new").value = "";
  });
  $("select-all").addEventListener("change", (event) => {
    packing.selected = event.target.checked ? new Set(packing.items.map((item) => item.id)) : new Set();
    renderPackingList();
  });
  const bulk = (action, notice) => async () => {
    if (!packing.selected.size) { return; }
    const ids = [...packing.selected];
    if (action === "delete") { packing.selected = new Set(); }
    packingAnswer(await packingRequest(`trips/${tripId}/packing/bulk`, { ids, action }), notice);
  };
  $("bulk-pack").addEventListener("click", bulk("pack", t("msg.bulk_packed")));
  $("bulk-unpack").addEventListener("click", bulk("unpack", t("msg.bulk_unpacked")));
  $("bulk-delete").addEventListener("click", bulk("delete", t("msg.bulk_deleted")));
  $("packing-list").addEventListener("click", async (event) => {
    const control = event.target.closest("input, button");
    const row = control && control.closest("li[data-item]");
    if (!row) { return; }
    const id = row.dataset.item;
    const item = packing.items.find((entry) => entry.id === id);
    if (control.dataset.select) {
      if (control.checked) { packing.selected.add(id); } else { packing.selected.delete(id); }
      renderPackingList();
    } else if (control.dataset.pack) {
      event.preventDefault(); // the server decides; the box is redrawn from its answer
      packingAnswer(await packingRequest(`trips/${tripId}/packing/${id}`, { packed: !item.packed }), "");
    } else if (control.dataset.rename) {
      packing.editing = id;
      renderPackingList();
      $(`item-text-${id}`).focus();
    } else if (control.dataset.cancel) {
      packing.editing = null;
      renderPackingList();
    } else if (control.dataset.save) {
      const text = $(`item-text-${id}`).value.trim();
      if (!text) { packing.notice = t("msg.item_needed"); renderPackingList(); return; }
      if (text.length > BOOT.limits.itemText) { packing.notice = t("msg.item_long"); renderPackingList(); return; }
      packing.editing = null;
      packingAnswer(await packingRequest(`trips/${tripId}/packing/${id}`, { text }), t("msg.item_renamed"));
    } else if (control.dataset.delete) {
      packingAnswer(await packingRequest(`trips/${tripId}/packing/${id}/delete`), t("msg.item_deleted"));
    }
  });
}

// -- history and one sealed version ------------------------------------------------------------------

async function showHistory(tripId, isCurrent) {
  const data = await tripData(`/api/trips/${tripId}/versions`, isCurrent);
  if (!data) { return; }
  planner.trip = data.trip;
  const rows = data.versions.length ? data.versions.map((v) => `
      <li><a id="version-${v.version}" href="#/trips/${h(tripId)}/versions/${v.version}">${h(t("label.sealed_version"))} ${v.version}</a>
        · ${v.stop_count} ${h(stopsWord(v.stop_count))} · ${h(t("label.sealed_by"))} ${h(v.sealed_by)} · ${h(v.sealed_on)}</li>`).join("")
    : `<li class="muted">${h(t("label.not_sealed"))}</li>`;
  $("view").innerHTML = `${tripHeader(data.trip, "history")}
    <ol class="versions" id="version-list">${rows}</ol>`;
}

async function showVersion(tripId, number, isCurrent) {
  const data = await tripData(`/api/trips/${tripId}/versions/${number}`, isCurrent);
  if (!data) { return; }
  const trip = data.trip;
  planner.trip = trip;
  const blocks = data.days.map((stops, i) => `
      <h3>${h(t("label.day"))} ${i + 1} · ${h(trip.day_labels[i])}</h3>
      <ul class="share-stops">${stops.map((stop) => `<li>${h(stop.name)} <span class="stop-length">${h(lengthLabel(stop.length))}</span>
        <span class="stop-kind">${h(kindLabel(stop.kind))}</span></li>`).join("")
        || `<li class="muted">${h(t("msg.empty_day"))}</li>`}</ul>`).join("");
  $("view").innerHTML = `${tripHeader(trip, "history")}
    <h2 id="version-title">${h(t("label.sealed_version"))} ${data.version}</h2>
    <p class="muted"><output id="version-count">${data.stop_count} ${h(stopsWord(data.stop_count))}</output></p>
    <section id="version-days">${blocks}</section>
    <p class="notice" id="version-notice" role="status"></p>
    <div class="toolbar">
      ${trip.can_edit ? `<button type="button" id="use-version">${h(t("act.use_version"))}</button>` : ""}
      <a id="version-back" href="#/trips/${h(tripId)}/history">${h(t("act.back_to_history"))}</a>
    </div>`;
  if (!trip.can_edit) { return; }
  $("use-version").addEventListener("click", () => {
    // A draft starting from this version; new stops still number after every sealed one.
    const next = Math.max(data.next_stop, trip.latest ? trip.latest.next_stop : 1);
    const draft = draftFromSealed({ version: data.version, next_stop: next, days: data.days }, trip.days);
    storeDraft(trip.id, draft, sealedDays(trip));
    flash = `${t("msg.draft_from")} ${data.version}`;
    go(`#/trips/${trip.id}`);
  });
}

// -- trip settings (#/trips/<id>/settings) ----------------------------------------------------------

function isoDay(offset) {
  const [y, m, d] = BOOT.today.split("-").map(Number);
  const when = new Date(Date.UTC(y, m - 1, d + offset));
  return when.toISOString().slice(0, 10);
}

/** Why a trip's name and first day can't be accepted, or "" when they can. */
function tripProblem(name, start) {
  if (!name) { return t("msg.trip_name_needed"); }
  if (name.length > BOOT.limits.tripName) { return t("msg.trip_name_long"); }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(start)) { return t("msg.start_needed"); }
  if (start < isoDay(0)) { return t("msg.start_past"); }
  if (start > isoDay(BOOT.limits.horizonDays)) { return t("msg.start_far"); }
  return "";
}

function regionOptions(regions, selected) {
  return regions.map((region) =>
    `<option value="${h(region.id)}"${region.id === selected ? " selected" : ""}>${h(region.name)}</option>`).join("");
}

async function showSettings(tripId, isCurrent) {
  const trip = await tripData(`/api/trips/${tripId}`, isCurrent);
  if (!trip) { return; }
  if (!trip.can_edit) { showAccessNotice(); return; }
  planner.trip = trip;
  $("view").innerHTML = `${tripHeader(trip, "settings")}
    <section class="planner-form" aria-labelledby="settings-title">
      <h2 id="settings-title">${h(t("title.trip_settings"))}</h2>
      <label for="settings-name">${h(t("field.trip_name"))}</label>
      <input type="text" id="settings-name" name="settings_name" value="${h(trip.name)}" autocomplete="off">
      <label for="settings-start">${h(t("field.trip_start"))}</label>
      <input type="date" id="settings-start" name="settings_start" value="${h(trip.start)}" min="${isoDay(0)}" max="${isoDay(BOOT.limits.horizonDays)}">
      <label for="settings-region">${h(t("field.trip_region"))}</label>
      <select id="settings-region" name="settings_region">${regionOptions(trip.regions, trip.region_id)}</select>
      <p class="problem" id="settings-problem" role="alert"></p>
      <button type="button" id="save-trip" class="lead">${h(t("act.save_trip"))}</button>
      <p class="notice" id="settings-notice" role="status">${h(takeFlash())}</p>
    </section>
    <section class="danger">
      <p class="muted">${h(t("msg.delete_explained"))}</p>
      <button type="button" id="delete-trip">${h(t("act.delete_trip"))}</button>
    </section>`;
  $("save-trip").addEventListener("click", async () => {
    const name = $("settings-name").value.trim();
    const start = $("settings-start").value;
    const problem = tripProblem(name, start);
    $("settings-problem").textContent = problem;
    if (problem) { return; }
    const reply = await request("POST", `/api/trips/${tripId}`, { name, start, region: $("settings-region").value });
    if (!reply.ok) { $("settings-problem").textContent = reply.data.error || t("msg.failed"); return; }
    flash = t("msg.trip_saved");
    if (isCurrent()) { showSettings(tripId, isCurrent); }
  });
  $("delete-trip").addEventListener("click", () => {
    openDialog(`
      <h2 id="dialog-title">${h(t("title.delete_trip"))}</h2>
      <p id="delete-question">${h(t("msg.delete_question"))} ${h(trip.name)}</p>
      <div class="toolbar">
        <button type="button" id="confirm-delete" class="lead">${h(t("act.confirm_delete"))}</button>
        <button type="button" id="cancel-delete">${h(t("act.cancel"))}</button>
      </div>`);
    $("cancel-delete").addEventListener("click", closeDialog);
    $("confirm-delete").addEventListener("click", async () => {
      const reply = await request("POST", `/api/trips/${tripId}/delete`);
      closeDialog();
      if (!reply.ok) { $("settings-problem").textContent = reply.data.error || t("msg.failed"); return; }
      local.remove("draft", tripId);
      local.remove("favourites", tripId);
      flash = `${t("msg.trip_deleted")} ${trip.name}`;
      go("#/trips");
    });
  });
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
        `<li>${h(stop.name)} <span class="stop-length">${h(lengthLabel(stop.length))}</span>
          <span class="stop-kind">${h(kindLabel(stop.kind))}</span></li>`).join("")}</ul>`);
  $("view").innerHTML = `
    <p class="muted">${h(t("title.share"))} · ${h(t("label.read_only"))}</p>
    <h1 id="share-title">${h(share.trip_name)}</h1>
    <p class="muted">${h(t("label.starts"))} ${h(share.start_label)}</p>
    <p class="share-meta"><strong id="share-version">${h(t("label.sealed_version"))} ${share.version}</strong>
      · <strong id="share-count">${share.stop_count} ${h(stopsWord(share.stop_count))}</strong></p>
    <section id="share-days">${blocks.join("")}</section>
    <p><output id="share-shown"></output></p>
    <p><a id="share-permalink" href="/share/${h(token)}">${h(t("act.permalink"))}</a>
       · <a id="share-trips" href="${TRIPS_HREF}">${h(t("act.all_trips"))}</a></p>`;
  const rendered = $("share-days").querySelectorAll("li").length;
  $("share-shown").textContent = `${t("label.showing")} ${rendered} ${stopsWord(rendered)}`;
}

// -- trip list (#/trips) --------------------------------------------------------------------

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
        <td>${h(trip.owner)}${trip.role === "viewer" ? ` <span class="flag">${h(t("label.read_only"))}</span>` : ""}</td>
        <td><a id="open-${h(trip.id)}" href="#/trips/${h(trip.id)}">${h(t("act.open"))}</a></td>
      </tr>`).join("") : `<tr><td colspan="6" class="muted">${h(t("msg.no_trips"))}</td></tr>`;
  const lengths = [];
  for (let n = BOOT.limits.minDays; n <= BOOT.limits.maxDays; n += 1) {
    lengths.push(`<option value="${n}"${n === 3 ? " selected" : ""}>${n} ${h(daysWord(n))}</option>`);
  }
  const regions = reply.ok ? reply.data.regions : [];
  $("view").innerHTML = `
    <h1>${h(t("title.trips"))}</h1>
    <p class="notice" id="trips-notice" role="status">${h(takeFlash())}</p>
    <table>
      <thead><tr><th>${h(t("col.trip"))}</th><th>${h(t("col.starts"))}</th><th>${h(t("col.length"))}</th>
        <th>${h(t("col.sealed"))}</th><th>${h(t("col.organiser"))}</th><th></th></tr></thead>
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
      <label for="trip-region">${h(t("field.trip_region"))}</label>
      <select id="trip-region" name="trip_region">${regionOptions(regions, regions.length ? regions[0].id : "")}</select>
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
  const why = tripProblem(name, start);
  if (why) { problem.textContent = why; return; }
  creating = true;
  const reply = await request("POST", "/api/trips",
    { name, start, days: Number($("trip-days").value), region: $("trip-region").value });
  creating = false;
  if (!reply.ok) { problem.textContent = reply.data.error || t("msg.failed"); return; }
  const hash = `#/trips/${reply.data.id}`;
  handoff.put(hash, reply.data);
  go(hash);
}

// -- the places catalogue (#/places?...) -------------------------------------------------------------

const places = { query: { q: "", kind: "", sort: "name", page: 1 } };
const KIND_CHOICES = ["", ...BOOT.kinds];
const SORT_CHOICES = [["name", "sort.name"], ["name-desc", "sort.name_desc"], ["region", "sort.region"]];

function placesQuery(search) {
  const params = new URLSearchParams(search);
  const page = Number(params.get("page"));
  return { q: params.get("q") || "", kind: params.get("kind") || "", sort: params.get("sort") || "name",
    page: Number.isInteger(page) && page > 0 ? page : 1 };
}

/** The catalogue's address for `query` (defaults left out, so equal queries share one address). */
function placesHash(query) {
  const params = new URLSearchParams();
  if (query.q) { params.set("q", query.q); }
  if (query.kind) { params.set("kind", query.kind); }
  if (query.sort && query.sort !== "name") { params.set("sort", query.sort); }
  if (query.page > 1) { params.set("page", String(query.page)); }
  const text = params.toString();
  return text ? `#/places?${text}` : "#/places";
}

async function showPlaces(search, isCurrent) {
  places.query = placesQuery(search);
  const query = places.query;
  const api = new URLSearchParams({ q: query.q, kind: query.kind, sort: query.sort, page: String(query.page) });
  const reply = await request("GET", `/api/places?${api}`);
  if (!isCurrent()) { return; }
  const data = reply.ok ? reply.data : { items: [], total: 0, page: query.page, pages: 1 };
  const rows = data.items.length ? data.items.map((place) => `
      <li><a id="place-${h(place.id)}" href="#/places/${h(place.id)}">${h(place.name)}</a>
        <span class="muted">${h(place.region)} · ${h(kindLabel(place.kind))} · ${h(lengthLabel(place.length))}</span></li>`).join("")
    : `<li class="muted">${h(t("msg.no_places"))}</li>`;
  $("view").innerHTML = `
    <h1>${h(t("title.places"))}</h1>
    <div class="filters" role="search">
      <span><label for="places-q">${h(t("field.search"))}</label>
        <input type="search" id="places-q" name="places_q" value="${h(query.q)}" autocomplete="off"></span>
      <button type="button" id="places-search">${h(t("act.search"))}</button>
      <span><label for="places-kind">${h(t("field.kind"))}</label>
        <select id="places-kind" name="places_kind">${KIND_CHOICES.map((kind) =>
          `<option value="${h(kind)}"${kind === query.kind ? " selected" : ""}>${h(kind ? kindLabel(kind) : t("label.all_kinds"))}</option>`).join("")}</select></span>
      <span><label for="places-sort">${h(t("field.sort"))}</label>
        <select id="places-sort" name="places_sort">${SORT_CHOICES.map(([value, key]) =>
          `<option value="${value}"${value === query.sort ? " selected" : ""}>${h(t(key))}</option>`).join("")}</select></span>
    </div>
    <p><output id="places-total">${data.total} ${h(t("label.places"))}</output></p>
    <ul class="places" id="places-list">${rows}</ul>
    <nav class="pager" aria-label="${h(t("label.pages"))}">
      ${data.page > 1 ? `<button type="button" id="places-prev">${h(t("act.previous"))}</button>` : ""}
      <span>${h(t("label.page"))} <output id="places-page">${data.page}</output> ${h(t("label.of"))} <output id="places-pages">${data.pages}</output></span>
      ${data.page < data.pages ? `<button type="button" id="places-next">${h(t("act.next"))}</button>` : ""}
    </nav>`;
  const runSearch = () => go(placesHash({ ...places.query, q: $("places-q").value.trim(), page: 1 }));
  $("places-search").addEventListener("click", runSearch);
  $("places-q").addEventListener("keydown", (event) => { if (event.key === "Enter") { runSearch(); } });
  $("places-kind").addEventListener("change", (event) => {
    go(placesHash({ ...places.query, kind: event.target.value, page: 1 }));
  });
  $("places-sort").addEventListener("change", (event) => {
    go(placesHash({ ...places.query, sort: event.target.value })); // same places, so the same page
  });
  if ($("places-prev")) {
    $("places-prev").addEventListener("click", () => go(placesHash({ ...places.query, page: query.page - 1 })));
  }
  if ($("places-next")) {
    $("places-next").addEventListener("click", () => go(placesHash({ ...places.query, page: query.page + 1 })));
  }
}

async function showPlace(placeId, isCurrent) {
  const reply = await request("GET", `/api/places/${placeId}`);
  if (!isCurrent()) { return; }
  if (!reply.ok) { return showMissing(t("title.missing_place"), t("msg.missing_place")); }
  const { place, trips } = reply.data;
  const dayOptions = (trip) => Array.from({ length: trip.days }, (_, i) =>
    `<option value="${i}">${h(t("label.day"))} ${i + 1}</option>`).join("");
  const adder = trips.length ? `
    <section class="planner-form" aria-labelledby="add-title">
      <h2 id="add-title">${h(t("title.add_to_trip"))}</h2>
      <label for="add-trip">${h(t("field.add_trip"))}</label>
      <select id="add-trip" name="add_trip">${trips.map((trip) => `<option value="${h(trip.id)}">${h(trip.name)}</option>`).join("")}</select>
      <label for="add-day">${h(t("field.add_day"))}</label>
      <select id="add-day" name="add_day">${dayOptions(trips[0])}</select>
      <button type="button" id="add-to-trip" class="lead">${h(t("act.add_to_trip"))}</button>
    </section>` : `<p class="muted" id="add-none">${h(t("msg.plan_first"))}</p>`;
  $("view").innerHTML = `
    <h1 id="place-name">${h(place.name)}</h1>
    <p class="muted" id="place-facts">${h(place.region)} · ${h(kindLabel(place.kind))} · ${h(lengthLabel(place.length))}</p>
    <p id="place-summary">${h(place.summary)}</p>
    ${adder}
    <p><a id="place-back" href="${placesHash(places.query)}">${h(t("act.back_to_places"))}</a></p>`;
  if (!trips.length) { return; }
  $("add-trip").addEventListener("change", (event) => {
    $("add-day").innerHTML = dayOptions(trips.find((trip) => trip.id === event.target.value));
  });
  $("add-to-trip").addEventListener("click", () => {
    const trip = trips.find((entry) => entry.id === $("add-trip").value);
    const dayIndex = Number($("add-day").value);
    // The stop joins the trip's local draft, exactly as if it had been pinned in the planner.
    const draft = itineraryFor(trip.id, trip.latest, { preferDraft: true, dayCount: trip.days });
    const id = `s${draft.next}`;
    draft.next += 1;
    draft.stops[id] = { name: place.name, length: place.length, kind: place.kind };
    draft.days[dayIndex].order.push(id);
    storeDraft(trip.id, draft, trip.latest ? trip.latest.days : Array.from({ length: trip.days }, () => []));
    flash = `${t("msg.added_place")} ${dayIndex + 1}`;
    go(`#/trips/${trip.id}`);
  });
}

// -- routing --------------------------------------------------------------------------------

function showMissing(title, message) {
  $("view").innerHTML = `<h1>${h(title)}</h1><p>${h(message)}</p>
    <p><a id="missing-trips" href="${TRIPS_HREF}">${h(t("act.all_trips"))}</a></p>`;
}

/** A trip that is not shared with the signed-in person, or a page only its owner may open. */
function showAccessNotice() {
  planner.trip = null;
  $("view").innerHTML = `<h1>${h(t("title.no_access"))}</h1><p id="access-notice">${h(t("msg.forbidden"))}</p>
    <p><a id="notice-trips" href="#/trips">${h(t("act.all_trips"))}</a></p>`;
}

function go(hash) {
  if (location.hash === hash) { route(); } else { location.hash = hash; }
}

let routeGeneration = 0;
async function route() {
  routeGeneration += 1;
  const mine = routeGeneration;
  const isCurrent = () => mine === routeGeneration;
  closeDialog();
  const hash = location.hash;
  let match;
  if (BOOT.mode === "share") {
    await showShare(BOOT.token, isCurrent); // a public share page shows its one itinerary
  } else if ((match = /^#\/trips\/(t\d+)$/.exec(hash))) {
    await showTrip(match[1], isCurrent);
  } else if ((match = /^#\/trips\/(t\d+)\/packing$/.exec(hash))) {
    await showPacking(match[1], isCurrent);
  } else if ((match = /^#\/trips\/(t\d+)\/history$/.exec(hash))) {
    await showHistory(match[1], isCurrent);
  } else if ((match = /^#\/trips\/(t\d+)\/versions\/(\d+)$/.exec(hash))) {
    await showVersion(match[1], match[2], isCurrent);
  } else if ((match = /^#\/trips\/(t\d+)\/settings$/.exec(hash))) {
    await showSettings(match[1], isCurrent);
  } else if ((match = /^#\/share\/([0-9a-f]{16})$/.exec(hash))) {
    planner.trip = null;
    await showShare(match[1], isCurrent);
  } else if ((match = /^#\/places(?:\?(.*))?$/.exec(hash))) {
    await showPlaces(match[1] || "", isCurrent);
  } else if ((match = /^#\/places\/(p\d+)$/.exec(hash))) {
    await showPlace(match[1], isCurrent);
  } else if (hash === "#/trips") {
    planner.trip = null;
    await showTrips(isCurrent);
  } else {
    history.replaceState(null, "", "#/trips");
    await route();
  }
}

/** Who is signed in, and the Account menu with Sign out (a form post); a link to sign in otherwise. */
function renderAccount() {
  const slot = $("account");
  if (!BOOT.user) {
    slot.innerHTML = `<a id="nav-sign-in" href="/login">${h(t("act.sign_in"))}</a>`;
    return;
  }
  slot.innerHTML = `<span id="signed-in-as">${h(BOOT.user.name)} · ${h(BOOT.user.role)}</span>
    <button type="button" id="nav-account" aria-expanded="false" aria-controls="account-menu">${h(t("nav.account"))}</button>
    <div class="account-menu" id="account-menu" hidden>
      <form method="post" action="/sign-out"><button type="submit" id="sign-out">${h(t("act.sign_out"))}</button></form>
    </div>`;
  $("nav-account").addEventListener("click", (event) => {
    const menu = $("account-menu");
    menu.hidden = !menu.hidden;
    event.currentTarget.setAttribute("aria-expanded", String(!menu.hidden));
  });
}

function start() {
  document.title = t("app.title");
  $("tagline").textContent = t("app.tagline");
  const placesLink = BOOT.mode === "share" ? "/#/places" : "#/places";
  $("primary-nav").innerHTML = `<a id="nav-trips" href="${TRIPS_HREF}">${h(t("nav.trips"))}</a>
    <a id="nav-places" href="${placesLink}">${h(t("nav.places"))}</a>`;
  renderAccount();
  window.addEventListener("hashchange", route);
  route();
}

start();
