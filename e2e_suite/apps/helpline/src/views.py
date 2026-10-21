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
AGENT_NAMES: dict[str, str] = {}  # filled from the seed at start: agents never change

TABS = ("conversation", "details", "history")


def t(key: str) -> str:
    return VOCAB.get(key, key)


def h(value) -> str:
    return escape("" if value is None else str(value), quote=True)


def person(code: str | None) -> str:
    return AGENT_NAMES.get(code, code) if code else t("assignee.none")


def status_label(code: str) -> str:
    return t(f"status.{code}")


def priority_label(code: str) -> str:
    return t(f"priority.{code}")


def queue_url(status: str, assignee: str) -> str:
    return "/queue?" + urlencode({"status": status, "assignee": assignee})


def ticket_url(ticket_id: int, tab: str = "conversation") -> str:
    return f"/tickets/{ticket_id}?tab={tab}"


def _options(pairs, current: str) -> str:
    return "".join(f'<option value="{h(v)}"{" selected" if v == current else ""}>{h(n)}</option>' for v, n in pairs)


# -- the frame -------------------------------------------------------------------------------


def document(title: str, main: str, counts: dict[str, int], me: dict, *, after: str = "") -> bytes:
    return f"""<!doctype html>
<html lang="en-GB"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:,">
<title>{h(title)} · {h(t("app.name"))}</title>
<link rel="stylesheet" href="/static/desk.css"></head>
<body>
<div id="page" class="frame">
{sidebar(counts, me)}
<main class="main">{main}</main>
</div>
{after}
<script src="/static/desk.js" defer></script>
</body></html>""".encode("utf-8")


def public_page(title: str, main: str) -> bytes:
    return f"""<!doctype html>
<html lang="en-GB"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:,">
<title>{h(title)} · {h(t("app.name"))}</title>
<link rel="stylesheet" href="/static/desk.css"></head>
<body><main class="main narrow"><p class="brand">{h(t("app.name"))}</p>{main}</main></body></html>""".encode("utf-8")


def sidebar(counts: dict[str, int], me: dict) -> str:
    lead = "lead" in me["roles"]
    reports = f'<a id="nav-reports" href="/reports">{h(t("nav.reports"))}</a>' if lead else ""
    return f"""<aside class="sidebar">
  <p class="brand">{h(t("app.name"))} <span class="muted">{h(t("app.tagline"))}</span></p>
  <div class="account">
    <span id="signed-in-as">{h(me["name"])} · {h(t("role.lead") if lead else t("role.agent"))}</span>
    <button id="nav-account" type="button" data-toggle="account-menu" aria-expanded="false">{h(t("act.account"))}</button>
    <div id="account-menu" class="account-menu" hidden>
      <form method="post" action="/logout"><button id="sign-out" type="submit">{h(t("act.sign_out"))}</button></form>
    </div>
  </div>
  <nav aria-label="{h(t("nav.label"))}">
    <a id="nav-queue" href="/queue">{h(t("nav.queue"))}</a>
    <a id="nav-unassigned" href="{h(queue_url("open", "none"))}">{h(t("nav.unassigned"))}</a>
    <a id="nav-new-ticket" href="/tickets/new">{h(t("nav.new_ticket"))}</a>
    <a id="nav-customers" href="/customers">{h(t("nav.customers"))}</a>
    <a id="nav-replies" href="/replies">{h(t("nav.replies"))}</a>
    <a id="nav-team" href="/team">{h(t("nav.team"))}</a>
    {reports}
  </nav>
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


def sign_in(wanted: str, username: str = "", refused: bool = False) -> bytes:
    error = f'<p id="login-error" class="error" role="alert">{h(t("login.refused"))}</p>' if refused else ""
    main = f"""<h1>{h(t("login.title"))}</h1>
<form class="compose" method="post" action="/login">
  <input type="hidden" name="next" value="{h(wanted)}">
  <label for="login-username">{h(t("login.username"))}</label>
  <input id="login-username" name="username" type="text" value="{h(username)}" autocomplete="username" required>
  <label for="login-password">{h(t("login.password"))}</label>
  <input id="login-password" name="password" type="password" autocomplete="current-password" required>
  {error}
  <button id="login-submit" type="submit">{h(t("act.sign_in"))}</button>
</form>
<p><a id="forgot-password" href="/help/sign-in">{h(t("act.forgot"))}</a></p>"""
    return public_page(t("login.title"), main)


def sign_in_help() -> bytes:
    main = f"""<h1>{h(t("help.title"))}</h1><p>{h(t("help.body"))}</p>
<p><a id="back-to-sign-in" href="/login">{h(t("act.back_sign_in"))}</a></p>"""
    return public_page(t("help.title"), main)


def access_notice(counts, me) -> bytes:
    main = f"""<h1>{h(t("access.title"))}</h1>
