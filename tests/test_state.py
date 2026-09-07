import datetime as dt

from quotacli.protocol import LimitWindow, Snapshot
from quotacli.state import StateStore


def _snapshot(provider_id="claude-code") -> Snapshot:
    return Snapshot(
        provider_id=provider_id,
        fetched_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
        windows=[
            LimitWindow(
                label="Session",
                used_fraction=0.5,
                resets_at=dt.datetime(2026, 1, 1, 5, tzinfo=dt.timezone.utc),
            )
        ],
        plan_label="pro",
    )


def test_missing_state_file_returns_empty(tmp_path):
    store = StateStore(path=tmp_path / "state.json")
    assert store.get_cached_snapshot("claude-code") is None
    assert store.get_backoff_deadline("claude-code") is None


def test_corrupt_state_file_treated_as_empty(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not valid json")
    store = StateStore(path=path)
    assert store.get_cached_snapshot("claude-code") is None


def test_snapshot_round_trip(tmp_path):
    store = StateStore(path=tmp_path / "state.json")
    snapshot = _snapshot()
    store.put_snapshot(snapshot)

    loaded = store.get_cached_snapshot("claude-code")
    assert loaded is not None
    assert loaded.provider_id == "claude-code"
    assert loaded.plan_label == "pro"
    assert loaded.fetched_at == snapshot.fetched_at
    assert len(loaded.windows) == 1
    assert loaded.windows[0].used_fraction == 0.5
    assert loaded.windows[0].resets_at == snapshot.windows[0].resets_at


def test_backoff_round_trip_and_clear(tmp_path):
    store = StateStore(path=tmp_path / "state.json")
    deadline = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    store.set_backoff_deadline("claude-code", deadline, attempt=2)

    entry = store.get_backoff_deadline("claude-code")
    assert entry == (deadline, 2)

    store.clear_backoff("claude-code")
    assert store.get_backoff_deadline("claude-code") is None


def test_multiple_providers_dont_clobber_each_other(tmp_path):
    store = StateStore(path=tmp_path / "state.json")
    store.put_snapshot(_snapshot("claude-code"))
    store.put_snapshot(_snapshot("codex"))

    assert store.get_cached_snapshot("claude-code") is not None
    assert store.get_cached_snapshot("codex") is not None


def test_save_is_atomic_no_leftover_temp_files(tmp_path):
    store = StateStore(path=tmp_path / "state.json")
    store.put_snapshot(_snapshot())

    leftovers = list(tmp_path.glob(".state-*"))
    assert leftovers == []
    assert (tmp_path / "state.json").exists()
