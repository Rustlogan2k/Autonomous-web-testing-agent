"""Quizcraft's HTML. The editor is one page (/quiz) whose panes static/editor.js draws from
the latest saved revision, embedded in the page as JSON; /revisions and /help are plain
server-rendered pages.

Every label is looked up in the vocabulary with `t`; survey content (titles, option labels)
and numbers are data. Regions an assertion reads hold only their value.
"""

from __future__ import annotations

import json
from html import escape

from survey import KINDS, MAX_QUESTIONS, numbers

VOCAB: dict[str, str] = {}
CLUB: dict[str, str] = {}
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

#: Labels the browser needs to draw the editor, preview and history in this vocabulary.
CLIENT_PREFIXES = ("kind.", "answer.", "history.", "saved.", "save.", "preview.", "inspector.", "rule.",
                   "question.", "tool.", "status.")


def t(key: str, **values: object) -> str:
    text = VOCAB.get(key, key)
    for name, value in values.items():
        text = text.replace("{" + name + "}", str(value))
    return text


def h(value: object) -> str:
    return escape("" if value is None else str(value), quote=True)


def _embedded(value: object) -> str:
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def when(iso: str) -> str:
    """'2026-06-10T09:00:00' -> '10 Jun 2026, 09:00' (the clock is fixed; no time zones)."""
    date, _, time = iso.partition("T")
    year, month, day = date.split("-")
    return f"{int(day)} {MONTHS[int(month) - 1]} {year}, {time[:5]}"


def saved_summary(number: int, document: dict) -> str:
    count, required = numbers(document)
    return t("saved.summary", n=number, k=count, r=required)


def frame(title: str, body: str, scripts: tuple[str, ...] = ()) -> bytes:
    tags = "\n".join(f'<script src="/static/{name}" defer></script>' for name in scripts)
    return f"""<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{h(title)} | {h(t("app.name"))}</title>
<link rel="icon" href="data:,">
<link rel="stylesheet" href="/static/quizcraft.css">
</head>
<body>
<header class="masthead">
  <a class="brand" id="nav-editor" href="/quiz">{h(t("app.name"))}</a>
  <span class="club">{h(CLUB.get("club", ""))}</span>
  <nav aria-label="{h(t("nav.label"))}">
    <a id="nav-revisions" href="/revisions">{h(t("nav.revisions"))}</a>
    <a id="nav-help" href="/help">{h(t("nav.help"))}</a>
  </nav>
</header>
<main>
{body}
</main>
{tags}
</body>
</html>""".encode("utf-8")


