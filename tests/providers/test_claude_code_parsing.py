import datetime as dt
import json
from pathlib import Path

from quotacli.providers.claude_code import parse_usage_response

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_limits_preferred_over_duplicate_fallback_windows():
    """When limits[] already covers session/weekly_all, the five_hour/
    seven_day fallback windows must not also appear (they carry identical
    data under different field names — verified against the live API)."""
    body = _load("claude_code_usage.json")
    windows = parse_usage_response(body)

    assert len(windows) == 2
    labels = [w.label for w in windows]
    assert labels == ["Session", "Weekly All"]

    session = windows[0]
    assert session.used_fraction == 0.20
    assert session.resets_at == dt.datetime(
        2026, 9, 7, 18, 10, 0, 276877, tzinfo=dt.timezone.utc
    )

    weekly = windows[1]
    assert weekly.used_fraction == 0.86


def test_fallback_windows_fire_when_counterpart_kind_missing():
    """limits[] here only has weekly_opus — no session or weekly_all — so
    both five_hour and seven_day fallback windows should be added alongside
    it, not skipped."""
    body = _load("claude_code_usage_fallback_merge.json")
    windows = parse_usage_response(body)

    labels = {w.label for w in windows}
    assert labels == {"Weekly Opus", "5h limit", "Weekly limit"}

    by_label = {w.label: w for w in windows}
    assert by_label["5h limit"].used_fraction == 0.42
    assert by_label["Weekly limit"].used_fraction == 0.12
    assert by_label["Weekly Opus"].used_fraction == 0.05


def test_empty_limits_and_no_fallback_windows_returns_empty():
    assert parse_usage_response({}) == []
    assert parse_usage_response({"limits": []}) == []


def test_limits_entry_missing_kind_is_skipped():
    body = {"limits": [{"percent": 50, "resets_at": None}]}
    assert parse_usage_response(body) == []


def test_window_with_no_resets_at_is_none():
    body = {"limits": [{"kind": "session", "percent": 10, "resets_at": None}]}
    windows = parse_usage_response(body)
    assert windows[0].resets_at is None
