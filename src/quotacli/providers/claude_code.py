"""Claude Code provider: reads the OAuth token Claude Code itself stores in
the macOS Keychain and hits its undocumented /usage endpoint.

Reference: Codenotch's Sources/Providers/Claude*.swift. Claude Code writes a
brand-new keychain item on every token rotation instead of updating one in
place, so `keychain.read_newest_generic_password` (enumerate + pick newest by
modification date) is required, not a naive single lookup.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os

import httpx

from quotacli import backoff, httpclient, keychain, state
from quotacli.protocol import ErrorKind, LimitWindow, Provider, ProviderError, Snapshot

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
BASE_SERVICE = "Claude Code-credentials"

# Each fallback window (top-level five_hour/seven_day) is skipped if `limits[]`
# already has an entry whose `kind` is its semantic counterpart — verified live:
# limits' "session" and five_hour carry identical percent/resets_at, as do
# "weekly_all" and seven_day, so these are the same window under two field
# names, not distinct ones. Falling back still fires if limits lacks that
# kind entirely (e.g. it vanished right as the window reset).
_FALLBACK_WINDOWS = (
    ("five_hour", {"session", "five_hour"}, "5h limit"),
    ("seven_day", {"weekly_all", "seven_day"}, "Weekly limit"),
)

_KIND_LABELS = {
    "five_hour": "5h limit",
    "seven_day": "Weekly limit",
}


def _service_name() -> str:
    """Default service unless CLAUDE_CONFIG_DIR points at a non-default
    profile, in which case Claude Code suffixes the keychain service with
    -<8hex> = SHA256(config_dir_path).hexdigest()[:8] — confirmed directly
    against Codenotch's ClaudeProfile.swift (keychainSuffix(forPath:))."""
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if not config_dir:
        return BASE_SERVICE
    digest = hashlib.sha256(config_dir.encode()).hexdigest()[:8]
    return f"{BASE_SERVICE}-{digest}"


def _read_credential() -> dict:
    try:
        raw = keychain.read_newest_generic_password(_service_name())
    except keychain.KeychainError as exc:
        raise ProviderError(
            ErrorKind.NEEDS_AUTH,
            "Claude Code: not signed in (no keychain credential found)",
        ) from exc
    try:
        payload = json.loads(raw)
        return payload["claudeAiOauth"]
    except (json.JSONDecodeError, KeyError) as exc:
        raise ProviderError(
            ErrorKind.BAD_RESPONSE, "Claude Code: malformed keychain payload"
        ) from exc


def _label_for_kind(kind: str) -> str:
    return _KIND_LABELS.get(kind, kind.replace("_", " ").title())


def _parse_resets_at(value: str | None) -> dt.datetime | None:
    """resets_at is an ISO8601 string (fractional seconds, explicit offset,
    e.g. "2026-09-07T18:10:00.276877+00:00") — confirmed against the live
    API response, not epoch seconds."""
    if not value:
        return None
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _window_from_limits_entry(entry: dict) -> LimitWindow:
    used = entry.get("percent")
    return LimitWindow(
        label=_label_for_kind(entry.get("kind", "")),
        used_fraction=(used / 100.0) if used is not None else None,
        resets_at=_parse_resets_at(entry.get("resets_at")),
    )


def _window_from_fallback(fallback: dict, label: str) -> LimitWindow:
    used = fallback.get("utilization")
    return LimitWindow(
        label=label,
        used_fraction=(used / 100.0) if used is not None else None,
        resets_at=_parse_resets_at(fallback.get("resets_at")),
    )


