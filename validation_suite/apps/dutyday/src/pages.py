"""Dutyday's pages, rendered on the server. No JavaScript: links carry the view, forms post changes.

Every label comes from the vocabulary profile; names of days and months are calendar data
and are the same in every profile.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from html import escape

import rota

VOCAB: dict = {}
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
               "October", "November", "December")


def t(key: str) -> str:
    return VOCAB.get(key, key)


def e(value) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def long_date(day: date) -> str:
    return f"{DAY_NAMES[day.weekday()]} {day.day} {MONTH_NAMES[day.month - 1]} {day.year}"


def short_date(day: date) -> str:
    return f"{DAY_NAMES[day.weekday()][:3]} {day.day} {MONTH_NAMES[day.month - 1][:3]}"


def month_name(month: date) -> str:
    return f"{MONTH_NAMES[month.month - 1]} {month.year}"


def slot_name(slot: str) -> str:
    return t(f"slot.{slot}")


def slot_times(slot: str) -> str:
    info = rota.SLOTS[slot]
    return f"{info['start']}–{info['end']} · {rota.hours_text(rota.slot_hours(slot))} h"


def status_label(status: str, needed: int) -> str:
    if status == "full":
        return t("status.full")
    if status == "needs":
        return t("status.needs").format(n=needed)
    return t("status.open")


def volunteer_name(volunteer: str) -> str:
    return rota.VOLUNTEERS[volunteer]["name"]


def volunteer_href(volunteer: str, day: date | None = None) -> str:
    week = f"?week={rota.monday_of(day).isoformat()}" if day else ""
    return f"/volunteers/{volunteer}{week}"


def layout(title: str, body: str, flash: str = "") -> bytes:
    notice = f'<p id="flash" class="flash" role="status">{e(flash)}</p>' if flash else ""
    return f"""<!doctype html>
<html lang="en-GB"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)} · {e(t("app.name"))}</title>
<link rel="icon" href="data:,">
<link rel="stylesheet" href="/static/rota.css"></head>
<body>
<header class="top">
  <p class="brand">{e(t("app.name"))} <span>{e(t("app.tagline"))}</span></p>
  <nav aria-label="{e(t("nav.label"))}">
    <a id="nav-rota" href="/rota">{e(t("nav.rota"))}</a>
    <a id="nav-volunteers" href="/volunteers">{e(t("nav.volunteers"))}</a>
    <a id="nav-help" href="/help">{e(t("nav.help"))}</a>
  </nav>
</header>
<main>{notice}
{body}
</main>
</body></html>""".encode("utf-8")


def message(title_key: str, body_key: str) -> str:
    return f'<h1>{e(t(title_key))}</h1>\n<p class="lede">{e(t(body_key))}</p>'


# -- the month grid --------------------------------------------------------------------------------


def _grid_cell(day: date, month: date, places: dict[date, int]) -> str:
    if day.month != month.month:
        return '<td class="outside"></td>'
    if day.weekday() == rota.SUNDAY:
        note, kind = t("grid.closed"), "closed"
    elif rota.is_past(day):
        note, kind = t("grid.past"), "past"
    elif places.get(day, 0) == 0:
        note, kind = t("grid.full"), "full"
    else:
        note, kind = t("grid.places").format(n=places[day]), "open"
    iso = day.isoformat()
    return (f'<td class="{kind}"><a id="day-{iso}" href="/rota/day/{iso}">{day.day}</a>'
            f'<span class="note">{e(note)}</span></td>')


def month_page(month: date, places: dict[date, int], *, feed: bool = False) -> str:
    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(month.year, month.month)
    head = "".join(f'<th scope="col">{name[:3]}</th>' for name in DAY_NAMES)
    rows = "\n".join("<tr>" + "".join(_grid_cell(day, month, places) for day in week) + "</tr>" for week in weeks)
    key = f"{month.year:04d}-{month.month:02d}"
    earlier, later = rota.adjacent_month(month, -1), rota.adjacent_month(month, 1)
    paging = "".join([
        f'<a id="month-prev" href="/rota?month={earlier:%Y-%m}">{e(t("act.month_prev"))}</a>' if earlier else "",
        f'<a id="month-next" href="/rota?month={later:%Y-%m}">{e(t("act.month_next"))}</a>' if later else ""])
    feed_box = (f'<p id="feed-address" class="feed">{e(t("month.feed_text"))} <code>/feeds/food-bank-rota.ics</code></p>'
                if feed else "")
    return f"""<div class="month-head"><h1 id="month-title">{e(month_name(month))}</h1>
