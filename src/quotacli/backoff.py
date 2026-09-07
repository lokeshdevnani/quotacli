"""Pure backoff/retry-after math, shared by the Claude Code and Codex
providers. Kept dependency-free and side-effect-free so it's trivially
unit-testable without credentials, network, or state.
"""

from __future__ import annotations

import datetime as dt
from email.utils import parsedate_to_datetime


def parse_retry_after(header_value: str | None) -> float:
    """Parse a Retry-After header into seconds (>=0).

    Accepts either a plain integer-seconds string or an HTTP-date string.
    Missing or unparseable input returns 0.0 — callers must never trust this
    value alone (some APIs send `Retry-After: 0`), only use it to raise a
    floor computed independently.
    """
    if not header_value:
        return 0.0
    header_value = header_value.strip()
    if header_value.isdigit():
        return float(header_value)
    try:
        when = parsedate_to_datetime(header_value)
    except (TypeError, ValueError):
        return 0.0
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    delta = (when - dt.datetime.now(dt.timezone.utc)).total_seconds()
    return max(0.0, delta)


def claude_code_backoff_seconds(attempt: int, retry_after_seconds: float) -> float:
    """Exponential backoff for Claude Code's /usage endpoint.

    Floor starts at 60s and doubles per consecutive 429 (attempt is
    0-indexed on the first 429), capped at attempt index 4 (60*2^4=960,
    clamped to the overall 900s/15min ceiling). The server's Retry-After
    header only ever raises this floor, never overrides it — Anthropic's
    endpoint has been observed sending `Retry-After: 0`.
    """
    floor = min(900.0, 60.0 * (2 ** min(attempt, 4)))
    return max(floor, retry_after_seconds)


def codex_backoff_seconds(retry_after_seconds: float) -> float:
    """Flat backoff for Codex's usage endpoint: minimum 60s, or the
    server's Retry-After value if it asks for longer. No exponential
    doubling on repeated 429s (simpler than Claude Code's scheme)."""
    return max(60.0, retry_after_seconds)
