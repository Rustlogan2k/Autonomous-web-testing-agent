"""Package: envs

The value types are re-exported eagerly; the environment classes are loaded on first
access instead.

That asymmetry is deliberate. `envs.types` is a leaf — it imports nothing from this
package — but `envs.base_env` imports `reward.functional_triggers`, while four modules
under `reward/` import `envs.types`. Eagerly importing the env classes here therefore
made `envs` and `reward` mutually dependent, and every `from ..envs.types import ...`
inside `reward/` pulled the whole environment in behind it. The cycle was real but
invisible: whichever package a script happened to import first completed, so it worked
everywhere until a new import line in `scripts/score_judge.py` reversed the order and
`RewardSignal` failed to resolve from a partially initialized module.

Deferring the two heavy names breaks the cycle at its source rather than relying on
import order, and has a second benefit worth keeping: importing `web_testing_agent.judge`
or `web_testing_agent.reward` no longer drags Playwright into the process.
"""

from importlib import import_module
from typing import TYPE_CHECKING

from .types import ActionSpec, ActionType, BugSignals, InputValueCategory, NetworkEvent, RawObservation

if TYPE_CHECKING:  # so type checkers and IDEs still resolve these normally
    from .base_env import WebTestingEnv
    from .functional_env import WebFunctionalEnv

_LAZY = {"WebTestingEnv": ".base_env", "WebFunctionalEnv": ".functional_env"}

__all__ = [
    "ActionSpec",
    "ActionType",
    "BugSignals",
    "InputValueCategory",
    "NetworkEvent",
    "RawObservation",
    "WebFunctionalEnv",
    "WebTestingEnv",
]


def __getattr__(name: str):
    """PEP 562 lazy attribute access for the environment classes."""
    if name in _LAZY:
        value = getattr(import_module(_LAZY[name], __name__), name)
        globals()[name] = value  # cache, so this runs once per name
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY))
