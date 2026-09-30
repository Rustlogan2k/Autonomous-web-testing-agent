"""How an executed action becomes a replayable step, and a live step an oracle record.

**Why the oracle has its own serializer.** `agents.go_explore.to_replay_step` was written
for the archive, which stores *routes back to a state*. It deliberately drops `REFRESH`,
`BROWSER_BACK` and `BROWSER_FORWARD`, because a stored route containing a history move
goes somewhere else on the way back. That is right for a route and wrong for a fault
trace: a persistence failure is *defined* by a reload, and a trace with the reload removed
no longer contains the fault. Verifying such a candidate with the archive's serializer
replays a different action sequence, fails to reproduce, and the run is recorded as
censored when the agent had in fact found the bug.

A full trace replayed from a fresh session rebuilds the same history the original run had,
so the archive's objection does not apply here.

**Why `nth` is emitted.** The scripted matcher resolves a step by id, by selector or by a
substring of the visible label, and takes the first control that fits. Applications repeat
labels constantly -- every row of a table has an `Edit`. Without an ordinal, a trace
recorded against the third row replays against the first, and the fault either fails to
reproduce or reproduces for the wrong reason. The ordinal is computed with the matcher's
own predicate, so "the n-th control this step could mean" is one definition, not two.

**Why a failed action records no input.** `echoes_input` compares a page against what the
user entered. A `fill()` that Playwright refused -- text into a number field -- entered
nothing, and recording the attempted value would make the oracle compare the application
against an input it never received.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..envs.types import ActionSpec, ActionType
from ..evaluation.scripted import ScriptedPolicy

#: Params that identify *which* variant of an action to replay. TYPE carries both a
#: generated `value` and its `category`; the category is matched because the value is
#: regenerated from it on every page.
_DISCRIMINATING_PARAMS: dict[ActionType, tuple[str, ...]] = {
    ActionType.TYPE: ("category",),
    ActionType.SELECT: ("option",),
    ActionType.RESIZE_VIEWPORT: ("preset",),
    ActionType.SCROLL: ("direction",),
}

#: Page-independent actions: addressed by type (and params) alone.
_FIXED_TYPES = frozenset({
    ActionType.SCROLL, ActionType.RESIZE_VIEWPORT, ActionType.BROWSER_BACK,
    ActionType.BROWSER_FORWARD, ActionType.REFRESH,
})


def replay_step_of(spec: ActionSpec | None,
                   available: Sequence[ActionSpec] | None = None) -> dict | None:
    """Serialize an executed action into the scripted-step schema, or None for a no-op.

    `available` is the action set the agent chose from. When given, the step carries the
    ordinal that makes the matcher resolve back to exactly this control; without it the
    step means "the first control that fits", which is only safe on a page with no
    repeated labels.
    """
    if spec is None or spec.action_type is ActionType.NO_OP:
        return None

    step: dict = {"type": spec.action_type.value}
    selector = spec.selector or ""
    label = (spec.element_id or "").strip()

    if spec.action_type not in _FIXED_TYPES:
        if selector.startswith('[id="') and selector.endswith('"]'):
            step["id"] = selector[5:-2]
        elif "[name=" in selector:
            # A name-addressed control: exact, and stable when the rest of the page moves.
            step["selector"] = selector
        elif label:
            step["text"] = label
        elif selector:
            step["selector"] = selector
        else:
            return None

    params = {
        key: spec.params[key]
        for key in _DISCRIMINATING_PARAMS.get(spec.action_type, ())
        if key in (spec.params or {})
    }
    if params:
        step["params"] = params

    if available is not None:
        ordinal = 0
        for other in available:
            if other is spec or other.index == spec.index:
                break
            if ScriptedPolicy.accepts(step, other):
                ordinal += 1
        if ordinal:
            step["nth"] = ordinal
    return step


def entered_value(spec: ActionSpec | None, exec_info: dict | None = None) -> dict:
    """`{field_label: value}` for an action that actually entered one, else `{}`.

    The label is `element_id` -- the registry's name for the control (`"quantity"`), not
    its DOM id (`"qty"`) -- so a fault's `input_field` names something a person would
    recognise and does not have to track the fixture's markup.

    Always a dict, never None: `build_trajectory` falls back to parsing the replay step
    when no explicit record is supplied, and that fallback can only see the value
    *category*. An empty dict says "nothing was entered" authoritatively.
    """
    if spec is None or spec.action_type not in (ActionType.TYPE, ActionType.SELECT):
        return {}
    if exec_info is not None and not exec_info.get("success", False):
        return {}
    params = spec.params or {}
    value = params.get("value", params.get("option"))
    label = (spec.element_id or "").strip()
    if not label or value is None:
        return {}
    return {label: str(value)}


def observed_record(info: dict, executed: ActionSpec | None = None,
                    available: Sequence[ActionSpec] | None = None) -> dict:
    """One step as the oracle records it, built from the env's `info`.

    Used by the replay runner *and* by the exploration harness, so a trajectory observed
    live and the same trajectory observed on replay are assembled by the same code. `info`
    is the dict returned by `reset()` (then `executed` is None) or by `step()`.
    """
    page = info.get("page") or {}
    return {
        "url": page.get("url", ""),
        "html": page.get("html", ""),
        "action": replay_step_of(executed, available),
        "entered": entered_value(executed, info.get("exec_info")),
    }
