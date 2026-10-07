// Unit tests for the form's rules (src/fields.js) and the draft store (src/applications.js),
// over the seed, no server needed. Run: node --test tests/

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, test } from "node:test";

import * as store from "../src/applications.js";
import { FIELDS, SECTIONS, checkValue, completeness, isCalendarDate, sectionComplete } from "../src/fields.js";

const seed = JSON.parse(readFileSync(new URL("../seed/fosterly.json", import.meta.url), "utf8"));
const TODAY = "2026-06-10";
const fresh = () => store.storeFromSeed(seed);

describe("field checks", () => {
  test("every field belongs to exactly one section", () => {
    const all = Object.values(FIELDS).flat();
    assert.equal(new Set(all).size, all.length);
    assert.deepEqual(Object.keys(FIELDS), SECTIONS);
  });

  test("the tester's typical values are accepted as typed", () => {
    for (const [name, value] of [["full_name", "Test User Input"], ["email", "tester@example.com"],
      ["phone", "+1-202-555-0143"], ["years_experience", "42"], ["available_from", "2026-06-15"]]) {
      assert.deepEqual(checkValue(name, value, TODAY), { value }, name);
    }
  });

  test("boundary values are refused with a reason, never stored", () => {
    assert.equal(checkValue("full_name", "A".repeat(2048), TODAY).problem, "error.name_long");
    assert.equal(checkValue("email", "not-an-email", TODAY).problem, "error.email");
    assert.equal(checkValue("phone", "1", TODAY).problem, "error.phone");
    assert.equal(checkValue("phone", "1".repeat(32), TODAY).problem, "error.phone");
    assert.equal(checkValue("years_experience", "999999999", TODAY).problem, "error.years");
    assert.equal(checkValue("available_from", "0001-01-01", TODAY).problem, "error.date_past");
    assert.equal(checkValue("available_from", "2026-02-30", TODAY).problem, "error.date");
    assert.equal(checkValue("home_type", "castle", TODAY).problem, "error.choice");
    assert.equal(checkValue("garden", "yes", TODAY).problem, "error.flag");
  });

  test("an empty string clears an answer", () => {
    assert.deepEqual(checkValue("email", "", TODAY), { value: "" });
    assert.deepEqual(checkValue("available_from", "", TODAY), { value: "" });
  });

  test("calendar dates", () => {
    assert.ok(isCalendarDate("2028-02-29"));
    assert.ok(!isCalendarDate("2026-02-29"));
    assert.ok(isCalendarDate("9999-12-31"));
  });
});

describe("completeness", () => {
  test("the seeded draft FOS-1001 is complete in every section", () => {
    const draft = store.findDraft(fresh(), "FOS-1001");
    assert.deepEqual(completeness(draft.answers).done, 5);
  });

  test("ticking other animals needs the species and the count", () => {
    const answers = { ...store.findDraft(fresh(), "FOS-1001").answers, other_animals: true };
    assert.equal(sectionComplete("home", answers), false);
    assert.equal(sectionComplete("home", { ...answers, animal_species: "cat" }), false);
    assert.equal(sectionComplete("home", { ...answers, animal_species: "cat", animal_count: "2" }), true);
  });

  test("the declaration counts only while About you is complete", () => {
    const answers = { ...store.findDraft(fresh(), "FOS-1001").answers };
    assert.equal(sectionComplete("declaration", answers), true);
    assert.equal(sectionComplete("declaration", { ...answers, phone: "" }), false);
  });
});

describe("drafts and submission", () => {
  test("new drafts and applications are numbered from the seed's counters", () => {
    const data = fresh();
    assert.equal(store.openDraft(data), "FOS-1002");
    assert.equal(store.openDraft(data), "FOS-1003");
    const outcome = store.submit(data, "FOS-1001", TODAY);
    assert.equal(outcome.application.ref, "FA-2001");
    assert.equal(store.findDraft(data, "FOS-1001"), null);
  });

  test("an autosave stores only fields of its own section and refuses the declaration early", () => {
    const data = fresh();
    const draft = store.findDraft(data, store.openDraft(data));
    assert.deepEqual(store.saveAnswers(draft, "about", { full_name: "Jo", available_from: "2026-07-01" }, TODAY).ok, true);
    assert.equal(draft.answers.full_name, "Jo");
    assert.equal(draft.answers.available_from, "");
    const refused = store.saveAnswers(draft, "declaration", { declaration: true }, TODAY);
    assert.equal(refused.ok, false);
    assert.equal(draft.answers.declaration, false);
  });

  test("animals described and then hidden by unticking are not submitted", () => {
    const data = fresh();
    const draft = store.findDraft(data, "FOS-1001");
    store.saveAnswers(draft, "home", { other_animals: true }, TODAY);
    store.saveAnswers(draft, "home", { animal_species: "cat" }, TODAY);
    store.saveAnswers(draft, "home", { animal_count: "2" }, TODAY);
    store.saveAnswers(draft, "home", { other_animals: false }, TODAY);
    assert.equal(draft.answers.animal_species, "cat", "the hidden answers stay in the draft");
    const { application } = store.submit(data, "FOS-1001", TODAY);
    assert.equal(application.other_animals, false);
    assert.deepEqual(application.animals, []);
  });

  test("a ticked box with no animal described cannot be submitted", () => {
    const data = fresh();
    store.saveAnswers(store.findDraft(data, "FOS-1001"), "home", { other_animals: true }, TODAY);
    const outcome = store.submit(data, "FOS-1001", TODAY);
    assert.equal(outcome.ok, false);
    assert.deepEqual(outcome.missing, ["home"]);
    assert.equal(data.applications.length, 0);
  });
});
