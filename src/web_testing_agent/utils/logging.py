"""Centralized loguru configuration shared by every module in the project."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger as _logger

load_dotenv()

_CONFIGURED = False
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _configure() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    _logger.remove()
    _logger.add(
        sys.stderr,
        level=level,
        format=(
            "<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | "
            "<cyan>{extra[module]}</cyan> - <level>{message}</level>"
        ),
    )

    log_dir = _REPO_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    _logger.add(
        log_dir / "web_testing_agent.log",
        level="DEBUG",
        rotation="20 MB",
        retention=5,
        enqueue=True,
        format="{time} | {level:<8} | {extra[module]} - {message}",
    )
    _CONFIGURED = True


def get_logger(name: str):
    """Return a module-scoped logger, e.g. `logger = get_logger(__name__)`."""
    _configure()
    return _logger.bind(module=name)
