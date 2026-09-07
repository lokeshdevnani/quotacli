"""Provider registry, in canonical display order."""

from __future__ import annotations

from quotacli.protocol import Provider
from quotacli.providers.antigravity import AntigravityProvider
from quotacli.providers.claude_code import ClaudeCodeProvider
from quotacli.providers.codex import CodexProvider
from quotacli.providers.cursor import CursorProvider
from quotacli.providers.grok import GrokProvider


def build_providers() -> list[Provider]:
    """Instantiate all five providers, in canonical display order."""
    return [
        ClaudeCodeProvider(),
        GrokProvider(),
        CodexProvider(),
        CursorProvider(),
        AntigravityProvider(),
    ]
