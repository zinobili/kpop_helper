import json

import pytest

from app.translate import (
    _extract_retry_delay_seconds,
    _is_daily_quota_exceeded,
    _parse_json_objects,
)


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
