"""Shared provider interface: the contract every provider module implements.

An ABC (not typing.Protocol) is used deliberately — there are five known,
hand-written implementations, not third-party plugins, so nominal inheritance
is the more honest fit and lets the base class carry shared helpers later.
"""

from __future__ import annotations

import abc
import dataclasses
import datetime as dt
import enum


class ErrorKind(enum.Enum):
    NEEDS_AUTH = "needs_auth"  # never signed in / no credential found at all
    CREDENTIAL_EXPIRED = "credential_expired"  # was valid; provider will self-refresh
    RATE_LIMITED = "rate_limited"  # 429, backoff in effect
    ACCESS_DENIED = "access_denied"  # authenticated but server said no
    NOTHING_METERED = "nothing_metered"  # valid account, nothing to show yet
    BAD_RESPONSE = "bad_response"  # network error, timeout, unexpected shape


@dataclasses.dataclass(frozen=True, slots=True)
class ProviderError(Exception):
    kind: ErrorKind
    message: str
    retry_after: dt.datetime | None = None

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


@dataclasses.dataclass(frozen=True, slots=True)
class LimitWindow:
    label: str
    used_fraction: float | None
    resets_at: dt.datetime | None
    detail: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class Snapshot:
    provider_id: str
    fetched_at: dt.datetime
    windows: list[LimitWindow]
    plan_label: str | None = None
    is_stale: bool = False


class Provider(abc.ABC):
    id: str
    display_name: str

    @abc.abstractmethod
    def fetch_snapshot(self) -> Snapshot:
        """Fetch live usage. Raises ProviderError on any failure; never
        returns a partially-valid Snapshot to signal failure."""
        raise NotImplementedError
