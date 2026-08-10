"""Unit tests for the Ollama backend. No daemon required — the transport is stubbed.

What matters here is not that HTTP works, but that every way the backend can fail
produces an *error* verdict rather than a clean one. A judge whose outages read as
"no bug found" silently deflates recall, and on a long scoring run that looks exactly
like a negative result.
"""

import json

import pytest

from web_testing_agent.judge import OllamaJudge, Verdict
from web_testing_agent.judge.verdict import VERDICT_SCHEMA

VALID = {
    "is_bug": True, "bug_type": "dead_control", "severity": 0.5, "confidence": 0.8,
    "step": 2, "expected": "a download", "actual": "nothing", "evidence": "NO OBSERVABLE CHANGE",
}


def _judge(monkeypatch, response: dict, capture: dict | None = None) -> OllamaJudge:
    judge = OllamaJudge(model="test-model")

    def fake_post(path: str, body: dict) -> dict:
        if capture is not None:
            capture.update({"path": path, "body": body})
        return response

    monkeypatch.setattr(judge, "_post", fake_post)
    return judge


def _ok(content: str, **extra) -> dict:
    return {"message": {"content": content}, "done_reason": "stop",
            "prompt_eval_count": 900, "eval_count": 60, **extra}


# --- request shape ---------------------------------------------------------------


def test_the_verdict_schema_constrains_decoding(monkeypatch):
    capture: dict = {}
    _judge(monkeypatch, _ok(json.dumps(VALID)), capture).judge("STEP 1")
    assert capture["body"]["format"] == VERDICT_SCHEMA
    assert capture["path"] == "/api/chat"


def test_temperature_is_zero(monkeypatch):
    """The verdict becomes reward; a non-deterministic judge makes it non-stationary."""
    capture: dict = {}
    _judge(monkeypatch, _ok(json.dumps(VALID)), capture).judge("STEP 1")
    assert capture["body"]["options"]["temperature"] == 0.0


def test_context_length_is_set_explicitly(monkeypatch):
    """Left to the server default, llama.cpp truncates the system prompt away silently."""
    capture: dict = {}
    _judge(monkeypatch, _ok(json.dumps(VALID)), capture).judge("STEP 1")
    assert capture["body"]["options"]["num_ctx"] >= 8192


def test_the_system_prompt_is_sent(monkeypatch):
    capture: dict = {}
    _judge(monkeypatch, _ok(json.dumps(VALID)), capture).judge("STEP 1")
    roles = [m["role"] for m in capture["body"]["messages"]]
    assert roles == ["system", "user"]
    assert "QA engineer" in capture["body"]["messages"][0]["content"]


def test_streaming_is_disabled(monkeypatch):
    capture: dict = {}
    _judge(monkeypatch, _ok(json.dumps(VALID)), capture).judge("STEP 1")
    assert capture["body"]["stream"] is False


# --- response handling -----------------------------------------------------------


def test_a_valid_response_parses(monkeypatch):
    verdict = _judge(monkeypatch, _ok(json.dumps(VALID))).judge("STEP 1")
    assert verdict.ok and verdict.is_bug and verdict.bug_type == "dead_control"


def test_reasoning_model_think_blocks_are_stripped(monkeypatch):
    """deepseek-r1 emits drafts inline; parsing the first `{` would take a discarded one."""
    content = (
        "<think>Maybe it is a bug? Draft: {\"is_bug\": false, \"bug_type\": \"other\"} "
        "no wait, the button did nothing.</think>" + json.dumps(VALID)
    )
    verdict = _judge(monkeypatch, _ok(content)).judge("STEP 1")
    assert verdict.ok and verdict.is_bug is True


def test_an_empty_response_is_an_error_not_a_clean_verdict(monkeypatch):
    """A model that hits its output cap mid-object returns nothing usable."""
    verdict = _judge(monkeypatch, _ok("   ", done_reason="length")).judge("STEP 1")
    assert not verdict.ok
    assert "length" in verdict.error


def test_a_transport_failure_is_an_error_not_a_clean_verdict(monkeypatch):
    judge = OllamaJudge(model="test-model")

    def boom(path: str, body: dict) -> dict:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(judge, "_post", boom)
    verdict = judge.judge("STEP 1")
    assert not verdict.ok and not verdict.is_bug
    assert "connection refused" in verdict.error


def test_malformed_content_is_an_error(monkeypatch):
    verdict = _judge(monkeypatch, _ok("I think the button is broken")).judge("STEP 1")
    assert not verdict.ok


# --- accounting ------------------------------------------------------------------


def test_usage_accumulates_across_calls(monkeypatch):
    judge = _judge(monkeypatch, _ok(json.dumps(VALID)))
    for _ in range(3):
        judge.judge("STEP 1")
    usage = judge.usage_summary()
    assert usage["calls"] == 3
    assert usage["prompt_tokens"] == 2700 and usage["output_tokens"] == 180
    assert usage["model"] == "ollama/test-model"