<p id="access-notice" class="notice" role="status">{h(t("access.notice"))}</p>
<p><a id="access-back" href="/queue">{h(t("nav.back"))}</a></p>"""
    return document(t("access.title"), main, counts, me)


# -- the queue -------------------------------------------------------------------------------


def queue_page(db, rows, status: str, assignee: str, counts, me, flash: str = "") -> bytes:
    status_names = [("open", t("filter.open")), ("all", t("filter.all"))] + \
        [(code, status_label(code)) for code in desk.STATUSES]
    assignee_names = [("any", t("filter.any")), ("none", t("filter.none")), ("noor", person("noor")),
                      ("kai", person("kai"))]
    main = f"""<h1>{h(t("queue.title"))}</h1>
<form id="queue-filters" class="filters" method="get" action="/queue" data-fragment="/queue/rows">
  <label for="filter-status">{h(t("field.status"))}</label>
  <select id="filter-status" name="status">{_options(status_names, status)}</select>
  <label for="filter-assignee">{h(t("field.assignee"))}</label>
  <select id="filter-assignee" name="assignee">{_options(assignee_names, assignee)}</select>
  <button id="apply-filters" type="submit">{h(t("act.apply"))}</button>
  <button id="refresh-queue" type="button" data-fragment="/queue/rows" data-keep-query>{h(t("act.refresh"))}</button>
</form>
{queue_flash(flash)}
{queue_count(len(rows))}
<table class="queue">
  <thead><tr><th>{h(t("col.ticket"))}</th><th>{h(t("col.subject"))}</th><th>{h(t("col.customer"))}</th>
    <th>{h(t("col.priority"))}</th><th>{h(t("col.status"))}</th><th>{h(t("col.assignee"))}</th>
    <th>{h(t("col.action"))}</th></tr></thead>
  {queue_rows(db, rows, status, assignee)}
</table>"""
    return document(t("queue.title"), main, counts, me)


def queue_flash(message: str) -> str:
    return f'<output id="queue-flash" class="flash" role="status">{h(message)}</output>'


def queue_count(n: int) -> str:
    return f'<p id="queue-count" class="muted">{h(t("queue.count").format(n=n))}</p>'


def queue_rows(db, rows, status: str, assignee: str) -> str:
    if not rows:
        return f'<tbody id="queue-rows"><tr><td colspan="7">{h(t("queue.empty"))}</td></tr></tbody>'
    names = {c["_id"]: c["name"] for c in db.customers.find({"_id": {"$in": [r["customer_id"] for r in rows]}},
                                                          {"name": 1})}
    oncall = desk.on_call(db)["_id"]
    filters = urlencode({"status": status, "assignee": assignee})
    body = []
    for row in rows:
        tid = row["_id"]
        blocked = row["assignee_id"] == oncall or row["status"] == "closed"
        body.append(f"""<tr>
  <td>#{tid}</td>
  <td><a id="open-{tid}" href="/tickets/{tid}">{h(row["subject"])}</a></td>
  <td>{h(names.get(row["customer_id"], ""))}</td><td>{h(priority_label(row["priority"]))}</td>
  <td>{h(status_label(row["status"]))}</td><td>{h(person(row["assignee_id"]))}</td>
  <td><form method="post" action="/queue/handoff/{tid}?{h(filters)}" data-fragment>
    <button id="handoff-{tid}" type="submit"{" disabled" if blocked else ""}>{h(t("act.handoff"))}</button></form></td>
</tr>""")
    return f'<tbody id="queue-rows">{"".join(body)}</tbody>'


# -- a ticket --------------------------------------------------------------------------------


def ticket_page(db, row, tab: str, counts, me, now) -> bytes:
    main = f"""<p class="crumbs"><a id="back-to-queue" href="/queue">{h(t("nav.back"))}</a></p>
{ticket_summary(db, row)}
{ticket_flash("")}
{ticket_actions(db, row)}
{ticket_tabs(row["_id"], tab)}
{tab_panel(db, row, tab, now)}"""
    return document(f'#{row["_id"]} {row["subject"]}', main, counts, me,
                    after=merge_dialog(db, row) + delete_dialog(row))


def ticket_summary(db, row) -> str:
    links = desk.linked(db, row)
    items = "".join(f'<li><a id="linked-{other["_id"]}" href="/tickets/{other["_id"]}">#{other["_id"]}</a> '
                    f'{h(other["subject"])}</li>' for other in links) or f'<li class="muted">{h(t("linked.none"))}</li>'
    merged = ""
    if row["merged_into"]:
        merged = (f'<dt>{h(t("summary.merged_into"))}</dt><dd><a id="merged-into" '
                  f'href="/tickets/{row["merged_into"]}">#{row["merged_into"]}</a></dd>')
    role = desk.latest_role(row)
    who = desk.customer(db, row["customer_id"])
    tags = " ".join(f'<span class="tag">{h(tag)}</span>' for tag in row["tags"])
    return f"""<section id="ticket-summary" class="summary">
  <h1><span class="number">#{row["_id"]}</span> {h(row["subject"])}</h1>
  <p class="muted"><a id="ticket-customer" href="/customers/{h(row["customer_id"])}">{h(who["name"] if who else "")}</a> {tags}</p>
  <dl class="facts">
    <dt>{h(t("summary.status"))}</dt><dd><output id="ticket-status" class="status s-{h(row["status"])}">{h(status_label(row["status"]))}</output></dd>
    <dt>{h(t("summary.assignee"))}</dt><dd><output id="ticket-assignee">{h(person(row["assignee_id"]))}</output></dd>
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


