// The record form (step 2): unsent answers are kept as a draft in this browser's
// localStorage, under a key the server namespaces per session and per site, and restored
// when the page is loaded again (a reload, Back, or the form coming back with a refusal).
// The draft is written synchronously on every change, before the page has settled; it is
// dropped once the server confirms the sighting was recorded (see feed.js).
(() => {
  "use strict";

  const form = document.getElementById("record-form");
  if (!form) return;
  const labels = JSON.parse(document.getElementById("record-labels").textContent);
  const t = (key, params = {}) => Object.entries(params)
    .reduce((text, [name, value]) => text.split(`{${name}}`).join(String(value)), labels[key] ?? key);
  const key = form.dataset.draftKey;
  const summary = document.getElementById("draft-summary");
  const FIELDS = ["species", "count", "seen_on", "notes"];

  /** The form as it stands, field by field, as strings. */
  function answers() {
    const data = new FormData(form);
    return Object.fromEntries(FIELDS.map((name) => [name, String(data.get(name) ?? "")]));
  }

  function saveDraft() {
    try {
      window.localStorage.setItem(key, JSON.stringify(answers()));
    } catch {
      // Storage full or disabled: the form still works, it just will not survive a reload.
    }
  }

  function storedDraft() {
    try {
      const raw = window.localStorage.getItem(key);
      const draft = raw ? JSON.parse(raw) : null;
      return draft && typeof draft === "object" ? draft : null;
    } catch {
      return null;
    }
  }

  function dropDraft() {
    try {
      window.localStorage.removeItem(key);
    } catch {
      // Nothing stored, nothing to drop.
    }
  }

  /** One line describing the unsent sighting: "Heron × 1 at Marsh hide, today". */
  function describe() {
    const now = answers();
    const species = form.elements.namedItem("species");
    const chosen = now.species && species.selectedIndex > 0 ? species.options[species.selectedIndex].textContent : "";
    let when = now.seen_on;
    if (!when) when = t("draft.no_date");
    else if (when === form.dataset.today) when = t("draft.today");
    return t("draft.summary", {
      species: chosen || t("draft.no_species"),
      count: now.count || t("draft.no_count"),
      site: form.dataset.siteName,
      when,
    });
  }

  function drawSummary() {
    summary.textContent = describe();
  }

  const draft = storedDraft();
  if (draft) {
    for (const name of FIELDS) {
      const field = form.elements.namedItem(name);
      if (field && typeof draft[name] === "string") field.value = draft[name];
    }
  }
  drawSummary();

  form.querySelectorAll("input, select, textarea").forEach((el) => el.addEventListener("input", saveDraft));
  form.addEventListener("input", drawSummary);

  document.getElementById("discard-draft").addEventListener("click", () => {
    dropDraft();
    form.reset();
    drawSummary();
  });

  // One submission per page: a second click while the first is on its way is ignored.
  let sending = false;
  form.addEventListener("submit", (event) => {
    if (sending) event.preventDefault();
    sending = true;
  });
})();