def test_failed_calls_are_still_counted(monkeypatch):
    """Otherwise a run that mostly failed looks like a fast, cheap, clean run."""
    judge = OllamaJudge(model="test-model")
    monkeypatch.setattr(judge, "_post", lambda p, b: (_ for _ in ()).throw(RuntimeError("nope")))
    judge.judge("STEP 1")
    assert judge.usage_summary()["calls"] == 1


def test_an_oversized_window_is_flagged(monkeypatch):
    """Silent front-truncation would remove the instructions and leave no trace."""
    judge = OllamaJudge(model="test-model", num_ctx=512)
    monkeypatch.setattr(judge, "_post", lambda p, b: _ok(json.dumps(VALID)))
    judge.judge("STEP 1\n" + "x" * 20_000)
    assert judge.usage_summary()["prompts_over_context"] == 1


def test_a_normal_window_is_not_flagged(monkeypatch):
    judge = _judge(monkeypatch, _ok(json.dumps(VALID)))
    judge.judge("STEP 1\n  action: CLICK on 'Export data'")
    assert judge.usage_summary()["prompts_over_context"] == 0


# --- preflight -------------------------------------------------------------------


def test_preflight_rejects_a_missing_model(monkeypatch):
    """Fail before the run, not a third of the way through it."""
    judge = OllamaJudge(model="not-installed:7b")
    monkeypatch.setattr(judge, "available_models", lambda: ["llama3:8b"])
    with pytest.raises(RuntimeError, match="not installed"):
        judge.preflight()


def test_preflight_accepts_an_installed_model(monkeypatch):
    judge = OllamaJudge(model="llama3:8b")
    monkeypatch.setattr(judge, "available_models", lambda: ["llama3:8b", "qwen2.5:7b-instruct"])
    judge.preflight()


def test_preflight_tolerates_a_different_tag_of_the_same_model(monkeypatch):
    judge = OllamaJudge(model="qwen2.5:7b-instruct")
    monkeypatch.setattr(judge, "available_models", lambda: ["qwen2.5:latest"])
    judge.preflight()


# --- reasoning models and unconstrained backends ---------------------------------


def test_a_reasoning_model_that_spends_its_budget_thinking_is_diagnosed(monkeypatch):
    """Measured on gpt-oss:120b-cloud at num_predict=512: the whole budget went to the
    thinking channel, content came back empty, and done_reason='length' was the only clue."""
    response = {"message": {"content": "", "thinking": "x" * 4000}, "done_reason": "length"}
    verdict = _judge(monkeypatch, response).judge("STEP 1")
    assert not verdict.ok
    assert "reasoning channel" in verdict.error
    assert "num_predict" in verdict.error


def test_thinking_models_get_a_larger_output_budget_by_default():
    assert OllamaJudge(model="m").num_predict > OllamaJudge(model="m", think=False).num_predict


def test_think_is_only_sent_when_explicitly_set(monkeypatch):
    """Omitting the field leaves each model's own default alone."""
    capture: dict = {}
    _judge(monkeypatch, _ok(json.dumps(VALID)), capture).judge("STEP 1")
    assert "think" not in capture["body"]

    capture.clear()
    judge = OllamaJudge(model="test-model", think=False)
    monkeypatch.setattr(judge, "_post", lambda p, b: capture.update({"body": b}) or _ok(json.dumps(VALID)))
    judge.judge("STEP 1")
    assert capture["body"]["think"] is False


def test_unstructured_mode_instructs_json_in_the_prompt(monkeypatch):
    """Ollama enforces `format` locally, so a :cloud model accepts it and ignores it."""
    capture: dict = {}
    judge = OllamaJudge(model="test-model", structured=False)
    monkeypatch.setattr(judge, "_post", lambda p, b: capture.update({"body": b}) or _ok(json.dumps(VALID)))
    judge.judge("STEP 1")

    assert "format" not in capture["body"]
    user_message = capture["body"]["messages"][-1]["content"]
    assert '"discrepancy"' in user_message and '"is_bug"' in user_message
    # The prompt template must not drift from the schema it stands in for.
    assert user_message.index('"observed"') < user_message.index('"is_bug"')


def test_a_model_ignoring_the_schema_is_counted(monkeypatch):
    """Otherwise the run just accumulates parse errors and looks like a broken model."""
    judge = _judge(monkeypatch, _ok("**observed:** the button did nothing"))
    judge.judge("STEP 1")
    assert judge.usage_summary()["schema_ignored"] == 1


def test_valid_json_does_not_count_as_schema_ignored(monkeypatch):
    judge = _judge(monkeypatch, _ok(json.dumps(VALID)))
    judge.judge("STEP 1")
    assert judge.usage_summary()["schema_ignored"] == 0
