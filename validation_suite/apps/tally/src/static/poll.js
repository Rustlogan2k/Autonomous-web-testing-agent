// /poll in the browser: the voting kiosk (open phase) and the round stepper (counted and
// declared phases). Classic script, run at the end of the page.
//
// Kiosk: Start ballot and Cast ballot are one fetch each; the ballot panel stays on screen
// and the status line and the officer's figures are updated in place from each answer, so
// every press of Cast ballot reaches the server and is answered there.
// Stepper: the count's rounds arrive embedded as JSON; the page opens on the final round
// and Previous/Next round re-render one round at a time.
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const h = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[ch]);

  async function post(url, payload) {
    try {
      const response = await fetch(url, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      return await response.json();
    } catch {
      return null;
    }
  }

  // -- kiosk ---------------------------------------------------------------------------------

  const status = $("kiosk-status");

  /** Officer figures only grow while the poll is open; an overtaken answer must not shrink them. */
  function showFigures(answer) {
    for (const [id, value] of [["ballots-cast", answer.cast], ["papers-issued", answer.issued]]) {
      const element = $(id);
      if (element && Number.isInteger(value) && value > Number(element.textContent)) element.textContent = String(value);
    }
  }

  function say(answer) {
    if (!status) return;
    status.textContent = answer ? answer.message : status.dataset.offline || "";
    status.className = answer && answer.ok ? "ok" : "refused";
  }

  const startButton = $("start-ballot");
  if (startButton) {
    startButton.addEventListener("click", async () => {
      const paper = Number(startButton.dataset.paper);
      const answer = await post("/api/kiosk/start", { paper });
      say(answer);
      if (answer) showFigures(answer);
      if (answer && answer.ok) {
        $("ballot-panel").hidden = false;
        startButton.hidden = true;
      }
    });
  }

  const castButton = $("cast-ballot");
  if (castButton) {
    castButton.addEventListener("click", async () => {
      const panel = $("ballot-panel");
      const preferences = Array.from(panel.querySelectorAll("select")).map((select) => select.value);
      const answer = await post("/api/ballots", { paper: Number(panel.dataset.paper), preferences });
      say(answer);
      if (answer) showFigures(answer);
    });
  }

  const printButton = $("print-paper");
  if (printButton) {
    printButton.addEventListener("click", () => {
      const blank = $("blank-paper");
      blank.hidden = !blank.hidden;
      document.body.classList.toggle("print-paper", !blank.hidden);
    });
  }

  // -- round stepper -------------------------------------------------------------------------

  const data = $("count-data");
  const view = $("round-view");
  if (!data || !view) return;
  const { rounds, candidates, labels } = JSON.parse(data.textContent);
  const t = (key, params = {}) => Object.entries(params)
    .reduce((text, [name, value]) => text.split(`{${name}}`).join(String(value)), labels[key] ?? key);
  const nameOf = (id) => (candidates.find((c) => c.id === id) || { name: id }).name;
  let shown = rounds.length - 1;

  function outcomeText(outcome) {
    if (outcome.kind === "elected") return t("round.elected", { name: nameOf(outcome.candidate) });
    if (outcome.kind === "eliminated") return t("round.eliminated", { name: nameOf(outcome.candidate) });
    return t("round.tied");
  }

  function render() {
    const round = rounds[shown];
    const rows = candidates.map((c) => {
      const votes = Object.prototype.hasOwnProperty.call(round.tallies, c.id) ? String(round.tallies[c.id]) : t("round.out");
      return `<tr><th scope="row">${h(c.name)}</th><td>${h(votes)}</td></tr>`;
    }).join("");
    view.innerHTML = `<p class="round-head">${h(t("round.label"))} <strong id="round-number">${round.number}</strong> ${h(t("round.of", { total: rounds.length }))}</p>`
      + `<table class="tallies"><thead><tr><th scope="col">${h(t("round.candidate"))}</th><th scope="col">${h(t("round.votes"))}</th></tr></thead><tbody>${rows}</tbody></table>`
      + `<p>${h(t("round.continuing"))} <strong id="round-continuing">${round.continuing}</strong>`
      + ` · ${h(t("round.exhausted"))} <span id="round-exhausted">${round.exhausted}</span>`
      + ` · ${h(t("round.highest"))} <strong id="round-leader-votes">${round.leaderVotes}</strong></p>`
      + `<p class="outcome">${h(t("round.outcome"))} <strong id="round-outcome">${h(outcomeText(round.outcome))}</strong></p>`
      + `<p class="stepper"><button type="button" id="round-prev"${shown === 0 ? " disabled" : ""}>${h(t("act.round_prev"))}</button>`
      + ` <button type="button" id="round-next"${shown === rounds.length - 1 ? " disabled" : ""}>${h(t("act.round_next"))}</button></p>`;
  }

  view.addEventListener("click", (event) => {
    const button = event.target instanceof Element ? event.target.closest("button") : null;
    if (!button || button.disabled) return;
    if (button.id === "round-prev" && shown > 0) shown -= 1;
    else if (button.id === "round-next" && shown < rounds.length - 1) shown += 1;
    else return;
    render();
  });

  render();
})();
