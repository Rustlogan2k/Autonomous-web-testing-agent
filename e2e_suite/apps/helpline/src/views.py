"""Helpline's HTML: whole pages, and the fragments that replace parts of them.

Every swappable part of a page is a function returning one element with a stable `id`.
A full page is assembled from those same functions, and an action's response is simply the
list of parts it changed: the browser script replaces each element by id. So a fragment
and a full render of the same state are, by construction, the same markup.

Every label comes from the vocabulary (looked up with `t`); data (names, subjects, numbers,
timestamps) never does.
"""

from __future__ import annotations

from html import escape
from urllib.parse import urlencode

import desk

VOCAB: dict[str, str] = {}

TABS = ("conversation", "details", "history")


def t(key: str) -> str:
    return VOCAB.get(key, key)


def h(value) -> str:
    return escape("" if value is None else str(value), quote=True)


def person(code: str) -> str:
    return desk.PEOPLE.get(code, code) if code else t("assignee.none")


def status_label(code: str) -> str:
    return t(f"status.{code}")


def priority_label(code: str) -> str:
    return t(f"priority.{code}")


def queue_url(status: str, assignee: str) -> str:
    return "/queue?" + urlencode({"status": status, "assignee": assignee})


def ticket_url(ticket_id: int, tab: str = "conversation") -> str:
    return f"/tickets/{ticket_id}?tab={tab}"


# -- the frame -------------------------------------------------------------------------------


def document(title: str, main: str, counts: dict[str, int], *, after: str = "") -> bytes:
    return f"""<!doctype html>
<html lang="en-GB"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:,">
<title>{h(title)} · {h(t("app.name"))}</title>
<link rel="stylesheet" href="/static/desk.css"></head>
<body>
<div id="page" class="frame">
{sidebar(counts)}
<main class="main">{main}</main>
</div>
{after}
<script src="/static/desk.js" defer></script>
</body></html>""".encode("utf-8")


def sidebar(counts: dict[str, int]) -> str:
    return f"""<aside class="sidebar">
  <p class="brand">{h(t("app.name"))} <span class="muted">{h(t("app.tagline"))}</span></p>
  <nav aria-label="{h(t("nav.label"))}">
    <a id="nav-queue" href="/queue">{h(t("nav.queue"))}</a>
    <a id="nav-unassigned" href="{h(queue_url("open", "none"))}">{h(t("nav.unassigned"))}</a>
  </nav>
  <p class="muted">{h(t("nav.signed_in"))}</p>
  {desk_counts(counts)}
  <section id="oncall-notice" class="notice" aria-labelledby="oncall-title">
    <h2 id="oncall-title">{h(t("notice.title"))}</h2>
    <p>{h(t("notice.body"))}</p>
    <button id="notice-done" type="button" data-hide="oncall-notice">{h(t("act.mark_done"))}</button>
  </section>
</aside>"""


def desk_counts(counts: dict[str, int]) -> str:
    rows = "".join(
        f'<dt>{h(status_label(code))}</dt><dd><output id="count-{code.replace("_", "-")}">{counts[code]}</output></dd>'
        for code in desk.STATUSES)
    return f'<dl id="desk-counts" class="counts" aria-label="{h(t("counts.title"))}">{rows}</dl>'


# -- the queue -------------------------------------------------------------------------------


