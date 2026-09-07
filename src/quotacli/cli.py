"""quotacli entrypoint. A click Group (not a bare command) so a future
`quotacli caffeinate` subcommand can be added later without restructuring.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import click
from rich.live import Live

from quotacli.protocol import ErrorKind, Provider, ProviderError
from quotacli.providers import build_providers
from quotacli.render import RenderRow, build_json, build_table, console
from quotacli.state import StateStore

DEFAULT_INTERVAL = 30


def _safe_fetch(provider: Provider, store: StateStore) -> RenderRow:
    try:
        snapshot = provider.fetch_snapshot()
        return RenderRow.from_snapshot(provider.id, provider.display_name, snapshot)
    except ProviderError as exc:
        cached = store.get_cached_snapshot(provider.id)
        return RenderRow.from_error(provider.id, provider.display_name, exc, cached)


def gather_rows(providers: list[Provider], store: StateStore) -> list[RenderRow]:
    order = {p.id: i for i, p in enumerate(providers)}
    rows: list[RenderRow] = []
    with ThreadPoolExecutor(max_workers=max(1, len(providers))) as pool:
        futures = {pool.submit(_safe_fetch, p, store): p for p in providers}
        for future in as_completed(futures):
            rows.append(future.result())
    rows.sort(key=lambda r: order[r.provider_id])
    return rows


def _visible_rows(rows: list[RenderRow], *, show_all: bool, explicit_ids: set[str]) -> list[RenderRow]:
    """Hide providers that have never been signed in and have nothing
    cached to show — pure noise if you don't use that tool. A provider
    named explicitly via --provider is always shown regardless (asking for
    it by name is an unambiguous request to see it, even if unauthenticated),
    and --all bypasses this filter entirely."""
    if show_all:
        return rows
    visible = []
    for row in rows:
        if row.provider_id in explicit_ids:
            visible.append(row)
            continue
        if (
            row.snapshot is None
            and row.error is not None
            and row.error.kind == ErrorKind.NEEDS_AUTH
        ):
            continue
        visible.append(row)
    return visible


def _print_verbose_tiers(providers: list[Provider]) -> None:
    """Antigravity's bridge-discovery tier can't be meaningfully unit-tested
    end-to-end, so -v reports which of its 4 tiers actually answered."""
    for provider in providers:
        tier = getattr(provider, "last_tier", None)
        if tier is not None:
            console.print(f"[dim]{provider.display_name}: answered via {tier}[/dim]", highlight=False)


@click.group(invoke_without_command=True)
@click.option("--watch", is_flag=True, help="Live-refreshing view, like top.")
@click.option(
    "--interval",
    default=DEFAULT_INTERVAL,
    show_default=True,
    help="Refresh interval in seconds for --watch.",
)
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
@click.option(
    "--provider",
    "provider_ids",
    multiple=True,
    help="Limit to one or more provider ids (repeatable), e.g. --provider claude-code.",
)
@click.option("-v", "--verbose", is_flag=True, help="Report which data source answered per provider.")
@click.option(
    "-a",
    "--all",
    "show_all",
    is_flag=True,
    help="Include providers that aren't signed in (hidden by default).",
)
@click.pass_context
def main(
    ctx: click.Context,
    watch: bool,
    interval: int,
    as_json: bool,
    provider_ids: tuple[str, ...],
    verbose: bool,
    show_all: bool,
) -> None:
    """Quota status for Claude Code, Cursor, Codex, Antigravity, and Grok.

    Providers that have never been signed in are hidden by default; pass
    -a/--all to see them too.
    """
    if ctx.invoked_subcommand is not None:
        return

    providers = build_providers()
    explicit_ids: set[str] = set(provider_ids)
    if explicit_ids:
        known = {p.id for p in providers}
        unknown = explicit_ids - known
        if unknown:
            raise click.UsageError(
                f"Unknown provider id(s): {', '.join(sorted(unknown))}. "
                f"Known: {', '.join(sorted(known))}."
            )
        providers = [p for p in providers if p.id in explicit_ids]

    store = StateStore()

    if as_json and watch:
        raise click.UsageError("--json and --watch cannot be combined.")
    if verbose and watch:
        raise click.UsageError("--verbose and --watch cannot be combined.")

    if as_json:
        rows = _visible_rows(gather_rows(providers, store), show_all=show_all, explicit_ids=explicit_ids)
        console.print_json(build_json(rows))
        return

    if not watch:
        rows = _visible_rows(gather_rows(providers, store), show_all=show_all, explicit_ids=explicit_ids)
        console.print(build_table(rows))
        if verbose:
            _print_verbose_tiers(providers)
        return

    with Live(console=console, refresh_per_second=1) as live:
        try:
            while True:
                rows = _visible_rows(
                    gather_rows(providers, store), show_all=show_all, explicit_ids=explicit_ids
                )
                live.update(build_table(rows))
                time.sleep(interval)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
