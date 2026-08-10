"""A policy that walks a fixed sequence of actions — used to build judge corpora.

Random exploration is the right baseline for *measuring an explorer*, but it is the
wrong instrument for measuring a *judge*: if the rollout never reaches settings.html,
never checks the box and never refreshes, the corpus contains no evidence of BUG-09
and a judge that would have caught it scores zero. That failure is indistinguishable
from a judge that cannot detect it at all.

Scripting the known repro paths separates the two questions. A scripted corpus asks
"can the judge recognize this bug when it is put in front of it?" — the upper bound.
A random corpus asks "does it hold up on ordinary traffic, without inventing bugs?"
— the false-positive rate. Both are needed; neither alone is interpretable.

Matching is by `id` against the selector the action registry generates, because
`element_id` there is a human label ("Export data"), not the DOM id, and labels change
with copy edits while ids do not.
"""

from __future__ import annotations

from ..envs.types import ActionSpec, ActionType
from ..utils.logging import get_logger

logger = get_logger(__name__)

# Slot 0 of every action space is NO_OP (see `_fixed_action_specs`), so it is the
# safe filler when a scripted step cannot be matched on the current page.
_NO_OP_INDEX = 0


class ScriptedPolicy:
    """Executes `steps` in order, one per `act()` call, then idles on NO_OP.

    A step is a dict:

        {"type": "CLICK", "id": "btn-export"}          # exact selector [id="btn-export"]
        {"type": "TYPE", "id": "age", "params": {"category": "boundary_max"}}
        {"type": "RESIZE_VIEWPORT", "params": {"preset": "mobile"}}
        {"type": "NO_OP"}                              # let the page do something on its own

    An unmatched step is retried for `max_retries` calls (a click that triggered a
    navigation may need a step to land) and then skipped with a warning. Skips are
    recorded in `misses`: a script that silently failed produces a corpus missing the
    very bug it was written to capture, so the caller must be able to see it.
    """

    def __init__(self, steps: list[dict], name: str = "scripted", max_retries: int = 2) -> None:
        self.name = name
        self.steps = list(steps)
        self.max_retries = max_retries
        self._cursor = 0
        self._retries = 0
        self.misses: list[dict] = []

    def reset(self) -> None:
        self._cursor = 0
        self._retries = 0

    @property
    def finished(self) -> bool:
        return self._cursor >= len(self.steps)

    @property
    def completed_steps(self) -> int:
        return min(self._cursor, len(self.steps)) - len(self.misses)

    def act(self, observation: dict, info: dict) -> int:
        if self.finished:
            return _NO_OP_INDEX

        step = self.steps[self._cursor]
        specs: list[ActionSpec] = info.get("action_specs") or []
        index = self._match(step, specs)

        if index is None:
            self._retries += 1
            if self._retries > self.max_retries:
                logger.warning("[{}] step {} {} never matched; skipping", self.name, self._cursor, step)
                self.misses.append({"index": self._cursor, "step": dict(step)})
                self._cursor += 1
                self._retries = 0
            return _NO_OP_INDEX

        self._cursor += 1
        self._retries = 0
        return index

    @staticmethod
    def _match(step: dict, specs: list[ActionSpec]) -> int | None:
        """Resolve a scripted step to an action index on the current page.

        Two selectors, because two kinds of target exist. `id` is exact and stable and
        is right whenever the element has one — the toy fixture gives everything an id
        precisely so its repro paths cannot drift. Real applications mostly do not:
        Gitea's explore tabs, footer links and repository tab bar carry no ids at all,
        so a repro path there has to name the control the way a person would, by its
        visible label. `text` matches case-insensitively against `element_id`, which is
        the registry's display label.

        Labels are the weaker selector — a copy edit or a locale change breaks them —
        so a script that uses `text` should be checked against `misses` after capture,
        which `capture_traces.py` reports loudly.
        """
        wanted_type = ActionType(step["type"])
        wanted_id = step.get("id")
        wanted_text = (step.get("text") or "").strip().lower()
        selector = f'[id="{wanted_id}"]' if wanted_id else None
        constraints = step.get("params") or {}
        # Which of several identical labels to take, 0-based. Real applications repeat
        # them constantly: Gitea's login page has two controls reading "Sign In" — the
        # navbar link and the form's submit button — and taking the first silently
        # navigated to the page the macro was already on, so the login "succeeded"
        # three steps out of three while leaving the session anonymous.
        skip = int(step.get("nth", 0))

        for spec in specs:
            if spec.action_type is not wanted_type:
                continue
            if selector is not None and spec.selector != selector:
                continue
            if wanted_text and wanted_text not in (spec.element_id or "").strip().lower():
                continue
            if any(spec.params.get(key) != value for key, value in constraints.items()):
                continue
            if skip:
                skip -= 1
                continue
            return spec.index
        return None
