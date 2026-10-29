"""Deterministic test-value generation for TYPE actions, keyed by HTML input type and value category."""

from __future__ import annotations

import re

from .types import InputValueCategory

_LONG_STRING = "A" * 2048

_BY_TYPE: dict[str, dict[InputValueCategory, str]] = {
    "text": {
        InputValueCategory.VALID_TYPICAL: "Test User Input",
        InputValueCategory.BOUNDARY_MIN: "a",
        InputValueCategory.BOUNDARY_MAX: _LONG_STRING,
        InputValueCategory.EMPTY_STRING: "",
        InputValueCategory.TYPE_MISMATCH: "12345",
    },
    "email": {
        InputValueCategory.VALID_TYPICAL: "tester@example.com",
        InputValueCategory.BOUNDARY_MIN: "a@b.co",
        InputValueCategory.BOUNDARY_MAX: f"{'a' * 240}@example.com",
        InputValueCategory.EMPTY_STRING: "",
        InputValueCategory.TYPE_MISMATCH: "not-an-email",
    },
    "number": {
        InputValueCategory.VALID_TYPICAL: "42",
        InputValueCategory.BOUNDARY_MIN: "0",
        InputValueCategory.BOUNDARY_MAX: "999999999",
        InputValueCategory.EMPTY_STRING: "",
        InputValueCategory.TYPE_MISMATCH: "not-a-number",
    },
    "tel": {
        # Digits only: many validators (Spring's @Digits, HTML patterns) reject "+" and dashes.
        InputValueCategory.VALID_TYPICAL: "2025550143",
        InputValueCategory.BOUNDARY_MIN: "1",
        InputValueCategory.BOUNDARY_MAX: "1" * 32,
        InputValueCategory.EMPTY_STRING: "",
        InputValueCategory.TYPE_MISMATCH: "not-a-phone-number!!",
    },
    "url": {
        InputValueCategory.VALID_TYPICAL: "https://example.com/page",
        InputValueCategory.BOUNDARY_MIN: "http://a.co",
        InputValueCategory.BOUNDARY_MAX: f"https://example.com/{'a' * 512}",
        InputValueCategory.EMPTY_STRING: "",
        InputValueCategory.TYPE_MISMATCH: "not a url",
    },
    "password": {
        InputValueCategory.VALID_TYPICAL: "CorrectHorseBattery9!",
        InputValueCategory.BOUNDARY_MIN: "a",
        InputValueCategory.BOUNDARY_MAX: _LONG_STRING,
        InputValueCategory.EMPTY_STRING: "",
        InputValueCategory.TYPE_MISMATCH: " ",
    },
    "date": {
        InputValueCategory.VALID_TYPICAL: "2026-06-15",
        InputValueCategory.BOUNDARY_MIN: "0001-01-01",
        InputValueCategory.BOUNDARY_MAX: "9999-12-31",
        InputValueCategory.EMPTY_STRING: "",
        InputValueCategory.TYPE_MISMATCH: "not-a-date",
    },
    "search": {
        InputValueCategory.VALID_TYPICAL: "search query",
        InputValueCategory.BOUNDARY_MIN: "a",
        InputValueCategory.BOUNDARY_MAX: _LONG_STRING,
        InputValueCategory.EMPTY_STRING: "",
        InputValueCategory.TYPE_MISMATCH: "\t\n",
    },
    "textarea": {
        InputValueCategory.VALID_TYPICAL: "This is a typical multi-line\ncomment used for functional testing.",
        InputValueCategory.BOUNDARY_MIN: "a",
        InputValueCategory.BOUNDARY_MAX: _LONG_STRING * 4,
        InputValueCategory.EMPTY_STRING: "",
        InputValueCategory.TYPE_MISMATCH: "12345",
    },
}

_BY_TYPE["postcode"] = {
    InputValueCategory.VALID_TYPICAL: "10001",
    InputValueCategory.BOUNDARY_MIN: "1",
    InputValueCategory.BOUNDARY_MAX: "9" * 16,
    InputValueCategory.EMPTY_STRING: "",
    InputValueCategory.TYPE_MISMATCH: "ABCDE",
}

_DEFAULT_TYPE = "text"

#: What a plain text field holds, read from its name, id and label, in priority order. Many
#: forms use `type="text"` for phone numbers, emails and dates and then validate them, so a
#: generic value is refused and the form never goes through.
_MEANINGS = (
    ("tel", re.compile(r"phone|\btel\b|telephone|mobile|\bcell\b")),
    ("email", re.compile(r"e-?mail")),
    ("postcode", re.compile(r"\bzip\b|zip_?code|post_?code|postal|pin_?code")),
    ("date", re.compile(r"\bdate\b|birth|\bdob\b|_date\b|date_")),
    ("url", re.compile(r"\burl\b|website|homepage")),
    ("number", re.compile(r"\b(age|qty|quantity|amount|price|count|number)\b")),
)


def field_type(html_input_type: str, hints: str) -> str:
    """The value set for a field: its HTML type, or for a plain text field what its name, id
    and label say it holds (`telephone` -> tel, `birthDate` -> date)."""
    if (html_input_type or "text").lower() != "text":
        return html_input_type
    words = re.sub(r"([a-z])([A-Z])", r"\1 \2", hints or "").lower()
    return next((kind for kind, pattern in _MEANINGS if pattern.search(words)), html_input_type or "text")


def credential_kind(html_input_type: str, hints: str) -> str | None:
    """Which part of an account a field holds - "password", "email" or "username" - read from
    its HTML type and its name, id and label (`credentials[username]`, `form-username`,
    `user_email`), or None for any other field."""
    if (html_input_type or "").lower() == "password":
        return "password"
    words = set(re.findall(r"[a-z]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", hints or "").lower()))
    if words & {"password", "passwd"}:
        return "password"
    if (html_input_type or "").lower() == "email" or field_type("text", hints) == "email":
        return "email"
    if words & {"username", "login", "loginname"} or {"user", "name"} <= words:
        return "username"
    return None


def credentials_from_login(steps: list[dict]) -> tuple[tuple[str, str], ...]:
    """The account a sign-in macro logs in with, as (kind, value) pairs for
    `RegistryOptions.credentials`: each TYPE step's literal value, by the field it targets."""
    found: dict[str, str] = {}
    for step in steps:
        value = (step.get("params") or {}).get("value")
        if step.get("type") != "TYPE" or value is None:
            continue
        hints = " ".join(str(step.get(key) or "") for key in ("selector", "id", "text"))
        kind = credential_kind("", hints)
        if kind is not None:
            found.setdefault(kind, value)
    return tuple(sorted(found.items()))


def generate_input_value(html_input_type: str, category: InputValueCategory) -> str:
    """Return a deterministic test value for an HTML input type + value category.

    Unknown/custom input types (e.g. `color`, `range`) fall back to the generic
    "text" value set, since Playwright's `.fill()` accepts a plain string for
    virtually every editable input regardless of its `type` attribute.
    """
    values = _BY_TYPE.get(html_input_type.lower(), _BY_TYPE[_DEFAULT_TYPE])
    return values[category]
