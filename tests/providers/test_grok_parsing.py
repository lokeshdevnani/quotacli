import datetime as dt
import json
from pathlib import Path

from quotacli.providers.grok import _is_trusted, parse_billing_response

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_credit_usage_percent_headline_window():
    body = _load("grok_billing.json")
    windows = parse_billing_response(body)

    assert len(windows) == 1
    assert windows[0].label == "Grok Build"
    assert windows[0].used_fraction == 0.08
    assert windows[0].resets_at == dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc)


def test_reset_falls_back_to_billing_period_end_when_current_period_missing():
    body = {
        "config": {
            "creditUsagePercent": 5.0,
            "billingPeriodEnd": "2026-09-30T00:00:00Z",
        }
    }
    windows = parse_billing_response(body)
    assert windows[0].resets_at == dt.datetime(2026, 9, 30, tzinfo=dt.timezone.utc)


def test_falls_back_to_product_usage_when_credit_percent_absent():
    body = {
        "config": {
            "productUsage": [
                {"product": "GrokBuild", "usagePercent": 15.0},
                {"product": "GrokChat", "usagePercent": 3.0},
            ]
        }
    }
    windows = parse_billing_response(body)
    assert [w.label for w in windows] == ["Grok Build", "Grok Chat"]
    assert windows[0].used_fraction == 0.15
    assert windows[1].used_fraction == 0.03


def test_nothing_metered_returns_empty_list():
    assert parse_billing_response({}) == []
    assert parse_billing_response({"config": {}}) == []


def test_trusted_issuer_by_key_prefix():
    assert _is_trusted("https://auth.x.ai::client123", {}) is True


def test_trusted_issuer_by_oidc_field():
    assert _is_trusted("some-other-key", {"oidc_issuer": "https://auth.x.ai"}) is True


def test_untrusted_enterprise_entry_excluded():
    assert _is_trusted("https://enterprise.okta.com::client", {}) is False
