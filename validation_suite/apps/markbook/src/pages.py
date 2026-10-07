"""Markbook's HTML, rendered on the server. Every label comes from the vocabulary profile.

Two route trees, two page frames: teacher pages (`/teacher/...`) carry the teacher's
navigation; the student's own page (`/student/<s>`) has the student frame and, because a
teacher reaches it through "Preview as student", a banner leading back.
"""

from __future__ import annotations

from html import escape

import notes

VOCAB: dict[str, str] = {}


def t(key: str) -> str:
    return VOCAB[key]


def e(value) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def class_title(klass: dict) -> str:
    return f'{klass["name"]} {klass["subject"]}'


def _document(title: str, body: str, body_class: str, script: bool) -> bytes:
    tail = '\n<script src="/static/markbook.js" defer></script>' if script else ""
    return f"""<!doctype html>
<html lang="en-GB"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)} · {e(t("app.name"))}</title>
<link rel="icon" href="data:,">
<link rel="stylesheet" href="/static/markbook.css"></head>
<body class="{body_class}">
{body}{tail}
</body></html>""".encode("utf-8")


def teacher_page(title: str, main: str, klass: dict | None = None, script: bool = False) -> bytes:
    classbar = ""
    if klass is not None:
        base = f'/teacher/classes/{e(klass["id"])}'
        classbar = f"""<nav class="classbar" aria-label="{e(class_title(klass))}">
  <strong>{e(class_title(klass))}</strong>
  <a id="nav-markbook" href="{base}">{e(t("nav.markbook"))}</a>
  <a id="nav-report" href="{base}/report">{e(t("nav.report"))}</a>
</nav>"""
    body = f"""<header class="bar">
  <span class="brand">{e(t("app.name"))}</span><span class="role">{e(t("role.teacher"))}</span>
  <nav aria-label="{e(t("nav.label"))}">
    <a id="nav-classes" href="/teacher">{e(t("nav.classes"))}</a>
    <a id="nav-help" href="/help">{e(t("nav.help"))}</a>
  </nav>
</header>
{classbar}
<main>{main}</main>"""
    return _document(title, body, "teacher", script)


def student_page(title: str, main: str, student: dict, record_href: str) -> bytes:
    banner = t("student.preview").format(name=student["name"])
    body = f"""<header class="bar student">
  <span class="brand">{e(t("app.name"))}</span><span class="role">{e(t("role.student"))}</span>
</header>
<p class="preview">{e(banner)} <a id="end-preview" href="{e(record_href)}">{e(t("act.end_preview"))}</a></p>
<main>{main}</main>"""
    return _document(title, body, "student", False)


def flash_line(flash: dict | None, ident: str) -> str:
    if not flash:
        return ""
    return f'<p class="flash {e(flash["tone"])}" id="{ident}" role="status">{e(flash["message"])}</p>'


def status_label(assessment: dict) -> str:
    return t("status.published") if assessment["published"] else t("status.draft")


def mark_text(score: int | None) -> str:
    return "-" if score is None else str(score)


def comment_item(note: dict) -> str:
    """One comment with its badge; the same markup on every page that lists comments."""
    return (f'<li class="comment"><span class="badge {"shared" if note["canned"] else "private"}">'
            f'{e(t(notes.badge_key(note)))}</span> <q>{e(note["body"])}</q></li>')


# -- teacher: classes hub ------------------------------------------------------------------------


def classes_hub(entries: list[dict]) -> str:
    cards = "".join(f"""<li class="card">
  <a id="class-{e(c["id"])}" href="/teacher/classes/{e(c["id"])}">{e(class_title(c))}</a>
  <p>{e(c["term"])} · {e(c["teacher"])}</p>
  <p>{e(t("classes.students"))}: {c["students"]} · {e(t("classes.assessments"))}: {c["assessments"]}
     ({c["published"]} {e(t("classes.published"))})</p>
</li>""" for c in entries)
    return f"""<h1>{e(t("classes.title"))}</h1>
<p class="lede">{e(t("classes.lede"))}</p>
<ul class="cards">{cards}</ul>"""


