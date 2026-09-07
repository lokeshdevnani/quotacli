# quotacli

Terminal quota tracker for Claude Code, Cursor, Codex, Antigravity, and Grok.
Reads the credentials each tool already stores locally on your Mac — no new
sign-in flow, nothing sent anywhere except each provider's own usage endpoint.

macOS only: every provider's read mechanism (Keychain, `~/Library/...` paths,
`lsof`/`ps` process discovery) is macOS-specific.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Usage

```
quotacli                                  # one-shot table, all 5 providers
quotacli --watch [--interval 30]          # live-refreshing view, like top
quotacli --json                           # machine-readable output
quotacli --provider claude-code           # limit to one or more providers (repeatable)
quotacli --provider antigravity -v        # report which of Antigravity's 4 data-source tiers answered
```

State (last-known-good snapshots, and Claude Code/Codex 429 backoff
deadlines) persists at `~/.config/quotacli/state.json`.

## Development

```bash
pip install -e ".[dev]"
pytest
```

All 5 providers' undocumented, reverse-engineered endpoints can drift without
notice. The pure response-parsing function per provider
(`providers/<name>.py::parse_*`) is unit-tested against frozen fixtures in
`tests/fixtures/` — periodically run `quotacli <provider-id>` live and diff
against those fixtures to catch upstream API changes.

What isn't (and can't be) covered by the unit tests:

- **Keychain enumeration** (Claude Code, Antigravity) — needs a machine with
  the real tool installed and signed in. Claude Code specifically rotates
  its keychain item on every token refresh instead of updating one in
  place, so the "pick the newest of several duplicates by modification
  date" logic in `keychain.py` can only be validated against a real account
  that's rotated at least once.
- **Antigravity's tier-2 local bridge** (`ps`/`lsof` process discovery,
  scoped self-signed-TLS bypass) — only testable with Antigravity actually
  running. Use `quotacli --provider antigravity -v` to see which of the 4
  tiers answered.
- **429 backoff persisted across real repeated invocations** — the backoff
  math and state round-trip are both unit-tested in isolation and trusted
  to compose correctly; forcing a real 429 against the live API to test the
  full path end-to-end isn't practical.

## Not yet built

A `caffeinate`-style subcommand (keep the Mac awake while a tracked
provider's CLI is actively running) is planned but intentionally deferred —
the CLI is a `click.Group` from the start so it can be added later without
restructuring the entrypoint.