def quiz_page(number: int, document: dict) -> bytes:
    count, _ = numbers(document)
    labels = {key: value for key, value in VOCAB.items() if key.startswith(CLIENT_PREFIXES)}
    adders = "".join(
        f'<button id="add-type-{kind}" type="button" class="type" data-kind="{kind}" aria-pressed="false">'
        f'{h(t("kind." + kind))}</button>' for kind in KINDS)
    body = f"""<h1 id="survey-title">{h(document["title"])}</h1>
<div class="toolbar">
  <div class="tabs" role="tablist" aria-label="{h(t("tabs.label"))}">
    <button id="tab-editor" type="button" role="tab" aria-selected="true" aria-controls="pane-editor">{h(t("act.tab_editor"))}</button>
    <button id="tab-preview" type="button" role="tab" aria-selected="false" aria-controls="pane-preview">{h(t("act.tab_preview"))}</button>
    <button id="tab-history" type="button" role="tab" aria-selected="false" aria-controls="pane-history">{h(t("act.tab_history"))}</button>
  </div>
  <div class="commands">
    <button id="undo" type="button" disabled>{h(t("act.undo"))}</button>
    <button id="redo" type="button" disabled>{h(t("act.redo"))}</button>
    <button id="save-quiz" type="button">{h(t("act.save"))}</button>
  </div>
</div>
<p class="status"><output id="question-count">{count}</output> {h(t("status.questions"))}
  &middot; <span id="unsaved-flag">{h(t("status.all_saved"))}</span>
  &middot; {h(t("status.last_saved"))} <span id="saved-summary">{h(saved_summary(number, document))}</span></p>
<p class="status">{h(t("status.last_step"))} <span id="history-last-kind">{h(t("history.kind_none"))}</span>
  &middot; {h(t("status.ahead"))} <output id="history-ahead">0</output></p>
<p id="save-note" class="note" role="status"></p>
<section id="pane-editor" class="pane" role="tabpanel">
  <div class="split">
    <div class="list">
      <h2>{h(t("editor.questions"))}</h2>
      <ol id="question-list" class="questions"></ol>
      <div class="add-menu">
        <span>{h(t("editor.add_type"))}</span> {adders}
        <button id="add-question" type="button">{h(t("act.add_question"))}</button>
      </div>
      <p class="muted">{h(t("editor.limit", max=MAX_QUESTIONS))}</p>
    </div>
    <div id="inspector" class="inspector">
      <h2>{h(t("editor.inspector"))}</h2>
      <p id="inspector-empty" class="muted">{h(t("inspector.empty"))}</p>
      <div id="inspector-body" hidden>
        <p class="kind" id="inspector-kind"></p>
        <div class="field"><label for="q-title">{h(t("inspector.title"))}</label>
          <input id="q-title" name="question_title" type="text" autocomplete="off"></div>
        <div class="field check"><input id="q-required" name="question_required" type="checkbox">
          <label for="q-required">{h(t("inspector.required"))}</label></div>
        <div id="logic-panel" class="logic" hidden>
          <label for="rule-target">{h(t("inspector.rule"))}</label>
          <select id="rule-target" name="rule_target"></select>
        </div>
        <p class="actions">
          <button id="move-up" type="button">{h(t("act.move_up"))}</button>
          <button id="move-down" type="button">{h(t("act.move_down"))}</button>
          <button id="delete-question" type="button" class="danger">{h(t("act.delete"))}</button>
        </p>
      </div>
    </div>
  </div>
  <p class="tools">
    <button id="copy-link" type="button" class="quiet">{h(t("act.copy_link"))}</button>
    <button id="spell-check" type="button" class="quiet">{h(t("act.spell_check"))}</button>
    <span id="tool-note" class="note" role="status"></span>
  </p>
</section>
<section id="pane-preview" class="pane" role="tabpanel" hidden>
  <div id="preview-card" class="card"></div>
  <p class="actions">
    <button id="preview-back" type="button">{h(t("act.preview_back"))}</button>
    <button id="preview-next" type="button">{h(t("act.preview_next"))}</button>
    <button id="preview-restart" type="button" class="quiet">{h(t("act.preview_restart"))}</button>
  </p>
  <aside class="trace">{h(t("preview.trace_answer"))} <span id="preview-answer-2"></span>
    &middot; {h(t("preview.trace_rule"))} <span id="preview-rule-2"></span></aside>
</section>
<section id="pane-history" class="pane" role="tabpanel" hidden>
  <h2>{h(t("pane.history"))}</h2>
  <ol id="history-list" class="history"></ol>
</section>
<script type="application/json" id="quiz-data">{_embedded({"revision": number, "document": document, "max": MAX_QUESTIONS})}</script>
<script type="application/json" id="quiz-labels">{_embedded(labels)}</script>"""
    return frame(document["title"], body, ("preview.js", "editor.js"))


def revisions_page(revisions: list[dict]) -> bytes:
    rows = "\n".join(
        f'<tr id="rev-{r["number"]}"><td>{r["number"]}</td><td>{h(when(r["saved_at"]))}</td>'
        f'<td>{numbers(r["document"])[0]}</td><td>{numbers(r["document"])[1]}</td>'
        f'<td>{h(r["document"]["title"])}</td></tr>' for r in revisions)
    body = f"""<h1>{h(t("revisions.title"))}</h1>
<p>{h(t("revisions.intro"))}</p>
<table class="grid">
<thead><tr><th>{h(t("revisions.col_number"))}</th><th>{h(t("revisions.col_saved"))}</th>
<th>{h(t("revisions.col_questions"))}</th><th>{h(t("revisions.col_required"))}</th><th>{h(t("revisions.col_title"))}</th></tr></thead>
<tbody>
{rows}
</tbody>
</table>
<p class="back"><a id="back-to-editor" href="/quiz">{h(t("act.back_to_editor"))}</a></p>"""
    return frame(t("revisions.title"), body)


def help_page() -> bytes:
    body = f"""<h1>{h(t("help.title"))}</h1>
<p>{h(t("help.edit"))}</p>
<p>{h(t("help.undo"))}</p>
<p>{h(t("help.preview"))}</p>
<p>{h(t("help.save"))}</p>
<p class="back"><a id="back-to-editor" href="/quiz">{h(t("act.back_to_editor"))}</a></p>"""
    return frame(t("help.title"), body)


def not_found() -> bytes:
    body = f"""<h1>{h(t("missing.title"))}</h1>
<p>{h(t("missing.body"))}</p>
<p class="back"><a id="back-to-editor" href="/quiz">{h(t("act.back_to_editor"))}</a></p>"""
    return frame(t("missing.title"), body)
