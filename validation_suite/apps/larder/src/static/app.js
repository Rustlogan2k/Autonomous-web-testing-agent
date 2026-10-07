// Larder in the browser: a History API router over a master–detail split pane.
//
// Routes are real paths: /recipes, /recipes/<id>, /recipes/<id>/method, /list, /settings,
// /conversions. In-app links are intercepted and followed with history.pushState; Back and
// Forward are rendered from the popstate event. The breadcrumb is derived from the address;
// the detail pane from the route state the history entry carries (falling back to the
// address when an entry has none). Every view renders synchronously from data the page
// already holds; a change is one request whose answer is rendered as soon as it arrives.
(function () {
  "use strict";

  const BOOT = JSON.parse(document.getElementById("larder-boot").textContent);
  const LABELS = BOOT.vocab;
  const units = window.LarderUnits;
  const recipes = BOOT.recipes;
  const [MIN_SERVINGS, MAX_SERVINGS] = BOOT.range;
  const servings = { ...BOOT.servings };
  let list = BOOT.list;
  /** The kitchen settings as last saved (merged here after each accepted save). */
  let settings = { ...BOOT.settings };
  /** The recipe whose data fills the detail pane, or null. */
  let current = null;
  let notice = "";
  let shared = false;
  let printLayout = false;

  function t(key, params = {}) {
    const text = Object.prototype.hasOwnProperty.call(LABELS, key) ? LABELS[key] : key;
    return Object.entries(params).reduce((out, [name, value]) => out.split(`{${name}}`).join(String(value)), text);
  }

  const h = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[ch]);

  const recipeById = (id) => recipes.find((recipe) => recipe.id === id) || null;

  /** What an address means. Unknown addresses are the not-found view. */
  function parse(pathname) {
    if (pathname === "/recipes" || pathname === "/") return { view: "recipes", recipeId: null };
    const match = /^\/recipes\/(r\d+)(\/method)?$/.exec(pathname);
    if (match) return { view: match[2] ? "method" : "recipe", recipeId: match[1] };
    if (pathname === "/list") return { view: "list", recipeId: null };
    if (pathname === "/settings") return { view: "settings", recipeId: null };
    if (pathname === "/conversions") return { view: "conversions", recipeId: null };
    return { view: "missing", recipeId: null };
  }

  // -- requests ------------------------------------------------------------------------------

  async function send(method, url, payload) {
    try {
      const response = await fetch(url, {
        method,
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload ?? {}),
      });
      return await response.json();
    } catch {
      return { ok: false, message: t("notice.offline") };
    }
  }

  // -- pieces --------------------------------------------------------------------------------

  function navBar(view) {
    const item = (id, href, key, active) =>
      `<a id="${id}" href="${href}" data-link${active ? ' aria-current="page"' : ""}>${h(t(key))}</a>`;
    const onRecipes = view === "recipes" || view === "recipe" || view === "method";
    return `<header class="masthead"><span class="wordmark">${h(t("app.name"))}</span>`
      + `<span class="tagline">${h(t("app.tagline"))}</span>`
      + `<nav class="primary" aria-label="${h(t("nav.label"))}">`
      + item("nav-recipes", "/recipes", "nav.recipes", onRecipes)
      + item("nav-list", "/list", "nav.list", view === "list")
      + item("nav-units", "/settings", "nav.units", view === "settings")
      + item("nav-conversions", "/conversions", "nav.conversions", view === "conversions")
      + "</nav></header>";
  }

  /** The breadcrumb comes from the address alone. */
  function breadcrumb(pathname) {
    const route = parse(pathname);
    const parts = [h(t("nav.recipes"))];
    if (route.view === "recipe" || route.view === "method") {
      parts.push(`${h(t("crumb.recipe"))} <span id="crumb-ref">${h(route.recipeId.slice(1))}</span>`);
      if (route.view === "method") parts.push(h(t("crumb.method")));
    } else if (route.view === "list") {
      parts[0] = h(t("nav.list"));
    } else if (route.view === "settings") {
      parts[0] = h(t("nav.units"));
    } else if (route.view === "conversions") {
      parts[0] = h(t("nav.conversions"));
    } else if (route.view === "missing") {
      parts[0] = h(t("crumb.missing"));
    }
    return `<p class="crumbs" aria-label="${h(t("crumb.label"))}">${parts.join(" › ")}</p>`;
  }

  function masterPane() {
    const rows = recipes.map((recipe) => {
      const selected = current && current.id === recipe.id;
      return `<li><a id="recipe-${recipe.id}" href="/recipes/${recipe.id}" data-recipe="${recipe.id}"`
        + `${selected ? ' aria-current="true"' : ""}>${h(recipe.name)}</a>`
        + ` <span class="muted">${h(t("master.for", { count: servings[recipe.id] }))}</span></li>`;
    }).join("");
    return `<aside class="master" aria-labelledby="master-title"><h2 id="master-title">${h(t("master.title"))}</h2>`
      + `<ul class="recipes">${rows}</ul></aside>`;
  }

  /** Ingredient lines of `recipe` at the servings chosen for it. */
  function linesOf(recipe) {
    const count = servings[recipe.id];
    return recipe.lines.map((line) => ({
      name: line.name, unit: line.unit, aisle: line.aisle,
      base: line.amount, scaled: units.scale(line.amount, recipe.serves, count),
    }));
  }

  function ingredientsTable(recipe) {
    const us = settings.unitSystem === "us";
    const rows = linesOf(recipe).map((line, index) => {
      const n = index + 1;
      const unit = line.unit ? ` <span class="unit" id="unit-${n}">${h(line.unit)}</span>` : "";
      const usCell = us ? `<td id="u-${n}">${h(units.usAmount(line))}</td>` : "";
      return `<tr><th scope="row">${h(line.name)}</th><td id="m-${n}">${h(units.metricAmount(line))}${unit}</td>${usCell}</tr>`;
    }).join("");
    return `<table class="ingredients"><thead><tr><th scope="col">${h(t("table.ingredient"))}</th>`
      + `<th scope="col">${h(t("table.amount"))}</th>${us ? `<th scope="col">${h(t("table.us"))}</th>` : ""}</tr></thead>`
      + `<tbody>${rows}</tbody></table>`;
  }

  function methodSteps(recipe) {
    const temperature = recipe.oven === null ? "" : units.oven(recipe.oven, settings.ovenScale, t("oven.gas_value"));
    const steps = recipe.method.map((step) => `<li>${h(step.split("{oven}").join(temperature))}</li>`).join("");
    return `<ol id="method-steps" class="method">${steps}</ol>`;
  }

  function detailPane(route) {
    if (!current) {
      const key = route.view === "recipes" ? "detail.empty" : "detail.missing";
      return `<section class="detail detail-empty"><p>${h(t(key))}</p></section>`;
    }
    const recipe = current;
    const base = `/recipes/${recipe.id}`;
    const tab = (id, href, key, active) =>
      `<a id="${id}" href="${href}" data-link${active ? ' aria-current="page"' : ""}>${h(t(key))}</a>`;
    const oven = recipe.oven === null ? ""
      : `<p class="oven">${h(t("detail.oven", { temperature: units.oven(recipe.oven, settings.ovenScale, t("oven.gas_value")) }))}</p>`;
    return `<section class="detail" aria-labelledby="detail-title">`
      + `<h2 id="detail-title">${h(recipe.name)}</h2>`
      + `<p class="meta">${h(t("detail.ref"))} <span id="detail-ref">${recipe.number}</span></p>`
      + `<div class="stepper"><span>${h(t("detail.serves"))}</span> <output id="detail-serves">${servings[recipe.id]}</output>`
      + ` <button type="button" id="serve-less">${h(t("act.serve_less"))}</button>`
      + ` <button type="button" id="serve-more">${h(t("act.serve_more"))}</button></div>`
      + oven
      + `<nav class="tabs" aria-label="${h(t("tab.label"))}">`
      + tab("tab-ingredients", base, "tab.ingredients", route.view === "recipe")
      + tab("tab-method", `${base}/method`, "tab.method", route.view === "method")
      + "</nav>"
      + (route.view === "method" ? methodSteps(recipe) : ingredientsTable(recipe))
      + `<div class="actions"><button type="button" id="add-to-list">${h(t("act.add_to_list"))}</button>`
      + ` <button type="button" id="print-view">${h(t(printLayout ? "act.screen_view" : "act.print_view"))}</button>`
      + ` <button type="button" id="share-recipe">${h(t("act.share_recipe"))}</button></div>`
      + (shared ? `<p class="share">${h(t("share.address"))} <code>${h(location.origin + location.pathname)}</code></p>` : "")
      + (printLayout ? `<p class="print-note">${h(t("print.note"))}</p>` : "")
      + "</section>";
  }

  function listView() {
    const toBuy = list.filter((item) => !item.ticked).length;
    const aisles = [];
    for (const item of list) if (!aisles.includes(item.aisle)) aisles.push(item.aisle);
    const groups = aisles.map((aisle) => {
      const rows = list.filter((item) => item.aisle === aisle).map((item) => {
        const recipe = recipeById(item.recipe);
        const line = { unit: item.unit, base: item.amount, scaled: item.amount };
        const amount = `${units.tidy(item.amount)}${item.unit ? ` ${item.unit}` : ""}`;
        const us = settings.unitSystem === "us" && item.unit ? ` (${units.usAmount(line)})` : "";
        return `<li class="${item.ticked ? "ticked" : ""}"><span class="item">${h(item.name)} ${h(amount)}${h(us)}</span>`
          + ` <span class="muted">${h(t("list.from", { name: recipe ? recipe.name : item.recipe }))}</span>`
          + ` <button type="button" id="tick-${item.id}" data-item="${item.id}">${h(t(item.ticked ? "act.untick" : "act.tick"))}</button></li>`;
      }).join("");
      return `<h3>${h(t(`aisle.${aisle}`))}</h3><ul>${rows}</ul>`;
    }).join("");
    const names = [...new Set(list.map((item) => item.recipe))].map((id) => recipeById(id)?.name ?? id);
    return `<section class="page"><h2>${h(t("list.title"))}</h2>`
      + `<p>${h(t("list.to_buy"))} <strong id="list-count">${toBuy}</strong>`
      + (names.length ? ` · ${h(t("list.recipes"))} ${h(names.join(", "))}` : "") + "</p>"
      + `<section id="list-lines" class="list">${list.length ? groups : `<p class="muted">${h(t("list.empty"))}</p>`}</section>`
      + `<p><button type="button" id="clear-ticked">${h(t("act.clear_ticked"))}</button></p></section>`;
  }

  function summaryOf(saved) {
    return `${t(saved.unitSystem === "us" ? "units.us" : "units.metric")}, ${t(`oven.${saved.ovenScale}`)}`;
  }

  function settingsView() {
    const option = (value, key, chosen) => `<option value="${value}"${value === chosen ? " selected" : ""}>${h(t(key))}</option>`;
    return `<section class="page"><h2>${h(t("settings.title"))}</h2>`
      + `<p>${h(t("settings.summary_label"))} <strong id="settings-summary">${h(summaryOf(settings))}</strong></p>`
      + `<div class="subform"><h3>${h(t("settings.measures"))}</h3>`
      + `<label for="unit-system">${h(t("settings.measures_label"))}</label> `
      + `<select id="unit-system" name="unit_system">${option("metric", "units.metric", settings.unitSystem)}${option("us", "units.us", settings.unitSystem)}</select>`
      + ` <button type="button" id="save-measures">${h(t("act.save_measures"))}</button></div>`
      + `<div class="subform"><h3>${h(t("settings.oven"))}</h3>`
      + `<label for="oven-scale">${h(t("settings.oven_label"))}</label> `
      + `<select id="oven-scale" name="oven_scale">${option("celsius", "oven.celsius", settings.ovenScale)}`
      + `${option("fahrenheit", "oven.fahrenheit", settings.ovenScale)}${option("gas", "oven.gas", settings.ovenScale)}</select>`
      + ` <button type="button" id="save-oven">${h(t("act.save_oven"))}</button></div></section>`;
  }

  function conversionsView() {
    const measure = (metric, us) => `<tr><td>${h(metric)}</td><td>${h(us)}</td></tr>`;
    const temps = units.GAS_MARKS.map(([celsius]) => `<tr><td>${h(units.oven(celsius, "celsius", ""))}</td>`
      + `<td>${h(units.oven(celsius, "fahrenheit", ""))}</td><td>${h(units.oven(celsius, "gas", t("oven.gas_value")))}</td></tr>`).join("");
    return `<section class="page"><h2>${h(t("conv.title"))}</h2><p>${h(t("conv.intro"))}</p>`
      + `<h3>${h(t("conv.measures"))}</h3><table class="chart"><thead><tr><th scope="col">${h(t("conv.metric"))}</th>`
      + `<th scope="col">${h(t("conv.us"))}</th></tr></thead><tbody>`
      + measure(`${units.GRAMS_PER_OUNCE} g`, "1 oz") + measure(`${units.MILLILITRES_PER_FLUID_OUNCE} ml`, "1 fl oz")
      + measure("236.56 ml", "1 cup (8 fl oz)") + measure("453.6 g", "1 lb (16 oz)") + "</tbody></table>"
      + `<h3>${h(t("conv.oven"))}</h3><table class="chart"><thead><tr><th scope="col">${h(t("oven.celsius"))}</th>`
      + `<th scope="col">${h(t("oven.fahrenheit"))}</th><th scope="col">${h(t("oven.gas"))}</th></tr></thead>`
      + `<tbody>${temps}</tbody></table></section>`;
  }

  function missingView() {
    return `<section class="page"><h2>${h(t("missing.title"))}</h2><p>${h(t("missing.body"))}</p></section>`;
  }

  // -- rendering -----------------------------------------------------------------------------

  /**
   * Draw the page for `pathname`. The detail pane shows `recipeId` when the history entry
   * names one, otherwise the recipe the address names.
   */
  function show(pathname, recipeId) {
    const route = parse(pathname);
    const onRecipes = route.view === "recipes" || route.view === "recipe" || route.view === "method";
    current = onRecipes ? recipeById(recipeId || route.recipeId) : null;
    let body;
    if (onRecipes) body = `<div class="split">${masterPane()}${detailPane(route)}</div>`;
    else if (route.view === "list") body = listView();
    else if (route.view === "settings") body = settingsView();
    else if (route.view === "conversions") body = conversionsView();
    else body = missingView();
    document.body.classList.toggle("print-layout", printLayout && Boolean(current));
    document.getElementById("app").innerHTML = navBar(route.view)
      + `<main>${breadcrumb(pathname)}<p id="notice" role="status">${h(notice)}</p>${body}</main>`;
  }

  const redraw = () => show(location.pathname, current && current.id);

  /** Follow an in-app link (menu, tabs): a new history entry for the address. */
  function navigate(href) {
    notice = "";
    shared = false;
    printLayout = false;
    const route = parse(href);
    if (location.pathname !== href) history.pushState({ recipe: route.recipeId }, "", href);
    show(href, route.recipeId);
    window.scrollTo(0, 0);
  }

  /** Open a recipe from the list: the detail pane swaps, the list stays on screen. */
  function openRecipe(next, href) {
    notice = "";
    shared = false;
    if (location.pathname !== href) history.pushState({ recipe: next.id }, "", href);
    show(href, next.id);
  }

  // -- actions -------------------------------------------------------------------------------

  async function stepServings(change) {
    if (!current) return;
    const recipe = current;
    const answer = await send("POST", `/api/recipes/${recipe.id}/servings`, { change });
    if (Number.isInteger(answer.servings)) servings[recipe.id] = answer.servings;
    notice = answer.ok ? "" : answer.message || "";
    redraw();
  }

  async function addToList() {
    if (!current) return;
    const answer = await send("POST", "/api/list", { recipe: current.id });
    if (Array.isArray(answer.list)) list = answer.list;
    notice = answer.message || "";
    redraw();
  }

  async function changeList(url) {
    const answer = await send("POST", url, {});
    if (Array.isArray(answer.list)) list = answer.list;
    notice = answer.message || "";
    redraw();
  }

  /**
   * Save one settings group. A group whose choice is already the saved one has nothing to
   * save and sends nothing. The server answers only whether it accepted the change.
   */
  async function saveGroup(field, value, savedKey) {
    if (settings[field] === value) {
      notice = t("notice.unchanged");
      redraw();
      return;
    }
    const answer = await send("PATCH", "/api/settings", { [field]: value });
    if (answer.ok) {
      settings = { ...settings, [field]: value };
      notice = t(savedKey);
    } else {
      notice = answer.message || "";
    }
    redraw();
  }

  const BUTTONS = {
    "serve-less": () => stepServings(-1),
    "serve-more": () => stepServings(1),
    "add-to-list": () => addToList(),
    "clear-ticked": () => changeList("/api/list/clear-ticked"),
    "save-measures": () => saveGroup("unitSystem", document.getElementById("unit-system").value, "notice.measures_saved"),
    "save-oven": () => saveGroup("ovenScale", document.getElementById("oven-scale").value, "notice.oven_saved"),
    "print-view": () => {
      printLayout = !printLayout;
      redraw();
    },
    "share-recipe": () => {
      shared = true;
      redraw();
    },
  };

  document.addEventListener("click", (event) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) return;
    const target = event.target instanceof Element ? event.target : null;
    const recipeLink = target && target.closest("a[data-recipe]");
    if (recipeLink) {
      event.preventDefault();
      const next = recipeById(recipeLink.dataset.recipe);
      if (next) openRecipe(next, recipeLink.getAttribute("href"));
      return;
    }
    const link = target && target.closest("a[data-link]");
    if (link) {
      event.preventDefault();
      navigate(link.getAttribute("href"));
      return;
    }
    const button = target && target.closest("button");
    if (!button) return;
    if (button.dataset.item) {
      changeList(`/api/list/items/${button.dataset.item}/tick`);
      return;
    }
    const action = BUTTONS[button.id];
    if (action) action();
  });

  window.addEventListener("popstate", (event) => {
    notice = "";
    shared = false;
    printLayout = false;
    const state = event.state || {};
    show(location.pathname, state.recipe || null);
  });

  // A load or a reload renders from the address.
  show(location.pathname, null);
})();