def ticket_actions(db, row) -> str:
    tid, status = row["_id"], row["status"]
    delete = f'<button id="delete-ticket" class="quiet" type="button" data-opens="delete-ticket-dialog">{h(t("act.delete_ticket"))}</button>'
    if status == "closed":
        return f'<div id="ticket-actions" class="actions"><p class="muted">{h(t("actions.closed"))}</p>{delete}</div>'
    oncall = desk.on_call(db)
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
    if row["assignee_id"] != oncall["_id"]:
        parts.append(_action(tid, "handoff", "handoff", f'{t("act.handoff_to")} {oncall["name"].split()[0]}'))
    if desk.can_merge_into(row):
        parts.append(f'<button id="open-merge" type="button" data-opens="merge-dialog">{h(t("act.merge_open"))}</button>')
    parts.append(delete)
    return f'<div id="ticket-actions" class="actions">{"".join(parts)}</div>'


def ticket_tabs(ticket_id: int, current: str) -> str:
    names = {"conversation": t("tab.conversation"), "details": t("tab.details"), "history": t("tab.history")}
    links = "".join(
        f'<a id="tab-{tab}" role="tab" href="{ticket_url(ticket_id, tab)}" '
        f'data-fragment="/tickets/{ticket_id}/tab/{tab}" aria-selected="{"true" if tab == current else "false"}">'
        f'{h(names[tab])}</a>' for tab in TABS)
    return f'<nav id="ticket-tabs" class="tabs" role="tablist">{links}</nav>'


def tab_panel(db, row, tab: str, now) -> str:
    if tab == "details":
        inner = _details(db, row, now)
    elif tab == "history":
        inner = _history(row)
    else:
        inner = _conversation(db, row)
    return f'<section id="tab-panel" class="panel" role="tabpanel" data-tab="{tab}">{inner}</section>'


def _conversation(db, row) -> str:
    who = desk.customer(db, row["customer_id"])
    items = []
    for entry in desk.conversation(row):
        if entry["role"] == "system":
            items.append(f'<li class="entry system"><span class="badge">{h(t("role.system"))}</span> '
                         f'<time>{h(entry["at"])}</time> <span>{h(t("event." + entry["event"]))}</span></li>')
            continue
        author = (who["name"] if who else "") if entry["role"] == "customer" else person(entry["author_id"])
        items.append(f'<li class="entry {h(entry["role"])}"><span class="badge">{h(t("role." + entry["role"]))}</span> '
                     f'<strong>{h(author)}</strong> <time>{h(entry["at"])}</time>'
                     f'<p class="body">{h(entry["body"])}</p></li>')
    thread = f'<ol id="conversation" class="thread">{"".join(items)}</ol>'
    if row["status"] == "closed":
        return thread + f'<p class="muted">{h(t("conversation.closed"))}</p>'
    tid = row["_id"]
    saved = "".join(f'<option value="{h(r["_id"])}" data-body="{h(r["body"])}">{h(r["title"])}</option>'
                    for r in desk.replies(db))
    return thread + f"""
<form id="reply-form" class="compose" method="post" action="/tickets/{tid}/reply" data-fragment>
  <label for="saved-reply">{h(t("field.saved_reply"))}</label>
  <span class="inline-pick"><select id="saved-reply" name="saved_reply"><option value="">{h(t("saved.choose"))}</option>{saved}</select>
  <button id="use-saved-reply" type="button" data-insert="saved-reply" data-into="reply-body">{h(t("act.use_saved"))}</button></span>
  <label for="reply-body">{h(t("field.reply"))}</label>
  <textarea id="reply-body" name="reply_body" rows="4"></textarea>
  <button id="send-reply" type="submit">{h(t("act.send_reply"))}</button>
</form>
<form id="customer-form" class="compose secondary" method="post" action="/tickets/{tid}/customer-reply" data-fragment>
  <label for="customer-body">{h(t("field.customer_reply"))}</label>
  <textarea id="customer-body" name="customer_body" rows="3"></textarea>
  <button id="log-customer-reply" type="submit">{h(t("act.log_customer"))}</button>
</form>"""


