"""The survey document: what a saved revision may contain, and how one is described.

A document is `{"title": str, "questions": [question, ...]}`; a question is
`{"id", "kind", "title", "required", "options", "rule"}`. Option values are fixed codes and a
skip rule is always "If No, skip to <a later question>", so it exists only on yes/no
questions and names its target by question id. The editor in the browser builds documents
of exactly this shape; the server refuses anything else and stores what it accepts as
canonical JSON, so two equal documents always compare equal.
"""

from __future__ import annotations

import json
import re

KINDS = ("choice", "yesno", "rating", "text")
MAX_QUESTIONS = 9
TITLE_LIMIT = 200
OPTION_LIMIT = 80
_QUESTION_ID = re.compile(r"q[1-9][0-9]{0,2}")
_OPTION_VALUE = re.compile(r"[a-z0-9-]{1,24}")


class Invalid(ValueError):
    """A document the server will not store. `key` names the vocabulary message."""

    def __init__(self, key: str, position: int = 0) -> None:
        super().__init__(key)
        self.key, self.position = key, position


def _title(value: object, key: str, position: int = 0) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Invalid(f"{key}_missing", position)
    if len(value) > TITLE_LIMIT:
        raise Invalid(f"{key}_long", position)
    return value


def _options(question: dict, position: int) -> list[dict]:
    options = question.get("options") or []
    if question["kind"] != "choice":
        if options:
            raise Invalid("error.options", position)
        return []
    if not isinstance(options, list) or not 2 <= len(options) <= 8:
        raise Invalid("error.options", position)
    clean, seen = [], set()
    for option in options:
        if not isinstance(option, dict):
            raise Invalid("error.options", position)
        value, label = option.get("value"), option.get("label")
        if not isinstance(value, str) or not _OPTION_VALUE.fullmatch(value) or value in seen:
            raise Invalid("error.options", position)
        if not isinstance(label, str) or not label.strip() or len(label) > OPTION_LIMIT:
            raise Invalid("error.options", position)
        seen.add(value)
        clean.append({"value": value, "label": label})
    return clean


def check_document(document: object) -> dict:
    """The document as it will be stored, or `Invalid` naming the first problem."""
    if not isinstance(document, dict):
        raise Invalid("error.document")
    title = _title(document.get("title"), "error.survey_title")
    questions = document.get("questions")
    if not isinstance(questions, list) or not 1 <= len(questions) <= MAX_QUESTIONS:
        raise Invalid("error.count")
    checked: list[dict] = []
    ids: list[str] = []
    for position, question in enumerate(questions, 1):
        if not isinstance(question, dict):
            raise Invalid("error.document", position)
        qid = question.get("id")
        if not isinstance(qid, str) or not _QUESTION_ID.fullmatch(qid) or qid in ids:
            raise Invalid("error.document", position)
        if question.get("kind") not in KINDS:
            raise Invalid("error.document", position)
        if not isinstance(question.get("required"), bool):
            raise Invalid("error.document", position)
        ids.append(qid)
        checked.append({"id": qid, "kind": question["kind"],
                        "title": _title(question.get("title"), "error.question_title", position),
                        "required": question["required"], "options": _options(question, position),
                        "rule": question.get("rule")})
    for position, question in enumerate(checked, 1):
        rule = question["rule"]
        if rule is None:
            continue
        later = ids[position:]
        if (question["kind"] != "yesno" or not isinstance(rule, dict) or rule.get("when") != "no"
                or rule.get("target") not in later or set(rule) != {"when", "target"}):
            raise Invalid("error.rule", position)
        question["rule"] = {"when": "no", "target": rule["target"]}
    return {"title": title, "questions": checked}


def canonical(document: dict) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def numbers(document: dict) -> tuple[int, int]:
    """(questions, required questions): what a revision's one-line summary states."""
    questions = document["questions"]
    return len(questions), sum(1 for question in questions if question["required"])
