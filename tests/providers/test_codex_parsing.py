import datetime as dt
import json
from pathlib import Path

import pytest

from quotacli.protocol import ErrorKind, ProviderError
from quotacli.providers.codex import parse_usage_response

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_primary_and_secondary_windows_parsed():
    body = _load("codex_usage.json")
    windows = parse_usage_response(body)

    assert len(windows) == 2
    assert windows[0].label == "5h limit"
    assert windows[0].used_fraction == 0.64
    assert windows[0].resets_at == dt.datetime.fromtimestamp(1788804084, tz=dt.timezone.utc)

    assert windows[1].label == "Weekly limit"
    assert windows[1].used_fraction == 0.49


def test_free_plan_30_day_primary_window_not_assumed_5h():
    """Free-plan accounts have been observed with a 30-day primary window
    instead of the usual 5h — the label must reflect the actual window
    length, not assume 5h."""
    body = _load("codex_usage_30d_free.json")
    windows = parse_usage_response(body)

    assert len(windows) == 1
    assert windows[0].label == "Monthly limit"
    assert windows[0].used_fraction == 0.90
    assert windows[0].resets_at is not None


def test_reset_after_seconds_used_when_reset_at_absent():
    body = {
        "rate_limit": {
            "primary_window": {
                "used_percent": 10,
                "limit_window_seconds": 3600,
                "reset_after_seconds": 120,
            }
        }
    }
    windows = parse_usage_response(body)
    now = dt.datetime.now(dt.timezone.utc)
    assert abs((windows[0].resets_at - now).total_seconds() - 120) < 5


def test_additional_rate_limits_and_code_review_ignored():
    body = {
        "rate_limit": {"primary_window": {"used_percent": 1, "limit_window_seconds": 300}},
        "additional_rate_limits": {"used_percent": 99},
        "code_review_rate_limit": {"used_percent": 99},
    }
    windows = parse_usage_response(body)
    assert len(windows) == 1
    assert windows[0].used_fraction == 0.01


def test_no_windows_returns_empty_list():
    assert parse_usage_response({}) == []
    assert parse_usage_response({"rate_limit": {}}) == []


def test_window_missing_used_percent_raises_bad_response():
    body = {"rate_limit": {"primary_window": {"limit_window_seconds": 300}}}
    with pytest.raises(ProviderError) as exc_info:
        parse_usage_response(body)
    assert exc_info.value.kind == ErrorKind.BAD_RESPONSE


@pytest.mark.parametrize(
    "seconds,expected_label",
    [
        (59 * 60, "59m limit"),
        (60 * 60, "1h limit"),
        (23 * 3600 + 59 * 60, "23h limit"),
        (24 * 3600, "1d limit"),
        (7 * 86400, "Weekly limit"),
        (30 * 86400, "Monthly limit"),
        (45 * 86400, "45d limit"),
    ],
)
def test_label_boundaries(seconds, expected_label):
    body = {
        "rate_limit": {
            "primary_window": {"used_percent": 1, "limit_window_seconds": seconds}
        }
    }
    windows = parse_usage_response(body)
    assert windows[0].label == expected_label