<nav class="paging" aria-label="{e(t("month.paging"))}">{paging}</nav></div>
<p class="lede">{e(t("month.lede"))}</p>
<table class="grid"><thead><tr>{head}</tr></thead>
<tbody>
{rows}
</tbody></table>
<form class="jump" action="/rota/jump" method="get">
  <input type="hidden" name="month" value="{key}">
  <label for="jump-date">{e(t("field.jump_date"))}</label>
  <input id="jump-date" name="date" type="date">
  <button id="jump-go" type="submit">{e(t("act.jump"))}</button>
</form>
<p class="extras"><a id="print-month" href="/rota/print?month={key}">{e(t("act.print"))}</a>
<a id="calendar-feed" href="/rota?month={key}&amp;feed=show">{e(t("act.feed"))}</a></p>
{feed_box}"""


# -- a day ---------------------------------------------------------------------------------------------


def _roster(day: date, shift: dict, editable: bool) -> str:
    items = []
    for entry in shift["roster"]:
        remove = ""
        if editable:
            remove = (f'<form class="inline" method="post" action="/rota/day/{day.isoformat()}/remove">'
                      f'<input type="hidden" name="assignment" value="{e(entry["id"])}">'
                      f'<button id="remove-{e(entry["id"])}" type="submit">{e(t("act.remove"))}</button></form>')
        vid = entry["volunteer"]
        items.append(f'<li><a id="vol-{e(vid)}" href="{volunteer_href(vid, day)}">{e(volunteer_name(vid))}</a>'
                     f'{remove}</li>')
    if not items:
        items.append(f'<li class="empty">{e(t("day.roster_empty"))}</li>')
    return f'<ul id="roster-{shift["slot"]}" class="roster">{"".join(items)}</ul>'


def _assign_form(day: date, slot: str) -> str:
    options = "".join(f'<option value="{e(vid)}">{e(v["name"])}</option>' for vid, v in rota.VOLUNTEERS.items())
    return f"""<form class="assign" method="post" action="/rota/day/{day.isoformat()}/assign">
    <input type="hidden" name="slot" value="{slot}">
    <label for="assign-{slot}">{e(t("field.volunteer"))}</label>
    <select id="assign-{slot}" name="volunteer"><option value="">{e(t("field.choose"))}</option>{options}</select>
    <button id="assign-{slot}-go" type="submit">{e(t("act.assign"))}</button>
  </form>"""


def _shift_section(day: date, shift: dict, editable: bool) -> str:
    slot = shift["slot"]
    needed = t("day.of_needed").format(n=rota.PLACES)
    form = _assign_form(day, slot) if editable else ""
    return f"""<section class="shift {shift["status"]}">
  <h2>{e(slot_name(slot))} <small>{e(slot_times(slot))}</small></h2>
  <p class="fill"><span id="count-{slot}">{len(shift["roster"])}</span> {e(needed)}
    <strong id="status-{slot}" class="status">{e(status_label(shift["status"], shift["needed"]))}</strong></p>
  {_roster(day, shift, editable)}
  {form}
