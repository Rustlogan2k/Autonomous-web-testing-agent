/* ctxbench core: one equipment-loan request task, rendered under the structure, vocabulary
   and decoy setting in config.js. This file is byte-identical in every variant; the seeded
   faults are patches to it. State lives in sessionStorage, so a fresh browser context
   starts a fresh request. */
"use strict";

var CFG = window.CTX_CONFIG;
var DEPOSIT = 1200; // pence per item on loan
var TODAY = "2026-06-10";
var LAST_DAY = "2026-12-31";
var EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
var ITEMS = [
  { id: "laser", name: "Laser cutter pass" },
  { id: "printer", name: "3D printer slot" },
  { id: "solder", name: "Soldering kit" },
  { id: "camera", name: "Camera rig" }
];
var KEY = "ctx-request";

function t(key) { return CFG.labels[key] || key; }
function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}
function money(pence) { return "£" + (pence / 100).toFixed(2); }
function fresh() {
  return { item: null, quantity: 1, purpose: "study", dateDraft: "", date: "", configDone: false,
           nameDraft: "", emailDraft: "", name: "", email: "", contactDone: false,
           committed: false, ref: "", deposit: 0, returnTo: "", notice: "" };
}
function load() {
  try { return JSON.parse(sessionStorage.getItem(KEY)) || fresh(); } catch (e) { return fresh(); }
}
var state = load();
function save() { sessionStorage.setItem(KEY, JSON.stringify(state)); }
function validDate(d) { return /^\d{4}-\d{2}-\d{2}$/.test(d) && d > TODAY && d <= LAST_DAY; }
function itemName(id) { for (var i = 0; i < ITEMS.length; i++) { if (ITEMS[i].id === id) { return ITEMS[i].name; } } return ""; }

/* -- where we are --------------------------------------------------------------------- */

var ORDERS = {
  S1: ["items", "configure", "contact", "review", "done"],
  S3: ["items", "configure", "contact", "review", "done"],
  S4: ["items", "configure", "contact", "review", "done"],
  S5: ["items", "contact", "configure", "review", "done"],
  S2: ["items", "configure", "contact", "review", "done"]
};
var ORDER = ORDERS[CFG.structure];
var MULTIPAGE = CFG.structure === "S1" || CFG.structure === "S2" || CFG.structure === "S5";

