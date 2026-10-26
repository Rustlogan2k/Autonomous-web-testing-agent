"""Specification inference: an oracle for applications that come without one.

The tester explores briefly, an LLM reads what the pages show and proposes rules a correct
application must obey (`rules.py`), and the rules then act as the oracle for the rest of the
run (`monitor.py`): a violated rule is a candidate, replayed twice in fresh sessions,
minimised, and judged by the LLM against the evidence before it is reported. Nothing here
claims to know the application's intent; every finding is a reproduced, minimised violation
of a rule a person can read and reject.
"""

from .monitor import SpecMonitor, SpecReplayer
from .rules import Rule, evaluate, parse_rules

__all__ = ["Rule", "SpecMonitor", "SpecReplayer", "evaluate", "parse_rules"]
