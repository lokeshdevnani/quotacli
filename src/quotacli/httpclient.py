"""Shared httpx.Client factory. Each provider call gets a short-lived client
(`with make_client() as c:`) rather than a shared pool — call volume is low
enough that connection reuse isn't worth the lifecycle management, especially
across --watch ticks that can span many minutes.
"""

from __future__ import annotations

import httpx

DEFAULT_TIMEOUT = 15.0
USER_AGENT = "quotacli/0.1"


def make_client(*, timeout: float = DEFAULT_TIMEOUT, verify: bool = True) -> httpx.Client:
    """verify=False must only ever be used for Antigravity's self-signed
    loopback bridge call — never for a real internet-facing endpoint."""
    return httpx.Client(timeout=timeout, verify=verify, headers={"User-Agent": USER_AGENT})
