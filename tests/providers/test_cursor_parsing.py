import datetime as dt
import json
from pathlib import Path

from quotacli.providers.cursor import parse_usage_summary

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_headline_and_api_windows_from_real_shape():
    body = _load("cursor_usage_summary.json")
    windows = parse_usage_summary(body)

    assert len(windows) == 2
    assert windows[0].label == "Included usage"
    assert windows[0].used_fraction == 1.0
    assert windows[0].resets_at == dt.datetime(
        2026, 9, 9, 17, 51, 13, tzinfo=dt.timezone.utc
    )
    assert windows[1].label == "API usage"
    assert windows[1].used_fraction == 1.0


def test_free_plan_bonus_allowance_not_confused_with_zero_usage():
    """Free-tier used/limit sit at 0 even with real usage — the allowance
    arrives via breakdown.bonus. totalPercentUsed is still the correct
    headline figure to report (spec's documented 9.5% example)."""
    body = {
        "billingCycleEnd": "2026-09-09T17:51:13.000Z",
        "individualUsage": {
            "plan": {
                "used": 0,
                "limit": 0,
                "breakdown": {"included": 0, "bonus": 19, "total": 19},
                "autoPercentUsed": 0,
                "apiPercentUsed": 19,
                "totalPercentUsed": 9.5,
            }
        },
    }
    windows = parse_usage_summary(body)
    assert windows[0].label == "Included usage"
    assert windows[0].used_fraction == 0.095
    assert windows[1].label == "API usage"
    assert windows[1].used_fraction == 0.19


def test_api_percent_omitted_when_zero():
    body = {
        "individualUsage": {"plan": {"totalPercentUsed": 5.0, "apiPercentUsed": 0}}
    }
    windows = parse_usage_summary(body)
    assert len(windows) == 1
    assert windows[0].label == "Included usage"


def test_on_demand_window_when_enabled_with_limit():
    body = {
        "individualUsage": {
            "plan": {"totalPercentUsed": 10.0},
            "onDemand": {"enabled": True, "used": 5.0, "limit": 20.0},
        }
    }
    windows = parse_usage_summary(body)
    labels = [w.label for w in windows]
    assert "On-demand" in labels
    on_demand = next(w for w in windows if w.label == "On-demand")
    assert on_demand.used_fraction == 0.25
    assert on_demand.detail == "$5.00 of $20.00"


def test_on_demand_window_skipped_when_disabled():
    body = {
        "individualUsage": {
            "plan": {"totalPercentUsed": 10.0},
            "onDemand": {"enabled": False, "used": 0, "limit": None},
        }
    }
    windows = parse_usage_summary(body)
    assert all(w.label != "On-demand" for w in windows)


def test_nothing_metered_when_plan_dict_empty():
    assert parse_usage_summary({"individualUsage": {"plan": {}}}) == []
    assert parse_usage_summary({}) == []


def test_billing_cycle_end_without_fractional_seconds():
    body = {
        "billingCycleEnd": "2026-09-09T17:51:13+00:00",
        "individualUsage": {"plan": {"totalPercentUsed": 1.0}},
    }
    windows = parse_usage_summary(body)
    assert windows[0].resets_at == dt.datetime(
        2026, 9, 9, 17, 51, 13, tzinfo=dt.timezone.utc
    )
