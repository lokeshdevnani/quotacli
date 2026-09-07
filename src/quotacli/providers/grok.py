"""Grok provider: reads the Grok CLI's own session file and hits its billing
endpoint. Simplest of the five — plain JSON file, no keychain, flat 60s
backoff on 429 with no persistence across runs.

Reference: Codenotch's GrokCredentials.swift / GrokLocalProvider.swift /
GrokUsage.swift.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import httpx

from quotacli import httpclient
from quotacli.protocol import ErrorKind, LimitWindow, Provider, ProviderError, Snapshot

BILLING_URL = "https://cli-chat-proxy.grok.com/v1/billing?format=credits"
AUTH_PATH = Path.home() / ".grok" / "auth.json"
TRUSTED_ISSUER = "https://auth.x.ai"
DEFAULT_TOKEN_TTL = dt.timedelta(days=30)


def _is_trusted(key: str, entry: dict) -> bool:
    if key.startswith(TRUSTED_ISSUER):
        return True
    return entry.get("oidc_issuer") == TRUSTED_ISSUER


def _read_credential() -> dict:
    """Only entries whose key/oidc_issuer is the public x.ai issuer are
    trusted — this excludes enterprise/customer IdP entries whose tokens
    must never be sent to the public cli-chat-proxy endpoint. Among trusted
    entries, prefer the first still-unexpired one, else fall back to the
    first trusted entry regardless."""
    try:
        raw = AUTH_PATH.read_text()
    except FileNotFoundError as exc:
        raise ProviderError(
            ErrorKind.NEEDS_AUTH, "Grok: not signed in (no ~/.grok/auth.json)"
        ) from exc

    try:
        entries: dict = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderError(ErrorKind.BAD_RESPONSE, "Grok: malformed auth.json") from exc

    trusted = [(key, entry) for key, entry in entries.items() if _is_trusted(key, entry)]
    if not trusted:
        raise ProviderError(ErrorKind.NEEDS_AUTH, "Grok: no trusted credential entry")

    now = dt.datetime.now(dt.timezone.utc)
    for _key, entry in trusted:
        expires_at = _parse_expiry(entry)
        if expires_at is None or expires_at > now:
            return entry
    return trusted[0][1]


def _parse_expiry(entry: dict) -> dt.datetime | None:
    value = entry.get("expires_at")
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _humanize_product(name: str) -> str:
    out = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0:
            out.append(" ")
        out.append(ch)
    return "".join(out)


def parse_billing_response(body: dict) -> list[LimitWindow]:
    """Pure parsing function, unit-testable against canned fixtures."""
    config = body.get("config") or {}
    resets_at = _resolve_reset(config)

    credit_percent = config.get("creditUsagePercent")
    if credit_percent is not None:
        product_usage = config.get("productUsage") or []
        label = "Grok Build"
        if product_usage and product_usage[0].get("product"):
            label = _humanize_product(product_usage[0]["product"])
        return [
            LimitWindow(
                label=label,
                used_fraction=credit_percent / 100.0,
                resets_at=resets_at,
            )
        ]

    windows = []
    for product in config.get("productUsage") or []:
        percent = product.get("usagePercent")
        if percent is None:
            continue
        windows.append(
            LimitWindow(
                label=_humanize_product(product.get("product", "Usage")),
                used_fraction=percent / 100.0,
                resets_at=resets_at,
            )
        )
    return windows


def _resolve_reset(config: dict) -> dt.datetime | None:
    current_period = config.get("currentPeriod") or {}
    end = current_period.get("end") or config.get("billingPeriodEnd")
    if not end:
        return None
    try:
        return dt.datetime.fromisoformat(str(end).replace("Z", "+00:00"))
    except ValueError:
        return None


class GrokProvider(Provider):
    id = "grok"
    display_name = "Grok"

    def fetch_snapshot(self) -> Snapshot:
        credential = _read_credential()
        token = credential.get("key")
        if not token:
            raise ProviderError(ErrorKind.NEEDS_AUTH, "Grok: credential entry missing key")

        expires_at = _parse_expiry(credential)
        if expires_at is not None and expires_at <= dt.datetime.now(dt.timezone.utc):
            raise ProviderError(ErrorKind.CREDENTIAL_EXPIRED, "Grok: token expired")

        headers = {
            "Authorization": f"Bearer {token}",
            "X-XAI-Token-Auth": "xai-grok-cli",
            "Accept": "application/json",
        }
        try:
            with httpclient.make_client() as client:
                resp = client.get(BILLING_URL, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError(ErrorKind.BAD_RESPONSE, f"Grok: network error: {exc}") from exc

        if resp.status_code in (401, 403):
            raise ProviderError(ErrorKind.NEEDS_AUTH, f"Grok: HTTP {resp.status_code}")
        if resp.status_code == 429:
            raise ProviderError(
                ErrorKind.RATE_LIMITED,
                "Grok: rate-limited",
                retry_after=dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60),
            )
        if resp.status_code >= 400:
            raise ProviderError(ErrorKind.BAD_RESPONSE, f"Grok: HTTP {resp.status_code}")

        try:
            body = resp.json()
        except json.JSONDecodeError as exc:
            raise ProviderError(ErrorKind.BAD_RESPONSE, "Grok: non-JSON response") from exc

        windows = parse_billing_response(body)
        if not windows:
            raise ProviderError(ErrorKind.NOTHING_METERED, "Grok: nothing metered")

        return Snapshot(
            provider_id=self.id,
            fetched_at=dt.datetime.now(dt.timezone.utc),
            windows=windows,
        )