</section>"""


def day_page(day: date, shifts: list[dict]) -> str:
    month = day.replace(day=1)
    back = f'<a id="back-to-month" href="/rota?month={month:%Y-%m}">{e(t("act.back_to_month"))}</a>'
    head = f'<h1 id="day-date">{e(long_date(day))}</h1>\n<p class="crumbs">{back}</p>'
    if not rota.in_season(day):
        return f'{head}\n<p class="lede">{e(t("day.no_rota"))}</p>'
    if not shifts:
        return f'{head}\n<p class="lede">{e(t("day.closed"))}</p>'
    editable = not rota.is_past(day)
    note = "" if editable else f'<p class="lede">{e(t("day.read_only"))}</p>'
    sections = "\n".join(_shift_section(day, shift, editable) for shift in shifts)
    return f'{head}\n{note}\n<div class="shifts">\n{sections}\n</div>'


# -- volunteers ----------------------------------------------------------------------------------------


def volunteers_page(hours: dict[str, float]) -> str:
    limit = rota.hours_text(rota.WEEKLY_LIMIT)
    rows = "".join(
        f'<li><a id="vol-{e(vid)}" href="{volunteer_href(vid)}">{e(v["name"])}</a> '
        f'<span class="muted">{e(t("vol.list_hours").format(hours=rota.hours_text(hours[vid]), limit=limit))}</span></li>'
        for vid, v in rota.VOLUNTEERS.items())
    return f'<h1>{e(t("vol.title"))}</h1>\n<p class="lede">{e(t("vol.lede"))}</p>\n<ul class="people">{rows}</ul>'


def volunteer_page(volunteer: str, monday: date, shifts: list[dict], upcoming: dict | None) -> str:
    earlier, later = rota.adjacent_week(monday, -1), rota.adjacent_week(monday, 1)
    paging = "".join([
        f'<a id="week-prev" href="/volunteers/{volunteer}?week={earlier.isoformat()}">{e(t("act.week_prev"))}</a>'
        if earlier else "",
        f'<span class="week">{e(t("vol.week_of").format(date=long_date(monday)))}</span>',
        f'<a id="week-next" href="/volunteers/{volunteer}?week={later.isoformat()}">{e(t("act.week_next"))}</a>'
        if later else ""])
    items = "".join(
        f'<li>{e(short_date(s["day"]))} · {e(slot_name(s["slot"]))} <small>{e(slot_times(s["slot"]))}</small> '
        f'<a id="withdraw-{e(s["id"])}" href="/assignments/{e(s["id"])}/withdraw">{e(t("act.withdraw"))}</a></li>'
        for s in shifts) or f'<li class="empty">{e(t("vol.no_shifts"))}</li>'
    hours = t("vol.hours_of").format(hours=rota.hours_text(rota.hours_of(shifts)),
                                     limit=rota.hours_text(rota.WEEKLY_LIMIT))
    if upcoming:
        when = f'{short_date(upcoming["day"])}, {slot_name(upcoming["slot"])}'
        coming = (f'<p class="next">{e(t("vol.next"))}: <span id="next-when">{e(when)}</span>. '
                  f'{e(t("vol.next_in"))}: <strong id="next-in">{rota.days_until(upcoming["day"])}</strong></p>')
    else:
        coming = f'<p class="next">{e(t("vol.no_next"))}</p>'
    return f"""<h1>{e(volunteer_name(volunteer))}</h1>
{coming}
<nav class="paging weeks" aria-label="{e(t("vol.paging"))}">{paging}</nav>
<ul id="vol-shifts" class="week-shifts">{items}</ul>
<p class="hours">{e(t("vol.hours"))}: <strong id="week-hours">{e(hours)}</strong></p>"""


def withdraw_page(found: dict) -> str:
    vid, day = found["volunteer"], found["day"]
    summary = f'{volunteer_name(vid)} · {long_date(day)} · {slot_name(found["slot"])} {slot_times(found["slot"])}'
    return f"""<h1>{e(t("withdraw.title"))}</h1>
<p id="withdraw-summary" class="lede">{e(summary)}</p>
<p>{e(t("withdraw.lede"))}</p>
<div class="actions">
  <form method="post" action="/assignments/{e(found["id"])}/withdraw">
    <button id="confirm-withdraw" type="submit">{e(t("act.confirm_withdraw"))}</button>
  </form>
  <a id="cancel-withdraw" href="{volunteer_href(vid, day)}">{e(t("act.cancel_withdraw"))}</a>
</div>"""


# -- print layout and help ---------------------------------------------------------------------------


def print_page(month: date, days: dict[date, list[dict]]) -> str:
    head = "".join(f'<th scope="col">{e(slot_name(slot))}</th>' for slot in rota.SLOT_ORDER)
    rows, day = [], month
    while day.month == month.month:
        if day in days:
            cells = "".join(
                f'<td>{e(", ".join(volunteer_name(r["volunteer"]) for r in s["roster"]) or "–")} '
                f'<small>{e(status_label(s["status"], s["needed"]))}</small></td>' for s in days[day])
        else:
            cells = f'<td colspan="{len(rota.SLOT_ORDER)}" class="muted">{e(t("grid.closed"))}</td>'
        rows.append(f'<tr><th scope="row">{e(short_date(day))}</th>{cells}</tr>')
        day += timedelta(days=1)
    body = "\n".join(rows)
    return f"""<h1>{e(t("print.title").format(month=month_name(month)))}</h1>
<p class="crumbs"><a id="print-back" href="/rota?month={month:%Y-%m}">{e(t("act.back_to_rota"))}</a></p>
<table class="print"><thead><tr><th></th>{head}</tr></thead>
<tbody>
{body}
</tbody></table>"""


def help_page() -> str:
    rules = "".join(f"<li>{e(t(key))}</li>" for key in ("help.places", "help.week", "help.day", "help.past",
                                                         "help.withdraw"))
    return f'<h1>{e(t("help.title"))}</h1>\n<p class="lede">{e(t("help.lede"))}</p>\n<ul class="rules">{rules}</ul>'
