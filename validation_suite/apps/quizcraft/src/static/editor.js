// The survey editor: a working copy of the document, edited only through command objects.
//
// Every edit (add, delete, move, rename, required, skip rule) is a command with apply() and
// revert(). execute() applies it to the working copy and pushes it on the undo stack; undo
// pops it, reverts it and pushes it on the redo stack; redo does the opposite. Consecutive
// renames of one question are absorbed into one command. Both stacks live only in this
// page's memory. Save posts the whole working copy; the server appends it as a new revision.
// The pane (editor, preview, history) and the preview step are kept in the address with
// history.replaceState, so a reload returns to the same pane over the latest saved revision.
(() => {
  "use strict";

  const data = JSON.parse(document.getElementById("quiz-data").textContent);
  const labels = JSON.parse(document.getElementById("quiz-labels").textContent);
  const t = (key, params = {}) => Object.entries(params)
    .reduce((text, [name, value]) => text.split(`{${name}}`).join(String(value)), labels[key] ?? key);
  const $ = (id) => document.getElementById(id);
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const esc = (text) => String(text).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[ch]);

  /** JSON with sorted keys: equal documents always give equal text. */
  function canon(value) {
    if (Array.isArray(value)) return `[${value.map(canon).join(",")}]`;
    if (value !== null && typeof value === "object") {
      return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canon(value[key])}`).join(",")}}`;
    }
    return JSON.stringify(value);
  }

  const MAX = data.max;
  const KINDS = ["choice", "yesno", "rating", "text"];
  const preview = window.QuizPreview.create(labels);
  const state = {
    doc: clone(data.document),
    saved: { revision: data.revision, text: canon(data.document) },
    undo: [],
    redo: [],
    lastKind: null,
    selected: null,
    addKind: "text",
    view: "editor",
    previewDoc: null,
    saving: false,
  };

  const indexOf = (doc, id) => doc.questions.findIndex((question) => question.id === id);
  const find = (doc, id) => doc.questions[indexOf(doc, id)];
  const serialize = (doc) => JSON.stringify({ base: state.saved.revision, document: doc });

  // -- commands --------------------------------------------------------------------------

  /** Change one field of one question (title, required or rule). */
  function setField(doc, id, field, after) {
    const position = indexOf(doc, id) + 1;
    const before = clone(find(doc, id)[field]);
    const describe = field === "required" ? (after ? "history.required_on" : "history.required_off") : `history.${field}`;
    return {
      kind: field, id, before, after: clone(after), label: t(describe, { n: position }),
      apply(target) { find(target, id)[field] = clone(this.after); },
      revert(target) { find(target, id)[field] = clone(this.before); },
      absorb(other) {
        if (field !== "title" || other.kind !== "title" || other.id !== id) return false;
        this.after = other.after;
        return true;
      },
    };
  }

  function nextId(doc) {
    const highest = doc.questions.reduce((top, question) => Math.max(top, Number(question.id.slice(1))), 0);
    return `q${highest + 1}`;
  }

  function addQuestion(doc, kind, index) {
    const question = {
      id: nextId(doc), kind, title: t("question.new_title"), required: false, rule: null,
      options: kind === "choice"
        ? [1, 2].map((n) => ({ value: `option-${n}`, label: t("question.option", { n }) }))
        : [],
    };
    return {
      kind: "add", id: question.id, label: t("history.add", { n: index + 1 }),
      apply(target) { target.questions.splice(index, 0, clone(question)); },
      revert(target) { target.questions.splice(index, 1); },
    };
  }

  /** Deleting a question also removes the skip rules that pointed at it (undo restores them). */
  function deleteQuestion(doc, index) {
    const question = clone(doc.questions[index]);
    const pointing = doc.questions.filter((q) => q.rule && q.rule.target === question.id).map((q) => q.id);
    return {
      kind: "delete", id: question.id, label: t("history.delete", { n: index + 1 }),
      apply(target) {
        target.questions.splice(index, 1);
        for (const id of pointing) find(target, id).rule = null;
      },
      revert(target) {
        target.questions.splice(index, 0, clone(question));
        for (const id of pointing) find(target, id).rule = { when: "no", target: question.id };
      },
    };
  }

  /** Skip rules always point forward: a move that would make one point back drops it. */
  function moveQuestion(doc, index, delta) {
    const other = index + delta;
    const swap = (list) => { [list[index], list[other]] = [list[other], list[index]]; };
    const after = clone(doc.questions);
    swap(after);
    const dropped = after
      .filter((q, i) => q.rule && after.findIndex((x) => x.id === q.rule.target) <= i)
      .map((q) => ({ id: q.id, rule: clone(q.rule) }));
    return {
      kind: delta < 0 ? "move_up" : "move_down", id: doc.questions[index].id,
      label: t(delta < 0 ? "history.move_up" : "history.move_down", { n: index + 1 }),
      apply(target) {
        swap(target.questions);
        for (const lost of dropped) find(target, lost.id).rule = null;
      },
      revert(target) {
        for (const lost of dropped) find(target, lost.id).rule = clone(lost.rule);
        swap(target.questions);
      },
    };
  }

  // -- the history -------------------------------------------------------------------------

  function execute(command) {
    const top = state.undo[state.undo.length - 1];
    if (state.lastKind === "change" && top && top.absorb && top.absorb(command)) {
      top.apply(state.doc);
    } else {
      command.apply(state.doc);
      state.undo.push(command);
    }
    state.redo.length = 0; // a new change ends the redo branch
    state.lastKind = "change";
    render();
  }

  function undo() {
    const command = state.undo.pop();
    if (!command) return;
    command.revert(state.doc);
    state.redo.push(command);
    state.lastKind = "undo";
    render();
  }

  function redo() {
    const command = state.redo.pop();
    if (!command) return;
    command.apply(state.doc);
    state.undo.push(command);
    state.lastKind = "redo";
    render();
  }

  // -- saving ------------------------------------------------------------------------------

  const unsaved = () => canon(state.doc) !== state.saved.text;
  const note = (text, refused = false) => {
    $("save-note").textContent = text;
    $("save-note").className = refused ? "note refused" : "note";
  };

  function save() {
    if (state.saving) return;
    if (!unsaved()) {
      note(t("save.nothing"));
      return;
    }
    state.saving = true;
    const snapshot = canon(state.doc);
    const payload = serialize(state.doc);
    note(t("save.saving"));
    fetch("/api/revisions", {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: payload,
    })
      .then((response) => response.json())
      .then((answer) => {
        state.saving = false;
        if (!answer.ok) {
          note(answer.message, true);
          return;
        }
        state.saved = { revision: answer.revision, text: snapshot };
        note(t("save.saved", { n: answer.revision }));
        render();
      })
      .catch(() => {
        state.saving = false;
        note(t("save.failed"), true);
      });
  }

  function savedSummary() {
    const saved = JSON.parse(state.saved.text);
    const required = saved.questions.filter((question) => question.required).length;
    return t("saved.summary", { n: state.saved.revision, k: saved.questions.length, r: required });
  }

  // -- drawing -----------------------------------------------------------------------------

  function questionButton(question, index) {
    const selected = question.id === state.selected;
    const rule = window.QuizPreview.positionOf(state.doc, question.rule?.target ?? "");
    const badges = [t(`kind.${question.kind}`)];
    if (question.required) badges.push(t("question.required_badge"));
    if (question.rule && rule) badges.push(t("rule.badge", { n: rule }));
    return `<li><button id="q-${index + 1}" type="button" class="question${selected ? " selected" : ""}" data-index="${index}" aria-pressed="${selected}">
  <span class="num">${index + 1}.</span> ${esc(question.title.trim() ? question.title : t("question.untitled"))}
  ${badges.map((badge) => `<em>${esc(badge)}</em>`).join(" ")}</button></li>`;
  }

  function drawInspector() {
    const index = state.selected ? indexOf(state.doc, state.selected) : -1;
    const question = index >= 0 ? state.doc.questions[index] : null;
    $("inspector-empty").hidden = Boolean(question);
    $("inspector-body").hidden = !question;
    if (!question) return;
    const title = $("q-title");
    if (title.value !== question.title) title.value = question.title;
    $("q-required").checked = question.required;
    $("inspector-kind").textContent = t("inspector.kind", { n: index + 1, kind: t(`kind.${question.kind}`) });
    $("logic-panel").hidden = question.kind !== "yesno";
    const current = question.rule ? window.QuizPreview.positionOf(state.doc, question.rule.target) : 0;
    const later = state.doc.questions.slice(index + 1).map((_, offset) => index + offset + 2);
    $("rule-target").innerHTML = [`<option value="none"${current ? "" : " selected"}>${esc(t("rule.none"))}</option>`]
      .concat(later.map((n) => `<option value="${n}"${n === current ? " selected" : ""}>${esc(t("rule.option", { n }))}</option>`))
      .join("");
    $("move-up").disabled = index === 0;
    $("move-down").disabled = index === state.doc.questions.length - 1;
  }

  function drawHistory() {
    const done = state.undo.map((command) => `<li class="done">${esc(command.label)}</li>`);
    const here = `<li class="here" aria-current="step">${esc(t("history.here"))}</li>`;
    const undone = [...state.redo].reverse().map((command) => `<li class="undone">${esc(command.label)}</li>`);
    $("history-list").innerHTML = [`<li class="origin">${esc(t("history.origin", { n: state.saved.revision }))}</li>`]
      .concat(done, [here], undone).join("\n");
  }

  function drawPreview() {
    $("preview-card").innerHTML = preview.cardHtml();
    $("preview-back").disabled = !preview.run.path.length && preview.run.step <= 1;
    $("preview-next").disabled = preview.finished();
    drawTrace();
  }

  function drawTrace() {
    const trace = preview.run.doc ? preview.traceOfSecond() : { answer: "", rule: "" };
    $("preview-answer-2").textContent = trace.answer;
    $("preview-rule-2").textContent = trace.rule;
  }

  function render() {
    const count = state.doc.questions.length;
    if (state.selected && indexOf(state.doc, state.selected) < 0) state.selected = null;
    $("question-list").innerHTML = state.doc.questions.map(questionButton).join("\n");
    drawInspector();
    $("add-question").disabled = count >= MAX;
    for (const kind of KINDS) $(`add-type-${kind}`).setAttribute("aria-pressed", String(kind === state.addKind));
    $("delete-question").disabled = count <= 1;
    $("undo").disabled = !state.undo.length;
    $("redo").disabled = !state.redo.length;
    $("question-count").textContent = String(count);
    $("unsaved-flag").textContent = t(unsaved() ? "status.unsaved" : "status.all_saved");
    $("saved-summary").textContent = savedSummary();
    $("history-last-kind").textContent = t(`history.kind_${state.lastKind ?? "none"}`);
    $("history-ahead").textContent = String(state.redo.length);
    drawHistory();
    for (const view of ["editor", "preview", "history"]) {
      $(`pane-${view}`).hidden = view !== state.view;
      $(`tab-${view}`).setAttribute("aria-selected", String(view === state.view));
    }
    if (state.view === "preview") drawPreview();
  }

  function remember() {
    const params = new URLSearchParams({ view: state.view });
    if (state.view === "preview") params.set("step", preview.finished() ? "end" : String(preview.run.step));
    window.history.replaceState(null, "", `/quiz?${params}`);
  }

  /** Switch pane. Entering the preview freezes a copy of the document and starts a new run. */
  function show(view, step = 1) {
    if (view === "preview") {
      state.previewDoc = clone(state.doc);
      preview.start(state.previewDoc, step);
    }
    state.view = view;
    render();
    remember();
  }

  // -- wiring ------------------------------------------------------------------------------

  const selectedIndex = () => (state.selected ? indexOf(state.doc, state.selected) : -1);

  $("question-list").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-index]");
    if (!button) return;
    state.selected = state.doc.questions[Number(button.dataset.index)]?.id ?? null;
    render();
  });
  for (const kind of KINDS) {
    $(`add-type-${kind}`).addEventListener("click", () => {
      state.addKind = kind;
      render();
    });
  }
  $("add-question").addEventListener("click", () => {
    if (state.doc.questions.length >= MAX) return;
    const at = selectedIndex() >= 0 ? selectedIndex() + 1 : state.doc.questions.length;
    const command = addQuestion(state.doc, state.addKind, at);
    state.selected = command.id;
    execute(command);
  });
  $("delete-question").addEventListener("click", () => {
    const index = selectedIndex();
    if (index < 0 || state.doc.questions.length <= 1) return;
    execute(deleteQuestion(state.doc, index));
  });
  $("move-up").addEventListener("click", () => {
    const index = selectedIndex();
    if (index > 0) execute(moveQuestion(state.doc, index, -1));
  });
  $("move-down").addEventListener("click", () => {
    const index = selectedIndex();
    if (index >= 0 && index < state.doc.questions.length - 1) execute(moveQuestion(state.doc, index, 1));
  });
  $("q-title").addEventListener("input", (event) => {
    if (state.selected) execute(setField(state.doc, state.selected, "title", event.target.value));
  });
  $("q-required").addEventListener("change", (event) => {
    if (state.selected) execute(setField(state.doc, state.selected, "required", event.target.checked));
  });
  $("rule-target").addEventListener("change", (event) => {
    if (!state.selected) return;
    const target = state.doc.questions[Number(event.target.value) - 1];
    execute(setField(state.doc, state.selected, "rule", target ? { when: "no", target: target.id } : null));
  });
  $("undo").addEventListener("click", undo);
  $("redo").addEventListener("click", redo);
  $("save-quiz").addEventListener("click", save);
  $("tab-editor").addEventListener("click", () => show("editor"));
  $("tab-preview").addEventListener("click", () => show("preview"));
  $("tab-history").addEventListener("click", () => show("history"));
  $("copy-link").addEventListener("click", () => {
    $("tool-note").textContent = t("tool.link", { url: `${window.location.origin}/quiz` });
  });
  $("spell-check").addEventListener("click", () => {
    $("tool-note").textContent = t("tool.spelling_ok");
  });

  $("preview-next").addEventListener("click", () => {
    preview.next();
    drawPreview();
    remember();
  });
  $("preview-back").addEventListener("click", () => {
    preview.back();
    drawPreview();
    remember();
  });
  $("preview-restart").addEventListener("click", () => {
    preview.start(state.previewDoc);
    drawPreview();
    remember();
  });
  const onAnswer = (event) => {
    const control = event.target.closest("[data-question]");
    if (!control) return;
    preview.answer(control.dataset.question, control.value);
    drawTrace();
  };
  $("preview-card").addEventListener("change", onAnswer);
  $("preview-card").addEventListener("input", onAnswer);

  // -- start: the pane and preview step from the address, over the latest saved revision -----

  const params = new URLSearchParams(window.location.search);
  const view = ["editor", "preview", "history"].includes(params.get("view")) ? params.get("view") : "editor";
  if (view === "preview") {
    const step = params.get("step") === "end" ? state.doc.questions.length + 1 : Number(params.get("step"));
    state.previewDoc = clone(state.doc);
    preview.start(state.previewDoc, step);
  }
  state.view = view;
  render();
})();
