"""The multi-application benchmark: the suite, its admission checks, and the budgeted hunt.

    suite       which applications exist, how they are served, how an env is configured
    validate    admission control -- every seeded fault reachable, checkable, and absent
                from the clean build
    monitor     online seeded-fault detection and verification during a run
    hunt        one policy, one application, one step budget, with the oracle watching

Nothing in this package imports a policy implementation, a reward model or a language
model. It measures agents; it does not contain one.
"""

from .hunt import HuntResult, run_hunt
from .monitor import FaultMonitor, FaultRecord
from .suite import (
    BUILDS,
    EnvSettings,
    SuiteApp,
    control_key,
    discover,
    load_app,
    make_env,
    materialize_without,
    serve_app,
    suite_root,
)
from .validate import AppValidation, validate_app

__all__ = [
    "BUILDS", "AppValidation", "EnvSettings", "FaultMonitor", "FaultRecord", "HuntResult",
    "SuiteApp", "control_key", "discover", "load_app", "make_env", "materialize_without",
    "run_hunt", "serve_app", "suite_root",
    "validate_app",
]
