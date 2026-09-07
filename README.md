# quotacli

[![CI](https://github.com/lokeshdevnani/quotacli/actions/workflows/ci.yml/badge.svg)](https://github.com/lokeshdevnani/quotacli/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/quotacli-mac.svg)](https://pypi.org/project/quotacli-mac/)
[![Python](https://img.shields.io/pypi/pyversions/quotacli-mac.svg)](https://pypi.org/project/quotacli-mac/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

One command to see how much of Claude Code, Cursor, Codex, Antigravity, and
Grok you have left — and when it resets — without opening five different
apps or dashboards.

![quotacli demo: one-shot table, --compact statusline output, and --watch live-refreshing](docs/demo.gif)

Or, embedded in a tmux status line: `◆ 88% · ◇ 49% · ▲ 100% · ● 25%`

It works by reading the credentials each of those tools already stores
locally on your Mac — no new sign-in flow, nothing sent anywhere except each
provider's own usage endpoint. **macOS only**: every read mechanism
(Keychain, `~/Library/...` paths, `lsof`/`ps` process discovery) is
macOS-specific.

## Install

```bash
pip install quotacli-mac
```

(the PyPI distribution is named `quotacli-mac` — PyPI's anti-squatting check
blocked the plain `quotacli` name as too close to an existing project — but
the command it installs is just `quotacli`.)

Or from source:

```bash
git clone https://github.com/lokeshdevnani/quotacli.git
cd quotacli
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Usage

```
quotacli                                  # one-shot table, all 5 providers
quotacli --watch [--interval 30]          # live-refreshing view, like top
quotacli --compact                        # single line, statusline-style
quotacli --json                           # machine-readable output
quotacli --all                            # include providers you've never signed in to (hidden by default)
quotacli --provider claude-code           # limit to one or more providers (repeatable)
quotacli --provider antigravity -v        # report which of Antigravity's 4 data-source tiers answered
```

State (last-known-good snapshots, and Claude Code/Codex 429 backoff
deadlines) persists at `~/.config/quotacli/state.json`.

### tmux status line

```tmux
set -g status-right '#(~/.venv/bin/quotacli --compact) | %H:%M'
```

(point that at wherever `quotacli` actually lives — the venv's `bin/`, or
just `quotacli` if you installed it globally via pip.)

## Development

```bash
pip install -e ".[dev]"
pytest
```

The README demo is recorded, not hand-drawn — regenerate it after a visible
output change with:

```bash
brew install asciinema agg
asciinema rec --window-size 104x23 --command "bash docs/demo.sh" docs/demo.cast --overwrite -q
agg --idle-time-limit 1.2 --font-size 15 docs/demo.cast docs/demo.gif
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

- A `caffeinate`-style subcommand (keep the Mac awake while a tracked
  provider's CLI is actively running) — the CLI is a `click.Group` from the
  start so it can be added later without restructuring the entrypoint.
- An alert banner and urgency-based sort for the full table view.
- A trend sparkline per window, using the history already available in
  `state.json`.

## License

MIT — see [LICENSE](LICENSE).
