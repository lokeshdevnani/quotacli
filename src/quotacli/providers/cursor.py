"""Cursor provider: reads the editor's own VS-Code-fork global-state SQLite
DB (read-only) and hits cursor.com's usage-summary endpoint with a cookie
built from the borrowed session token.

Reference: Codenotch's CursorCredentials.swift / CursorLocalProvider.swift /
CursorUsage.swift.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

import httpx

from quotacli import httpclient
from quotacli.protocol import ErrorKind, LimitWindow, Provider, ProviderError, Snapshot

USAGE_URL = "https://cursor.com/api/usage-summary"
DB_PATH = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Cursor"
    / "User"
    / "globalStorage"
    / "state.vscdb"
)

_KEYS = (
    "cursorAuth/accessToken",
    "cursorAuth/stripeMembershipAuthId",
    "cursorAuth/cachedEmail",
    "cursorAuth/stripeMembershipType",
)


def _open_readonly(path: Path) -> sqlite3.Connection:
    """mode=ro first (sees WAL-mode writes while Cursor is running, needs the
    -shm/-wal sidecars), falling back to immutable=1 (safe once there's no
    WAL to miss, e.g. Cursor isn't running)."""
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return sqlite3.connect(f"file:{path}?immutable=1", uri=True)


def _read_credential() -> dict[str, str]:
    if not DB_PATH.exists():
        raise ProviderError(ErrorKind.NEEDS_AUTH, "Cursor: not installed (no state.vscdb)")

    try:
        conn = _open_readonly(DB_PATH)
    except sqlite3.OperationalError as exc:
        raise ProviderError(ErrorKind.BAD_RESPONSE, f"Cursor: cannot open state.vscdb: {exc}") from exc

    try:
        cur = conn.cursor()
        values: dict[str, str] = {}
        for key in _KEYS:
            cur.execute("SELECT value FROM ItemTable WHERE key = ?", (key,))
            row = cur.fetchone()
            if row is not None:
                values[key] = row[0]
        return values
    finally:
        conn.close()


def _build_cookie(values: dict[str, str]) -> str:
    account_id = values.get("cursorAuth/stripeMembershipAuthId")
    access_token = values.get("cursorAuth/accessToken")
    if not account_id or not access_token:
        raise ProviderError(ErrorKind.NEEDS_AUTH, "Cursor: not signed in")
    return f"WorkosCursorSessionToken={account_id}::{access_token}"


def parse_usage_summary(body: dict) -> list[LimitWindow]:
    """Pure parsing function, unit-testable against canned fixtures.

    Cursor meters an allowance, not a raw request count — `used`/`limit`
    can sit at 0 on a free plan even with real usage, since free-tier
    allowance arrives via `breakdown.bonus`. The headline window is always
    `plan.totalPercentUsed`.
    """
    individual = body.get("individualUsage") or {}
    plan = individual.get("plan") or {}
    on_demand = individual.get("onDemand") or {}

    resets_at = _parse_iso(body.get("billingCycleEnd"))
    windows: list[LimitWindow] = []

    total_percent = plan.get("totalPercentUsed")
    if total_percent is not None:
        windows.append(
            LimitWindow(label="Included usage", used_fraction=total_percent / 100.0, resets_at=resets_at)
        )

    api_percent = plan.get("apiPercentUsed")
    if api_percent:
        windows.append(LimitWindow(label="API usage", used_fraction=api_percent / 100.0, resets_at=resets_at))

    if on_demand.get("enabled") and (on_demand.get("limit") or 0) > 0:
        used = on_demand.get("used", 0)
        limit = on_demand["limit"]
        windows.append(
            LimitWindow(
                label="On-demand",
                used_fraction=used / limit if limit else None,
                resets_at=resets_at,
                detail=f"${used:.2f} of ${limit:.2f}",
            )
        )

    return windows


def _parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class CursorProvider(Provider):
    id = "cursor"
    display_name = "Cursor"

    def fetch_snapshot(self) -> Snapshot:
        # Re-read on every fetch (never cached) — the editor rotates the
        # token itself, and a cached copy could go stale mid-session.
        values = _read_credential()
        cookie = _build_cookie(values)

        try:
            with httpclient.make_client() as client:
                resp = client.get(
                    USAGE_URL, headers={"Cookie": cookie, "Accept": "application/json"}
                )
        except httpx.HTTPError as exc:
            raise ProviderError(ErrorKind.BAD_RESPONSE, f"Cursor: network error: {exc}") from exc

        if resp.status_code in (401, 403):
            raise ProviderError(ErrorKind.NEEDS_AUTH, f"Cursor: HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise ProviderError(ErrorKind.BAD_RESPONSE, f"Cursor: HTTP {resp.status_code}")

        try:
            body = resp.json()
        except json.JSONDecodeError as exc:
            raise ProviderError(ErrorKind.BAD_RESPONSE, "Cursor: non-JSON response") from exc

        windows = parse_usage_summary(body)
        if not windows:
            plan_name = (body.get("membershipType") or "current").title()
            if body.get("isUnlimited"):
                raise ProviderError(
                    ErrorKind.NOTHING_METERED,
                    f"Cursor: unlimited on the {plan_name} plan — nothing to meter",
                )
            raise ProviderError(
                ErrorKind.NOTHING_METERED,
                f"Cursor: the {plan_name} plan has nothing to meter yet",
            )

        return Snapshot(
            provider_id=self.id,
            fetched_at=dt.datetime.now(dt.timezone.utc),
            windows=windows,
            plan_label=body.get("membershipType"),
        )
