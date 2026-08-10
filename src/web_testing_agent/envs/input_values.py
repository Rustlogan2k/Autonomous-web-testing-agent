"""Deterministic test-value generation for TYPE actions, keyed by HTML input type and value category."""

from __future__ import annotations

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
        InputValueCategory.VALID_TYPICAL: "+1-202-555-0143",
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

_DEFAULT_TYPE = "text"


def generate_input_value(html_input_type: str, category: InputValueCategory) -> str:
    """Return a deterministic test value for an HTML input type + value category.

    Unknown/custom input types (e.g. `color`, `range`) fall back to the generic
    "text" value set, since Playwright's `.fill()` accepts a plain string for
    virtually every editable input regardless of its `type` attribute.
    """
    values = _BY_TYPE.get(html_input_type.lower(), _BY_TYPE[_DEFAULT_TYPE])
    return values[category]
