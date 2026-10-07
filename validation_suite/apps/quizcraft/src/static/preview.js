// The respondent preview: the survey one question at a time, as a member would answer it.
//
// Entering the preview freezes a copy of the working document (the editor keeps editing its
// own copy), so a walk-through is never changed under the respondent's feet. A run keeps
// its answers and the steps it has shown; Previous question returns along that path, so a
// question the skip logic jumped over is never shown on the way back either. Skip rules are
// always "If No, skip to <question>" on a yes/no question. Answers are stored as the option's
// value code (yes, no, weekly, ...); labels are only for display.
(() => {
  "use strict";

  /** Positions are 1-based, as shown to the secretary and in the address bar. */
  function positionOf(doc, id) {
    const index = doc.questions.findIndex((question) => question.id === id);
    return index < 0 ? 0 : index + 1;
  }

  /** The skip rule of the question at `step`, with its target as a position (or null). */
  function ruleAt(doc, step) {
    const question = doc.questions[step - 1];
    if (!question || question.kind !== "yesno" || !question.rule) return null;
    const target = positionOf(doc, question.rule.target);
    return target > step ? { when: question.rule.when, target } : null;
  }

  /** The choices a question offers, as [value, label] pairs. */
  function choicesOf(question, labels) {
    switch (question.kind) {
      case "yesno":
        return [["yes", labels["answer.yes"]], ["no", labels["answer.no"]]];
      case "choice":
        return question.options.map((option) => [option.value, option.label]);
      case "rating":
        return ["1", "2", "3", "4", "5"].map((value) => [value, value]);
      default:
        return [];
    }
  }

  function create(labels) {
    const t = (key, params = {}) => Object.entries(params)
      .reduce((text, [name, value]) => text.split(`{${name}}`).join(String(value)), labels[key] ?? key);

    /** What a member sees for a stored answer: the option's label (free text as typed). */
    function labelOf(question, value) {
      if (value === undefined || value === "") return "";
      const choice = choicesOf(question, labels).find(([code]) => code === value);
      return choice ? choice[1] : value;
    }

    /** The step after `step`, applying the question's skip rule to this run's answer. */
    function nextStep(doc, answers, step) {
      const q = doc.questions[step - 1];
      const rule = ruleAt(doc, step);
      if (rule) {
        if (answers[q.id] === rule.when) return rule.target;
      }
      return step + 1;
    }

    const run = { doc: null, step: 1, answers: {}, path: [], problem: "" };

    function start(doc, step = 1) {
      run.doc = doc;
      run.answers = {};
      run.path = [];
      run.problem = "";
      run.step = Number.isInteger(step) && step >= 1 && step <= doc.questions.length + 1 ? step : 1;
    }

    const finished = () => run.step > run.doc.questions.length;

    function next() {
      if (finished()) return;
      const question = run.doc.questions[run.step - 1];
      if (question.required && !String(run.answers[question.id] ?? "").trim()) {
        run.problem = t("preview.needs_answer");
        return;
      }
      run.problem = "";
      run.path.push(run.step);
      run.step = nextStep(run.doc, run.answers, run.step);
    }

    function back() {
      run.problem = "";
      if (run.path.length) run.step = run.path.pop();
      else if (run.step > 1) run.step = Math.min(run.step, run.doc.questions.length + 1) - 1;
    }

    function answer(id, value) {
      run.answers[id] = value;
      run.problem = "";
    }

    const esc = (text) => String(text).replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[ch]);

    /** The card for the current step: one question with its answer control, or the end. */
    function cardHtml() {
      const total = run.doc.questions.length;
      if (finished()) {
        return `<p id="preview-end" class="end">${esc(t("preview.end"))}</p>
<p class="muted">${esc(t("preview.end_note", { answered: Object.values(run.answers).filter((v) => String(v).trim()).length }))}</p>`;
      }
      const question = run.doc.questions[run.step - 1];
      const id = `answer-${run.step}`;
      const value = run.answers[question.id] ?? "";
      const choices = choicesOf(question, labels);
      const control = question.kind === "text"
        ? `<input id="${id}" name="answer_${run.step}" type="text" value="${esc(value)}" data-question="${esc(question.id)}">`
        : `<select id="${id}" name="answer_${run.step}" data-question="${esc(question.id)}"><option value="">${esc(t("preview.choose"))}</option>${
          choices.map(([code, label]) => `<option value="${esc(code)}"${code === value ? " selected" : ""}>${esc(label)}</option>`).join("")}</select>`;
      return `<p class="step">${esc(t("preview.question"))} <output id="preview-step">${run.step}</output> ${esc(t("preview.of", { total }))}</p>
<h2 class="ask">${esc(question.title.trim() ? question.title : t("question.untitled"))}</h2>
<p class="muted">${esc(t(question.required ? "preview.required" : "preview.optional"))}</p>
<div class="field"><label for="${id}">${esc(t("preview.your_answer"))}</label>${control}</div>
${run.problem ? `<p class="refused" role="alert">${esc(run.problem)}</p>` : ""}`;
    }

    /** The walk-through facts for question 2: this run's answer and where its rule skips to. */
    function traceOfSecond() {
      const question = run.doc.questions[1];
      if (!question) return { answer: "", rule: "" };
      const rule = ruleAt(run.doc, 2);
      return { answer: labelOf(question, run.answers[question.id]), rule: rule ? String(rule.target) : "" };
    }

    return { run, start, next, back, answer, finished, cardHtml, traceOfSecond, labelOf };
  }

  window.QuizPreview = { create, positionOf, choicesOf };
})();
