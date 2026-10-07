/* Markbook's only script: the markbook's click-to-edit cells, and the export help panel.
   Every other page is plain server-rendered HTML with forms.

   One editor at a time. Saving sends one PATCH; the server answers with the student's
   recomputed row (all four marks, weighted %, level) and the assessment's column average,
   which replace those cells. The page sends the maxima it was drawn with, so a page drawn
   before a maximum changed is told to reload instead of mixing old and new figures. */
"use strict";

(() => {
  const helpToggle = document.getElementById("export-csv-help");
  if (helpToggle) {
    helpToggle.addEventListener("click", () => {
      const panel = document.getElementById(helpToggle.getAttribute("aria-controls"));
      const open = helpToggle.getAttribute("aria-expanded") === "true";
      helpToggle.setAttribute("aria-expanded", String(!open));
      panel.hidden = open;
    });
  }

  const grid = document.getElementById("markbook");
  if (!grid) return;
  const template = document.getElementById("cell-editor");
  const status = document.getElementById("cell-status");
  const problem = document.getElementById("cell-error");
  let editing = null; // {cell, button, input, saving}

  function closeEditor() {
    if (!editing) return;
    editing.cell.replaceChildren(editing.button);
    editing = null;
  }

  function openEditor(button) {
    closeEditor();
    problem.textContent = "";
    status.textContent = "";
    const editor = template.content.firstElementChild.cloneNode(true);
    const input = editor.querySelector("input");
    input.value = button.dataset.score;
    input.max = button.dataset.max;
    input.title = button.dataset.name;
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") save();
      if (event.key === "Escape") closeEditor();
    });
    const cell = button.parentElement;
    cell.replaceChildren(editor);
    editing = { cell, button, input, saving: false };
    input.focus();
  }

  function cellButton(student, assessment) {
    // The button of the cell being edited is out of the document until the editor closes.
    if (editing && editing.button.dataset.student === student && editing.button.dataset.assessment === assessment) {
      return editing.button;
    }
    return document.getElementById(`mark-${student}-${assessment}`);
  }

  function showRow(row) {
    for (const [assessment, score] of Object.entries(row.scores)) {
      const button = cellButton(row.student, assessment);
      if (!button) continue;
      button.textContent = score === null ? "-" : String(score);
      button.dataset.score = score === null ? "" : String(score);
    }
    document.getElementById(`pct-${row.student}`).textContent = row.pct;
    document.getElementById(`level-${row.student}`).textContent = row.level;
  }

  async function save() {
    if (!editing || editing.saving) return;
    const session = editing;
    const { button, input } = session;
    session.saving = true;
    session.cell.querySelector("#save-mark").disabled = true;
    problem.textContent = "";
    status.textContent = grid.dataset.saving;
    const address = `/teacher/classes/${grid.dataset.class}/marks/${button.dataset.student}/${button.dataset.assessment}`;
    let reply;
    try {
      const response = await fetch(address, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mark: input.value, basis: grid.dataset.basis }),
      });
      reply = await response.json();
    } catch (_error) {
      reply = { ok: false, error: grid.dataset.unreachable };
    }
    status.textContent = "";
    if (!reply.ok) {
      problem.textContent = reply.error;
      session.saving = false;
      const again = session.cell.querySelector("#save-mark");
      if (again) again.disabled = false;
      return;
    }
    if (editing === session) closeEditor();
    showRow(reply.row);
    document.getElementById(`avg-${reply.column.assessment}`).textContent = reply.column.average;
    document.getElementById("class-average").textContent = reply.class_average;
  }

  grid.addEventListener("click", (event) => {
    const target = event.target.closest("button");
    if (!target) return;
    if (target.classList.contains("cell")) openEditor(target);
    else if (target.id === "save-mark") save();
    else if (target.id === "cancel-mark") {
      closeEditor();
      problem.textContent = "";
    }
  });
})();