def _details(db, row, now) -> str:
    who = desk.customer(db, row["customer_id"]) or {}
    plan = who.get("plan", {})
    due = desk.sla(db, row, now)
    late = f' <strong class="late">{h(t("sla.late"))}</strong>'
    facts = ((t("details.customer"), who.get("name")), (t("details.email"), who.get("email")),
             (t("details.phone"), who.get("phone")),
             (t("details.plan"), f'{plan.get("name", "")} · {plan.get("speed_mbps", "")} Mbps · {plan.get("router", {}).get("model", "")}'),
             (t("details.channel"), t(f"channel.{row['channel']}")), (t("details.opened"), row["opened"]))
    rows = "".join(f"<dt>{h(k)}</dt><dd>{h(v)}</dd>" for k, v in facts)
    rows += (f'<dt>{h(t("sla.respond_by"))}</dt><dd>{h(due["respond_by"])}{late if due["response_late"] else ""}</dd>'
             f'<dt>{h(t("sla.resolve_by"))}</dt><dd>{h(due["resolve_by"])}{late if due["resolve_late"] else ""}</dd>')
    return f'<dl id="customer-details" class="facts">{rows}</dl>'


def _history_line(event) -> str:
    who = event["actor"] if event["event"] == "created" else person(event["actor"])
    detail, kind = event["detail"], event["event"]
    if kind == "created":
        return t("hist.created").format(who=who, channel=t(f"channel.{detail}"))
    if kind == "status":
        before, _, after = detail.partition(">")
        return t("hist.status").format(who=who, before=status_label(before), after=status_label(after))
    if kind == "assigned":
        return t("hist.assigned").format(who=who, to=person(detail))
    if kind in ("reply", "customer_reply"):
        return t(f"hist.{kind}").format(who=who)
    if kind in ("merged_into", "merged_from"):
        return t(f"hist.{kind}").format(who=who, other=detail)
    return kind


def _history(row) -> str:
    items = "".join(f'<li><time>{h(event["at"])}</time> {h(_history_line(event))}</li>' for event in row["history"])
    return f'<ol id="history-list" class="history">{items}</ol>'


def merge_dialog(db, row, *, error: str = "", open_: bool = False) -> str:
    """The merge modal. Rendered closed; the script opens it (and resets its form)."""
    hidden = "" if open_ else " hidden"
    if not desk.can_merge_into(row):
        return '<div id="merge-dialog" class="dialog" role="dialog" aria-modal="true" hidden></div>'
    candidates = desk.merge_candidates(db, row)
    names = {c["_id"]: c["name"] for c in db.customers.find({}, {"name": 1})}
    if candidates:
        options = "".join(f'<option value="{c["_id"]}">#{c["_id"]} {h(c["subject"])} ({h(names.get(c["customer_id"]))})</option>'
                          for c in candidates)
        chooser = f"""<label for="merge-source">{h(t("field.merge_source"))}</label>
    <select id="merge-source" name="merge_source" required autocomplete="off">
      <option value="" selected>{h(t("merge.choose"))}</option>{options}</select>
    <p id="merge-error" class="error" role="alert">{h(error)}</p>
    <button id="merge-confirm" type="submit">{h(t("act.merge_confirm"))}</button>"""
    else:
        chooser = f'<p class="muted">{h(t("merge.none"))}</p>'
    return f"""<div id="merge-dialog" class="dialog" role="dialog" aria-modal="true" aria-labelledby="merge-title"{hidden}>
  <form class="dialog-box" method="post" action="/tickets/{row["_id"]}/merge" data-fragment autocomplete="off">
    <h2 id="merge-title">{h(t("merge.title"))}</h2>
    <p class="muted">{h(t("merge.help"))}</p>
    {chooser}
    <button id="merge-cancel" type="button" data-dismiss="merge-dialog">{h(t("act.cancel"))}</button>
  </form>
</div>"""


def _confirm_dialog(ident: str, action: str, question: str, confirm_id: str, keep_id: str) -> str:
    return f"""<div id="{ident}" class="dialog" role="dialog" aria-modal="true" hidden>
  <form class="dialog-box" method="post" action="{h(action)}">
    <p>{h(question)}</p>
    <button id="{keep_id}" type="button" data-dismiss="{ident}">{h(t("act.keep"))}</button>
    <button id="{confirm_id}" class="danger" type="submit">{h(t("act.confirm_delete"))}</button>
  </form>
</div>"""


