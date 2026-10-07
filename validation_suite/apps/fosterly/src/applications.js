// Drafts and submitted applications of one session. A draft is written field by field as
// each autosaved answer is accepted; submitting copies it into an immutable application and
// retires the draft. Pure functions over the session's store object; server.js persists it.

import { FIELDS, SECTIONS, animalsOf, blankAnswers, checkValue, completeness, sectionComplete } from "./fields.js";

/** A fresh store from the seed: the saved drafts and the next references to hand out. */
export function storeFromSeed(seed) {
  return {
    drafts: seed.drafts.map((draft) => ({ ref: draft.ref, answers: { ...blankAnswers(), ...draft.answers } })),
    applications: [],
    next_draft: seed.next_draft,
    next_application: seed.next_application,
  };
}

export const findDraft = (store, ref) => store.drafts.find((draft) => draft.ref === ref) ?? null;
export const findApplication = (store, ref) => store.applications.find((app) => app.ref === ref) ?? null;
export const submittedFrom = (store, draftRef) => store.applications.find((app) => app.from === draftRef) ?? null;

/** Open a new, empty draft and return its reference (FOS-1002, FOS-1003, ...). */
export function openDraft(store) {
  const ref = `FOS-${store.next_draft}`;
  store.next_draft += 1;
  store.drafts.push({ ref, answers: blankAnswers() });
  return ref;
}

/**
 * Autosave: keep the fields of `body` that `section` owns, check each, and store them.
 * Answers {ok: true, completeness} or {ok: false, problem}. Nothing is stored on a refusal.
 */
export function saveAnswers(draft, section, body, today) {
  if (!Object.prototype.hasOwnProperty.call(FIELDS, section)) return { ok: false, problem: "error.section" };
  if (body === null || typeof body !== "object" || Array.isArray(body)) return { ok: false, problem: "error.value" };
  const allowed = FIELDS[section];
  const changes = {};
  for (const name of Object.keys(body).filter((key) => allowed.includes(key))) {
    const outcome = checkValue(name, body[name], today);
    if (outcome.problem) return { ok: false, problem: outcome.problem, field: name };
    changes[name] = outcome.value;
  }
  const next = { ...draft.answers, ...changes };
  if (changes.declaration === true && !sectionComplete("about", next)) {
    return { ok: false, problem: "error.declaration_first", field: "declaration" };
  }
  draft.answers = next;
  return { ok: true, completeness: completeness(next) };
}

/**
 * The submission gate. Refused (nothing changes) while any section is incomplete; otherwise
 * the draft becomes application FA-n and leaves the list of saved drafts.
 */
export function submit(store, ref, today) {
  const stored = findDraft(store, ref);
  if (!stored) return { ok: false, problem: "error.draft_missing" };
  const progress = completeness(stored.answers);
  if (progress.done < progress.of) {
    return { ok: false, problem: "flash.incomplete", missing: SECTIONS.filter((section) => !progress.sections[section]) };
  }
  const draft = { ...stored.answers, animals: animalsOf(stored.answers) };
  const application = {
    ref: `FA-${store.next_application}`,
    from: stored.ref,
    submitted_on: today,
    full_name: draft.full_name,
    email: draft.email,
    phone: draft.phone,
    home_type: draft.home_type,
    garden: draft.garden,
    other_animals: draft.other_animals,
    animals: draft.other_animals ? draft.animals : [],
    years_experience: draft.years_experience,
    experience_notes: draft.experience_notes,
    available_from: draft.available_from,
    min_weeks: draft.min_weeks,
  };
  store.next_application += 1;
  store.applications.push(application);
  store.drafts = store.drafts.filter((other) => other.ref !== ref);
  return { ok: true, application };
}

/** Business state only: what the harness compares between sessions. */
export function committed(store) {
  return {
    drafts: [...store.drafts].sort((a, b) => a.ref.localeCompare(b.ref)),
    applications: [...store.applications].sort((a, b) => a.ref.localeCompare(b.ref)),
    next_draft: store.next_draft,
    next_application: store.next_application,
  };
}
