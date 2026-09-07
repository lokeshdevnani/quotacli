"""Rendering: a rich Table for one-shot output, the same table re-rendered
inside a rich Live view for --watch, and a JSON encoder for --json.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json

from rich.console import Console
from rich.table import Table

from quotacli.protocol import ErrorKind, ProviderError, Snapshot

console = Console()


@dataclasses.dataclass(frozen=True, slots=True)
class RenderRow:
    provider_id: str
    display_name: str
    snapshot: Snapshot | None
    error: ProviderError | None = None

    @classmethod
    def from_snapshot(cls, provider_id: str, display_name: str, snapshot: Snapshot) -> "RenderRow":
        return cls(provider_id=provider_id, display_name=display_name, snapshot=snapshot)

    @classmethod
    def from_error(
        cls,
        provider_id: str,
        display_name: str,
        error: ProviderError,
        stale_snapshot: Snapshot | None,
    ) -> "RenderRow":
        snapshot = None
        if stale_snapshot is not None:
            snapshot = dataclasses.replace(stale_snapshot, is_stale=True)
        return cls(provider_id=provider_id, display_name=display_name, snapshot=snapshot, error=error)


def _format_reset(resets_at: dt.datetime | None) -> str:
    if resets_at is None:
        return "—"
    now = dt.datetime.now(dt.timezone.utc)
    delta = resets_at - now
    if delta.total_seconds() <= 0:
        return "now"
    total_minutes = int(delta.total_seconds() // 60)
    days, rem_minutes = divmod(total_minutes, 60 * 24)
    hours, minutes = divmod(rem_minutes, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if not days and minutes:
        parts.append(f"{minutes}m")
    return "in " + " ".join(parts) if parts else "in <1m"


def _format_used(used_fraction: float | None) -> str:
    if used_fraction is None:
        return "—"
    return f"{used_fraction * 100:.0f}%"


_ERROR_MESSAGES = {
    ErrorKind.NEEDS_AUTH: "not signed in",
    ErrorKind.CREDENTIAL_EXPIRED: "credential expired",
    ErrorKind.RATE_LIMITED: "rate-limited",
    ErrorKind.ACCESS_DENIED: "access denied",
    ErrorKind.NOTHING_METERED: "nothing to meter",
    ErrorKind.BAD_RESPONSE: "error",
}


def build_table(rows: list[RenderRow]) -> Table:
    table = Table(show_header=True, header_style="bold")
    table.add_column("Provider")
    table.add_column("Window")
    table.add_column("Used", justify="right")
    table.add_column("Resets")

    for row in rows:
        if row.snapshot is None:
            reason = _ERROR_MESSAGES.get(row.error.kind, "error") if row.error else "error"
            table.add_row(row.display_name, f"[red]{reason}[/red]", "—", "—")
            continue

        windows = row.snapshot.windows
        name_style = row.display_name
        if row.snapshot.is_stale:
            reason = _ERROR_MESSAGES.get(row.error.kind, "stale") if row.error else "stale"
            name_style = f"{row.display_name} [dim](stale, {reason})[/dim]"

        if not windows:
            table.add_row(name_style, "[dim]nothing metered[/dim]", "—", "—")
            continue

        for i, window in enumerate(windows):
            table.add_row(
                name_style if i == 0 else "",
                window.label,
                _format_used(window.used_fraction),
                _format_reset(window.resets_at),
            )

    return table


def _json_default(value):
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, ErrorKind):
        return value.value
    raise TypeError(f"not JSON serializable: {value!r}")


def build_json(rows: list[RenderRow]) -> str:
    payload = []
    for row in rows:
        entry: dict = {"provider_id": row.provider_id, "display_name": row.display_name}
        if row.snapshot is not None:
            entry["snapshot"] = dataclasses.asdict(row.snapshot)
        if row.error is not None:
            entry["error"] = {"kind": row.error.kind.value, "message": row.error.message}
        payload.append(entry)
    return json.dumps(payload, indent=2, default=_json_default)
