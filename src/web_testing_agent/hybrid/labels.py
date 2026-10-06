"""What a page is and what each of its controls does, from closed vocabularies.

The hybrid tester's state is built from these labels instead of raw text and layout (plan
§3 step 3). One page snapshot is labelled once: deterministic facts first (a field whose
`type` attribute already says `email` or `date` needs no model), then one call to the local
model for the rest, with the answer constrained by a JSON schema built for exactly this
page's controls. Answers are cached by content, like the progress judgments, so a page
costs one model call however many times any run reaches it.

The model never decides what a fault is; these labels only say where the tester is and
what it could do there.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from ..envs.types import ActionSpec, ActionType
from ..reward.progress import DEFAULT_HOST, DEFAULT_MODEL, JudgmentCache, page_digest
from ..utils.logging import get_logger

logger = get_logger(__name__)

LABEL_VERSION = "labels-v1"

PAGE_KINDS: tuple[str, ...] = (
    "home",
    "list",
    "detail",
    "form",
    "review",
    "outcome",
    "info",
    "login",
    "error",
    "other",
)
CONTROL_ROLES: tuple[str, ...] = (
    "commit",
    "advance",
    "save",
    "edit",
    "add",
    "remove",
    "open",
    "navigate",
    "back",
    "search_or_filter",
    "help",
    "other",
)
FIELD_MEANINGS: tuple[str, ...] = (
    "email",
    "name",
    "date",
    "number",
    "money",
    "phone",
    "password",
    "address",
    "free_text",
    "choice",
    "other",
)

#: An input's declared type, where it settles the field's meaning without a model.
_TYPE_MEANING = {
    "email": "email",
    "date": "date",
    "datetime-local": "date",
    "month": "date",
    "week": "date",
    "time": "date",
    "number": "number",
    "range": "number",
    "tel": "phone",
    "password": "password",
}
_CLICKABLE = (ActionType.CLICK, ActionType.RAPID_CLICK)
_FIELDS = (ActionType.TYPE, ActionType.SELECT)
#: Controls described to the model per page; the rest are labelled "other" and flagged.
MAX_CONTROLS = 40

SYSTEM_PROMPT = f"""\
You label a web page for an automated software tester. You are shown a text snapshot of the
page as rendered, then a numbered list of its interactive controls. Say what kind of page it
is and what each control does, using only the listed words.

Page kinds:
- home: the application's start or landing page
- list: a list, table, catalogue, task list or search results to choose from
- detail: one item or record shown in detail
- form: a page or dialog whose main purpose is entering or choosing values
- review: a summary of entered values awaiting confirmation
- outcome: the result of a completed action (confirmation, receipt, success or failure notice)
- info: help, about or other purely informational content
- login: sign-in or sign-up
- error: an error page
- other: none of the above

Control roles (c-numbered controls; buttons and links):
- commit: performs the final, irreversible action of a task (place order, send, submit for good)
- advance: moves to the next step of a task without being final
- save: stores the values entered in the current step
- edit: returns to change something already entered
- add / remove: adds an item to, or removes one from, a selection, basket or list
- open: starts a task or opens a section, item or dialog
- navigate: goes to another page or section that is not part of a task's steps
- back: goes back, cancels or closes
- search_or_filter: searches, filters or sorts
- help: opens help or documentation
- other: anything else, including controls whose purpose cannot be told

Field meanings (f-numbered fields): {", ".join(FIELD_MEANINGS)}. Use "choice" for a field
that picks among fixed options when no more specific meaning applies.

Judge each control by its label, its field name, the page it is on and the task the page
belongs to. Answer only with the requested JSON."""


# -- the page's controls, as the model sees them ------------------------------------------------


class _InputTypes(HTMLParser):
    """`id` / `name` -> declared input type, and the set of textarea ids and names."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.types: dict[str, str] = {}
        self.textareas: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k: (v or "") for k, v in attrs}
        keys = [k for k in (values.get("id"), values.get("name")) if k]
        if tag == "input":
            for key in keys:
                self.types[key] = values.get("type", "text").lower()
        elif tag == "textarea":
            self.textareas.update(keys)


def control_identity(spec: ActionSpec) -> str:
    """The control an action acts on: several actions (click, rapid click, each typed value
    category, each select option) share one control and one label."""
    return spec.selector or f"{spec.action_type.value}:{spec.element_id}"


def _selector_id(spec: ActionSpec) -> str:
    selector = spec.selector or ""
    return selector[5:-2] if selector.startswith('[id="') and selector.endswith('"]') else ""