# -- teacher: the markbook matrix ---------------------------------------------------------------


def markbook(klass: dict, assessments: list[dict], rows: list[dict], footer: dict, basis: str) -> str:
    base = f'/teacher/classes/{e(klass["id"])}'
    heads = "".join(f"""<th scope="col" class="{"published" if a["published"] else "draft"}">
  <a id="assess-{e(a["id"])}" href="{base}/assessments/{e(a["id"])}">{e(a["title"])}</a>
  <small>{e(t("col.out_of"))} <span id="max-{e(a["id"])}">{a["max_marks"]}</span> · {e(t("col.weight"))} {a["weight"]}%</small>
  <small class="state">{e(status_label(a))}</small>
</th>""" for a in assessments)
    body = []
    for row in rows:
        s = row["student"]
        cells = "".join(
            f'<td class="mark"><button type="button" class="cell" id="mark-{e(s["id"])}-{e(a["id"])}" '
            f'data-student="{e(s["id"])}" data-assessment="{e(a["id"])}" '
            f'data-score="{e("" if row["scores"][a["id"]] is None else row["scores"][a["id"]])}" '
            f'data-max="{a["max_marks"]}" data-name="{e(s["name"])}, {e(a["title"])}">'
            f'{mark_text(row["scores"][a["id"]])}</button></td>' for a in assessments)
        body.append(f"""<tr>
  <th scope="row"><a id="student-{e(s["id"])}" href="{base}/students/{e(s["id"])}">{e(s["name"])}</a></th>
  {cells}
  <td class="pct" id="pct-{e(s["id"])}">{e(row["pct"])}</td>
  <td class="level" id="level-{e(s["id"])}">{e(row["level"])}</td>
</tr>""")
    averages = "".join(f'<td id="avg-{e(a["id"])}">{e(footer["averages"][a["id"]])}</td>' for a in assessments)
    return f"""<h1>{e(t("markbook.title"))}</h1>
<p class="lede">{e(t("markbook.lede"))}</p>
<p class="status" id="cell-status" aria-live="polite"></p>
<p class="error" id="cell-error" role="alert"></p>
<div class="scroll">
<table id="markbook" class="matrix" data-class="{e(klass["id"])}" data-basis="{e(basis)}"
       data-saving="{e(t("msg.saving"))}" data-unreachable="{e(t("msg.unreachable"))}">
<thead><tr><th scope="col">{e(t("col.student"))}</th>{heads}
  <th scope="col">{e(t("col.pct"))}</th><th scope="col">{e(t("col.level"))}</th></tr></thead>
<tbody>{"".join(body)}</tbody>
<tfoot><tr><th scope="row">{e(t("row.average"))}</th>{averages}
  <td id="class-average">{e(footer["class_average"])}</td><td></td></tr></tfoot>
</table>
</div>
<template id="cell-editor"><span class="editor"><label for="mark">{e(t("field.mark"))}</label>
<input id="mark" name="mark" type="number" min="0" step="1" inputmode="numeric">
<button type="button" id="save-mark">{e(t("act.save_mark"))}</button>
<button type="button" id="cancel-mark">{e(t("act.cancel_mark"))}</button></span></template>
<p><button type="button" id="export-csv-help" aria-expanded="false" aria-controls="export-help">{e(t("act.export_help"))}</button></p>
<div id="export-help" class="panel" hidden><p>{e(t("export.body"))}</p></div>"""


# -- teacher: one assessment ---------------------------------------------------------------------


