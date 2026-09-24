import json
import logging

import pytest

from app import config, translate
from app.translate import (
    _extract_retry_delay_seconds,
    _is_daily_quota_exceeded,
    _parse_json_objects,
    _RunStats,
    _take_call_info,
    _translate_batch,
    _translate_batch_local,
    _translate_batch_once,
    translate_cues,
)
from app.vtt_utils import Cue


def test_parse_json_objects_reorders_by_index():
    raw = json.dumps([{"i": 1, "t": "second"}, {"i": 0, "t": "first"}])
    assert _parse_json_objects(raw, expected_len=2) == ["first", "second"]


def test_parse_json_objects_strips_markdown_code_fence():
    raw = '```json\n[{"i": 0, "t": "hi"}]\n```'
    assert _parse_json_objects(raw, expected_len=1) == ["hi"]


def test_parse_json_objects_raises_on_missing_index():
    raw = json.dumps([{"i": 0, "t": "only one"}])
    with pytest.raises(ValueError, match=r"missing"):
        _parse_json_objects(raw, expected_len=2)


class _FakeGeminiError(Exception):
    """Stands in for google.genai.errors.ClientError without needing the real SDK/network."""

    def __init__(self, details):
        super().__init__("fake gemini error")
        self.details = details
        self.response = None


# Real shape captured from an actual 429 response for gemini-3.6-flash free tier.
DAILY_QUOTA_ERROR = _FakeGeminiError(
    {
        "error": {
            "code": 429,
            "status": "RESOURCE_EXHAUSTED",
            "details": [
                {"@type": "type.googleapis.com/google.rpc.Help", "links": []},
                {
                    "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                    "violations": [
                        {
                            "quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
                            "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                            "quotaDimensions": {"location": "global", "model": "gemini-3.6-flash"},
                            "quotaValue": "20",
                        }
                    ],
                },
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "0s"},
            ],
        }
    }
)

PER_MINUTE_QUOTA_ERROR = _FakeGeminiError(
    {
        "error": {
            "code": 429,
            "status": "RESOURCE_EXHAUSTED",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                    "violations": [
                        {
                            "quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier",
                            "quotaValue": "5",
                        }
                    ],
                },
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "16s"},
            ],
        }
    }
)


def test_detects_daily_quota_violation():
    assert _is_daily_quota_exceeded(DAILY_QUOTA_ERROR) is True


def test_does_not_flag_per_minute_violation_as_daily():
    assert _is_daily_quota_exceeded(PER_MINUTE_QUOTA_ERROR) is False


def test_extracts_retry_delay_from_per_minute_error():
    assert _extract_retry_delay_seconds(PER_MINUTE_QUOTA_ERROR) == 16.0


def test_extract_retry_delay_returns_none_when_absent():
    assert _extract_retry_delay_seconds(_FakeGeminiError({})) is None


def test_translate_batch_local_requires_a_model(monkeypatch):
    monkeypatch.setattr(config, "LOCAL_LLM_MODEL", "")
    with pytest.raises(RuntimeError, match="No local LLM model selected"):
        _translate_batch_local(["hi"], "ko", None)


def test_translate_batch_local_posts_to_configured_base_url(monkeypatch):
    monkeypatch.setattr(config, "LOCAL_LLM_BASE_URL", "http://localhost:9999/v1")
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps([{"i": 0, "t": "你好"}])}}]}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setattr("httpx.post", fake_post)

    result = _translate_batch_local(["hi"], "ko", "llama3")

    assert result == ["你好"]
    assert captured["url"] == "http://localhost:9999/v1/chat/completions"
    assert captured["json"]["model"] == "llama3"


def test_translate_batch_local_falls_back_to_configured_model(monkeypatch):
    monkeypatch.setattr(config, "LOCAL_LLM_MODEL", "default-model")
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps([{"i": 0, "t": "hi"}])}}]}

    def fake_post(url, json=None, timeout=None):
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setattr("httpx.post", fake_post)

    _translate_batch_local(["hi"], "ko", None)

    assert captured["json"]["model"] == "default-model"