def delete_dialog(row) -> str:
    return _confirm_dialog("delete-ticket-dialog", f"/tickets/{row['_id']}/delete", t("dialog.delete_ticket"),
                           "confirm-delete-ticket", "keep-ticket")


# -- new ticket (a three-step wizard) ----------------------------------------------------------------


def _steps(current: int) -> str:
    names = [t("wizard.customer"), t("wizard.details"), t("wizard.review")]
    marker = ' aria-current="step"'
    return '<ol class="steps">' + "".join(
        f'<li{marker if n == current else ""}>{n}. {h(name)}</li>' for n, name in enumerate(names, 1)) + "</ol>"


def new_ticket_customer(rows, q: str, counts, me) -> bytes:
    items = "".join(f'<li><a id="choose-customer-{h(c["_id"])}" href="/tickets/new/details?customer={h(c["_id"])}">'
                    f'{h(c["name"])}</a> <span class="muted">{h(c["email"])}</span></li>' for c in rows)
    main = f"""<h1>{h(t("wizard.title"))}</h1>{_steps(1)}
<form class="filters" method="get" action="/tickets/new" role="search">
  <label for="wizard-search">{h(t("wizard.find"))}</label>
  <input id="wizard-search" name="q" type="search" value="{h(q)}" autocomplete="off">
  <button id="wizard-search-go" type="submit">{h(t("act.search"))}</button>
</form>
<ul id="wizard-customers" class="plain">{items or f'<li class="muted">{h(t("customers.none"))}</li>'}</ul>"""
    return document(t("wizard.title"), main, counts, me)


def new_ticket_details(who: dict, values: dict, error: str, counts, me) -> bytes:
    channels = [(c, t(f"channel.{c}")) for c in desk.CHANNELS]
    priorities = [(p, priority_label(p)) for p in desk.PRIORITY_RANK]
    problem = f'<p id="wizard-error" class="error" role="alert">{h(error)}</p>' if error else ""
    main = f"""<h1>{h(t("wizard.title"))}</h1>{_steps(2)}
<p class="muted">{h(t("wizard.for"))} <strong id="wizard-customer">{h(who["name"])}</strong></p>
<form class="compose" method="post" action="/tickets/new/review">
  <input type="hidden" name="customer" value="{h(who["_id"])}">
  <label for="ticket-subject">{h(t("field.subject"))}</label>
  <input id="ticket-subject" name="subject" type="text" value="{h(values.get("subject", ""))}">
  <label for="ticket-channel">{h(t("field.channel"))}</label>
  <select id="ticket-channel" name="channel">{_options(channels, values.get("channel", "email"))}</select>
  <label for="ticket-priority">{h(t("field.priority"))}</label>
  <select id="ticket-priority" name="priority">{_options(priorities, values.get("priority", "normal"))}</select>
  <label for="ticket-message">{h(t("field.message"))}</label>
  <textarea id="ticket-message" name="message" rows="4">{h(values.get("message", ""))}</textarea>
  {problem}
  <p class="actions"><a id="wizard-back" href="/tickets/new">{h(t("act.back"))}</a>
    <button id="review-ticket" type="submit">{h(t("act.review_ticket"))}</button></p>
</form>"""
    return document(t("wizard.title"), main, counts, me)


def new_ticket_review(who: dict, values: dict, counts, me) -> bytes:
    hidden = "".join(f'<input type="hidden" name="{k}" value="{h(values[k])}">'
                     for k in ("subject", "channel", "priority", "message"))
    change = "/tickets/new/details?" + urlencode({"customer": who["_id"], **{k: values[k] for k in
                                                   ("subject", "channel", "priority", "message")}})
    main = f"""<h1>{h(t("wizard.title"))}</h1>{_steps(3)}
<dl class="facts" id="wizard-review">
  <dt>{h(t("col.customer"))}</dt><dd>{h(who["name"])}</dd>
  <dt>{h(t("field.subject"))}</dt><dd><span id="review-subject">{h(values["subject"])}</span></dd>
  <dt>{h(t("field.channel"))}</dt><dd>{h(t("channel." + values["channel"]))}</dd>
  <dt>{h(t("field.priority"))}</dt><dd><span id="review-priority">{h(priority_label(values["priority"]))}</span></dd>
  <dt>{h(t("field.message"))}</dt><dd><span id="review-message">{h(values["message"])}</span></dd>
</dl>
<form class="actions" method="post" action="/tickets/new">
  <input type="hidden" name="customer" value="{h(who["_id"])}">{hidden}
  <a id="change-ticket" href="{h(change)}">{h(t("act.change_details"))}</a>
  <button id="create-ticket" type="submit">{h(t("act.create_ticket"))}</button>
</form>"""
    return document(t("wizard.title"), main, counts, me)