def queue_page(rows, status: str, assignee: str, counts: dict[str, int]) -> bytes:
    status_names = {"open": t("filter.open"), "all": t("filter.all"),
                    **{code: status_label(code) for code in desk.STATUSES}}
    assignee_names = {"any": t("filter.any"), "none": t("filter.none"), "noor": "Noor", "kai": "Kai"}

    def options(names: dict[str, str], current: str) -> str:
        return "".join(f'<option value="{h(v)}"{" selected" if v == current else ""}>{h(n)}</option>'
                       for v, n in names.items())

    main = f"""<h1>{h(t("queue.title"))}</h1>
<form id="queue-filters" class="filters" method="get" action="/queue" data-fragment="/queue/rows">
  <label for="filter-status">{h(t("field.status"))}</label>
  <select id="filter-status" name="status">{options(status_names, status)}</select>
  <label for="filter-assignee">{h(t("field.assignee"))}</label>
  <select id="filter-assignee" name="assignee">{options(assignee_names, assignee)}</select>
  <button id="apply-filters" type="submit">{h(t("act.apply"))}</button>
  <button id="refresh-queue" type="button" data-fragment="/queue/rows" data-keep-query>{h(t("act.refresh"))}</button>
</form>
{queue_flash("")}
{queue_count(len(rows))}
<table class="queue">
  <thead><tr><th>{h(t("col.ticket"))}</th><th>{h(t("col.subject"))}</th><th>{h(t("col.customer"))}</th>
    <th>{h(t("col.priority"))}</th><th>{h(t("col.status"))}</th><th>{h(t("col.assignee"))}</th>
    <th>{h(t("col.action"))}</th></tr></thead>
  {queue_rows(rows, status, assignee)}
</table>"""
    return document(t("queue.title"), main, counts)


def queue_flash(message: str) -> str:
    return f'<output id="queue-flash" class="flash" role="status">{h(message)}</output>'


def queue_count(n: int) -> str:
    return f'<p id="queue-count" class="muted">{h(t("queue.count").format(n=n))}</p>'


def queue_rows(rows, status: str, assignee: str) -> str:
    if not rows:
        return f'<tbody id="queue-rows"><tr><td colspan="7">{h(t("queue.empty"))}</td></tr></tbody>'
    filters = urlencode({"status": status, "assignee": assignee})
    body = []
    for row in rows:
        blocked = row["assignee"] == desk.ONCALL or row["status"] == "closed"
        body.append(f"""<tr>
  <td>#{row["id"]}</td>
  <td><a id="open-{row["id"]}" href="/tickets/{row["id"]}">{h(row["subject"])}</a></td>
  <td>{h(row["customer"])}</td><td>{h(priority_label(row["priority"]))}</td>
  <td>{h(status_label(row["status"]))}</td><td>{h(person(row["assignee"]))}</td>
  <td><form method="post" action="/queue/handoff/{row["id"]}?{h(filters)}" data-fragment>
    <button id="handoff-{row["id"]}" type="submit"{" disabled" if blocked else ""}>{h(t("act.handoff"))}</button></form></td>
</tr>""")
    return f'<tbody id="queue-rows">{"".join(body)}</tbody>'


# -- a ticket --------------------------------------------------------------------------------


def ticket_page(conn, row, tab: str, counts: dict[str, int]) -> bytes:
    main = f"""<p class="crumbs"><a id="back-to-queue" href="/queue">{h(t("nav.back"))}</a></p>
{ticket_summary(conn, row)}
{ticket_flash("")}
{ticket_actions(row)}
{ticket_tabs(row["id"], tab)}
{tab_panel(conn, row, tab)}"""
    return document(f'#{row["id"]} {row["subject"]}', main, counts, after=merge_dialog(conn, row))


def ticket_summary(conn, row) -> str:
    links = desk.linked(conn, row["id"])
    items = "".join(f'<li><a id="linked-{other["id"]}" href="/tickets/{other["id"]}">#{other["id"]}</a> '
                    f'{h(other["subject"])}</li>' for other in links) or f'<li class="muted">{h(t("linked.none"))}</li>'
    merged = ""
    if row["merged_into"]:
        merged = (f'<dt>{h(t("summary.merged_into"))}</dt><dd><a id="merged-into" '
                  f'href="/tickets/{row["merged_into"]}">#{row["merged_into"]}</a></dd>')
    role = desk.latest_role(conn, row["id"])
    return f"""<section id="ticket-summary" class="summary">
  <h1><span class="number">#{row["id"]}</span> {h(row["subject"])}</h1>
  <p class="muted">{h(row["customer"])}</p>
  <dl class="facts">
    <dt>{h(t("summary.status"))}</dt><dd><output id="ticket-status" class="status s-{h(row["status"])}">{h(status_label(row["status"]))}</output></dd>
    <dt>{h(t("summary.assignee"))}</dt><dd><output id="ticket-assignee">{h(person(row["assignee"]))}</output></dd>
    <dt>{h(t("summary.priority"))}</dt><dd><output id="ticket-priority">{h(priority_label(row["priority"]))}</output></dd>
    <dt>{h(t("summary.latest"))}</dt><dd><output id="latest-role">{h(t(f"role.{role}") if role else "")}</output></dd>
    {merged}
    <dt>{h(t("summary.linked"))}</dt><dd><ul id="linked-tickets" class="linked">{items}</ul></dd>
  </dl>
</section>"""