def assessment_page(klass: dict, a: dict, marked: int, class_size: int, highest: int | None,
                    flash: dict | None) -> str:
    base = f'/teacher/classes/{e(klass["id"])}/assessments/{e(a["id"])}'
    withdraw_off = "" if a["published"] else " disabled"
    return f"""<h1>{e(a["title"])} <span class="tag" id="assess-status">{e(status_label(a))}</span></h1>
{flash_line(flash, "assess-flash")}
<dl class="facts">
  <dt>{e(t("assess.weight"))}</dt><dd>{a["weight"]}%</dd>
  <dt>{e(t("assess.marked"))}</dt><dd>{marked} / {class_size}</dd>
  <dt>{e(t("assess.highest"))}</dt><dd id="assess-highest">{e(t("assess.none") if highest is None else highest)}</dd>
</dl>
<form method="post" action="{base}/maximum" novalidate class="row">
  <label for="max-marks">{e(t("field.max_marks"))}</label>
  <input id="max-marks" name="max_marks" type="number" min="1" max="1000" step="1" value="{a["max_marks"]}">
  <button id="save-assessment" type="submit">{e(t("act.save_assessment"))}</button>
</form>
<p class="muted">{e(t("assess.publish_help"))}</p>
<div class="row">
  <form method="post" action="{base}/publish"><button id="publish-assessment" type="submit">{e(t("act.publish"))}</button></form>
  <form method="post" action="{base}/withdraw"><button id="withdraw-assessment" type="submit"{withdraw_off}>{e(t("act.withdraw"))}</button></form>
</div>
<p><a id="back-to-markbook" href="/teacher/classes/{e(klass["id"])}">{e(t("act.back_markbook"))}</a></p>"""


# -- teacher: one student's record ---------------------------------------------------------------


def record_page(klass: dict, student: dict, assessments: list[dict], scores: dict, pct: str, level: str,
                feedback: list[dict], bank: list[dict], note: dict | None, editing_note: bool,
                flash: dict | None) -> str:
    base = f'/teacher/classes/{e(klass["id"])}/students/{e(student["id"])}'
    marks = "".join(f"""<tr><th scope="row">{e(a["title"])}</th>
  <td>{mark_text(scores.get(a["id"]))} / {a["max_marks"]}</td><td>{e(status_label(a))}</td></tr>""" for a in assessments)
    given = "".join(comment_item(n) for n in feedback) or f'<li class="empty">{e(t("record.no_feedback"))}</li>'
    options = f'<option value="">{e(t("feedback.choose"))}</option>' + "".join(
        f'<option value="{e(c["id"])}">{e(c["body"])}</option>' for c in bank)
    if note is not None and not editing_note:
        note_html = f"""<blockquote class="note" id="private-note-text"><span class="badge private">{e(t("badge.private"))}</span>
  <span class="body">{e(note["body"])}</span></blockquote>
<p><a id="edit-note" href="{base}?note=edit">{e(t("act.edit_note"))}</a></p>"""
    else:
        cancel = (f' <a id="cancel-note" href="{base}">{e(t("act.cancel_note"))}</a>' if note is not None else "")
        # The newline after <textarea> is dropped by the HTML parser, so a note that itself
        # starts with a newline comes back into the editor unchanged.
        note_html = f"""<form method="post" action="{base}/note" novalidate>
  <label for="private-note">{e(t("field.private_note"))}</label>
  <textarea id="private-note" name="private_note" rows="4">
{e(note["body"] if note else "")}</textarea>
  <p class="row"><button id="save-note" type="submit">{e(t("act.save_note"))}</button>{cancel}</p>
</form>"""
    return f"""<h1>{e(student["name"])}</h1>
{flash_line(flash, "record-flash")}
<section><h2>{e(t("record.marks"))}</h2>
<table class="record"><thead><tr><th scope="col">{e(t("student.assessment"))}</th>
  <th scope="col">{e(t("field.mark"))}</th><th scope="col">{e(t("record.visible"))}</th></tr></thead>
<tbody>{marks}</tbody></table>
<p>{e(t("col.pct"))}: <strong id="record-pct">{e(pct)}</strong> · {e(t("col.level"))}: <strong id="record-level">{e(level)}</strong></p>
</section>
<section><h2>{e(t("record.feedback"))}</h2>
<ul class="comments" id="teacher-feedback">{given}</ul>
<form method="post" action="{base}/feedback" novalidate class="row">
  <label for="feedback">{e(t("field.feedback"))}</label>
  <select id="feedback" name="feedback">{options}</select>
  <button id="save-feedback" type="submit">{e(t("act.save_feedback"))}</button>
</form>
</section>
<section><h2>{e(t("record.note"))}</h2>
{note_html}
</section>
<p class="row"><a id="preview-as-student" href="/student/{e(student["id"])}">{e(t("act.preview_student"))}</a>
  <a id="back-to-markbook" href="/teacher/classes/{e(klass["id"])}">{e(t("act.back_markbook"))}</a></p>"""


