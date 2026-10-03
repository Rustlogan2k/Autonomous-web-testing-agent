"""The state and action representation every arm of the transfer experiment is scored on.

**One representation, two scoring rules.** The learned agent scores actions with a network
over these features; the search baseline scores them with a fixed, hand-written weight
vector over *the same* features. The earlier search-versus-learning comparison in this
project was confounded precisely because the two sides read different things -- the search
arm was blind to control labels and the learners never received visit counts. Building one
extractor and handing its output to both removes that confound by construction: whatever
separates the arms afterwards is how they score, not what they were shown.

**Nothing here identifies an application.** No URL, DOM id, selector, slot index or hashed
string reaches a feature. Identity is used only to *count* -- how often this action has
been taken here -- and a count means the same thing on a site the agent has never seen.
That rule is inherited from `agents.semantic_action_features` (v2), which this module
extends rather than replaces; v2's own blocks are reused unchanged.

**What is added to v2, and why each addition is about transfer.**

* *The value category of a TYPE action* as a one-hot, not only an ordinal. "Leave it
  empty" and "enter a boundary value" are different testing moves, and a constraint-bypass
  fault is reached by the first while a typical-value walk never takes it.
* *The state of the control being acted on*: is the field already filled, does it already
  hold the value this action would enter, is the toggle already on. Without these the
  agent cannot tell a useful TYPE from a redundant one, on any application.
* *What happened the last times this action was tried here*: did the page change, did it
  lead somewhere new. This is the agent's memory of the run, expressed as rates.
* *Label meaning on the frozen commit/abandon axis* (the A2 anchor score) and the frozen
  word-list indicator (A1), as two scalars. The hand-priority baselines score with exactly
  these; giving them to the learner as inputs keeps information access equal in both
  directions.

**The state vector is about the page as a workspace**, not about which page it is: how
many controls of each kind are on offer, how much of the form is filled in, how much of
what is on offer has already been tried, plus the env's own episode context.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from html.parser import HTMLParser

import numpy as np

from ..agents.label_priors import AnchorProjectionPrior, a1_lexical_score
from ..agents.semantic_action_features import (
    _FIXED_TYPES,
    _FORM_TYPES,
    _VALUE_ORDINAL,
    action_identity,
    label_of,
)
from ..agents.semantic_labels import LABEL_DIM
from ..envs.types import EPISODE_CONTEXT_DIM, MAX_ACTIONS, ActionSpec, ActionType

FEATURE_VERSION = "v3.1.2026-10-03"

# -- action blocks -----------------------------------------------------------------------

_ACTION_TYPES: tuple[ActionType, ...] = tuple(ActionType)
TYPE_DIM = len(_ACTION_TYPES)

#: v2's roles plus `toggle`. A checkbox and a plain button are both CLICKs with no href,
#: and v2 could not tell them apart; they are different controls on every application.
ROLES: tuple[str, ...] = (
    "link", "submit_control", "button", "toggle", "select", "text_entry",
    "rapid_click", "navigation_move", "page_reload", "viewport", "no_op",
)
ROLE_DIM = len(ROLES)
_ROLE_INDEX = {role: index for index, role in enumerate(ROLES)}

FLAG_FIELDS: tuple[str, ...] = (
    "navigational", "in_viewport", "opens_new_tab", "is_newly_revealed",
    "has_href", "is_fixed_action", "is_form_control", "is_repeatable_probe",
)
FLAG_DIM = len(FLAG_FIELDS)

VALUE_CATEGORIES: tuple[str, ...] = (
    "valid_typical", "boundary_min", "boundary_max", "empty_string", "type_mismatch",
)
CATEGORY_DIM = len(VALUE_CATEGORIES)

NUMERIC_FIELDS: tuple[str, ...] = (
    "value_ordinal",        # TYPE value plausibility, v2's scale
    "label_length",         # log-scaled
    "taken_here",           # log count of this action at this state, whole run
    "taken_this_episode",   # log count of this action at this state, this episode
    "taken_anywhere",       # log count of this action on any state, whole run
    "tried_here",           # 1 when it has been taken here at all
    "changed_state_rate",   # share of tries here that changed the page; 0.5 when untried
    "reached_new_rate",     # share of tries here that reached an episode-new state
    "anchor_commit_score",  # frozen A2 axis: commit-like minus abandon-like
    "lexical_commit_score", # frozen A1 indicator in {-1, 0, +1}
    "field_is_filled",      # the control this acts on currently holds a value
    "value_already_set",    # ...and it is the very value this action would enter
    "toggle_is_on",         # the checkbox/radio this acts on is currently checked
)
NUMERIC_DIM = len(NUMERIC_FIELDS)

ACTION_DIM = TYPE_DIM + LABEL_DIM + ROLE_DIM + FLAG_DIM + CATEGORY_DIM + NUMERIC_DIM

TYPE_SLICE = slice(0, TYPE_DIM)
LABEL_SLICE = slice(TYPE_SLICE.stop, TYPE_SLICE.stop + LABEL_DIM)
ROLE_SLICE = slice(LABEL_SLICE.stop, LABEL_SLICE.stop + ROLE_DIM)
FLAG_SLICE = slice(ROLE_SLICE.stop, ROLE_SLICE.stop + FLAG_DIM)
CATEGORY_SLICE = slice(FLAG_SLICE.stop, FLAG_SLICE.stop + CATEGORY_DIM)
NUMERIC_SLICE = slice(CATEGORY_SLICE.stop, CATEGORY_SLICE.stop + NUMERIC_DIM)


def numeric_index(name: str) -> int:
    """Column of a named numeric feature within the full action vector."""
    return NUMERIC_SLICE.start + NUMERIC_FIELDS.index(name)


def flag_index(name: str) -> int:
    return FLAG_SLICE.start + FLAG_FIELDS.index(name)


# -- state block -------------------------------------------------------------------------

STATE_FIELDS: tuple[str, ...] = (
    *(f"context_{i}" for i in range(EPISODE_CONTEXT_DIM)),
    "links", "submit_controls", "buttons", "toggles", "selects", "text_entries",
    "action_count", "untried_here_fraction", "untried_episode_fraction",
    "newly_revealed_count", "text_field_count", "fields_filled_fraction",
    "toggles_on_fraction", "steps_since_page_changed",
    # Episode history the LLM reward terms depend on; written by the agent from its reward
    # composer (zero for an agent without the LLM term). See `RewardComposer.episode_state`.
    "llm_best_progress_this_episode", "llm_outcomes_paid_this_episode",
)
STATE_DIM = len(STATE_FIELDS)


# -- reading form state out of the observed markup ------------------------------------------


@dataclass(slots=True)
class _Control:
    kind: str           # "text" | "toggle" | "select"
    value: str = ""
    checked: bool = False
    hidden: bool = False


class _FormStateParser(HTMLParser):
    """Current values of form controls, by id and by name.

    Reads the markup the env already captured, in which live values have been projected
    into attributes (`base_env._SERIALIZE_JS`), so this needs no browser round-trip.
    """

    _TEXTLESS = frozenset({"hidden", "file", "button", "submit", "image", "reset"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.by_id: dict[str, _Control] = {}
        self.by_name: dict[str, _Control] = {}
        self.controls: list[_Control] = []
        self._select: _Control | None = None
        self._textarea: _Control | None = None
        self._buffer: list[str] = []

    def _register(self, attributes: dict[str, str | None], control: _Control) -> None:
        control.hidden = attributes.get("data-hidden") == "true"
        self.controls.append(control)
        identifier, name = attributes.get("id"), attributes.get("name")
        if identifier:
            self.by_id.setdefault(identifier, control)
        if name:
            self.by_name.setdefault(name, control)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "input":
            kind = (attributes.get("type") or "text").lower()
            if kind in self._TEXTLESS:
                return
            if kind in ("checkbox", "radio"):
                self._register(attributes, _Control("toggle", checked="checked" in attributes))
            else:
                self._register(attributes, _Control("text", value=attributes.get("value") or ""))
        elif tag == "textarea":
            self._textarea = _Control("text")
            self._buffer = []
            self._register(attributes, self._textarea)
        elif tag == "select":
            self._select = _Control("select")
            self._register(attributes, self._select)
        elif tag == "option" and self._select is not None and "selected" in attributes:
            self._select.value = attributes.get("value") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "textarea" and self._textarea is not None:
            self._textarea.value = "".join(self._buffer)
            self._textarea = None
        elif tag == "select":
            self._select = None

    def handle_data(self, data: str) -> None:
        if self._textarea is not None:
            self._buffer.append(data)


def read_form_state(html: str) -> _FormStateParser:
    parser = _FormStateParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:  # noqa: BLE001 - malformed markup degrades the features, never the run
        pass
    return parser


def _control_for(spec: ActionSpec, form: _FormStateParser) -> _Control | None:
    selector = spec.selector or ""
    if selector.startswith('[id="') and selector.endswith('"]'):
        return form.by_id.get(selector[5:-2])
    marker = '[name="'
    if marker in selector and selector.endswith('"]'):
        return form.by_name.get(selector[selector.index(marker) + len(marker):-2])
    return None


def role_of(spec: ActionSpec, control: _Control | None = None) -> str:
    """Which of `ROLES` this action is."""
    action_type = spec.action_type
    params = spec.params or {}
    if action_type is ActionType.CLICK:
        if params.get("href"):
            return "link"
        if control is not None and control.kind == "toggle":
            return "toggle"
        # The registry describes a checkbox/radio click as "toggle <name>"; that survives
        # when the control could not be matched back to the markup.
        if (spec.description or "").startswith("toggle "):
            return "toggle"
        return "submit_control" if params.get("navigational") else "button"
    if action_type is ActionType.SELECT:
        return "select"
    if action_type is ActionType.TYPE:
        return "text_entry"
    if action_type is ActionType.RAPID_CLICK:
        return "rapid_click"
    if action_type in (ActionType.BROWSER_BACK, ActionType.BROWSER_FORWARD):
        return "navigation_move"
    if action_type is ActionType.REFRESH:
        return "page_reload"
    if action_type in (ActionType.SCROLL, ActionType.RESIZE_VIEWPORT):
        return "viewport"
    return "no_op"


def semantic_label(spec: ActionSpec) -> str:
    """The text whose meaning is embedded for this action.

    For a form control that is the label a person reads beside it, when the registry
    captured one -- `How many nights?` rather than the DOM name `n_nights`. Otherwise it
    is v2's `label_of`, which already refuses to embed a selector.
    """
    text = str((spec.params or {}).get("label_text") or "").strip()
    return text or label_of(spec)


# -- the extractor ---------------------------------------------------------------------------


@dataclass(slots=True)
class Encoded:
    """One observation, as the policy sees it."""

    state: np.ndarray                 # (STATE_DIM,)
    actions: np.ndarray               # (n_valid, ACTION_DIM)
    state_key: str = ""
    #: `actions` as float16, filled in on first use by the replay path. An observation is
    #: `s'` of one transition and `s` of the next, so both refer to this one array.
    compact: np.ndarray | None = None

    @property
    def count(self) -> int:
        return int(self.actions.shape[0])

    def compact_actions(self) -> np.ndarray:
        if self.compact is None:
            self.compact = self.actions.astype(np.float16)
        return self.compact


@dataclass
class FeatureExtractor:
    """Builds `Encoded` from the env's `info`, and keeps the run's action memory.

    Call `begin_episode()` at each reset, `encode(info)` to observe, and
    `record(before, spec, after)` once a step has been taken. `record` is what keeps the
    count and outcome features live; a caller that forgets it silently gives its policy a
    representation in which nothing has ever been tried, which is exactly the defect the
    earlier learned arms had.
    """

    labels: object = None
    anchor: AnchorProjectionPrior | None = None
    _run: dict = field(default_factory=dict, init=False)
    _episode: dict = field(default_factory=dict, init=False)
    _anywhere: dict = field(default_factory=dict, init=False)
    _outcomes: dict = field(default_factory=dict, init=False)
    _since_change: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if self.labels is None:
            from .label_store import StoredLabelEncoder

            self.labels = StoredLabelEncoder()
        if self.anchor is None:
            self.anchor = AnchorProjectionPrior(encoder=self.labels)

    # -- memory -----------------------------------------------------------------------

    def begin_episode(self) -> None:
        self._episode = {}
        self._since_change = 0

    def record(self, before: dict, spec: ActionSpec | None, after: dict) -> None:
        """Note that `spec` was taken in the state `before` described, and what it did."""
        if spec is None:
            return
        state_key = before.get("state_key", "")
        identity = action_identity(spec)
        key = (state_key, identity)
        self._run[key] = self._run.get(key, 0) + 1
        self._episode[key] = self._episode.get(key, 0) + 1
        self._anywhere[identity] = self._anywhere.get(identity, 0) + 1

        changed = after.get("state_key", "") != state_key
        context = after.get("episode_context")
        reached_new = bool(context is not None and len(context) > 2 and context[2] > 0.5)
        tries, changes, news = self._outcomes.get(key, (0, 0, 0))
        self._outcomes[key] = (tries + 1, changes + int(changed), news + int(reached_new))
        self._since_change = 0 if changed else self._since_change + 1

    def taken_here(self, state_key: str, spec: ActionSpec) -> int:
        return self._run.get((state_key, action_identity(spec)), 0)

    # -- encoding ---------------------------------------------------------------------

    def encode(self, info: dict) -> Encoded:
        specs: list[ActionSpec] = list(info.get("action_specs") or [])[:MAX_ACTIONS]
        state_key = info.get("state_key", "")
        page = info.get("page") or {}
        form = read_form_state(page.get("html", ""))
        revealed = {tuple(entry) for entry in (info.get("newly_revealed") or [])}

        block = np.zeros((len(specs), ACTION_DIM), dtype=np.float32)
        if specs:
            texts = [semantic_label(spec) for spec in specs]
            vectors = self.labels.encode_batch(texts)
            for row, (spec, text, vector) in enumerate(zip(specs, texts, vectors, strict=True)):
                block[row] = self._encode_action(
                    spec, text, vector, state_key, revealed, _control_for(spec, form))

        return Encoded(state=self._encode_state(info, specs, block, form, revealed),
                       actions=block, state_key=state_key)

    def _encode_action(self, spec: ActionSpec, text: str, label_vector: np.ndarray,
                       state_key: str, revealed: set, control: _Control | None) -> np.ndarray:
        identity = action_identity(spec)
        params = spec.params or {}
        vector = np.zeros(ACTION_DIM, dtype=np.float32)

        vector[TYPE_SLICE.start + _ACTION_TYPES.index(spec.action_type)] = 1.0
        vector[LABEL_SLICE] = label_vector
        vector[ROLE_SLICE.start + _ROLE_INDEX[role_of(spec, control)]] = 1.0
        vector[FLAG_SLICE] = (
            1.0 if params.get("navigational") else 0.0,
            1.0 if params.get("in_viewport") else 0.0,
            1.0 if params.get("opens_new_tab") else 0.0,
            1.0 if identity in revealed else 0.0,
            1.0 if params.get("href") else 0.0,
            1.0 if spec.action_type in _FIXED_TYPES else 0.0,
            1.0 if spec.action_type in _FORM_TYPES else 0.0,
            1.0 if spec.action_type is ActionType.RAPID_CLICK else 0.0,
        )
        category = str(params.get("category", ""))
        if category in VALUE_CATEGORIES:
            vector[CATEGORY_SLICE.start + VALUE_CATEGORIES.index(category)] = 1.0

        key = (state_key, identity)
        run_count = self._run.get(key, 0)
        tries, changes, news = self._outcomes.get(key, (0, 0, 0))
        intended = params.get("value", params.get("option"))
        is_entry = spec.action_type in _FORM_TYPES
        # Fixed actions carry a generated description ("scroll down"), not a control
        # label, and have no place on a commit/abandon axis.
        has_label = spec.action_type not in _FIXED_TYPES and bool(text)
        vector[NUMERIC_SLICE] = (
            _VALUE_ORDINAL.get(category, 0.0),
            min(math.log1p(len(text)) / 4.0, 1.0),
            min(math.log1p(run_count) / 4.0, 1.0),
            min(math.log1p(self._episode.get(key, 0)) / 3.0, 1.0),
            min(math.log1p(self._anywhere.get(identity, 0)) / 5.0, 1.0),
            1.0 if run_count else 0.0,
            changes / tries if tries else 0.5,
            news / tries if tries else 0.5,
            float(np.clip(self.anchor(text), -1.0, 1.0)) if has_label else 0.0,
            a1_lexical_score(text) if has_label else 0.0,
            1.0 if (control is not None and is_entry and control.value) else 0.0,
            1.0 if (control is not None and is_entry and intended is not None
                    and control.value == str(intended)) else 0.0,
            1.0 if (control is not None and control.kind == "toggle" and control.checked) else 0.0,
        )
        return vector

    def _encode_state(self, info: dict, specs: list[ActionSpec], block: np.ndarray,
                      form: _FormStateParser, revealed: set) -> np.ndarray:
        context = np.asarray(
            info.get("episode_context", np.zeros(EPISODE_CONTEXT_DIM)), dtype=np.float32)
        if context.shape != (EPISODE_CONTEXT_DIM,):
            context = np.zeros(EPISODE_CONTEXT_DIM, dtype=np.float32)

        def role_share(*names: str) -> float:
            if not specs:
                return 0.0
            columns = [ROLE_SLICE.start + _ROLE_INDEX[name] for name in names]
            return float(min(block[:, columns].sum() / 20.0, 1.0))

        count = len(specs)
        tried_run = float(block[:, numeric_index("tried_here")].sum()) if count else 0.0
        tried_episode = float(
            (block[:, numeric_index("taken_this_episode")] > 0).sum()) if count else 0.0
        visible = [c for c in form.controls if not c.hidden]
        text_fields = [c for c in visible if c.kind in ("text", "select")]
        toggles = [c for c in visible if c.kind == "toggle"]

        tail = np.array([
            role_share("link"), role_share("submit_control"), role_share("button"),
            role_share("toggle"), role_share("select"), role_share("text_entry"),
            min(count / MAX_ACTIONS, 1.0),
            1.0 - tried_run / count if count else 0.0,
            1.0 - tried_episode / count if count else 0.0,
            min(len(revealed) / 5.0, 1.0),
            min(len(text_fields) / 10.0, 1.0),
            sum(1 for c in text_fields if c.value) / len(text_fields) if text_fields else 0.0,
            sum(1 for c in toggles if c.checked) / len(toggles) if toggles else 0.0,
            min(self._since_change / 10.0, 1.0),
            0.0, 0.0,
        ], dtype=np.float32)
        return np.concatenate([context, tail])


def describe_layout() -> dict:
    """The layout, for run metadata and for anything that slices the vectors by name."""
    return {
        "version": FEATURE_VERSION,
        "action_dim": ACTION_DIM,
        "state_dim": STATE_DIM,
        "action_blocks": [
            {"name": "action_type", "start": TYPE_SLICE.start, "stop": TYPE_SLICE.stop,
             "fields": [t.value for t in _ACTION_TYPES]},
            {"name": "semantic_label", "start": LABEL_SLICE.start, "stop": LABEL_SLICE.stop,
             "fields": ["frozen all-MiniLM-L6-v2, PCA-projected, unit norm"]},
            {"name": "role", "start": ROLE_SLICE.start, "stop": ROLE_SLICE.stop,
             "fields": list(ROLES)},
            {"name": "flags", "start": FLAG_SLICE.start, "stop": FLAG_SLICE.stop,
             "fields": list(FLAG_FIELDS)},
            {"name": "value_category", "start": CATEGORY_SLICE.start,
             "stop": CATEGORY_SLICE.stop, "fields": list(VALUE_CATEGORIES)},
            {"name": "numeric", "start": NUMERIC_SLICE.start, "stop": NUMERIC_SLICE.stop,
             "fields": list(NUMERIC_FIELDS)},
        ],
        "state_fields": list(STATE_FIELDS),
        "excluded_by_design": [
            "URL or URL hash", "DOM id or id hash", "CSS selector", "element index",
            "action slot index", "hashed label", "application identifier",
        ],
    }
