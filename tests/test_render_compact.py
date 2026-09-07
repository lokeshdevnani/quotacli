import datetime as dt

from quotacli.protocol import ErrorKind, LimitWindow, ProviderError, Snapshot
from quotacli.render import build_compact
from quotacli.render import RenderRow


def _snapshot(windows: list[LimitWindow], provider_id="claude-code") -> Snapshot:
    return Snapshot(
        provider_id=provider_id,
        fetched_at=dt.datetime.now(dt.timezone.utc),
        windows=windows,
    )


def test_picks_highest_used_fraction_as_headline():
    windows = [
        LimitWindow(label="A", used_fraction=0.2, resets_at=None),
        LimitWindow(label="B", used_fraction=0.9, resets_at=None),
        LimitWindow(label="C", used_fraction=0.5, resets_at=None),
    ]
    row = RenderRow.from_snapshot("claude-code", "Claude Code", _snapshot(windows))
    line = build_compact([row])
    assert "90%" in line


def test_no_numeric_windows_shows_dash():
    windows = [LimitWindow(label="Requests today", used_fraction=None, resets_at=None, detail="~5")]
    row = RenderRow.from_snapshot("antigravity", "Antigravity", _snapshot(windows))
    line = build_compact([row])
    assert "—" in line


def test_error_row_with_no_snapshot_shows_question_mark():
    row = RenderRow.from_error(
        "grok", "Grok", ProviderError(ErrorKind.NEEDS_AUTH, "not signed in"), stale_snapshot=None
    )
    line = build_compact([row])
    assert "?" in line


def test_stale_snapshot_still_shown_but_dimmed():
    windows = [LimitWindow(label="A", used_fraction=0.42, resets_at=None)]
    row = RenderRow.from_error(
        "claude-code",
        "Claude Code",
        ProviderError(ErrorKind.RATE_LIMITED, "rate-limited"),
        stale_snapshot=_snapshot(windows),
    )
    line = build_compact([row])
    assert "42%" in line
    assert "[dim]" in line


def test_multiple_providers_space_separated():
    row_a = RenderRow.from_snapshot(
        "claude-code", "Claude Code", _snapshot([LimitWindow("A", 0.1, None)], "claude-code")
    )
    row_b = RenderRow.from_snapshot("codex", "Codex", _snapshot([LimitWindow("A", 0.2, None)], "codex"))
    line = build_compact([row_a, row_b])
    assert line.count(" ") == 1
