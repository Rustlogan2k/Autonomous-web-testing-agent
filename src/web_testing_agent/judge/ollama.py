"""Local (and Ollama-hosted) judge backend.

This is the backend the project actually ships. A final-year deliverable whose core
claim depends on a paid frontier API is not a self-contained tool, and the offline
corpus makes a local model cheap to iterate against — every prompt revision is a
re-run over fixed files rather than a browser session.

It also removes the largest execution risk in the plan. Running a quantized model on
Windows through `bitsandbytes` means a CUDA extension build, version pinning, and a
toolchain that is well known to break; Ollama ships GGUF weights through llama.cpp and
sidesteps all of it.

Deliberately dependency-free: `urllib` from the standard library rather than an HTTP
client, since the entire protocol here is one POST returning one JSON object.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from ..utils.logging import get_logger
from .prompt import SYSTEM_PROMPT, build_messages, build_user_prompt
from .verdict import VERDICT_SCHEMA, Verdict

logger = get_logger(__name__)

DEFAULT_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODEL = "qwen2.5:7b-instruct"

# Set explicitly and never left to the server default, which is commonly 4096 and can
# be as low as 2048. llama.cpp truncates an over-long prompt from the *front*, which
# is where the system prompt lives — the judge would quietly lose its entire
# instruction set and start reporting whatever looked odd, with no error anywhere.
DEFAULT_NUM_CTX = 8192

# Verdicts are a small fixed JSON object. Capping output stops a model that ignores
# the schema from generating until it hits the context limit.
DEFAULT_NUM_PREDICT = 512

# Reasoning models spend tokens in a separate `thinking` channel *before* emitting any
# content, and `num_predict` caps the two together. Measured on gpt-oss:120b-cloud at
# 512: the whole budget went to thinking, `content` came back empty, and the only clue
# was `done_reason="length"`. Anything that reasons needs a much larger ceiling.
REASONING_NUM_PREDICT = 4096

# Local inference on a 6 GB card is slow and highly variable, especially on the first
# call when weights are loaded from disk.
DEFAULT_TIMEOUT_S = 300.0


class OllamaJudge:
    """Judge backed by a model served by Ollama, local or hosted.

    `format=VERDICT_SCHEMA` is Ollama's structured-output mechanism and constrains
    decoding to conforming JSON, so the same schema drives both this backend and the
    Anthropic one. `Verdict.parse` still runs afterwards: it strips reasoning-model
    `<think>` blocks and clamps out-of-range scores, neither of which the grammar
    prevents.

    `temperature=0` because the verdict becomes a reward signal. A judge that returns
    different answers for the same window makes the reward non-stationary and the
    scoring run unreproducible.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        host: str = DEFAULT_HOST,
        num_ctx: int = DEFAULT_NUM_CTX,
        num_predict: int | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        temperature: float = 0.0,
        structured: bool = True,
        think: bool | None = None,
        prompt_style: str = "detailed",
    ) -> None:
        self.model = model
        self.name = f"ollama/{model}"
        self.host = host.rstrip("/")
        self.num_ctx = num_ctx
        self.think = think
        if num_predict is None:
            # Budget for a thinking channel unless it has been explicitly turned off.
            num_predict = DEFAULT_NUM_PREDICT if think is False else REASONING_NUM_PREDICT
        self.num_predict = num_predict
        self.timeout_s = timeout_s
        self.temperature = temperature
        self.structured = structured
        self.prompt_style = prompt_style
        self.prompt_tokens = 0
        self.output_tokens = 0
        self.total_seconds = 0.0
        self.calls = 0
        self.truncated_prompts = 0
        self.schema_ignored = 0

    # -- preflight -------------------------------------------------------------

    def available_models(self) -> list[str]:
        try:
            with urllib.request.urlopen(f"{self.host}/api/tags", timeout=10) as response:
                payload = json.loads(response.read())
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Could not reach Ollama at {self.host}. Is it running? ({exc})"
            ) from exc
        return [entry["name"] for entry in payload.get("models", [])]

    def preflight(self) -> None:
        """Fail before the run rather than 189 windows into it."""
        models = self.available_models()
        if self.model in models:
            return
        # Ollama resolves a bare name to its `:latest` tag.
        if any(name.split(":")[0] == self.model.split(":")[0] for name in models):
            logger.warning(
                "Exact tag {!r} not installed; available tags for that model: {}",
                self.model, [n for n in models if n.split(":")[0] == self.model.split(":")[0]],
            )
            return
        raise RuntimeError(
            f"Model {self.model!r} is not installed. Available: {models}\n"
            f"    ollama pull {self.model}"
        )

    # -- judging ---------------------------------------------------------------

    def judge(self, window_text: str, profile_text: str = "") -> Verdict:
        messages = build_messages(
            window_text,
            style=self.prompt_style,
            require_json=not self.structured,
            profile_text=profile_text,
        )
        self._warn_if_context_is_tight(messages)

        request: dict = {
            "model": self.model,
            "stream": False,
            "messages": messages,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.num_ctx,
                "num_predict": self.num_predict,
            },
        }
        if self.structured:
            request["format"] = VERDICT_SCHEMA
        if self.think is not None:
            request["think"] = self.think

        started = time.monotonic()
        try:
            payload = self._post("/api/chat", request)
        except Exception as exc:  # noqa: BLE001 - a failure must never read as "no bug"
            logger.warning("Ollama call failed: {}", exc)
            return Verdict.failed(f"{type(exc).__name__}: {exc}")
        finally:
            self.total_seconds += time.monotonic() - started
            self.calls += 1

        self.prompt_tokens += payload.get("prompt_eval_count", 0) or 0
        self.output_tokens += payload.get("eval_count", 0) or 0

        message = payload.get("message") or {}
        content = message.get("content") or ""
        if not content.strip():
            # A model that hits its output cap mid-object returns nothing usable. Say
            # so rather than letting an empty string parse as a clean verdict, and name
            # the actual cause: a reasoning model that spent the whole budget thinking
            # looks identical to a broken one unless the thinking length is reported.
            thinking = len(message.get("thinking") or "")
            reason = payload.get("done_reason")
            detail = f"empty response (done_reason={reason!r}"
            if thinking:
                detail += (
                    f", but {thinking} chars of thinking — the token budget went to the "
                    f"reasoning channel; raise num_predict above {self.num_predict} or pass think=False"
                )
            return Verdict.failed(detail + ")")

        verdict = Verdict.parse(content)
        if not verdict.ok and self.structured:
            # Ollama enforces `format` locally via llama.cpp, so a remotely hosted
            # model accepts the field and ignores it. Without this the run just
            # accumulates parse errors and looks like a broken model.
            self.schema_ignored += 1
            if self.schema_ignored == 1:
                logger.warning(
                    "{} returned non-JSON despite a format schema — it is probably not "
                    "honouring constrained decoding (common for :cloud models). "
                    "Re-run with --no-structured to instruct it in the prompt instead.",
                    self.model,
                )
        return verdict

    def _post(self, path: str, body: dict) -> dict:
        request = urllib.request.Request(
            f"{self.host}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc

    def _warn_if_context_is_tight(self, messages: list[dict]) -> None:
        """Silent front-truncation would drop the system prompt; make it loud instead.

        Counts every turn, not just the window: few-shot adds several thousand
        characters, and a budget computed from the window alone would miss the overflow
        exactly when the prompt style makes it most likely.
        """
        approx = sum(len(m["content"]) for m in messages) // 4 + self.num_predict
        if approx > self.num_ctx:
            self.truncated_prompts += 1
            logger.warning(
                "Window needs ~{} tokens but num_ctx is {}. The system prompt will be "
                "truncated away. Raise --num-ctx.", approx, self.num_ctx,
            )

    def usage_summary(self) -> dict:
        return {
            "model": self.name,
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "output_tokens": self.output_tokens,
            "wall_clock_s": round(self.total_seconds, 1),
            "seconds_per_call": round(self.total_seconds / self.calls, 2) if self.calls else 0.0,
            "num_ctx": self.num_ctx,
            "prompt_style": self.prompt_style,
            "prompts_over_context": self.truncated_prompts,
            "schema_ignored": self.schema_ignored,
        }
