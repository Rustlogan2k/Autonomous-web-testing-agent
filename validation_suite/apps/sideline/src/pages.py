"""Sideline's HTML: one function per view, for each of the three front doors.

Every label is looked up in the vocabulary profile with `t`; data (club names, scores,
dates, reasons) never is. Regions that the league's rules can be checked against carry an
`id` and hold only their value: a table cell holds one number, a status holds one word.
The league table's cells are identified by league position (`pts-1` is the leader's
points), and keep those ids when a visitor re-sorts the table in the browser.
"""

from __future__ import annotations

from html import escape

from results import CONFIRMED, DISPUTED, GOAL_CHOICES, REASON_LIMIT, Result
from standings import Row

VOCAB: dict[str, str] = {}
LEAGUE: dict[str, str] = {}
TEAMS: dict[str, str] = {}


def t(key: str) -> str:
    return VOCAB.get(key, key)


def h(value: object) -> str:
    return escape("" if value is None else str(value), quote=True)


class _Fill(dict):
    def __missing__(self, name: str) -> str:
        return "{" + name + "}"


def fill(key: str, **values: object) -> str:
    """A vocabulary sentence with its placeholders filled (an unknown one stays as written)."""
    return t(key).format_map(_Fill(values))


def label(key: str, **values: object) -> str:
    """`fill`, escaped for HTML."""
    return h(fill(key, **values))


def with_value(key: str, markup: str) -> str:
    """A vocabulary sentence whose `{n}` is replaced by already-built markup."""
    before, _, after = t(key).partition("{n}")
    return f"{h(before)}{markup}{h(after)}"


def team(code: str) -> str:
    return TEAMS.get(code, code)


def status_word(result: Result) -> str:
    return t(f"status.{result.status}")


def score(result: Result) -> str:
    return f"{result.home_goals}–{result.away_goals}" if result.has_score else "–"


# -- the frame -------------------------------------------------------------------------------


def document(title: str, portal: str, nav: list[tuple[str, str, str]], main: str,
             flash: str = "", home_link: bool = True) -> bytes:
    links = "".join(f'<a id="{ident}" href="{h(href)}">{h(text)}</a>' for ident, href, text in nav)
    if home_link:
        links += f'<a id="nav-home" class="switch" href="/">{h(t("act.nav_home"))}</a>'
    return f"""<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:,">
<title>{h(title)} | {h(t("app.name"))}</title>
<link rel="stylesheet" href="/static/sideline.css">
</head>
<body class="portal-{h(portal)}">
<header class="masthead">
  <p class="brand">{h(t("app.name"))} <span class="league">{h(LEAGUE.get("league", ""))}</span></p>
  <p class="portal-name">{h(t(f"portal.{portal}"))}</p>
  <nav class="portal-nav" aria-label="{h(t("nav.label"))}">{links}</nav>
</header>
<main class="sheet">
<p id="flash" class="flash" role="status">{h(flash)}</p>
{main}
</main>
<footer class="foot">{h(LEAGUE.get("division", ""))} &middot; {h(t("foot.season"))} {h(LEAGUE.get("season", ""))}</footer>
<script src="/static/sideline.js" defer></script>
</body>
</html>""".encode("utf-8")


def _secretary_nav() -> list[tuple[str, str, str]]:
    return [("nav-sec", "/sec", t("act.nav_sec"))]


def _club_nav(club: str) -> list[tuple[str, str, str]]:
    return [("nav-club", f"/club/{club}", t("act.nav_club"))]


def _public_nav() -> list[tuple[str, str, str]]:
    return [("nav-table", "/table", t("act.nav_table")), ("nav-results", "/results", t("act.nav_results"))]


# -- the landing page --------------------------------------------------------------------------


def landing(today: str) -> bytes:
    clubs = "".join(f'<li><a id="door-club-{h(code)}" href="/club/{h(code)}">{label("act.door_club", club=name)}</a></li>'
                    for code, name in TEAMS.items())
    main = f"""<h1>{h(t("home.title"))}</h1>
<p class="lead">{h(t("home.lead"))}</p>
<div class="doors">
  <section class="door">
    <h2>{h(t("door.secretary"))}</h2>
    <p>{h(t("door.secretary_body"))}</p>
    <a id="door-secretary" class="door-link" href="/sec">{h(t("act.door_secretary"))}</a>
  </section>
  <section class="door">
    <h2>{h(t("door.clubs"))}</h2>
    <p>{h(t("door.clubs_body"))}</p>
    <ul class="club-doors">{clubs}</ul>
  </section>
  <section class="door">
    <h2>{h(t("door.public"))}</h2>
    <p>{h(t("door.public_body"))}</p>
    <a id="door-public" class="door-link" href="/table">{h(t("act.door_public"))}</a>
  </section>
</div>
<p class="muted">{label("home.today", date=today)}</p>"""
    return document(t("home.title"), "home", [], main, home_link=False)


