// Unit tests of the kitchen rules (src/kitchen.js) and the browser's measures (src/static/units.js).
// No server needed: node --test tests/units.test.mjs

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { describe, test } from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

import * as kitchen from "../src/kitchen.js";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const SEED = JSON.parse(readFileSync(join(ROOT, "seed", "larder.json"), "utf8"));

/** units.js is a classic browser script; run it in a sandbox and take what it defines. */
function loadUnits() {
  const sandbox = { module: { exports: {} } };
  vm.runInNewContext(readFileSync(join(ROOT, "src", "static", "units.js"), "utf8"), sandbox);
  return sandbox.module.exports;
}

const units = loadUnits();
const recipe = (id) => SEED.recipes.find((r) => r.id === id);
const lineAt = (r, servings, index = 0) => ({
  unit: r.lines[index].unit, base: r.lines[index].amount, scaled: units.scale(r.lines[index].amount, r.serves, servings),
});
/** What the oracle reads: the first number in a region's text. */
const firstNumber = (text) => Number(/\d[\d,]*(?:\.\d+)?/.exec(text)[0].replace(/,/g, ""));

describe("measures", () => {
  test("Shortbread for 5: flour 312.5 g is 11.0 oz", () => {
    const line = lineAt(recipe("r1"), 5);
    assert.equal(units.metricAmount(line), "312.5");
    assert.equal(units.usAmount(line), "11.0 oz");
  });

  test("Shortbread as written: 250 g is 8.8 oz; Shortbread for 6 needs 375 g", () => {
    assert.equal(units.usAmount(lineAt(recipe("r1"), 4)), "8.8 oz");
    assert.equal(units.metricAmount(lineAt(recipe("r1"), 6)), "375");
  });

  test("volumes convert to US fluid ounces from the scaled amount; counts stay counts", () => {
    assert.equal(units.usAmount(lineAt(recipe("r4"), 6)), "33.8 fl oz");
    assert.equal(units.usAmount(lineAt(recipe("r4"), 3)), "16.9 fl oz");
    assert.equal(units.usAmount(lineAt(recipe("r2"), 5, 2)), "2.5");
  });

  test("every weight line's ounces match its displayed grams for every servings value 1-12", () => {
    for (const r of SEED.recipes) {
      for (let servings = 1; servings <= 12; servings += 1) {
        r.lines.forEach((source, index) => {
          if (source.unit !== "g") return;
          const line = lineAt(r, servings, index);
          const metric = firstNumber(units.metricAmount(line));
          const us = firstNumber(units.usAmount(line));
          const expected = Math.round((metric / units.GRAMS_PER_OUNCE) * 10) / 10;
          assert.ok(Math.abs(us - expected) <= 0.051, `${r.id} line ${index + 1} x${servings}: ${us} vs ${expected}`);
        });
      }
    }
  });

  test("oven temperatures on each scale", () => {
    assert.equal(units.oven(160, "celsius", "gas mark {mark}"), "160 °C");
    assert.equal(units.oven(160, "fahrenheit", "gas mark {mark}"), "320 °F");
    assert.equal(units.oven(160, "gas", "gas mark {mark}"), "gas mark 3");
  });
});

describe("kitchen rules", () => {
  test("scaling rounds to one decimal", () => {
    assert.equal(kitchen.scaled(250, 4, 5), 312.5);
    assert.equal(kitchen.scaled(1000, 6, 5), 833.3);
    assert.equal(kitchen.scaled(2, 4, 5), 2.5);
  });

  test("the servings stepper stays within 1-12", () => {
    const k = kitchen.freshKitchen(SEED);
    const r1 = recipe("r1");
    for (let i = 0; i < 3; i += 1) assert.equal(kitchen.stepServings(k, r1, -1, SEED.servings_range).ok, true);
    const refused = kitchen.stepServings(k, r1, -1, SEED.servings_range);
    assert.equal(refused.ok, false);
    assert.equal(refused.servings, 1);
    assert.deepEqual(refused.message, ["notice.serves_min", { min: 1 }]);
    for (let i = 0; i < 11; i += 1) kitchen.stepServings(k, r1, 1, SEED.servings_range);
    assert.equal(k.servings.r1, 12);
    assert.equal(kitchen.stepServings(k, r1, 1, SEED.servings_range).ok, false);
  });

  test("a recipe goes on the list once, at its chosen servings", () => {
    const k = kitchen.freshKitchen(SEED);
    k.servings.r1 = 6;
    assert.equal(kitchen.addToList(k, recipe("r1")).ok, true);
    assert.deepEqual(k.list.map((item) => [item.id, item.amount]), [["i1", 375], ["i2", 187.5], ["i3", 112.5]]);
    assert.equal(kitchen.addToList(k, recipe("r1")).ok, false);
    assert.equal(k.list.length, 3);
  });

  test("ticking and clearing", () => {
    const k = kitchen.freshKitchen(SEED);
    kitchen.addToList(k, recipe("r2"));
    assert.equal(kitchen.clearTicked(k).ok, false);
    assert.equal(kitchen.toggleItem(k, "i2").ok, true);
    assert.equal(kitchen.toggleItem(k, "i9").ok, false);
    assert.equal(kitchen.clearTicked(k).ok, true);
    assert.deepEqual(k.list.map((item) => item.name), ["plain flour", "eggs"]);
    assert.equal(kitchen.addToList(k, recipe("r2")).ok, false, "still on the list while any line remains");
  });

  test("a settings save names exactly one known group", () => {
    assert.deepEqual(kitchen.settingsPatch({ unitSystem: "us" }), { unitSystem: "us" });
    assert.deepEqual(kitchen.settingsPatch({ ovenScale: "gas" }), { ovenScale: "gas" });
    assert.equal(kitchen.settingsPatch({ unitSystem: "imperial" }), null);
    assert.equal(kitchen.settingsPatch({ unitSystem: "us", ovenScale: "gas" }), null);
    assert.equal(kitchen.settingsPatch({}), null);
    assert.equal(kitchen.settingsPatch(null), null);
  });
});