def test_translate_batch_claude_agent_parses_assistant_reply(monkeypatch):
    import claude_agent_sdk as sdk

    captured = {}

    async def fake_query(*, prompt, options):
        captured["prompt"] = prompt
        captured["options"] = options
        yield sdk.AssistantMessage(
            content=[sdk.TextBlock(text=json.dumps([{"i": 0, "t": "你好"}]))],
            model="claude-sonnet-5",
            usage={"input_tokens": 42, "output_tokens": 7},
            stop_reason="end_turn",
        )

    monkeypatch.setattr(sdk, "query", fake_query)

    result = translate._translate_batch_claude_agent(["hi"], "ko")

    assert result == ["你好"]
    assert captured["options"].tools == []
    assert captured["options"].permission_mode == "bypassPermissions"
    assert "hi" in captured["prompt"]
    info = _take_call_info()
    assert info == {
        "model": "claude-sonnet-5",
        "input_tokens": 42,
        "output_tokens": 7,
        "thinking_tokens": None,
        "stop_reason": "end_turn",
    }


def test_translate_batch_claude_agent_raises_on_assistant_error(monkeypatch):
    import claude_agent_sdk as sdk

    async def fake_query(*, prompt, options):
        yield sdk.AssistantMessage(
            content=[], model="claude-sonnet-5", error="authentication_failed"
        )

    monkeypatch.setattr(sdk, "query", fake_query)

    with pytest.raises(RuntimeError, match="authentication_failed"):
        translate._translate_batch_claude_agent(["hi"], "ko")


def test_translate_batch_claude_agent_raises_readable_error_when_cli_missing(monkeypatch):
    import claude_agent_sdk as sdk

    async def fake_query(*, prompt, options):
        raise sdk.CLINotFoundError()
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(sdk, "query", fake_query)

    with pytest.raises(RuntimeError, match="Claude Code CLI not found"):
        translate._translate_batch_claude_agent(["hi"], "ko")


def test_translate_batch_once_provider_override_wins_over_config_default(monkeypatch):
    monkeypatch.setattr(config, "TRANSLATION_PROVIDER", "deepseek")
    monkeypatch.setattr(config, "LOCAL_LLM_MODEL", "fallback-model")

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps([{"i": 0, "t": "hi"}])}}]}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        return FakeResponse()

    monkeypatch.setattr("httpx.post", fake_post)

    result = _translate_batch_once(["hi"], "ko", provider="local")

    assert result == ["hi"]
    assert captured["url"].endswith("/chat/completions")


def test_translate_batch_once_defaults_to_config_provider_when_not_given(monkeypatch):
    monkeypatch.setattr(config, "TRANSLATION_PROVIDER", "unknownprovider")
    with pytest.raises(RuntimeError, match="Unknown translation provider 'unknownprovider'"):
        _translate_batch_once(["hi"], "ko")


def test_translate_batch_local_records_token_usage_and_stop_reason(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "choices": [
                    {"message": {"content": json.dumps([{"i": 0, "t": "你好"}])}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 120, "completion_tokens": 30},
            }

    monkeypatch.setattr("httpx.post", lambda url, json=None, timeout=None: FakeResponse())
    _take_call_info()

    _translate_batch_local(["hi"], "ko", "llama3")

    info = _take_call_info()
    assert info["model"] == "llama3"
    assert info["input_tokens"] == 120
    assert info["output_tokens"] == 30
    assert info["stop_reason"] == "stop"


def test_translate_batch_local_tolerates_response_without_usage(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps([{"i": 0, "t": "hi"}])}}]}

    monkeypatch.setattr("httpx.post", lambda url, json=None, timeout=None: FakeResponse())

    assert _translate_batch_local(["hi"], "ko", "m") == ["hi"]
    assert _take_call_info()["input_tokens"] is None