# -- the secretary's desk ------------------------------------------------------------------------


def _confirmations(result: Result) -> str:
    if not result.has_score:
        return ""
    return label("sec.confirmed_by", n=result.confirmations)


def secretary(fixtures: list[Result], today: str, flash: str) -> bytes:
    rows = []
    for r in fixtures:
        actions = f'<a id="result-F{r.number}" href="/sec/results/{r.id}">{h(t("act.view_result"))}</a>'
        if not r.has_score and r.played_on <= today:
            actions += f' <a id="enter-F{r.number}" class="primary" href="/sec/results/{r.id}/edit">{h(t("act.enter_score"))}</a>'
        rows.append(f"""<tr class="st-{h(r.status)}">
  <td>{h(r.id)}</td><td>{h(r.played_on)}</td>
  <td>{h(team(r.home))}</td><td class="num">{h(score(r))}</td><td>{h(team(r.away))}</td>
  <td>{h(status_word(r))}</td><td>{_confirmations(r)}</td><td class="actions">{actions}</td>
</tr>""")
    waiting = sum(1 for r in fixtures if not r.has_score)
    open_ = sum(1 for r in fixtures if r.has_score and r.status != CONFIRMED)
    main = f"""<h1>{h(t("sec.title"))}</h1>
<p class="lead">{h(t("sec.lead"))}</p>
<p class="summary">{label("sec.summary", waiting=waiting, open=open_)}</p>
<table class="fixtures">
  <thead><tr><th>{h(t("col.fixture"))}</th><th>{h(t("col.date"))}</th><th>{h(t("col.home"))}</th>
    <th>{h(t("col.score"))}</th><th>{h(t("col.away"))}</th><th>{h(t("col.status"))}</th>
    <th>{h(t("col.confirmations"))}</th><th>{h(t("col.actions"))}</th></tr></thead>
  <tbody>{"".join(rows)}</tbody>
</table>"""
    return document(t("sec.title"), "secretary", _secretary_nav(), main, flash)


def _club_line(result: Result, club: str) -> str:
    if result.disputed_by == club and result.status == DISPUTED:
        state = t("conf.disputed")
    elif result.confirmed_by(club):
        state = t("conf.yes")
    else:
        state = t("conf.no")
    return f"<li>{h(team(club))}: {h(state)}</li>"


def result_detail(result: Result, flash: str) -> bytes:
    if result.has_score:
        line = (f'{h(team(result.home))} <strong id="result-score" class="score">{h(score(result))}</strong> '
                f'{h(team(result.away))}')
        edit_text = t("act.edit_result")
    else:
        line = f'{h(team(result.home))} {h(t("result.versus"))} {h(team(result.away))}'
        edit_text = t("act.enter_first")
    dispute = ""
    if result.status == DISPUTED:
        dispute = (f'<p class="dispute">{label("result.disputed_by", club=team(result.disputed_by or ""))} '
                   f'<q id="dispute-text">{h(result.dispute_reason)}</q></p>')
    main = f"""<h1>{label("result.title", fixture=result.id)}</h1>
<p class="fixture-line">{line}</p>
<p class="muted">{label("result.played_on", date=result.played_on)}</p>
<dl class="facts">
  <dt>{h(t("result.status"))}</dt><dd><strong id="result-status" class="status st-{h(result.status)}">{h(status_word(result))}</strong></dd>
  <dt>{h(t("result.confirmations"))}</dt><dd>{with_value("result.confirm_count", f'<strong id="confirm-count">{result.confirmations}</strong>')}</dd>
</dl>
<ul class="club-confirmations">{_club_line(result, result.home)}{_club_line(result, result.away)}</ul>
{dispute}
<p class="actions"><a id="edit-result" class="primary" href="/sec/results/{h(result.id)}/edit">{h(edit_text)}</a></p>"""
    return document(fill("result.title", fixture=result.id), "secretary", _secretary_nav(), main, flash)


def _goal_select(ident: str, name: str, club: str, chosen: int) -> str:
    options = "".join(f'<option value="{n}"{" selected" if n == chosen else ""}>{n}</option>' for n in GOAL_CHOICES)
    return (f'<p class="field"><label for="{ident}">{label("edit.goals_for", club=team(club))}</label>'
            f'<select id="{ident}" name="{name}">{options}</select></p>')


