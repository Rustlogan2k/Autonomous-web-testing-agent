"""Judge backends.

`Judge` is the seam every backend implements, so the offline scorer, and later the
live `FunctionalRewardModel`, are written once against the protocol rather than
against a vendor SDK. The first backend is a frontier model via the Anthropic API,
used to establish a *ceiling*: it answers "is this task doable from the evidence the
trace contains?" before any effort goes into running a small model locally. If the
ceiling is low, that is a fact about the observation, and no amount of quantization
work would have fixed it.

`prompt_cache=True` marks the system prompt as cacheable. Every judgment in a run
shares it verbatim, so after the first call it is read from cache instead of being
re-billed on all several hundred requests.
"""

from __future__ import annotations

import os
from typing import Protocol

from ..utils.logging import get_logger
from .prompt import PROFILE_GUIDANCE, SYSTEM_PROMPT, build_user_prompt
from .verdict import VERDICT_SCHEMA, Verdict

logger = get_logger(__name__)

DEFAULT_MODEL = "claude-opus-5"
# Verdicts are short; the ceiling only needs to cover the JSON object plus thinking.
DEFAULT_MAX_TOKENS = 2048


class Judge(Protocol):
    """Anything that can turn a rendered window into a verdict.

    `profile_text` is the (already sliced and budgeted) Application Profile section for
    this window, or `""` for ungrounded judging. It is a separate argument rather than
    part of `window_text` because evidence grounding is checked against the window: a
    profile merged into it would let a judge quote the specification as proof of a
    defect and score as grounded, which would silently void the only judge-quality
    metric that survives on a target without an answer key.
    """

    name: str

    def judge(self, window_text: str, profile_text: str = "") -> Verdict: ...


class StubJudge:
    """Always-clean judge. Useful as a floor: it scores 0 recall and 0 false positives.

    A real judge that cannot beat this on recall has contributed nothing, and one that
    beats it on recall while also beating it on false positives is the only outcome
    that counts as working.
    """

    name = "stub"

    def judge(self, window_text: str, profile_text: str = "") -> Verdict:
        return Verdict(is_bug=False, confidence=1.0, evidence="stub judge: never reports")


class ClaudeJudge:
    """Anthropic-hosted judge, used to measure the achievable ceiling.

    Structured outputs (`output_config.format`) guarantee the first content block is
    text containing schema-conforming JSON, so parsing cannot be the thing that fails.
    `Verdict.parse` is still tolerant, because the same code path serves local models
    that have no such guarantee.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        api_key: str | None = None,
        thinking: bool = True,
        prompt_cache: bool = True,
    ) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise RuntimeError(
                "The anthropic SDK is required for ClaudeJudge. Install it with:\n"
                "    pip install anthropic"
            ) from exc

        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Export it, or pass --judge stub to run "
                "the scoring pipeline without calling a model."
            )
        self._client = anthropic.Anthropic(api_key=key)
        self.model = model
        self.name = model
        self.max_tokens = max_tokens
        self.thinking = thinking
        # Kept in block form regardless of caching so the grounded variant can append a
        # second block without having to know which representation is in use.
        self._system_blocks: list[dict] = [{"type": "text", "text": SYSTEM_PROMPT}]
        if prompt_cache:
            self._system_blocks[0]["cache_control"] = {"type": "ephemeral"}
        self._system: list[dict] | str = self._system_blocks if prompt_cache else SYSTEM_PROMPT
        self.input_tokens = 0
        self.output_tokens = 0
        self.cached_tokens = 0

    def judge(self, window_text: str, profile_text: str = "") -> Verdict:
        # The cached system block is the ungrounded prompt, so the profile guidance is
        # appended as a second, uncached block rather than mutating the first. Rewriting
        # the cached prefix would invalidate the cache on every call whose grounding
        # state differs — and the A/B runs both states over the same corpus.
        system = self._system
        if profile_text:
            system = [*self._system_blocks, {"type": "text", "text": PROFILE_GUIDANCE}]
        request: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [
                {"role": "user", "content": build_user_prompt(window_text, profile_text=profile_text)}
            ],
            "output_config": {"format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
        }
        if self.thinking:
            request["thinking"] = {"type": "adaptive"}

        try:
            response = self._client.messages.create(**request)
        except Exception as exc:  # noqa: BLE001 - an outage must not read as "no bug"
            logger.warning("Judge call failed: {}", exc)
            return Verdict.failed(f"{type(exc).__name__}: {exc}")

        usage = getattr(response, "usage", None)
        if usage is not None:
            self.input_tokens += getattr(usage, "input_tokens", 0) or 0
            self.output_tokens += getattr(usage, "output_tokens", 0) or 0
            self.cached_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0

        text = next((block.text for block in response.content if block.type == "text"), "")
        if not text:
            return Verdict.failed("judge returned no text block")
        return Verdict.parse(text)

    def usage_summary(self) -> dict:
        return {
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_input_tokens": self.cached_tokens,
        }