def ticket_flash(message: str) -> str:
    return f'<output id="ticket-flash" class="flash" role="status">{h(message)}</output>'


def _action(ticket_id: int, path: str, ident: str, label: str, extra: str = "") -> str:
    return (f'<form method="post" action="/tickets/{ticket_id}/{path}" data-fragment>{extra}'
            f'<button id="{ident}" type="submit">{h(label)}</button></form>')


def ticket_actions(row) -> str:
    tid, status = row["id"], row["status"]
    if status == "closed":
        return f'<div id="ticket-actions" class="actions"><p class="muted">{h(t("actions.closed"))}</p></div>'
    parts = []
    if status == "new":
        parts.append(_action(tid, "move", "start-triage", t("act.start_triage"),
                             '<input type="hidden" name="to" value="triage">'))
    if status in ("triage", "waiting"):
        parts.append(_action(tid, "move", "start-work", t("act.start_work"),
                             '<input type="hidden" name="to" value="in_progress">'))
    if status in desk.OPEN_STATUSES:
        parts.append(_action(tid, "resolve", "resolve", t("act.resolve")))
    parts.append(_action(tid, "close", "close-ticket", t("act.close")))
    if row["assignee"] != desk.ONCALL:
        parts.append(_action(tid, "handoff", "handoff", f'{t("act.handoff_to")} Kai'))
    if desk.can_merge_into(row):
        parts.append(f'<button id="open-merge" type="button" data-opens="merge-dialog">{h(t("act.merge_open"))}</button>')
    return f'<div id="ticket-actions" class="actions">{"".join(parts)}</div>'


def ticket_tabs(ticket_id: int, current: str) -> str:
    names = {"conversation": t("tab.conversation"), "details": t("tab.details"), "history": t("tab.history")}
    links = "".join(
        f'<a id="tab-{tab}" role="tab" href="{ticket_url(ticket_id, tab)}" '
        f'data-fragment="/tickets/{ticket_id}/tab/{tab}" aria-selected="{"true" if tab == current else "false"}">'
        f'{h(names[tab])}</a>' for tab in TABS)
    return f'<nav id="ticket-tabs" class="tabs" role="tablist">{links}</nav>'


def tab_panel(conn, row, tab: str) -> str:
    if tab == "details":
        inner = _details(row)
    elif tab == "history":
        inner = _history(conn, row)
    else:
        inner = _conversation(conn, row)
    return f'<section id="tab-panel" class="panel" role="tabpanel" data-tab="{tab}">{inner}</section>'


def _conversation(conn, row) -> str:
    items = []
    for entry in desk.entries(conn, row["id"]):
        author = entry["author"] if entry["role"] == "customer" else person(entry["author"])
        items.append(f'<li class="entry {h(entry["role"])}"><span class="badge">{h(t("role." + entry["role"]))}</span> '
                     f'<strong>{h(author)}</strong> <time>{h(entry["at"])}</time>'
                     f'<p class="body">{h(entry["body"])}</p></li>')
    thread = f'<ol id="conversation" class="thread">{"".join(items)}</ol>'
    if row["status"] == "closed":
        return thread + f'<p class="muted">{h(t("conversation.closed"))}</p>'
    tid = row["id"]
    return thread + f"""
<form id="reply-form" class="compose" method="post" action="/tickets/{tid}/reply" data-fragment>
  <label for="reply-body">{h(t("field.reply"))}</label>
  <textarea id="reply-body" name="reply_body" rows="4"></textarea>
  <button id="send-reply" type="submit">{h(t("act.send_reply"))}</button>
</form>
<form id="customer-form" class="compose secondary" method="post" action="/tickets/{tid}/customer-reply" data-fragment>
  <label for="customer-body">{h(t("field.customer_reply"))}</label>
  <textarea id="customer-body" name="customer_body" rows="3"></textarea>
  <button id="log-customer-reply" type="submit">{h(t("act.log_customer"))}</button>
</form>"""