def parse_usage_response(body: dict) -> list[LimitWindow]:
    """Pure parsing function, unit-testable against canned fixtures without
    any network/keychain access.

    `limits[]` is preferred; `five_hour`/`seven_day` top-level windows are
    merged in only when `limits` has no entry for their semantic counterpart
    kind, because a `limits` entry can vanish right as it resets while the
    fallback window briefly still carries it.
    """
    windows: list[LimitWindow] = []
    seen_kinds: set[str] = set()

    for entry in body.get("limits", []) or []:
        kind = entry.get("kind")
        if kind is None:
            continue
        seen_kinds.add(kind)
        windows.append(_window_from_limits_entry(entry))

    for body_key, counterpart_kinds, label in _FALLBACK_WINDOWS:
        fallback_window = body.get(body_key)
        if fallback_window and seen_kinds.isdisjoint(counterpart_kinds):
            windows.append(_window_from_fallback(fallback_window, label))

    return windows


class ClaudeCodeProvider(Provider):
    id = "claude-code"
    display_name = "Claude Code"

    def __init__(self, store: state.StateStore | None = None):
        self._store = store or state.StateStore()

    def fetch_snapshot(self) -> Snapshot:
        self._check_backoff()

        credential = _read_credential()
        token = credential.get("accessToken")
        if not token:
            raise ProviderError(
                ErrorKind.NEEDS_AUTH, "Claude Code: keychain item missing accessToken"
            )

        try:
            body = self._call_usage_api(token)
        except ProviderError as exc:
            if exc.kind == ErrorKind.CREDENTIAL_EXPIRED:
                # 401/403: drop the cached read, retry once with a fresh one
                # in case Claude Code rotated the token mid-flight.
                credential = _read_credential()
                token = credential.get("accessToken")
                if not token:
                    raise ProviderError(
                        ErrorKind.NEEDS_AUTH, "Claude Code: no accessToken on retry"
                    ) from exc
                body = self._call_usage_api(token)
            else:
                raise

        self._store.clear_backoff(self.id)
        windows = parse_usage_response(body)
        snapshot = Snapshot(
            provider_id=self.id,
            fetched_at=dt.datetime.now(dt.timezone.utc),
            windows=windows,
            plan_label=credential.get("subscriptionType"),
        )
        self._store.put_snapshot(snapshot)
        return snapshot

    def _check_backoff(self) -> None:
        entry = self._store.get_backoff_deadline(self.id)
        if entry is None:
            return
        deadline, _attempt = entry
        now = dt.datetime.now(dt.timezone.utc)
        if now < deadline:
            raise ProviderError(
                ErrorKind.RATE_LIMITED,
                f"Claude Code: rate-limited, retry after {deadline.isoformat()}",
                retry_after=deadline,
            )

    def _call_usage_api(self, token: str) -> dict:
        headers = {
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
        }
        try:
            with httpclient.make_client() as client:
                resp = client.get(USAGE_URL, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError(
                ErrorKind.BAD_RESPONSE, f"Claude Code: network error: {exc}"
            ) from exc

        if resp.status_code in (401, 403):
            raise ProviderError(
                ErrorKind.CREDENTIAL_EXPIRED, f"Claude Code: HTTP {resp.status_code}"
            )

        if resp.status_code == 429:
            entry = self._store.get_backoff_deadline(self.id)
            attempt = entry[1] if entry else 0
            retry_after = backoff.parse_retry_after(resp.headers.get("Retry-After"))
            wait_seconds = backoff.claude_code_backoff_seconds(attempt, retry_after)
            deadline = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=wait_seconds)
            self._store.set_backoff_deadline(self.id, deadline, attempt=attempt + 1)
            raise ProviderError(
                ErrorKind.RATE_LIMITED,
                f"Claude Code: 429, backing off until {deadline.isoformat()}",
                retry_after=deadline,
            )

        if resp.status_code >= 400:
            raise ProviderError(ErrorKind.BAD_RESPONSE, f"Claude Code: HTTP {resp.status_code}")

        try:
            return resp.json()
        except json.JSONDecodeError as exc:
            raise ProviderError(
                ErrorKind.BAD_RESPONSE, "Claude Code: non-JSON response"
            ) from exc