# -- customers ----------------------------------------------------------------------------------


def customers_page(listing: dict, q: str, sort: str, counts, me, flash: str = "") -> bytes:
    def link(page: int) -> str:
        return "/customers?" + urlencode({"q": q, "sort": sort, "page": page})
    rows = "".join(
        f'<tr><td><a id="open-customer-{h(c["_id"])}" href="/customers/{h(c["_id"])}">{h(c["name"])}</a></td>'
        f'<td>{h(c["plan"]["name"])}</td><td><output id="speed-{n}">{c["plan"]["speed_mbps"]}</output> Mbps</td>'
        f'<td>{h(c["address"]["town"])}</td></tr>' for n, c in enumerate(listing["rows"], 1))
    if listing["total"]:
        summary = (f'<p class="muted">{h(t("customers.count").format(n=listing["total"]))} · {h(t("customers.plans"))} '
                   f'<output id="speed-min">{listing["slowest"]}</output>–<output id="speed-max">{listing["fastest"]}</output> Mbps</p>')
    else:
        summary = f'<p id="no-customers" class="muted">{h(t("customers.none"))}</p>'
    page, pages = listing["page"], listing["pages"]

    def pager(ident: str, target: int, key: str, enabled: bool) -> str:
        return (f'<a id="{ident}" href="{h(link(target))}">{h(t(key))}</a>' if enabled
                else f'<span class="muted">{h(t(key))}</span>')
    sorts = [("name", t("sort.name")), ("newest", t("sort.newest")), ("speed", t("sort.speed"))]
    flash_html = f'<p id="customers-flash" class="flash" role="status">{h(flash)}</p>' if flash else ""
    main = f"""<h1>{h(t("customers.title"))}</h1>
{flash_html}
<form class="filters" method="get" action="/customers" role="search">
  <label for="customer-search">{h(t("customers.search"))}</label>
  <input id="customer-search" name="q" type="search" value="{h(q)}" autocomplete="off">
  <label for="customer-sort">{h(t("field.sort"))}</label>
  <select id="customer-sort" name="sort">{_options(sorts, sort)}</select>
  <button id="customer-search-go" type="submit">{h(t("act.search"))}</button>
</form>
{summary}
<table class="queue"><thead><tr><th>{h(t("col.customer"))}</th><th>{h(t("details.plan"))}</th>
  <th>{h(t("col.speed"))}</th><th>{h(t("col.town"))}</th></tr></thead>
  <tbody id="customer-rows">{rows}</tbody></table>
<nav class="pager" aria-label="{h(t("label.pages"))}">
  {pager("page-first", 1, "act.first", page > 1)} {pager("page-prev", page - 1, "act.prev", page > 1)}
  <span>{h(t("label.page"))} <output id="page-number">{page}</output> / <output id="page-count">{pages}</output></span>
  {pager("page-next", page + 1, "act.next", page < pages)} {pager("page-last", pages, "act.last", page < pages)}
</nav>
<p><a id="add-customer" href="/customers/new">{h(t("act.add_customer"))}</a></p>"""
    return document(t("customers.title"), main, counts, me)


def customer_page(db, who: dict, counts, me, flash: str = "") -> bytes:
    plan, address = who["plan"], who["address"]
    tickets = "".join(f'<li><a id="customer-ticket-{r["_id"]}" href="/tickets/{r["_id"]}">#{r["_id"]}</a> '
                      f'{h(r["subject"])} · {h(status_label(r["status"]))}</li>' for r in desk.tickets_of(db, who["_id"]))
    flash_html = f'<p id="customer-flash" class="flash" role="status">{h(flash)}</p>' if flash else ""
    contacts = "".join(f'<li>{h(p["name"])} ({h(t("relation." + p["relation"]))}) · {h(p["phone"])}</li>'
                       for p in who["contacts"]) or f'<li class="muted">{h(t("customer.no_contacts"))}</li>'
    main = f"""<p class="crumbs"><a id="back-to-customers" href="/customers">{h(t("nav.customers"))}</a></p>
{flash_html}
<h1 id="customer-name">{h(who["name"])}</h1>
<dl class="facts" id="customer-facts">
  <dt>{h(t("details.email"))}</dt><dd><span id="customer-email">{h(who["email"])}</span></dd>
  <dt>{h(t("details.phone"))}</dt><dd><span id="customer-phone">{h(who["phone"])}</span></dd>
  <dt>{h(t("details.plan"))}</dt><dd><span id="customer-plan">{h(plan["name"])} · {plan["speed_mbps"]} Mbps</span></dd>
  <dt>{h(t("details.router"))}</dt><dd>{h(plan["router"]["model"])} ({h(plan["router"]["serial"])})</dd>
  <dt>{h(t("details.address"))}</dt><dd>{h(address["line1"])}, {h(address["town"])} {h(address["postcode"])}</dd>
  <dt>{h(t("details.since"))}</dt><dd>{h(who["since"])}</dd>
  <dt>{h(t("details.contacts"))}</dt><dd><ul id="customer-contacts" class="plain">{contacts}</ul></dd>
</dl>
<h2>{h(t("customer.tickets"))}</h2>
<ul id="customer-tickets" class="plain">{tickets or f'<li class="muted">{h(t("customer.no_tickets"))}</li>'}</ul>
<p class="actions">
  <a id="edit-customer" href="/customers/{h(who["_id"])}/edit">{h(t("act.edit_customer"))}</a>
  <a id="ticket-for-customer" href="/tickets/new/details?customer={h(who["_id"])}">{h(t("act.ticket_for"))}</a>
  <button id="delete-customer" class="quiet" type="button" data-opens="delete-customer-dialog">{h(t("act.delete_customer"))}</button>
</p>"""
    after = _confirm_dialog("delete-customer-dialog", f"/customers/{who['_id']}/delete", t("dialog.delete_customer"),
                            "confirm-delete-customer", "keep-customer")
    return document(who["name"], main, counts, me, after=after)


