// The foster-carer application form: which fields each section owns, what each field
// accepts, and when a section counts as complete. The server consults this module for every
// autosaved answer and again at the submission gate.

export const SECTIONS = ["about", "home", "experience", "availability", "declaration"];

/** The fields each section's autosave accepts: a PATCH body is filtered by this list. */
export const FIELDS = {
  about: ["full_name", "email", "phone"],
  home: ["home_type", "garden", "other_animals", "animal_species", "animal_count"],
  experience: ["years_experience", "experience_notes"],
  availability: ["available_from", "min_weeks"],
  declaration: ["declaration"],
};

/** Fixed option codes of the dropdowns (their display names are data in the seed). */
export const CHOICES = {
  home_type: ["house", "flat", "farm", "other"],
  animal_species: ["dog", "cat", "rabbit", "other"],
  animal_count: ["1", "2", "3", "4-or-more"],
  min_weeks: ["2", "4", "8", "12"],
};

const FLAGS = new Set(["garden", "other_animals", "declaration"]);
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const PHONE = /^\+?\d[\d ()-]{5,19}$/;
const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;
const LIMITS = { full_name: 120, email: 254, experience_notes: 2000 };

/** What a new draft starts with: every answer empty, every box unticked. */
export function blankAnswers() {
  const answers = {};
  for (const names of Object.values(FIELDS)) {
    for (const name of names) answers[name] = FLAGS.has(name) ? false : "";
  }
  return answers;
}

/** A real calendar date in ISO form (2026-02-30 is not one). */
export function isCalendarDate(text) {
  const match = ISO_DATE.exec(text);
  if (!match) return false;
  const [year, month, day] = match.slice(1).map(Number);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1];
  return year >= 1 && days !== undefined && day >= 1 && day <= days;
}

/**
 * Check one answer as the browser sent it. Returns {value} — the value to store, exactly as
 * given, so the page and the stored draft never disagree — or {problem} naming the refusal's
 * message key. An empty string clears a text or dropdown answer and is always accepted.
 */
export function checkValue(name, raw, today) {
  if (FLAGS.has(name)) return typeof raw === "boolean" ? { value: raw } : { problem: "error.flag" };
  if (typeof raw !== "string") return { problem: "error.value" };
  if (raw === "") return { value: "" };
  if (Object.prototype.hasOwnProperty.call(CHOICES, name)) {
    return CHOICES[name].includes(raw) ? { value: raw } : { problem: "error.choice" };
  }
  switch (name) {
    case "full_name":
      return raw.length <= LIMITS.full_name ? { value: raw } : { problem: "error.name_long" };
    case "email":
      return raw.length <= LIMITS.email && EMAIL.test(raw) ? { value: raw } : { problem: "error.email" };
    case "phone":
      return PHONE.test(raw) ? { value: raw } : { problem: "error.phone" };
    case "years_experience":
      return /^\d{1,2}$/.test(raw) && Number(raw) <= 60 ? { value: raw } : { problem: "error.years" };
    case "experience_notes":
      return raw.length <= LIMITS.experience_notes ? { value: raw } : { problem: "error.notes_long" };
    case "available_from":
      if (!isCalendarDate(raw)) return { problem: "error.date" };
      return raw >= today ? { value: raw } : { problem: "error.date_past" };
    default:
      return { problem: "error.value" };
  }
}

const filled = (value) => typeof value === "string" && value.trim() !== "";

/** The answers a section needs before it counts as complete. */
export function requiredFor(section, draft) {
  switch (section) {
    case "about":
      return ["full_name", "email", "phone"];
    case "home": {
      const required = ["home_type"];
      if (draft.other_animals) required.push("animal_species", "animal_count");
      return required;
    }
    case "experience":
      return ["years_experience"];
    case "availability":
      return ["available_from", "min_weeks"];
    default:
      return [];
  }
}

export function sectionComplete(section, draft) {
  if (section === "declaration") return draft.declaration === true && sectionComplete("about", draft);
  return requiredFor(section, draft).every((name) => filled(draft[name]));
}

/** How far a draft has got: {done, of, sections: {about: true, ...}}. */
export function completeness(draft) {
  const sections = Object.fromEntries(SECTIONS.map((section) => [section, sectionComplete(section, draft)]));
  return { done: Object.values(sections).filter(Boolean).length, of: SECTIONS.length, sections };
}

/** The animals a draft describes in its Other animals sub-section ([] when it describes none). */
export function animalsOf(draft) {
  return filled(draft.animal_species) ? [{ species: draft.animal_species, count: draft.animal_count || "" }] : [];
}
