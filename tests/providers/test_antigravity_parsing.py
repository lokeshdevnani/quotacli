import datetime as dt
import json
from pathlib import Path

from quotacli.providers.antigravity import (
    parse_bridge_response,
    parse_direct_quota_response,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_bridge_response_disambiguates_same_named_buckets_by_group():
    """Two groups (Gemini Models, Claude and GPT models) each have their own
    'Weekly Limit Remaining' / 'Five Hour Limit Remaining' buckets — the
    group name must be part of the label or these collide (verified live)."""
    body = _load("antigravity_quota_summary_bridge.json")
    windows = parse_bridge_response(body)

    labels = [w.label for w in windows]
    assert labels == [
        "Gemini Models (Weekly)",
        "Gemini Models (5h)",
        "Claude and GPT models (Weekly)",
        "Claude and GPT models (5h)",
    ]


def test_bridge_response_inverts_remaining_fraction_to_used():
    body = _load("antigravity_quota_summary_bridge.json")
    windows = parse_bridge_response(body)

    gemini_weekly = windows[0]
    assert abs(gemini_weekly.used_fraction - (1 - 0.7542135)) < 1e-9
    assert gemini_weekly.resets_at == dt.datetime(
        2026, 9, 12, 16, 57, 10, tzinfo=dt.timezone.utc
    )

    gemini_5h = windows[1]
    assert gemini_5h.used_fraction == 0.0  # remainingFraction 1 -> fully unused


def test_bridge_response_empty_groups_returns_empty():
    assert parse_bridge_response({}) == []
    assert parse_bridge_response({"response": {"groups": []}}) == []


def test_direct_quota_sanity_filter_excludes_zero_limit_and_out_of_range_used():
    """limit<=0 (nothing published) and used>limit*1.5 (implausible,
    reverse-engineered-shape sanity check) buckets must both be dropped."""
    body = _load("antigravity_quota_summary_fallback.json")
    windows = parse_direct_quota_response(body)

    assert len(windows) == 1
    assert windows[0].label == "Gemini Pro"
    assert windows[0].used_fraction == 0.4
    assert windows[0].resets_at == dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc)


def test_direct_quota_top_level_buckets_without_groups():
    body = {"buckets": [{"displayName": "X", "used": 1, "limit": 2}]}
    windows = parse_direct_quota_response(body)
    assert len(windows) == 1
    assert windows[0].used_fraction == 0.5


def test_direct_quota_empty_returns_empty():
    assert parse_direct_quota_response({}) == []
