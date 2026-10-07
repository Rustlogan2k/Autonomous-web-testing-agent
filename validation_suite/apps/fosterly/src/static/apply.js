// The application page: accordion sections (one open at a time) and per-field autosave.
//
// Every change of a field is sent at once, as a PATCH of that one field, to
// /api/drafts/<ref>/<section>. When the server accepts it, the page's copy of the accepted
// answers takes the value, the section's summary is redrawn from that copy and the meter
// shows the server's completeness. A refused value leaves the copy (and the summary) as it
// was and says why beside the field. There are no Save buttons and no timers.
(() => {
  "use strict";

  const form = document.getElementById("application");
  if (!form) return;
  const ref = form.dataset.ref;
  const answers = JSON.parse(document.getElementById("answers-data").textContent);
  const { labels, names } = JSON.parse(document.getElementById("apply-labels").textContent);
  const t = (key, params = {}) => Object.entries(params)
    .reduce((text, [name, value]) => text.split(`{${name}}`).join(String(value)), labels[key] ?? key);
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const meter = document.getElementById("completeness");
  const animalsGroup = document.getElementById("animals-group");

  const formatDate = (iso) => {
    const [year, month, day] = String(iso).split("-");
    return `${Number(day)} ${MONTHS[Number(month) - 1]} ${year}`;
  };
  const nameOf = (table, id) => table[id] ?? id;

  function animalsLine(a) {
    if (!String(a.animal_species).trim()) return "";
    const species = nameOf(names.species, a.animal_species);
    return a.animal_count ? `${nameOf(names.counts, a.animal_count)} x ${species}` : species;
  }

  /** The one-line summary of a section, from the accepted answers (views.js draws the same). */
  const SUMMARIES = {
    about(a) {
      const parts = [a.full_name, a.email, a.phone].map((part) => String(part).trim()).filter(Boolean);
      return parts.length ? parts.join(", ") : t("summary.not_started");
    },
    home(a) {
      if (!a.home_type) return t("summary.not_started");
      const animals = animalsLine(a);
      const others = a.other_animals
        ? t("summary.animals", { animals: animals || t("summary.animals_unknown") })
        : t("summary.no_animals");
      return [nameOf(names.home_types, a.home_type), t(a.garden ? "summary.garden" : "summary.no_garden"), others].join(", ");
    },
    experience(a) {
      return a.years_experience ? t("summary.years", { years: a.years_experience }) : t("summary.not_started");
    },
    availability(a) {
      const date = a.available_from ? formatDate(a.available_from) : "";
      const weeks = a.min_weeks;
      if (date && weeks) return t("summary.from_weeks", { date, weeks });
      if (date) return t("summary.from", { date });
      if (weeks) return t("summary.weeks_only", { weeks });
      return t("summary.not_started");
    },
    declaration(a) {
      return t(a.declaration ? "summary.signed" : "summary.unsigned");
    },
  };

  function drawSummary(section) {
    document.getElementById(`summary-${section}`).textContent = SUMMARIES[section](answers);
  }

  function drawProgress(progress) {
    meter.textContent = String(progress.done);
    for (const [section, done] of Object.entries(progress.sections)) {
      document.getElementById(`status-${section}`).textContent = t(done ? "status.done" : "status.todo");
    }
  }

  // -- accordion: one section open at a time; a header closes its own open section ----------

  const headers = Array.from(form.querySelectorAll("button[data-acc]"));
  for (const header of headers) {
    header.addEventListener("click", () => {
      const opening = header.getAttribute("aria-expanded") !== "true";
      for (const other of headers) {
        const open = opening && other === header;
        other.setAttribute("aria-expanded", String(open));
        document.getElementById(other.getAttribute("aria-controls")).hidden = !open;
      }
    });
  }

  // -- autosave ----------------------------------------------------------------------------

  const valueOf = (control) => (control.type === "checkbox" ? control.checked : control.value);
  const showAnimals = (ticked) => { animalsGroup.hidden = !ticked; };

  function save(control) {
    const name = control.name;
    const section = control.dataset.section;
    const value = valueOf(control);
    const note = document.getElementById(`note-${control.id}`);
    note.textContent = t("note.saving");
    note.className = "note";
    if (name === "other_animals") showAnimals(value);
    fetch(`/api/drafts/${encodeURIComponent(ref)}/${section}`, {
      method: "PATCH",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [name]: value }),
    })
      .then((response) => response.json())
      .then((answer) => {
        if (answer.ok) {
          answers[name] = value;
          note.textContent = t("note.saved");
          drawSummary(section);
          drawProgress(answer.completeness);
          return;
        }
        note.textContent = answer.message;
        note.className = "note refused";
        if (control.type === "checkbox") {
          control.checked = Boolean(answers[name]);
          if (name === "other_animals") showAnimals(control.checked);
        }
      })
      .catch(() => {
        note.textContent = t("note.offline");
        note.className = "note refused";
      });
  }

  for (const control of form.querySelectorAll("[data-section]")) {
    // Text-like fields report each edit as `input`; boxes and dropdowns report `change`.
    const event = control.type === "checkbox" || control.tagName === "SELECT" ? "change" : "input";
    control.addEventListener(event, () => save(control));
  }
  form.addEventListener("submit", (event) => event.preventDefault());

  // The summaries are always drawn from this page's copy of the accepted answers.
  Object.keys(SUMMARIES).forEach(drawSummary);
  showAnimals(Boolean(answers.other_animals));
})();
