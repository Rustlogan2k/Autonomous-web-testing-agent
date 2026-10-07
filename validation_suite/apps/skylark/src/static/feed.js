// The feed's "Load more": fetch the next keyset page as JSON, append its rows under the
// server-rendered ones, and keep "Showing n of m" and the bird tallies in step with what is
// listed. The server renders the first page and its tallies; this script only adds to them.
(() => {
  "use strict";

  const labelsNode = document.getElementById("feed-labels");
  const labels = labelsNode ? JSON.parse(labelsNode.textContent) : {};
  const t = (key, params = {}) => Object.entries(params)
    .reduce((text, [name, value]) => text.split(`{${name}}`).join(String(value)), labels[key] ?? key);

  // A sighting that was just recorded has left its record-form draft behind: drop it.
  const sent = document.querySelector('meta[name="skylark-sent-draft"]');
  if (sent) {
    try {
      window.localStorage.removeItem(sent.content);
    } catch {
      // Storage unavailable: there was no draft to drop.
    }
  }

  const button = document.getElementById("load-more");
  const list = document.getElementById("feed-list");
  if (!button || !list) return;
  const shown = document.getElementById("shown-count");
  const status = document.getElementById("feed-status");
  const totalNode = document.getElementById("tally-total");
  const siteNodes = [...document.querySelectorAll("[data-tally]")];

  // Birds counted so far, starting from the server's figures for the first page.
  const tally = {
    total: Number(totalNode.textContent),
    sites: Object.fromEntries(siteNodes.map((node) => [node.dataset.tally, Number(node.textContent)])),
  };
  let loading = false;

  function cell(text, className) {
    const td = document.createElement("td");
    if (className) td.className = className;
    td.textContent = String(text);
    return td;
  }

  /** The same row the server draws in views.js feedRow(). */
  function rowFor(row) {
    const tr = document.createElement("tr");
    tr.dataset.ref = row.ref;
    tr.className = row.status;
    const link = document.createElement("a");
    link.id = `sighting-${row.ref}`;
    link.href = `/sightings/${row.ref}`;
    link.textContent = row.speciesName;
    const name = document.createElement("td");
    name.append(link);
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = row.statusName;
    const state = document.createElement("td");
    state.append(badge);
    tr.append(cell(row.date), name, cell(row.count, "num"), cell(row.siteName), state);
    return tr;
  }

  function drawTallies() {
    totalNode.textContent = String(tally.total);
    for (const node of siteNodes) node.textContent = String(tally.sites[node.dataset.tally] ?? 0);
  }

  function reachedTheEnd() {
    const end = document.createElement("p");
    end.id = "feed-end";
    end.textContent = t("feed.end");
    button.replaceWith(end);
  }

  function appendPage(page) {
    list.append(...page.rows.map(rowFor));
    tally.total += page.countedBirds;
    for (const [site, birds] of Object.entries(page.bySite)) tally.sites[site] = (tally.sites[site] ?? 0) + birds;
    drawTallies();
    shown.textContent = String(list.rows.length);
    status.textContent = t("feed.loaded", { rows: page.rows.length, birds: page.birds });
    if (page.more) button.dataset.after = page.after;
    else reachedTheEnd();
  }

  async function loadMore() {
    if (loading) return; // one page at a time: a second click waits for the first page
    loading = true;
    button.disabled = true;
    try {
      const query = new URLSearchParams({ site: button.dataset.site, after: button.dataset.after });
      const response = await fetch(`/feed/more?${query}`, { credentials: "same-origin", headers: { Accept: "application/json" } });
      const page = await response.json();
      if (page.ok) appendPage(page);
      else status.textContent = page.message;
    } catch {
      status.textContent = t("feed.load_failed");
    } finally {
      loading = false;
      button.disabled = false;
    }
  }

  button.addEventListener("click", loadMore);
})();