function currentView() {
  if (MULTIPAGE) { return document.body.getAttribute("data-view") || "home"; }
  var hash = location.hash.replace(/^#/, "");
  return hash || "home";
}
function go(view) {
  if (MULTIPAGE) { location.href = view + ".html"; return; }
  if (location.hash === "#" + view) { render(); } else { location.hash = view; }
}
function stageDone(stage) {
  if (stage === "items") { return !!state.item; }
  if (stage === "configure") { return state.configDone; }
  if (stage === "contact") { return state.contactDone; }
  return false;
}
function reachable(view) {
  if (view === "home" || view === "help" || view === "hub") { return true; }
  if (view === "done") { return state.committed; }
  if (CFG.structure === "S2") {
    if (view === "items") { return true; }
    if (view === "review") { return stageDone("items") && stageDone("configure") && stageDone("contact"); }
    return stageDone("items");
  }
  var at = ORDER.indexOf(view);
  for (var i = 0; i < at; i++) { if (!stageDone(ORDER[i])) { return false; } }
  return true;
}
function nextAfter(stage) {
  if (state.returnTo) { var back = state.returnTo; state.returnTo = ""; return back; }
  if (CFG.structure === "S2") { return "hub"; }
  return ORDER[ORDER.indexOf(stage) + 1];
}

/* Ground truth for the diagnostic's probes: how far the request has got. Never rendered,
   never read by the tester's observation; the probe builder reads it to label actions. */
window.__ctxProgress = function () {
  var s = load();
  var req = 0;
  if (s.item) { req++; }
  if (validDate(s.dateDraft)) { req++; }
  if (s.configDone) { req++; }
  if (s.nameDraft.trim()) { req++; }
  if (EMAIL.test(s.emailDraft)) { req++; }
  if (s.contactDone) { req++; }
  if (s.committed) { req++; }
  state = s;
  var view = currentView();
  if (!VIEWS[view] || !reachable(view)) { view = "home"; }
  var position = view === "hub" ? 1 : (ORDER.indexOf(view) + 1 + (CFG.structure === "S2" ? 1 : 0));
  if (view === "home" || view === "help") { position = 0; }
  return req * 10 + Math.max(position, 0);
};
window.__ctxDecoys = CFG.decoy ? ["decoy-advance"] : [];

/* -- actions ------------------------------------------------------------------------------ */

function notice(key) { state.notice = t(key); save(); render(); }
function addItem(id) {
  if (state.item) { return notice("msg.one_item"); }
  state.item = id;
  state.deposit = DEPOSIT * state.quantity;
  state.notice = "";
  save();
  render();
}
function removeItem() { state.item = null; state.deposit = 0; state.notice = ""; save(); render(); }
function setQuantity(q) {
  state.quantity = parseInt(q, 10) || 1;
  state.deposit = DEPOSIT * state.quantity;
  save();
}
function saveConfig() {
  if (!validDate(state.dateDraft)) { return notice("msg.date_invalid"); }
  state.date = state.dateDraft;
  state.configDone = true;
  state.notice = "";
  save();
  go(nextAfter("configure"));
}
function saveContact() {
  if (!state.nameDraft.trim()) { return notice("msg.name_needed"); }
  if (!EMAIL.test(state.emailDraft)) { return notice("msg.email_invalid"); }
  state.name = state.nameDraft.trim();
  state.email = state.emailDraft.trim();
  state.contactDone = true;
  state.notice = "";
  save();
  go(nextAfter("contact"));
}
function advanceItems() {
  if (!state.item) { return notice("msg.item_needed"); }
  state.notice = "";
  save();
  go(nextAfter("items"));
}
function commitRequest() {
  if (!(state.item && state.configDone && state.contactDone)) { return notice("msg.incomplete"); }
  state.committed = true;
  state.ref = "CTX-1001";
  state.notice = "";
  save();
  go("done");
}
function editStage(stage) { state.returnTo = "review"; state.notice = ""; save(); go(stage); }
function decoyAdvance() { state.notice = t("msg.draft_kept"); save(); render(); }

/* -- views -------------------------------------------------------------------------------- */

function button(id, labelKey, extra) {
  return '<button type="button" id="' + id + '"' + (extra || "") + ">" + esc(t(labelKey)) + "</button>";
}
function progressButton(id) {
  // With decoys on, the training-time "advance" wording (act.decoy) sits on a useless
  // control and the control that really advances carries a different label.
  var real = button(id, CFG.decoy ? "act.advance_alt" : "act.advance", ' class="primary"');
  return (CFG.decoy ? button("decoy-advance", "act.decoy") : "") + real;
}
function viewItems() {
  var rows = ITEMS.map(function (it) {
    var chosen = state.item === it.id;
    return '<li class="item"><span>' + esc(it.name) + "</span> " +
      (chosen ? '<strong id="chosen-' + it.id + '">' + esc(t("label.chosen")) + "</strong> " + button("remove-" + it.id, "act.remove")
              : button("add-" + it.id, "act.add")) + "</li>";
  }).join("");
  return "<h1>" + esc(t("title.items")) + '</h1><ul id="item-list">' + rows + "</ul>" +
    '<div class="actions">' + progressButton("advance-items") + "</div>";
}
function option(value, label, current) {
  return '<option value="' + esc(value) + '"' + (String(value) === String(current) ? " selected" : "") + ">" + esc(label) + "</option>";
}
function viewConfigure() {
  return "<h1>" + esc(t("title.configure")) + "</h1>" +
    '<p class="muted">' + esc(t("label.item")) + ": " + esc(itemName(state.item)) + "</p>" +
    '<label for="quantity">' + esc(t("field.quantity")) + "</label>" +
    '<select id="quantity" name="quantity">' + [1, 2, 3, 4].map(function (q) { return option(q, String(q), state.quantity); }).join("") + "</select>" +
    '<label for="purpose">' + esc(t("field.purpose")) + "</label>" +
    '<select id="purpose" name="purpose">' + option("study", t("purpose.study"), state.purpose) +
      option("project", t("purpose.project"), state.purpose) + option("event", t("purpose.event"), state.purpose) + "</select>" +
    '<label for="return-date">' + esc(t("field.return_date")) + "</label>" +
    '<input id="return-date" name="return_date" type="date" value="' + esc(state.dateDraft) + '">' +
    '<div class="actions">' + (CFG.decoy ? button("decoy-advance", "act.decoy") : "") +
      button("save-config", "act.save", ' class="primary"') + "</div>";
}
function viewContact() {
  return "<h1>" + esc(t("title.contact")) + "</h1>" +
    '<label for="contact-name">' + esc(t("field.name")) + "</label>" +
    '<input id="contact-name" name="contact_name" type="text" value="' + esc(state.nameDraft) + '">' +
    '<label for="contact-email">' + esc(t("field.email")) + "</label>" +
    '<input id="contact-email" name="contact_email" type="email" value="' + esc(state.emailDraft) + '">' +
    '<div class="actions">' + (CFG.decoy ? button("decoy-advance", "act.decoy") : "") +
      button("save-contact", "act.save", ' class="primary"') + "</div>";
}
function viewReview() {
  return "<h1>" + esc(t("title.review")) + "</h1>" +
    '<dl class="summary">' +
    "<dt>" + esc(t("label.item")) + '</dt><dd><span id="review-item">' + esc(itemName(state.item)) + "</span></dd>" +
    "<dt>" + esc(t("field.quantity")) + '</dt><dd><span id="review-quantity">' + esc(state.quantity) + "</span></dd>" +
    "<dt>" + esc(t("label.deposit")) + '</dt><dd><output id="review-deposit">' + money(state.deposit) + "</output></dd>" +
    "<dt>" + esc(t("field.return_date")) + '</dt><dd><span id="review-date">' + esc(state.date) + "</span></dd>" +
    "<dt>" + esc(t("label.contact")) + '</dt><dd><span id="review-contact">' + esc(state.name) + " · " + esc(state.email) + "</span></dd>" +
    "</dl>" +
    '<div class="actions">' + button("edit-config", "act.edit") + button("edit-contact", "act.edit") +
      button("commit", "act.commit", ' class="primary"') + "</div>";
}
function viewDone() {
  return "<h1>" + esc(t("title.done")) + "</h1>" +
    '<p><strong id="done-ref">' + esc(state.ref) + "</strong></p>" +
    '<p><span id="done-contact">' + esc(state.name) + " · " + esc(state.email) + "</span></p>" +
    '<p><output id="done-deposit">' + money(state.deposit) + "</output></p>" +
    '<p><a id="new-request" href="' + (MULTIPAGE ? "index.html" : "#home") + '">' + esc(t("act.new")) + "</a></p>";
}
function viewHome() {
  var summary = state.item ? '<p id="home-summary">' + esc(itemName(state.item)) + "</p>" : "";
  var start = CFG.structure === "S4"
    ? button("start-request", "act.begin", ' class="primary"')
    : '<a id="start-request" class="primary" href="' + (MULTIPAGE ? (CFG.structure === "S2" ? "hub.html" : ORDER[0] + ".html") : "#items") + '">' + esc(t("act.begin")) + "</a>";
  return "<h1>" + esc(t("title.home")) + "</h1><p>" + esc(t("label.intro")) + "</p>" + summary + start;
}
function viewHub() {
  var rows = ["items", "configure", "contact"].map(function (stage) {
    var status = stageDone(stage) ? t("label.done") : t("label.todo");
    return '<li><a id="task-' + stage + '" href="' + stage + '.html">' + esc(t("title." + stage)) + "</a> <span>" + esc(status) + "</span></li>";
  }).join("");
  return "<h1>" + esc(t("title.hub")) + '</h1><ul id="task-list">' + rows + "</ul>" +
    '<div class="actions">' + button("go-review", "act.to_review", ' class="primary"') + "</div>";
}
function viewHelp() {
  return "<h1>" + esc(t("title.help")) + "</h1><p>" + esc(t("label.help")) + "</p>" +
    '<p><a id="help-back" href="' + (MULTIPAGE ? "index.html" : "#home") + '">' + esc(t("act.home")) + "</a></p>";
}
var VIEWS = { home: viewHome, items: viewItems, configure: viewConfigure, contact: viewContact,
              review: viewReview, done: viewDone, hub: viewHub, help: viewHelp };

function tabs() {
  if (CFG.structure !== "S3") { return ""; }
  return '<nav class="tabs">' + ["items", "configure", "contact", "review"].map(function (stage) {
    return reachable(stage) ? '<a id="tab-' + stage + '" href="#' + stage + '">' + esc(t("title." + stage)) + "</a>"
                            : "<span>" + esc(t("title." + stage)) + "</span>";
  }).join("") + "</nav>";
}
function chrome() {
  return '<header><span class="brand">' + esc(t("brand")) + '</span><a id="help-link" href="' +
    (MULTIPAGE ? "help.html" : "#help") + '">' + esc(t("title.help")) + "</a></header>";
}

function render() {
  state = load();
  var view = currentView();
  if (!VIEWS[view] || !reachable(view)) { view = "home"; }
  var body = VIEWS[view]();
  var note = state.notice ? '<p class="notice" id="notice" role="status">' + esc(state.notice) + "</p>" : "";
  var html;
  if (CFG.structure === "S4" && view !== "home" && view !== "help") {
    html = chrome() + "<main>" + viewHome() + "</main>" +
      '<div class="backdrop"><div class="modal" role="dialog" aria-modal="true" id="stage-dialog">' + note + body +
      '<p><a id="close-dialog" href="#home">' + esc(t("act.close")) + "</a></p></div></div>";
  } else {
    html = chrome() + tabs() + "<main>" + note + body + "</main>";
  }
  document.getElementById("app").innerHTML = html;
  wire(view);
}

function on(id, event, handler) { var el = document.getElementById(id); if (el) { el.addEventListener(event, handler); } }
function wire(view) {
  ITEMS.forEach(function (it) {
    on("add-" + it.id, "click", function () { addItem(it.id); });
    on("remove-" + it.id, "click", removeItem);
  });
  on("advance-items", "click", advanceItems);
  on("decoy-advance", "click", decoyAdvance);
  on("quantity", "change", function (e) { setQuantity(e.target.value); });
  on("purpose", "change", function (e) { state.purpose = e.target.value; save(); });
  on("return-date", "input", function (e) { state.dateDraft = e.target.value; save(); });
  on("return-date", "change", function (e) { state.dateDraft = e.target.value; save(); });
  on("save-config", "click", saveConfig);
  on("contact-name", "input", function (e) { state.nameDraft = e.target.value; save(); });
  on("contact-email", "input", function (e) { state.emailDraft = e.target.value; save(); });
  on("save-contact", "click", saveContact);
  on("edit-config", "click", function () { editStage("configure"); });
  on("edit-contact", "click", function () { editStage("contact"); });
  on("commit", "click", commitRequest);
  on("go-review", "click", function () { if (reachable("review")) { go("review"); } else { notice("msg.incomplete"); } });
  on("start-request", "click", function (e) { if (CFG.structure === "S4") { e.preventDefault(); go(nextOpenStage()); } });
}
function nextOpenStage() {
  for (var i = 0; i < ORDER.length; i++) { if (!stageDone(ORDER[i]) && ORDER[i] !== "done") { return ORDER[i]; } }
  return "review";
}

window.addEventListener("hashchange", render);
render();
