import json

import pytest

from app import config, translate


class _FakeResponse:
    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": json.dumps([{"i": 0, "t": "hi"}])}}]}


@pytest.mark.parametrize("setting", ["disabled", "enabled"])
def test_deepseek_request_carries_the_configured_thinking_mode(monkeypatch, setting):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(config, "DEEPSEEK_THINKING", setting)
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["payload"] = json
        return _FakeResponse()

    monkeypatch.setattr("httpx.post", fake_post)

    translate._translate_batch_deepseek(["x"], "ko")

    assert captured["payload"]["thinking"] == {"type": setting}


# ---------- retries ----------


class _StatusResponse:
    def __init__(self, status_code):
        self.status_code = status_code

    def raise_for_status(self):
        import httpx

        raise httpx.HTTPStatusError(
            "error", request=httpx.Request("POST", "http://x"), response=self
        )


def _scripted_post(monkeypatch, script):
    """Each script item is an Exception to raise, or a response object to return."""
    import httpx  # noqa: F401  (patched below via the string path)

    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        step = script[len(calls)]
        calls.append(step)
        if isinstance(step, Exception):
            raise step
        return step

    sleeps = []
    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setattr(translate.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(config, "DEEPSEEK_MAX_RETRIES", 3)
    return calls, sleeps


def test_dropped_connection_is_retried_with_backoff_then_succeeds(monkeypatch):
    import httpx

    calls, sleeps = _scripted_post(
        monkeypatch,
        [httpx.RemoteProtocolError("peer closed connection"), httpx.RemoteProtocolError("again"), _FakeResponse()],
    )

    assert translate._translate_batch_deepseek(["x"], "ko") == ["hi"]
    assert len(calls) == 3
    assert sleeps == [5.0, 10.0]


@pytest.mark.parametrize("error_name", ["ReadTimeout", "ConnectError"])
def test_timeouts_and_connect_errors_are_retried_too(monkeypatch, error_name):
    import httpx

    calls, _ = _scripted_post(monkeypatch, [getattr(httpx, error_name)("boom"), _FakeResponse()])

    assert translate._translate_batch_deepseek(["x"], "ko") == ["hi"]
    assert len(calls) == 2


def test_transport_errors_stop_after_max_retries_and_raise(monkeypatch):
    import httpx

    calls, sleeps = _scripted_post(
        monkeypatch, [httpx.RemoteProtocolError("down")] * 4  # MAX_RETRIES=3 -> 4 attempts
    )

    with pytest.raises(httpx.RemoteProtocolError):
        translate._translate_batch_deepseek(["x"], "ko")

    assert len(calls) == 4
    assert len(sleeps) == 3


def test_retry_log_names_the_transport_error(monkeypatch, caplog):
    import logging

    import httpx

    _scripted_post(monkeypatch, [httpx.RemoteProtocolError("cut off"), _FakeResponse()])

    with caplog.at_level(logging.WARNING, logger="kpop_helper.translate"):
        translate._translate_batch_deepseek(["x"], "ko")

    assert "RemoteProtocolError" in caplog.text
    assert "attempt 1/3" in caplog.text


def test_status_errors_still_follow_the_existing_rules(monkeypatch):
    import httpx

    calls, _ = _scripted_post(monkeypatch, [_StatusResponse(503), _FakeResponse()])
    assert translate._translate_batch_deepseek(["x"], "ko") == ["hi"]  # 5xx retried
    assert len(calls) == 2

    calls, _ = _scripted_post(monkeypatch, [_StatusResponse(401)])
    with pytest.raises(httpx.HTTPStatusError):
        translate._translate_batch_deepseek(["x"], "ko")  # 4xx not retried
    assert len(calls) == 1


# ---------- DEEPSEEK_THINKING parsing ----------


@pytest.mark.parametrize("value", ["on", "ON", "true", "1", "enabled", " Enabled "])
def test_parse_thinking_accepts_on_values(value):
    assert config.parse_thinking(value) == "enabled"


@pytest.mark.parametrize("value", ["off", "OFF", "false", "0", "disabled", " Disabled "])
def test_parse_thinking_accepts_off_values(value):
    assert config.parse_thinking(value) == "disabled"


@pytest.mark.parametrize("value", ["", "maybe", "high", "none"])
def test_parse_thinking_rejects_anything_else_with_a_clear_message(value):
    with pytest.raises(ValueError, match="DEEPSEEK_THINKING"):
        config.parse_thinking(value)
