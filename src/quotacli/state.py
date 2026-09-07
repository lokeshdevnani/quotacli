"""Persistent state: last-known-good snapshots (for stale-reading fallback
when a fetch fails) and 429 backoff deadlines (so a re-run of this one-shot
CLI mid-penalty doesn't hammer the API again).

Single JSON file at ~/.config/quotacli/state.json. No file locking — this is
a single-user, effectively single-process-at-a-time CLI; an occasional lost
update under --watch is an acceptable tradeoff for staying dependency-free.
Writes are atomic (temp file + os.replace) since --watch saves on every tick
and a mid-write kill (the normal Ctrl-C exit path) must never corrupt the file.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import tempfile
from pathlib import Path

from quotacli.protocol import LimitWindow, Snapshot

STATE_VERSION = 1


def _default_path() -> Path:
    return Path.home() / ".config" / "quotacli" / "state.json"


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_iso(value: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(value) if value else None


def _snapshot_to_dict(snapshot: Snapshot) -> dict:
    return {
        "fetched_at": _iso(snapshot.fetched_at),
        "plan_label": snapshot.plan_label,
        "windows": [
            {
                "label": w.label,
                "used_fraction": w.used_fraction,
                "resets_at": _iso(w.resets_at),
                "detail": w.detail,
            }
            for w in snapshot.windows
        ],
    }


def _snapshot_from_dict(provider_id: str, data: dict) -> Snapshot:
    windows = [
        LimitWindow(
            label=w["label"],
            used_fraction=w.get("used_fraction"),
            resets_at=_parse_iso(w.get("resets_at")),
            detail=w.get("detail"),
        )
        for w in data.get("windows", [])
    ]
    return Snapshot(
        provider_id=provider_id,
        fetched_at=_parse_iso(data.get("fetched_at")) or dt.datetime.now(dt.timezone.utc),
        windows=windows,
        plan_label=data.get("plan_label"),
    )


@dataclasses.dataclass
class StateFile:
    snapshots: dict[str, dict] = dataclasses.field(default_factory=dict)
    backoff: dict[str, dict] = dataclasses.field(default_factory=dict)


class StateStore:
    def __init__(self, path: Path | None = None):
        self.path = path or _default_path()

    def load(self) -> StateFile:
        try:
            raw = json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return StateFile()
        return StateFile(
            snapshots=raw.get("snapshots", {}),
            backoff=raw.get("backoff", {}),
        )

    def save(self, state: StateFile) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": STATE_VERSION,
            "snapshots": state.snapshots,
            "backoff": state.backoff,
        }
        fd, tmp_path = tempfile.mkstemp(
            dir=self.path.parent, prefix=".state-", suffix=".json.tmp"
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp_path, self.path)
        except BaseException:
            Path(tmp_path).unlink(missing_ok=True)
            raise

    def get_cached_snapshot(self, provider_id: str) -> Snapshot | None:
        state = self.load()
        data = state.snapshots.get(provider_id)
        if data is None:
            return None
        return _snapshot_from_dict(provider_id, data)

    def put_snapshot(self, snapshot: Snapshot) -> None:
        state = self.load()
        state.snapshots[snapshot.provider_id] = _snapshot_to_dict(snapshot)
        self.save(state)

    def get_backoff_deadline(self, provider_id: str) -> tuple[dt.datetime, int] | None:
        state = self.load()
        entry = state.backoff.get(provider_id)
        if entry is None:
            return None
        deadline = _parse_iso(entry.get("deadline"))
        if deadline is None:
            return None
        return deadline, entry.get("attempt", 0)

    def set_backoff_deadline(
        self, provider_id: str, deadline: dt.datetime, attempt: int = 0
    ) -> None:
        state = self.load()
        state.backoff[provider_id] = {"deadline": _iso(deadline), "attempt": attempt}
        self.save(state)

    def clear_backoff(self, provider_id: str) -> None:
        state = self.load()
        if provider_id in state.backoff:
            del state.backoff[provider_id]
            self.save(state)