def customer_form(who: dict | None, values: dict, error: str, counts, me) -> bytes:
    def value(key: str, current: str) -> str:
        return values.get(key, current)
    address = who["address"] if who else {"line1": "", "town": "", "postcode": ""}
    action = f"/customers/{who['_id']}/edit" if who else "/customers/new"
    title = t("customer.edit_title") if who else t("customer.new_title")
    plans = [(p, f"{p} ({s} Mbps)") for p, s in desk.PLANS.items()]
    problem = f'<p id="customer-error" class="error" role="alert">{h(error)}</p>' if error else ""
    back = f"/customers/{who['_id']}" if who else "/customers"
    main = f"""<h1>{h(title)}</h1>
<form class="compose" method="post" action="{h(action)}" autocomplete="off">
  <label for="customer-name-input">{h(t("field.name"))}</label>
  <input id="customer-name-input" name="name" type="text" value="{h(value("name", who["name"] if who else ""))}">
  <label for="customer-email-input">{h(t("details.email"))}</label>
  <input id="customer-email-input" name="email" type="email" value="{h(value("email", who["email"] if who else ""))}">
  <label for="customer-phone-input">{h(t("details.phone"))}</label>
  <input id="customer-phone-input" name="phone" type="tel" value="{h(value("phone", who["phone"] if who else ""))}">
  <label for="customer-plan-input">{h(t("details.plan"))}</label>
  <select id="customer-plan-input" name="plan">{_options(plans, value("plan", who["plan"]["name"] if who else "Fibre Essential"))}</select>
  <label for="customer-line1">{h(t("field.line1"))}</label>
  <input id="customer-line1" name="line1" type="text" value="{h(value("line1", address["line1"]))}">
  <label for="customer-town">{h(t("field.town"))}</label>
  <input id="customer-town" name="town" type="text" value="{h(value("town", address["town"]))}">
  <label for="customer-postcode">{h(t("field.postcode"))}</label>
  <input id="customer-postcode" name="postcode" type="text" value="{h(value("postcode", address["postcode"]))}">
  {problem}
  <p class="actions"><a id="customer-form-back" href="{h(back)}">{h(t("act.back"))}</a>
    <button id="save-customer" type="submit">{h(t("act.save_customer"))}</button></p>
</form>"""
    return document(title, main, counts, me)


# -- saved replies ------------------------------------------------------------------------------------


