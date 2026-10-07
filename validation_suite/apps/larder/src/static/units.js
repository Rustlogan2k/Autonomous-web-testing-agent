// Kitchen measures for the browser: scaling a recipe line to the servings chosen, and
// showing it in metric or US measures. A line is { unit, base, scaled }: `base` is the amount
// the recipe was written with, `scaled` the amount for the servings on screen.
// Classic script: it defines window.LarderUnits (and module.exports, for the unit tests).
(function (root) {
  "use strict";

  const GRAMS_PER_OUNCE = 28.35;
  const MILLILITRES_PER_FLUID_OUNCE = 29.57;
  const GAS_MARKS = [[140, 1], [150, 2], [160, 3], [180, 4], [190, 5], [200, 6], [220, 7], [230, 8], [240, 9]];

  /** One decimal place, without a trailing ".0": 312.5, 375, 2.5. */
  function tidy(value) {
    const rounded = Math.round(value * 10) / 10;
    return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
  }

  /** Always one decimal place, as US amounts are shown: 8.8, 11.0. */
  function oneDecimal(value) {
    return (Math.round(value * 10) / 10).toFixed(1);
  }

  /** The amount of a line for `servings` people, from a recipe written for `serves`. */
  function scale(amount, serves, servings) {
    return Math.round((amount * servings * 10) / serves) / 10;
  }

  function ounces(grams) {
    return `${oneDecimal(grams / GRAMS_PER_OUNCE)} oz`;
  }

  function fluidOunces(millilitres) {
    return `${oneDecimal(millilitres / MILLILITRES_PER_FLUID_OUNCE)} fl oz`;
  }

  /** The metric amount on screen: "312.5" plus the bare unit, shown separately. */
  function metricAmount(line) {
    return tidy(line.scaled);
  }

  /** The US amount of a line, converted from the amount on screen. Counts stay counts. */
  function usAmount(line) {
    if (line.unit === "g") return ounces(line.scaled); // weights convert the amount on screen
    if (line.unit === "ml") return fluidOunces(line.scaled);
    return tidy(line.scaled);
  }

  /** An oven temperature (written in Celsius) on the chosen scale. */
  function oven(celsius, scale, gasLabel) {
    if (scale === "fahrenheit") return `${Math.round((celsius * 9) / 5 + 32)} °F`;
    if (scale === "gas") {
      const mark = GAS_MARKS.reduce((best, [c, m]) => (Math.abs(c - celsius) < Math.abs(best[0] - celsius) ? [c, m] : best));
      return gasLabel.split("{mark}").join(String(mark[1]));
    }
    return `${celsius} °C`;
  }

  const api = {
    GRAMS_PER_OUNCE, MILLILITRES_PER_FLUID_OUNCE, GAS_MARKS,
    tidy, oneDecimal, scale, metricAmount, usAmount, oven,
  };
  root.LarderUnits = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(typeof window === "object" ? window : globalThis);
