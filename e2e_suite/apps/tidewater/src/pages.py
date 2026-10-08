"""The two pages Tidewater renders on the server: signing in, and what to do about a
forgotten password. Everything else is the single-page client in static/."""

from __future__ import annotations

from html import escape


def _frame(words: dict, heading: str, body: str) -> str:
    title = escape(words["app.title"])
    return (f'<!doctype html>\n<html lang="en-GB">\n<head>\n<meta charset="utf-8">\n'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'<title>{title}</title>\n<link rel="stylesheet" href="/styles.css">\n</head>\n<body>\n'
            f'<header class="topbar"><span class="brand">{title}</span></header>\n'
            f'<main id="view" class="narrow">\n<h1>{escape(heading)}</h1>\n{body}\n</main>\n'
            f'</body>\n</html>\n')


def sign_in(words: dict, wanted: str, username: str = "", refused: bool = False) -> str:
    """The sign-in form; after a refusal, with one message whatever was wrong."""
    w = {key: escape(value) for key, value in words.items()}
    notice = f'<p class="error" id="login-error" role="alert">{w["signin.refused"]}</p>' if refused else ""
    return _frame(words, words["signin.heading"], f"""
<form class="panel" id="sign-in-form" action="/login" method="post">
  <input name="next" type="hidden" value="{escape(wanted)}">
  <label for="login-username">{w["signin.username"]}</label>
  <input autocomplete="username" id="login-username" name="username" required type="text" value="{escape(username)}">
  <label for="login-password">{w["signin.password"]}</label>
  <input autocomplete="current-password" id="login-password" name="password" required type="password">
  {notice}
  <div class="actions">
    <button class="primary" id="login-submit" type="submit">{w["act.sign_in"]}</button>
  </div>
  <p class="muted"><a href="/help/password" id="forgot-password">{w["act.forgot_password"]}</a></p>
</form>""")


def password_help(words: dict) -> str:
    w = {key: escape(value) for key, value in words.items()}
    return _frame(words, words["help.heading"], f"""
<section class="panel">
  <p>{w["help.reset"]}</p>
  <p class="muted">{w["help.never_ask"]}</p>
</section>
<p><a href="/login" id="back-to-sign-in">{w["act.back_to_sign_in"]}</a></p>""")