# -- teacher: report -----------------------------------------------------------------------------


def report_page(klass: dict, levels: list[tuple[int, list[str]]], incomplete: list[str], produced: str) -> str:
    rows = "".join(f"""<tr><th scope="row">{e(t("col.level"))} {value}</th><td id="level-count-{value}">{len(names)}</td>
  <td>{e(", ".join(names))}</td></tr>""" for value, names in levels)
    return f"""<h1>{e(t("report.title"))}</h1>
<p class="lede">{e(t("report.lede"))}</p>
<table class="record"><thead><tr><th scope="col">{e(t("col.level"))}</th><th scope="col">{e(t("report.count"))}</th>
  <th scope="col">{e(t("report.names"))}</th></tr></thead>
<tbody>{rows}
<tr><th scope="row">{e(t("report.incomplete"))}</th><td id="level-count-none">{len(incomplete)}</td>
  <td>{e(", ".join(incomplete))}</td></tr></tbody></table>
<p class="muted">{e(class_title(klass))} · {e(t("report.produced"))} {e(produced)}</p>
<p><a id="report-back" href="/teacher/classes/{e(klass["id"])}">{e(t("act.back_markbook"))}</a></p>"""


# -- the student's own page ----------------------------------------------------------------------


def student_view(klass: dict, student: dict, published: list[dict], provisional: str,
                 comments: list[dict]) -> str:
    if published:
        rows = "".join(f"""<tr><th scope="row">{e(p["title"])}</th><td>{mark_text(p["score"])} / {p["max_marks"]}</td>
  <td>{mark_text(p["top"])}</td></tr>""" for p in published)
        foot = f"""<tfoot><tr><th scope="row">{e(t("student.provisional"))}</th>
  <td colspan="2"><strong>{e(provisional)}</strong></td></tr></tfoot>"""
    else:
        rows, foot = f'<tr><td colspan="3">{e(t("student.no_marks"))}</td></tr>', ""
    listed = "".join(comment_item(n) for n in comments) or f'<li class="empty">{e(t("student.no_comments"))}</li>'
    return f"""<h1>{e(student["name"])} <small>{e(class_title(klass))}</small></h1>
<h2>{e(t("student.title"))}</h2>
<table class="record" id="student-view"><thead><tr><th scope="col">{e(t("student.assessment"))}</th>
  <th scope="col">{e(t("student.mark"))}</th><th scope="col">{e(t("student.top"))}</th></tr></thead>
<tbody>{rows}</tbody>{foot}</table>
<p class="muted">{e(t("student.provisional_help"))}</p>
<h2>{e(t("student.comments"))}</h2>
<ul class="comments" id="student-comments">{listed}</ul>"""


# -- help and not found --------------------------------------------------------------------------


def help_page() -> str:
    sections = (("help.marks_title", "help.marks"), ("help.rounding_title", "help.rounding"),
                ("help.levels_title", "help.levels"), ("help.publish_title", "help.publish"),
                ("help.notes_title", "help.notes"), ("help.export_title", "export.body"))
    body = "".join(f"<h2>{e(t(title))}</h2><p>{e(t(text))}</p>" for title, text in sections)
    return f'<h1>{e(t("help.title"))}</h1>{body}'


def not_found() -> str:
    return f"""<h1>{e(t("missing.title"))}</h1><p>{e(t("missing.body"))}</p>
<p><a id="missing-home" href="/teacher">{e(t("nav.classes"))}</a></p>"""