def replies_page(rows, counts, me, flash: str = "") -> bytes:
    items, dialogs = [], []  # each reply's delete dialog sits at page level, like every modal
    for n, r in enumerate(rows, 1):
        rid = h(r["_id"])
        items.append(f"""<li class="saved" data-title-of="{rid}" data-saved="{h(r["title"])}">
  <span class="pos">{n}.</span> <strong id="title-{rid}">{h(r["title"])}</strong>
  <button id="edit-title-{rid}" class="quiet" type="button">{h(t("act.rename"))}</button>
  <span id="title-editor-{rid}" hidden><label class="inline" for="title-input-{rid}">{h(t("field.title"))}</label>
    <input id="title-input-{rid}" name="reply_title_{rid}" type="text">
    <button id="save-title-{rid}" type="button">{h(t("act.save"))}</button>
    <button id="cancel-title-{rid}" class="quiet" type="button">{h(t("act.cancel"))}</button></span>
  <span id="title-status-{rid}" class="muted" role="status"></span>
  <p class="body muted">{h(r["body"])}</p>
  <span class="row-actions">
    <form method="post" action="/replies/{rid}/up"><button id="reply-up-{rid}" type="submit">{h(t("act.move_up"))}</button></form>
    <form method="post" action="/replies/{rid}/down"><button id="reply-down-{rid}" type="submit">{h(t("act.move_down"))}</button></form>
    <button id="delete-reply-{rid}" class="quiet" type="button" data-opens="delete-reply-dialog-{rid}">{h(t("act.delete"))}</button>
  </span>
</li>""")
        dialogs.append(_confirm_dialog(f"delete-reply-dialog-{rid}", f"/replies/{rid}/delete", t("dialog.delete_reply"),
                                       f"confirm-delete-reply-{rid}", f"keep-reply-{rid}"))
    flash_html = f'<p id="replies-flash" class="flash" role="status">{h(flash)}</p>' if flash else ""
    first = rows[0]["title"] if rows else ""
    main = f"""<h1>{h(t("replies.title"))}</h1>
{flash_html}
<p class="muted">{h(t("replies.first"))} <strong id="first-reply">{h(first)}</strong></p>
<ol id="saved-replies" class="saved-list">{"".join(items)}</ol>
<form class="compose" method="post" action="/replies" autocomplete="off">
  <h2>{h(t("replies.add"))}</h2>
  <label for="new-reply-title">{h(t("field.title"))}</label>
  <input id="new-reply-title" name="title" type="text">
  <label for="new-reply-body">{h(t("field.reply"))}</label>
  <textarea id="new-reply-body" name="body" rows="3"></textarea>
  <button id="add-reply" type="submit">{h(t("act.add_reply"))}</button>
</form>"""
    return document(t("replies.title"), main, counts, me, after="".join(dialogs))


# -- team and reports ------------------------------------------------------------------------------------


def team_page(people: dict[str, dict], counts, me) -> bytes:
    rows = "".join(f'<tr><td>{h(a["name"])}</td><td>{h(", ".join(t("role." + r) for r in a["roles"]))}</td>'
                   f'<td>{h(t("team.on_call") if a["on_call"] else "")}</td></tr>' for a in people.values())
    main = f"""<h1>{h(t("team.title"))}</h1>
<table class="queue"><thead><tr><th>{h(t("field.name"))}</th><th>{h(t("team.roles"))}</th><th></th></tr></thead>
<tbody id="team-rows">{rows}</tbody></table>"""
    return document(t("team.title"), main, counts, me)


def reports_page(db, breaches: list[dict], counts, me) -> bytes:
    names = {c["_id"]: c["name"] for c in db.customers.find({}, {"name": 1})}
    rows = "".join(
        f'<tr><td><a id="breach-{b["ticket"]["_id"]}" href="/tickets/{b["ticket"]["_id"]}">#{b["ticket"]["_id"]}</a></td>'
        f'<td>{h(names.get(b["ticket"]["customer_id"]))}</td><td>{h(priority_label(b["ticket"]["priority"]))}</td>'
        f'<td>{h(b["respond_by"])}{" !" if b["response_late"] else ""}</td>'
        f'<td>{h(b["resolve_by"])}{" !" if b["resolve_late"] else ""}</td></tr>' for b in breaches)
    main = f"""<h1>{h(t("reports.title"))}</h1>
<p class="muted">{h(t("reports.help"))}</p>
<table class="queue"><thead><tr><th>{h(t("col.ticket"))}</th><th>{h(t("col.customer"))}</th><th>{h(t("col.priority"))}</th>
  <th>{h(t("sla.respond_by"))}</th><th>{h(t("sla.resolve_by"))}</th></tr></thead>
<tbody id="breach-rows">{rows or f'<tr><td colspan="5">{h(t("reports.none"))}</td></tr>'}</tbody></table>"""
    return document(t("reports.title"), main, counts, me)


def server_error() -> bytes:
    """The answer to a request the desk could not handle (HTTP 500). Rendered without the
    desk's frame, which needs the database that may be what failed."""
    body = (f'<main id="main"><h1>{h(t("failure.title"))}</h1><p>{h(t("failure.body"))}</p>'
            f'<p><a id="failure-back" href="/queue">{h(t("nav.back"))}</a></p></main>')
    return (f'<!doctype html><html lang="en-GB"><head><meta charset="utf-8"><link rel="icon" href="data:,"><title>{h(t("failure.title"))}</title>'
            f'<link rel="stylesheet" href="/static/desk.css"></head><body>{body}</body></html>').encode("utf-8")


def not_found(counts, me) -> bytes:
    main = f"""<h1>{h(t("missing.title"))}</h1><p>{h(t("missing.body"))}</p>
<p><a id="missing-back" href="/queue">{h(t("nav.back"))}</a></p>"""
    return document(t("missing.title"), main, counts, me)
