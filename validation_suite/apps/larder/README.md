# Larder — a personal recipe box

A home cook keeps a few recipes in Larder, scales one to the number of people eating, reads it
in metric or US measures (and oven temperatures in Celsius, Fahrenheit or gas marks), and builds
a shopping list from it, grouped by aisle, ticking things off while shopping.

Node 22 built-ins only (`node:http`, `node:fs`, `node:crypto`, `node:vm` in the tests); nothing
to install.

## Run

```sh
PORT=8080 node src/server.js
```

Then open http://127.0.0.1:8080/ (it redirects to `/recipes`).
**Docker:** `docker build -t larder . && docker run -p 8080:8080 larder`.

| variable | meaning |
|---|---|
| `PORT` | port to listen on (127.0.0.1; `HOST=0.0.0.0` listens on all interfaces) |
| `WTA_VOCAB` | `default` or `alt`: the same app with different wording |
| `WTA_FIXED_NOW` | the app's "now" (default `2026-06-10T09:00:00`); Larder keeps no dates, so it is only reported |
| `WTA_DATA_DIR` | where session files are written (default: a new temp directory) |
| `WTA_TEST_HOOKS=1` | enables `GET /__wta/state` (the session's servings, list and settings, and their sha256) |

**Tests:** `node --test "tests/**/*.test.mjs"` runs the unit tests of the kitchen rules and
measures (`tests/units.test.mjs`, no server needed) and the HTTP tests (`tests/api.test.mjs`),
which need a server started with `WTA_TEST_HOOKS=1` and `E2E_BASE_URL=http://127.0.0.1:8080/`.

## Views

All are real paths served by the same HTML shell and rendered by `static/app.js`.

| path | what it shows |
|---|---|
| `/recipes` | the recipe list (master pane) beside an empty detail pane |
| `/recipes/<id>` | list + detail: the recipe's number, servings stepper (**One fewer** / **One more**, 1-12), oven temperature, ingredients scaled to the servings (and their US amounts in US mode), **Add to shopping list**, Print view, Share recipe |
| `/recipes/<id>/method` | the same detail pane on its Method tab: the steps, with the oven temperature on the chosen scale |
| `/list` | the shopping list grouped by aisle, "Still to buy: n", **Got it** / **Still need it** per item, **Clear ticked items** |
| `/settings` | kitchen units: a *Measures* sub-form (metric / US) and an *Oven* sub-form (Celsius / Fahrenheit / gas mark), each with its own Save, and a summary of the saved settings |
| `/conversions` | the reference chart Larder converts with |
| anything else | a not-found view (the shell is answered with 404) |

## How it works

- **History API routing.** The server answers every app path with one shell carrying the
  session's kitchen as JSON (recipes, servings chosen, list, settings, labels). In-app links are
  intercepted: a recipe opened from the list is followed with
  `history.pushState({ recipe: <the recipe opened> }, "", "/recipes/<id>")`, the menu and the tabs
  likewise push the recipe their address names. Back and Forward are rendered from the
  `popstate` event: the detail pane from the entry's state (falling back to the address when the
  entry has none), the breadcrumb from `location.pathname`. A load or reload renders from the
  address. Nothing is fetched to change route.
- **Master–detail.** On recipe routes the list pane stays on screen and the detail pane swaps.
- **Scaling and measures** (`static/units.js`). A line's scaled amount is
  `amount × servings / serves`, to one decimal. Metric amounts are shown without a trailing
  ".0" (`312.5 g`, `375 g`); in US mode a weight is converted from the amount on screen at
  1 oz = 28.35 g and a volume at 1 US fl oz = 29.57 ml, both to one decimal (`11.0 oz`); counts
  stay counts. Oven temperatures are written in Celsius and shown in °C, °F or the nearest gas
  mark.
- **Changes are one request each**, answered with JSON the page renders at once:
  `POST /api/recipes/<id>/servings {change: ±1}` (refused at 1 and 12),
  `POST /api/list {recipe}` (a recipe's lines go on the list once, at its current servings; refused
  while any of its lines is still on the list), `POST /api/list/items/<item>/tick` (toggle),
  `POST /api/list/clear-ticked`, and `PATCH /api/settings` with exactly one group
  (`{unitSystem}` or `{ovenScale}`); a Save whose choice is already the saved one sends nothing
  and says there is nothing to save. The settings save answers only `{ok: true}`; the page then
  merges the saved field into its own copy of the settings, and the summary always shows that
  saved copy (changing a dropdown without saving does not alter it). Refusals are `200` with
  `{ok: false, message}`.
- **Persistence.** One JSON file per session (`wta_sid` cookie, HttpOnly) under
  `WTA_DATA_DIR/sessions/`, created from `seed/larder.json`. A request reads the file, changes it
  and writes it back without awaiting in between.
- **Data, not labels.** Recipe names, ingredients and method steps are recipe data; every other
  visible word comes from `vocab/<profile>.json`.
- *Print view* switches the page to a print layout in the browser only; *Share recipe* reveals
  the recipe's address to copy. Neither changes anything kept.

## Seed

r1 Shortbread (serves 4: plain flour 250 g, butter 125 g, caster sugar 75 g; oven 160 °C),
r2 Pancakes (serves 4: plain flour 150 g, milk 300 ml, eggs 2), r3 Tomato soup (serves 4: tomatoes
800 g, onion 1, vegetable stock 500 ml), r4 Lemonade (serves 6: water 1000 ml, lemons 4, caster
sugar 150 g). Settings: metric, Celsius. The list starts empty; items are numbered i1, i2, ... in
the order they are added.

## Workflows (`app.json`)

Scale Shortbread to 6 and add it (the list shows 375 g of flour); save US measures and open
Shortbread (8.8 oz); open Pancakes then its method; tick an item off (one fewer to buy); bake
Shortbread in Fahrenheit (320); Back after opening two recipes shows the first.

## Seeded faults

| fault | class / subtype | where | what goes wrong |
|---|---|---|---|
| LARD-1 | calculation_error / calculation | `src/static/units.js` | in US mode a weight is converted from the recipe's base amount instead of the scaled one: Shortbread for 5 shows 312.5 g beside 8.8 oz |
| LARD-2 | broken_transition / incorrect-navigation | `src/static/app.js` | opening a recipe from the list records the recipe previously on screen in the new history entry: after r1, r2, r3 and Back, `/recipes/r2` shows Shortbread |
| LARD-3 | persistence_failure / persistence | `src/server.js` | a settings save is merged into the defaults instead of the saved settings: saving the oven scale silently resets Measures to metric, visible after a reload |

Each fault is one literal find/replace in `app.json`; witnesses, assertions and the reasons each is
silent on the clean build are there and in `validation_suite/PLAN.md` §3.5.

## Deviations from the plan

- One region beyond the plan's list: `method_steps` (`#method-steps`, the method's step list), so
  the workflow "open Pancakes, then its method" can check that the method is shown. No fault reads it.
- Two workflows beyond the plan's four: "bake Shortbread in Fahrenheit" and "Back after opening
  two recipes shows the first" (both on the clean build).
