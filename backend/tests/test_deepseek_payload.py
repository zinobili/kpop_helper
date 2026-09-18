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
