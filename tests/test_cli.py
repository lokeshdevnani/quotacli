import datetime as dt

from quotacli.cli import _visible_rows
from quotacli.protocol import ErrorKind, ProviderError, Snapshot
from quotacli.render import RenderRow


def _needs_auth_row(provider_id="grok") -> RenderRow:
    return RenderRow.from_error(
        provider_id,
        provider_id.title(),
        ProviderError(ErrorKind.NEEDS_AUTH, "not signed in"),
        stale_snapshot=None,
    )


def _ok_row(provider_id="claude-code") -> RenderRow:
    snapshot = Snapshot(
        provider_id=provider_id,
        fetched_at=dt.datetime.now(dt.timezone.utc),
        windows=[],
    )
    return RenderRow.from_snapshot(provider_id, provider_id.title(), snapshot)


def _rate_limited_stale_row(provider_id="claude-code") -> RenderRow:
    snapshot = Snapshot(
        provider_id=provider_id,
        fetched_at=dt.datetime.now(dt.timezone.utc),
        windows=[],
    )
    return RenderRow.from_error(
        provider_id,
        provider_id.title(),
        ProviderError(ErrorKind.RATE_LIMITED, "rate-limited"),
        stale_snapshot=snapshot,
    )


def test_needs_auth_hidden_by_default():
    rows = [_ok_row(), _needs_auth_row()]
    visible = _visible_rows(rows, show_all=False, explicit_ids=set())
    assert [r.provider_id for r in visible] == ["claude-code"]


def test_all_flag_shows_needs_auth():
    rows = [_ok_row(), _needs_auth_row()]
    visible = _visible_rows(rows, show_all=True, explicit_ids=set())
    assert {r.provider_id for r in visible} == {"claude-code", "grok"}


def test_explicit_provider_shown_even_when_needs_auth():
    rows = [_needs_auth_row("grok")]
    visible = _visible_rows(rows, show_all=False, explicit_ids={"grok"})
    assert [r.provider_id for r in visible] == ["grok"]


def test_other_error_kinds_never_hidden():
    """Only NEEDS_AUTH-with-nothing-cached is noise; a rate-limited
    provider you actually use should still surface, stale snapshot and all."""
    rows = [_rate_limited_stale_row()]
    visible = _visible_rows(rows, show_all=False, explicit_ids=set())
    assert len(visible) == 1


def test_needs_auth_with_stale_snapshot_not_hidden():
    """If a provider was previously signed in and has cached data, a
    NEEDS_AUTH error now (e.g. signed out) should still surface the stale
    reading rather than disappearing silently."""
    snapshot = Snapshot(
        provider_id="claude-code",
        fetched_at=dt.datetime.now(dt.timezone.utc),
        windows=[],
    )
    row = RenderRow.from_error(
        "claude-code",
        "Claude Code",
        ProviderError(ErrorKind.NEEDS_AUTH, "not signed in"),
        stale_snapshot=snapshot,
    )
    visible = _visible_rows([row], show_all=False, explicit_ids=set())
    assert len(visible) == 1