def result_edit(result: Result) -> bytes:
    heading = "edit.correct_title" if result.has_score else "edit.enter_title"
    note = t("edit.correct_note") if result.has_score else t("edit.enter_note")
    main = f"""<h1>{label(heading, fixture=result.id)}</h1>
<p class="fixture-line">{h(team(result.home))} {h(t("result.versus"))} {h(team(result.away))} <span class="muted">{h(result.played_on)}</span></p>
<p class="lead">{h(note)}</p>
<form class="score-form" method="post" action="/sec/results/{h(result.id)}">
  {_goal_select("home-goals", "home_goals", result.home, result.home_goals or 0)}
  {_goal_select("away-goals", "away_goals", result.away, result.away_goals or 0)}
  <p class="actions"><button id="save-result" type="submit">{h(t("act.save_result"))}</button>
  <a id="cancel-edit" href="/sec/results/{h(result.id)}">{h(t("act.cancel_edit"))}</a></p>
</form>"""
    return document(fill(heading, fixture=result.id), "secretary", _secretary_nav(), main)


# -- a club captain's portal ---------------------------------------------------------------------


def club(code: str, fixtures: list[Result], flash: str) -> bytes:
    pending = sum(1 for r in fixtures if r.awaits(code))
    played, to_come = [], []
    for r in fixtures:
        if not r.has_score:
            to_come.append(f'<li>{h(r.id)} {h(r.played_on)}: {h(team(r.home))} {h(t("result.versus"))} '
                           f'{h(team(r.away))}</li>')
            continue
        dispute_text = t("act.dispute")
        played.append(f"""<tr class="st-{h(r.status)}">
  <td>{h(r.id)}</td><td>{h(r.played_on)}</td>
  <td>{h(team(r.home))} <span class="num">{h(score(r))}</span> {h(team(r.away))}</td>
  <td>{h(status_word(r))}</td>
  <td>{h(t("conf.yes") if r.confirmed_by(code) else t("conf.no"))}</td>
  <td>{h(t("conf.yes") if r.confirmed_by(r.opponent_of(code)) else t("conf.no"))}</td>
  <td class="actions"><form method="post" action="/club/{h(code)}/confirm/{h(r.id)}"><button id="confirm-F{r.number}" type="submit">{h(t("act.confirm"))}</button></form>
    <a id="dispute-F{r.number}" href="/club/{h(code)}/dispute/{h(r.id)}">{h(dispute_text)}</a></td>
</tr>""")
    upcoming = "".join(to_come) or f'<li class="muted">{h(t("club.none_to_come"))}</li>'
    main = f"""<h1>{label("club.title", club=team(code))}</h1>
<p class="lead">{with_value("club.pending", f'<strong id="club-pending">{pending}</strong>')}</p>
<table class="club-results">
  <thead><tr><th>{h(t("col.fixture"))}</th><th>{h(t("col.date"))}</th><th>{h(t("col.match"))}</th>
    <th>{h(t("col.status"))}</th><th>{h(t("col.you"))}</th><th>{h(t("col.opponent"))}</th>
    <th>{h(t("col.actions"))}</th></tr></thead>
  <tbody>{"".join(played)}</tbody>
</table>
<h2>{h(t("club.to_come"))}</h2>
<ul class="to-come">{upcoming}</ul>"""
    return document(fill("club.title", club=team(code)), "club", _club_nav(code), main, flash)


def dispute_form(code: str, result: Result, refusal: str, flash: str) -> bytes:
    if refusal:
        body = f'<p class="refusal">{h(refusal)}</p>'
    else:
        body = f"""<form class="dispute-form" method="post" action="/club/{h(code)}/dispute/{h(result.id)}">
  <p class="field"><label for="dispute-reason">{h(t("dispute.reason"))}</label>
  <input id="dispute-reason" name="dispute_reason" type="text" autocomplete="off"></p>
  <p class="hint muted">{label("dispute.hint", limit=REASON_LIMIT)}</p>
  <p class="actions"><button id="submit-dispute" type="submit">{h(t("act.submit_dispute"))}</button></p>
</form>"""
    main = f"""<h1>{label("dispute.title", fixture=result.id)}</h1>
<p class="fixture-line">{h(team(result.home))} <span class="num">{h(score(result))}</span> {h(team(result.away))}</p>
{body}
<p><a id="back-to-club" href="/club/{h(code)}">{h(t("act.back_to_club"))}</a></p>"""
    return document(fill("dispute.title", fixture=result.id), "club", _club_nav(code), main, flash)


# -- the public pages ----------------------------------------------------------------------------


def _signed(n: int) -> str:
    return f"+{n}" if n > 0 else ("−" + str(-n) if n < 0 else "0")