@dataclass
class Control:
    key: str  # "c3" / "f1": the name the model answers under
    identity: str
    kind: str  # "clickable" | "field"
    line: str  # how it is described to the model
    deterministic: str = ""  # a field meaning settled without the model


def describe_controls(specs: list[ActionSpec], html: str) -> list[Control]:
    """One entry per distinct control, in the registry's (DOM) order."""
    parser = _InputTypes()
    with contextlib.suppress(Exception):  # malformed markup only loses the type hints
        parser.feed(html or "")
    seen: dict[str, Control] = {}
    options: dict[str, list[str]] = {}
    clicks = fields = 0
    for spec in specs:
        identity = control_identity(spec)
        params = spec.params or {}
        if spec.action_type is ActionType.SELECT and "option" in params:
            options.setdefault(identity, []).append(str(params["option"]))
        if identity in seen or spec.action_type not in _CLICKABLE + _FIELDS:
            continue
        label = (spec.element_id or "").strip()
        if spec.action_type in _CLICKABLE:
            clicks += 1
            href = str(params.get("href") or "")
            kind = "link" if params.get("navigational") or href else "button"
            line = f'{kind} "{label}"' + (f" -> {href}" if href else "")
            seen[identity] = Control(f"c{clicks}", identity, "clickable", line)
        else:
            fields += 1
            dom = _selector_id(spec)
            declared = parser.types.get(dom) or parser.types.get(label) or ""
            shown = str(params.get("label_text") or "").strip()
            if spec.action_type is ActionType.SELECT:
                widget = "select"
            elif dom in parser.textareas or label in parser.textareas:
                widget = "textarea"
            else:
                widget = f"input type {declared or 'text'}"
            line = f'field "{shown or label}" (name {label}; {widget})'
            meaning = _TYPE_MEANING.get(declared, "")
            if widget == "textarea":
                meaning = "free_text"
            seen[identity] = Control(f"f{fields}", identity, "field", line, meaning)
    for identity, values in options.items():
        control = seen.get(identity)
        if control is not None:
            control.line += "; options: " + ", ".join(dict.fromkeys(values))
    return list(seen.values())


# -- labels and their cache --------------------------------------------------------------------


@dataclass
class PageLabels:
    key: str
    kind: str
    #: control identity -> role (clickables) or meaning (fields)
    roles: dict[str, str] = field(default_factory=dict)
    meanings: dict[str, str] = field(default_factory=dict)
    #: identities labelled "other" because the page had more than MAX_CONTROLS controls
    unlabelled: list[str] = field(default_factory=list)
    #: identities whose label came from the markup, not the model
    deterministic: list[str] = field(default_factory=list)
    model: str = ""
    seconds: float = 0.0
    source: str = "llm"

    def role(self, spec: ActionSpec) -> str:
        identity = control_identity(spec)
        return self.roles.get(identity) or self.meanings.get(identity) or ""

    def to_row(self) -> dict:
        row = asdict(self)
        row.pop("source", None)
        return row


class LabelCache(JudgmentCache):
    """The progress cache's shared, append-only store, holding label rows instead."""

    def _files(self) -> list[Path]:
        return sorted(self.directory.glob("labels*.jsonl")) if self.directory.is_dir() else []

    def get_labels(self, key: str) -> PageLabels | None:
        row = self._rows.get(key)
        if row is None:
            self.reload()
            row = self._rows.get(key)
        if row is None:
            return None
        return PageLabels(source="cache", **{k: v for k, v in row.items() if k != "source"})

    def put_labels(self, labels: PageLabels) -> None:
        row = labels.to_row()
        self._rows[labels.key] = row
        self.directory.mkdir(parents=True, exist_ok=True)
        shard = self.directory / f"labels.shard.{os.getpid()}.jsonl"
        with shard.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


@dataclass
class LabellerStats:
    lookups: int = 0
    cache_hits: int = 0
    llm_calls: int = 0
    failures: int = 0
    llm_seconds: float = 0.0

    def to_dict(self) -> dict:
        data = asdict(self)
        data["llm_seconds"] = round(self.llm_seconds, 2)
        return data