def _scripted_once(monkeypatch, script):
    """Replaces the provider call with a scripted sequence: each item is either a list of
    translations (success) or an Exception to raise, both after recording fake usage."""
    calls = []

    def fake_once(texts, source_lang, model=None, provider=None):
        step = script[len(calls)]
        calls.append(texts)
        translate._record_call_info(
            model="fake-model", input_tokens=100, output_tokens=50, stop_reason="end_turn"
        )
        if isinstance(step, Exception):
            raise step
        return step

    monkeypatch.setattr(translate, "_translate_batch_once", fake_once)
    return calls


def test_translate_batch_logs_usage_on_success(monkeypatch, caplog):
    _scripted_once(monkeypatch, [["你好"]])
    stats = _RunStats()

    with caplog.at_level(logging.INFO, logger="kpop_helper.translate"):
        result = _translate_batch(["hi"], "ko", provider="local", batch_label="1/3", stats=stats)

    assert result == ["你好"]
    assert "batch 1/3 ok" in caplog.text
    assert "in_tok=100" in caplog.text and "out_tok=50" in caplog.text
    assert (stats.api_calls, stats.retries, stats.input_tokens, stats.output_tokens) == (1, 0, 100, 50)


def test_translate_batch_retry_logs_reason_and_counts_wasted_tokens(monkeypatch, caplog):
    _scripted_once(monkeypatch, [ValueError("Translation response missing line numbers: [1]"), ["a"]])
    stats = _RunStats()

    with caplog.at_level(logging.INFO, logger="kpop_helper.translate"):
        result = _translate_batch(["x"], "ko", provider="deepseek", batch_label="2/5", stats=stats)

    assert result == ["a"]
    assert "batch 2/5 FAILED" in caplog.text
    assert "missing line numbers" in caplog.text
    assert "retrying" in caplog.text
    # The failed attempt still cost tokens, so both calls are counted.
    assert (stats.api_calls, stats.retries, stats.input_tokens) == (2, 1, 200)


def test_translate_batch_gives_up_after_max_attempts_and_logs_it(monkeypatch, caplog):
    errors = [ValueError("bad")] * translate._BATCH_RETRY_ATTEMPTS
    _scripted_once(monkeypatch, errors)
    stats = _RunStats()

    with caplog.at_level(logging.INFO, logger="kpop_helper.translate"):
        with pytest.raises(ValueError):
            _translate_batch(["x"], "ko", stats=stats)

    assert "giving up" in caplog.text
    assert stats.api_calls == translate._BATCH_RETRY_ATTEMPTS
    assert stats.retries == translate._BATCH_RETRY_ATTEMPTS - 1


def test_translate_batch_warns_when_output_was_truncated(monkeypatch, caplog):
    def fake_once(texts, source_lang, model=None, provider=None):
        translate._record_call_info(output_tokens=4096, stop_reason="max_tokens")
        raise json.JSONDecodeError("Unterminated string", "", 0)

    monkeypatch.setattr(translate, "_translate_batch_once", fake_once)

    with caplog.at_level(logging.INFO, logger="kpop_helper.translate"):
        with pytest.raises(json.JSONDecodeError):
            _translate_batch(["x"], "ko")

    assert "TRUNCATED" in caplog.text


def test_translate_cues_logs_summary_even_when_a_later_batch_fails(monkeypatch, caplog):
    monkeypatch.setattr(config, "TRANSLATE_BATCH_SIZE", 1)
    outcomes = [["a"], ValueError("bad"), ValueError("bad"), ValueError("bad")]
    _scripted_once(monkeypatch, outcomes)
    cues = [Cue(0, 1, "one"), Cue(1, 2, "two")]

    with caplog.at_level(logging.INFO, logger="kpop_helper.translate"):
        with pytest.raises(ValueError):
            translate_cues(cues, "ko", provider="deepseek")

    summary = [r.getMessage() for r in caplog.records if "translate summary" in r.getMessage()]
    assert len(summary) == 1
    assert "batches=1/2" in summary[0]
    assert "api_calls=4" in summary[0]
    assert "in_tok=400" in summary[0]
