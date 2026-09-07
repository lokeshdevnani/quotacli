"""Codex provider: reads ~/.codex/auth.json (plain file, no keychain) and
hits ChatGPT's backend usage endpoint.

Reference: Codenotch's CodexCredentials.swift / CodexLocalProvider.swift /
CodexUsage.swift.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import json
from pathlib import Path

import httpx

from quotacli import backoff, httpclient, state
from quotacli.protocol import ErrorKind, LimitWindow, Provider, ProviderError, Snapshot

USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
AUTH_PATH = Path.home() / ".codex" / "auth.json"


def _read_credential() -> dict:
    try:
        raw = AUTH_PATH.read_text()
    except FileNotFoundError as exc:
        raise ProviderError(
            ErrorKind.NEEDS_AUTH, "Codex: not signed in (no ~/.codex/auth.json)"
        ) from exc
    try:
        payload = json.loads(raw)
        return payload["tokens"]
    except (json.JSONDecodeError, KeyError) as exc:
        raise ProviderError(ErrorKind.BAD_RESPONSE, "Codex: malformed auth.json") from exc


def _decode_jwt_payload(token: str) -> dict:
    try:
        _, payload_b64, _ = token.split(".")
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        decoded = base64.urlsafe_b64decode(padded)
        return json.loads(decoded)
    except (ValueError, binascii.Error, json.JSONDecodeError):
        return {}


def _is_locally_expired(access_token: str) -> bool:
    """Soft, local-only check of the JWT's exp claim — the server still
    validates for real; this just avoids a wasted round trip."""
    claims = _decode_jwt_payload(access_token)
    exp = claims.get("exp")
    if exp is None:
        return False
    return dt.datetime.now(dt.timezone.utc).timestamp() >= exp


def _label_for_window_seconds(seconds: float) -> str:
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)}m limit"
    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)}h limit"
    days = int(hours / 24)
    if days == 7:
        return "Weekly limit"
    if days == 30:
        return "Monthly limit"
    return f"{days}d limit"


def _window_from(entry: dict | None) -> LimitWindow | None:
    if not entry:
        return None
    used_percent = entry.get("used_percent")
    if used_percent is None:
        raise ProviderError(ErrorKind.BAD_RESPONSE, "Codex: window missing used_percent")

    reset_at = entry.get("reset_at")
    if reset_at is not None:
        resets_at = dt.datetime.fromtimestamp(reset_at, tz=dt.timezone.utc)
    else:
        reset_after = entry.get("reset_after_seconds")
        resets_at = (
            dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=reset_after)
            if reset_after is not None
            else None
        )

    window_seconds = entry.get("limit_window_seconds")
    label = _label_for_window_seconds(window_seconds) if window_seconds is not None else "Limit"

    return LimitWindow(label=label, used_fraction=used_percent / 100.0, resets_at=resets_at)


def parse_usage_response(body: dict) -> list[LimitWindow]:
    """Pure parsing function, unit-testable against canned fixtures.

    Only primary_window/secondary_window are used — additional_rate_limits
    and code_review_rate_limit (if present) meter something else and are
    deliberately excluded.
    """
    rate_limit = body.get("rate_limit") or {}
    windows = []
    for key in ("primary_window", "secondary_window"):
        window = _window_from(rate_limit.get(key))
        if window is not None:
            windows.append(window)
    return windows


class CodexProvider(Provider):
    id = "codex"
    display_name = "Codex"

    def __init__(self, store: state.StateStore | None = None):
        self._store = store or state.StateStore()

    def fetch_snapshot(self) -> Snapshot:
        self._check_backoff()

        credential = _read_credential()
        access_token = credential.get("access_token")
        account_id = credential.get("account_id")
        if not access_token or not account_id:
            raise ProviderError(ErrorKind.NEEDS_AUTH, "Codex: auth.json missing tokens")

        if _is_locally_expired(access_token):
            raise ProviderError(ErrorKind.CREDENTIAL_EXPIRED, "Codex: token expired")

        body = self._call_usage_api(access_token, account_id)
        self._store.clear_backoff(self.id)

        windows = parse_usage_response(body)
        if not windows:
            raise ProviderError(ErrorKind.NOTHING_METERED, "Codex: no usage windows reported")

        snapshot = Snapshot(
            provider_id=self.id,
            fetched_at=dt.datetime.now(dt.timezone.utc),
            windows=windows,
        )
        self._store.put_snapshot(snapshot)
        return snapshot

    def _check_backoff(self) -> None:
        entry = self._store.get_backoff_deadline(self.id)
        if entry is None:
            return
        deadline, _attempt = entry
        if dt.datetime.now(dt.timezone.utc) < deadline:
            raise ProviderError(
                ErrorKind.RATE_LIMITED,
                f"Codex: rate-limited, retry after {deadline.isoformat()}",
                retry_after=deadline,
            )

    def _call_usage_api(self, access_token: str, account_id: str) -> dict:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "ChatGPT-Account-Id": account_id,
            "Accept": "application/json",
            "Cache-Control": "no-cache, no-store",
        }
        try:
            with httpclient.make_client() as client:
                resp = client.get(USAGE_URL, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError(ErrorKind.BAD_RESPONSE, f"Codex: network error: {exc}") from exc

        if resp.status_code in (401, 403):
            raise ProviderError(ErrorKind.NEEDS_AUTH, f"Codex: HTTP {resp.status_code}")

        if resp.status_code == 429:
            retry_after = backoff.parse_retry_after(resp.headers.get("Retry-After"))
            wait_seconds = backoff.codex_backoff_seconds(retry_after)
            deadline = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=wait_seconds)
            self._store.set_backoff_deadline(self.id, deadline)
            raise ProviderError(
                ErrorKind.RATE_LIMITED,
                f"Codex: 429, backing off until {deadline.isoformat()}",
                retry_after=deadline,
            )

        if resp.status_code >= 400:
            raise ProviderError(ErrorKind.BAD_RESPONSE, f"Codex: HTTP {resp.status_code}")

        try:
            return resp.json()
        except json.JSONDecodeError as exc:
            raise ProviderError(ErrorKind.BAD_RESPONSE, "Codex: non-JSON response") from exc