def league_table(ranked: list[Row], counted: int, today: str) -> bytes:
    body = []
    for position, row in enumerate(ranked, start=1):
        body.append(f"""<tr data-pos="{position}" data-pts="{row.points}" data-gf="{row.goals_for}">
  <td class="num">{position}</td><td><a id="team-{h(row.code)}" href="/teams/{h(row.code)}">{h(row.name)}</a></td>
  <td id="pl-{position}" class="num">{row.played}</td><td class="num">{row.won}</td><td class="num">{row.drawn}</td>
  <td class="num">{row.lost}</td><td id="gf-{position}" class="num">{row.goals_for}</td>
  <td id="ga-{position}" class="num">{row.goals_against}</td><td class="num">{_signed(row.goal_difference)}</td>
  <td id="pts-{position}" class="num pts">{row.points}</td>
</tr>""")
    main = f"""<h1>{h(t("table.title"))}</h1>
<p class="muted">{label("table.as_of", date=today)}</p>
<table id="league-table" class="league">
  <thead><tr>
    <th scope="col">{h(t("col.pos"))}</th><th scope="col">{h(t("col.club"))}</th>
    <th scope="col">{h(t("col.played"))}</th><th scope="col">{h(t("col.won"))}</th>
    <th scope="col">{h(t("col.drawn"))}</th><th scope="col">{h(t("col.lost"))}</th>
    <th scope="col" aria-sort="none"><button id="sort-gf" type="button" data-sort="gf">{h(t("act.sort_gf"))}</button></th>
    <th scope="col">{h(t("col.against"))}</th><th scope="col">{h(t("col.difference"))}</th>
    <th scope="col" aria-sort="descending"><button id="sort-pts" type="button" data-sort="pts">{h(t("act.sort_pts"))}</button></th>
  </tr></thead>
  <tbody>{"".join(body)}</tbody>
</table>
<p class="basis">{with_value("table.basis", f'<strong id="results-counted">{counted}</strong>')}</p>
<p class="muted">{h(t("table.rule"))}</p>
<p class="actions"><button id="print-table" type="button" aria-pressed="false">{h(t("act.print_table"))}</button></p>"""
    return document(t("table.title"), "public", _public_nav(), main)


def public_results(fixtures: list[Result]) -> bytes:
    confirmed = [r for r in fixtures if r.status == CONFIRMED]
    others = [r for r in fixtures if r.status != CONFIRMED]
    rows = "".join(f'<tr><td>{h(r.played_on)}</td><td>{h(team(r.home))}</td><td class="num">{h(score(r))}</td>'
                   f'<td>{h(team(r.away))}</td></tr>' for r in confirmed)
    if not rows:
        rows = f'<tr><td colspan="4" class="muted">{h(t("results.none"))}</td></tr>'
    pending = "".join(f'<li>{h(r.played_on)}: {h(team(r.home))} {h(t("result.versus"))} {h(team(r.away))}</li>'
                      for r in others) or f'<li class="muted">{h(t("results.nothing_pending"))}</li>'
    clubs = "".join(f'<li><a id="team-{h(code)}" href="/teams/{h(code)}">{h(name)}</a></li>'
                    for code, name in TEAMS.items())
    main = f"""<h1>{h(t("results.title"))}</h1>
<table class="results">
  <thead><tr><th>{h(t("col.date"))}</th><th>{h(t("col.home"))}</th><th>{h(t("col.score"))}</th><th>{h(t("col.away"))}</th></tr></thead>
  <tbody>{rows}</tbody>
</table>
<h2>{h(t("results.pending"))}</h2>
<ul class="pending">{pending}</ul>
<h2>{h(t("results.clubs"))}</h2>
<ul class="club-list">{clubs}</ul>"""
    return document(t("results.title"), "public", _public_nav(), main)


def team_page(code: str, position: int, row: Row, fixtures: list[Result], size: int) -> bytes:
    played = "".join(f'<li>{h(r.played_on)}: {h(team(r.home))} {h(score(r))} {h(team(r.away))}</li>'
                     for r in fixtures if r.status == CONFIRMED)
    played = played or f'<li class="muted">{h(t("results.none"))}</li>'
    to_come = "".join(f'<li>{h(r.played_on)}: {h(team(r.home))} {h(t("result.versus"))} {h(team(r.away))}</li>'
                      for r in fixtures if r.status != CONFIRMED)
    to_come = to_come or f'<li class="muted">{h(t("results.nothing_pending"))}</li>'
    main = f"""<h1>{h(team(code))}</h1>
<p class="lead">{label("team.standing", position=position, size=size, points=row.points, played=row.played)}</p>
<p><button id="follow-team" type="button" class="star" aria-pressed="false" data-on="{h(t("follow.on"))}" data-off="{h(t("act.follow"))}">{h(t("act.follow"))}</button></p>
<h2>{h(t("team.results"))}</h2>
<ul class="team-results">{played}</ul>
<h2>{h(t("team.to_come"))}</h2>
<ul class="team-to-come">{to_come}</ul>"""
    return document(team(code), "public", _public_nav(), main)


def not_found() -> bytes:
    main = f'<h1>{h(t("missing.title"))}</h1><p>{h(t("missing.body"))}</p>'
    return document(t("missing.title"), "home", [], main)
