// The kitchen's rules: servings, the shopping list and the kitchen settings. Pure functions
// over one session's data; server.js reads the session, calls one of these and writes it back.

export const UNIT_SYSTEMS = ["metric", "us"];
export const OVEN_SCALES = ["celsius", "fahrenheit", "gas"];

/** An amount for `servings` people from a recipe written for `serves`, to one decimal. */
export function scaled(amount, serves, servings) {
  return Math.round((amount * servings * 10) / serves) / 10;
}

/** A fresh session: every recipe at the number it was written for, an empty list. */
export function freshKitchen(seed) {
  return {
    servings: Object.fromEntries(seed.recipes.map((recipe) => [recipe.id, recipe.serves])),
    list: [],
    nextItem: 1,
    settings: { ...seed.settings },
  };
}

/**
 * One step of the servings stepper. `change` is +1 or -1; the result stays within `range`.
 * A step past either end changes nothing and says why.
 */
export function stepServings(kitchen, recipe, change, [min, max]) {
  const now = kitchen.servings[recipe.id] ?? recipe.serves;
  if (change !== 1 && change !== -1) return { ok: false, servings: now, message: ["notice.recipe_missing", {}] };
  const wanted = now + change;
  if (wanted < min) return { ok: false, servings: now, message: ["notice.serves_min", { min }] };
  if (wanted > max) return { ok: false, servings: now, message: ["notice.serves_max", { max }] };
  kitchen.servings[recipe.id] = wanted;
  return { ok: true, servings: wanted, message: null };
}

/** Whether any line of this recipe is still on the list (ticked or not). */
export function onList(kitchen, recipeId) {
  return kitchen.list.some((item) => item.recipe === recipeId);
}

/** Put every ingredient line of `recipe` on the list, at the servings chosen for it, once. */
export function addToList(kitchen, recipe) {
  if (onList(kitchen, recipe.id)) {
    return { ok: false, message: ["notice.already", { name: recipe.name }] };
  }
  const servings = kitchen.servings[recipe.id] ?? recipe.serves;
  for (const line of recipe.lines) {
    kitchen.list.push({
      id: `i${kitchen.nextItem}`,
      recipe: recipe.id,
      name: line.name,
      amount: scaled(line.amount, recipe.serves, servings),
      unit: line.unit,
      aisle: line.aisle,
      ticked: false,
    });
    kitchen.nextItem += 1;
  }
  return { ok: true, message: ["notice.added", { name: recipe.name, count: recipe.lines.length }] };
}

/** Tick an item off, or put a ticked one back. */
export function toggleItem(kitchen, itemId) {
  const item = kitchen.list.find((entry) => entry.id === itemId);
  if (!item) return { ok: false, message: ["notice.item_missing", {}] };
  item.ticked = !item.ticked;
  return { ok: true, message: [item.ticked ? "notice.ticked" : "notice.unticked", { name: item.name }] };
}

/** Remove every ticked item. With nothing ticked, nothing changes. */
export function clearTicked(kitchen) {
  const ticked = kitchen.list.filter((item) => item.ticked).length;
  if (!ticked) return { ok: false, message: ["notice.nothing_ticked", {}] };
  kitchen.list = kitchen.list.filter((item) => !item.ticked);
  return { ok: true, message: ["notice.cleared", { count: ticked }] };
}

/**
 * A settings save names exactly one group: the Measures form sends `unitSystem`, the Oven
 * form `ovenScale`. Anything else is refused (null).
 */
export function settingsPatch(body) {
  if (!body || typeof body !== "object" || Array.isArray(body)) return null;
  const keys = Object.keys(body);
  if (keys.length !== 1) return null;
  const [key] = keys;
  if (key === "unitSystem" && UNIT_SYSTEMS.includes(body[key])) return { unitSystem: body[key] };
  if (key === "ovenScale" && OVEN_SCALES.includes(body[key])) return { ovenScale: body[key] };
  return null;
}
