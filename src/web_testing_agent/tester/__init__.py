"""The product: point the learning tester at a web application and get a report back.

    session    one budgeted test session: agent, monitors, review, evidence
    triggers   verification (replay + minimization) for findings with no answer key
    review     the LLM's qualitative reading of outcome pages -- reported, never counted
    report     Markdown and standalone HTML
    targets    how an application is provided: a URL, a static site, a Docker repository
"""

from .session import SessionResult, SessionSettings, run_session

__all__ = ["SessionResult", "SessionSettings", "run_session"]
