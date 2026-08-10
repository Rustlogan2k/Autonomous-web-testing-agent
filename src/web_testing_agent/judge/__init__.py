"""Offline LLM judge: the component that must reach the bugs deterministic triggers cannot.

Nothing here imports the environment or Playwright. The judge is developed and scored
entirely against captured traces, so a prompt change costs a second rather than a
browser run, and a judge that does not work is discovered before any live-reward
integration has been written.
"""

from .client import ClaudeJudge, Judge, StubJudge
from .ollama import OllamaJudge
from .prompt import SYSTEM_PROMPT, build_user_prompt
from .scoring import Judgment, ScoreReport, score, summarize
from .validate import WindowIssue, summarize_issues, validate_corpus, validate_window
from .verdict import BUG_TYPES, VERDICT_SCHEMA, Verdict
from .window import WINDOW_STEPS, JudgeWindow, PageView, build_windows, page_view

__all__ = [
    "BUG_TYPES",
    "ClaudeJudge",
    "Judge",
    "JudgeWindow",
    "Judgment",
    "OllamaJudge",
    "PageView",
    "SYSTEM_PROMPT",
    "ScoreReport",
    "StubJudge",
    "VERDICT_SCHEMA",
    "WindowIssue",
    "Verdict",
    "WINDOW_STEPS",
    "build_user_prompt",
    "build_windows",
    "page_view",
    "score",
    "summarize_issues",
    "summarize",
    "validate_corpus",
    "validate_window",
]