class SemanticLabeller:
    """Labels for a page snapshot, from the cache or the model; None when unavailable."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        host: str | None = None,
        cache: LabelCache | None = None,
        seed: int = 7,
        timeout_s: float = 180.0,
    ) -> None:
        self.model = model
        self.host = (host or DEFAULT_HOST).rstrip("/")
        if not self.host.startswith("http"):
            self.host = "http://" + self.host
        self.cache = cache if cache is not None else LabelCache()
        self.seed = seed
        self.timeout_s = timeout_s
        self.stats = LabellerStats()

    def config(self) -> dict:
        return {
            "model": self.model,
            "label_version": LABEL_VERSION,
            "seed": self.seed,
            "temperature": 0.0,
            "max_controls": MAX_CONTROLS,
        }

    def label(
        self,
        url: str,
        html: str,
        specs: list[ActionSpec],
        *,
        context: str = "",
        visible_text: str | None = None,
    ) -> PageLabels | None:
        digest = page_digest(url, html, visible_text)
        controls = describe_controls(specs, html)
        listing = "\n".join(f"{c.key}: {c.line}" for c in controls)
        key = hashlib.sha256(
            "\x00".join((LABEL_VERSION, self.model, context, digest, listing)).encode(
                "utf-8", "replace"
            )
        ).hexdigest()[:40]
        self.stats.lookups += 1
        cached = self.cache.get_labels(key)
        if cached is not None:
            self.stats.cache_hits += 1
            return cached

        asked = [c for c in controls if not c.deterministic][:MAX_CONTROLS]
        overflow = [c for c in controls if not c.deterministic][MAX_CONTROLS:]
        started = time.monotonic()
        answer = self._ask(digest, context, asked)
        elapsed = time.monotonic() - started
        self.stats.llm_calls += 1
        self.stats.llm_seconds += elapsed
        if answer is None:
            self.stats.failures += 1
            return None

        labels = PageLabels(
            key=key, kind=answer["page_kind"], model=self.model, seconds=round(elapsed, 3)
        )
        given = answer.get("controls") or {}
        for control in controls:
            if control.deterministic:
                labels.meanings[control.identity] = control.deterministic
                labels.deterministic.append(control.identity)
            elif control in overflow:
                target = labels.roles if control.kind == "clickable" else labels.meanings
                target[control.identity] = "other"
                labels.unlabelled.append(control.identity)
            elif control.kind == "clickable":
                labels.roles[control.identity] = given.get(control.key, "other")
            else:
                labels.meanings[control.identity] = given.get(control.key, "other")
        self.cache.put_labels(labels)
        return labels

    def _schema(self, asked: list[Control]) -> dict:
        properties = {
            c.key: {
                "type": "string",
                "enum": list(CONTROL_ROLES if c.kind == "clickable" else FIELD_MEANINGS),
            }
            for c in asked
        }
        return {
            "type": "object",
            "required": ["page_kind", "controls"],
            "properties": {
                "page_kind": {"type": "string", "enum": list(PAGE_KINDS)},
                "controls": {
                    "type": "object",
                    "required": [c.key for c in asked],
                    "properties": properties,
                },
            },
        }

    def _ask(self, digest: str, context: str, asked: list[Control]) -> dict | None:
        listing = "\n".join(f"{c.key}: {c.line}" for c in asked) or "(none)"
        user = (
            (f"APPLICATION: {context}\n\n" if context else "")
            + digest
            + "\n\nCONTROLS TO LABEL:\n"
            + listing
        )
        request = {
            "model": self.model,
            "stream": False,
            "format": self._schema(asked),
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            "options": {
                "temperature": 0.0,
                "seed": self.seed,
                "num_ctx": 6144,
                "num_predict": 48 + 14 * len(asked),
            },
        }
        http_request = urllib.request.Request(  # noqa: S310 - host is operator-configured
            self.host + "/api/chat",
            data=json.dumps(request).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(http_request, timeout=self.timeout_s) as response:  # noqa: S310
                payload = json.loads(response.read().decode("utf-8"))
            content = ((payload.get("message") or {}).get("content") or "").strip()
            parsed = json.loads(content[content.find("{") : content.rfind("}") + 1])
        except (urllib.error.URLError, OSError, ValueError, TypeError) as exc:
            logger.warning("page labelling failed: {}: {}", type(exc).__name__, exc)
            return None
        if parsed.get("page_kind") not in PAGE_KINDS or not isinstance(
            parsed.get("controls"), dict
        ):
            logger.warning("page labelling returned an invalid answer: {}", str(parsed)[:200])
            return None
        valid = {c.key: CONTROL_ROLES if c.kind == "clickable" else FIELD_MEANINGS for c in asked}
        parsed["controls"] = {
            k: v for k, v in parsed["controls"].items() if k in valid and v in valid[k]
        }
        return parsed