def _details(row) -> str:
    facts = ((t("details.customer"), row["customer"]), (t("details.email"), row["email"]),
             (t("details.phone"), row["phone"]), (t("details.plan"), row["plan"]),
             (t("details.channel"), t(f"channel.{row['channel']}")), (t("details.opened"), row["opened"]))
    return '<dl id="customer-details" class="facts">' + "".join(
        f"<dt>{h(k)}</dt><dd>{h(v)}</dd>" for k, v in facts) + "</dl>"


def _history_line(event) -> str:
    who = event["actor"] if event["event"] == "created" else person(event["actor"])
    detail = event["detail"]
    kind = event["event"]
    if kind == "created":
        return t("hist.created").format(who=who, channel=t(f"channel.{detail}"))
    if kind == "status":
        before, _, after = detail.partition(">")
        return t("hist.status").format(who=who, before=status_label(before), after=status_label(after))
    if kind == "assigned":
        return t("hist.assigned").format(who=who, to=person(detail))
    if kind == "reply":
        return t("hist.reply").format(who=who)
    if kind == "customer_reply":
        return t("hist.customer_reply").format(who=who)
    if kind == "merged_into":
        return t("hist.merged_into").format(who=who, other=detail)
    if kind == "merged_from":
        return t("hist.merged_from").format(who=who, other=detail)
    return kind


def _history(conn, row) -> str:
    items = "".join(f'<li><time>{h(event["at"])}</time> {h(_history_line(event))}</li>'
                    for event in desk.history(conn, row["id"]))
    return f'<ol id="history-list" class="history">{items}</ol>'


def merge_dialog(conn, row, *, error: str = "", open_: bool = False) -> str:
    """The merge modal. Rendered closed; the script opens it (and resets its form)."""
    hidden = "" if open_ else " hidden"
    if not desk.can_merge_into(row):
        return f'<div id="merge-dialog" class="dialog" role="dialog" aria-modal="true" hidden></div>'
    candidates = desk.merge_candidates(conn, row["id"])
    if candidates:
        options = "".join(f'<option value="{c["id"]}">#{c["id"]} {h(c["subject"])} ({h(c["customer"])})</option>'
                          for c in candidates)
        chooser = f"""<label for="merge-source">{h(t("field.merge_source"))}</label>
    <select id="merge-source" name="merge_source" required autocomplete="off">
      <option value="" selected>{h(t("merge.choose"))}</option>{options}</select>
    <p id="merge-error" class="error" role="alert">{h(error)}</p>
    <button id="merge-confirm" type="submit">{h(t("act.merge_confirm"))}</button>"""
    else:
        chooser = f'<p class="muted">{h(t("merge.none"))}</p>'
    return f"""<div id="merge-dialog" class="dialog" role="dialog" aria-modal="true" aria-labelledby="merge-title"{hidden}>
  <form class="dialog-box" method="post" action="/tickets/{row["id"]}/merge" data-fragment autocomplete="off">
    <h2 id="merge-title">{h(t("merge.title"))}</h2>
    <p class="muted">{h(t("merge.help"))}</p>
    {chooser}
    <button id="merge-cancel" type="button" data-dismiss="merge-dialog">{h(t("act.cancel"))}</button>
  </form>
</div>"""


def not_found(counts: dict[str, int]) -> bytes:
    main = f"""<h1>{h(t("missing.title"))}</h1><p>{h(t("missing.body"))}</p>
<p><a id="missing-back" href="/queue">{h(t("nav.back"))}</a></p>"""
    return document(t("missing.title"), main, counts)
