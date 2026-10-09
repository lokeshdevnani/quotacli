"""Antigravity provider: most complex of the five, a 4-tier fallback chain.

1. loadCodeAssist — plan/tier label only, never usage numbers.
2. Preferred: the local language-server's own RPC. Discovered via ps/lsof
   if the Antigravity app is running; otherwise a headless instance of the
   app's bundled language server is started for the duration of the call
   (so the numbers don't depend on the app being open).
3. Fallback: Google's direct quota endpoint (usually 403 for personal
   accounts).
4. Last resort: count today's model-turn lines in a local JSONL transcript,
   reported as a bare estimated count, not a percentage.

Reference: Codenotch's AntigravityCredentials.swift / AntigravityProvider.swift
/ AntigravityBridge.swift / AntigravityActivity.swift.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import re
import secrets
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

from quotacli import httpclient
from quotacli.protocol import ErrorKind, LimitWindow, Provider, ProviderError, Snapshot

LOAD_CODE_ASSIST_URL = "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist"
DIRECT_QUOTA_URL = "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary"
BRIDGE_RPC_PATH = "/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary"
KEYCHAIN_SERVICE = "gemini"
KEYCHAIN_ACCOUNT = "antigravity"
GO_KEYRING_PREFIX = "go-keyring-base64:"
TRANSCRIPT_ROOT = Path.home() / ".gemini" / "antigravity" / "brain"
LANGUAGE_SERVER_BIN = Path("/Applications/Antigravity.app/Contents/Resources/bin/language_server")
CLOUD_CODE_ENDPOINT = "https://cloudcode-pa.googleapis.com"
# Cold start of the headless server was observed at ~15s; leave headroom.
HEADLESS_STARTUP_DEADLINE = 45.0


def _read_credential() -> dict:
    from quotacli import keychain

    try:
        raw = keychain.read_newest_generic_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
    except keychain.KeychainError as exc:
        raise ProviderError(
            ErrorKind.NEEDS_AUTH, "Antigravity: not signed in (no keychain credential found)"
        ) from exc

    text = raw.decode("utf-8", errors="strict")
    if not text.startswith(GO_KEYRING_PREFIX):
        raise ProviderError(ErrorKind.BAD_RESPONSE, "Antigravity: unrecognized credential format")

    try:
        decoded = base64.b64decode(text[len(GO_KEYRING_PREFIX) :])
        payload = json.loads(decoded)
        return payload["token"]
    except (ValueError, json.JSONDecodeError, KeyError) as exc:
        raise ProviderError(ErrorKind.BAD_RESPONSE, "Antigravity: malformed credential payload") from exc


def _fetch_plan_label(access_token: str) -> str | None:
    """Tier 1: identity/tier check. Never returns usage numbers — failure
    here beyond a plain network error is non-fatal to the overall fetch."""
    try:
        with httpclient.make_client() as client:
            resp = client.post(
                LOAD_CODE_ASSIST_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                json={"metadata": {"pluginType": "GEMINI"}},
            )
        if resp.status_code == 401:
            raise ProviderError(ErrorKind.CREDENTIAL_EXPIRED, "Antigravity: HTTP 401 on loadCodeAssist")
        if resp.status_code >= 400:
            return None
        body = resp.json()
        return body.get("currentTier") or body.get("current_tier")
    except httpx.HTTPError:
        return None


def _discover_bridge() -> tuple[str, list[int]] | None:
    """ps for the running language-server + its CSRF token, lsof for the
    port(s) it's listening on. Returns (csrf_token, ports), or None if the
    process/ports can't be found."""
    try:
        ps_output = subprocess.run(
            ["ps", "-Ao", "pid,command"], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None

    pid = None
    token = None
    for line in ps_output.splitlines():
        if "language_server" in line and "--csrf_token" in line:
            match_pid = re.match(r"\s*(\d+)", line)
            match_token = re.search(r"--csrf_token[= ](\S+)", line)
            if match_pid and match_token:
                pid = match_pid.group(1)
                token = match_token.group(1)
                break

    if pid is None or token is None:
        return None

    ports = _listening_ports(pid)
    if not ports:
        return None
    return token, ports


def _listening_ports(pid: str | int) -> list[int]:
    try:
        lsof_output = subprocess.run(
            ["lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []

    # Order preserved (not a set) — the process can have more than one
    # listening TCP port (observed live: two, one HTTPS and one plain HTTP),
    # and only one serves this RPC over TLS, so every port found must be
    # tried, not one picked arbitrarily.
    seen = set()
    ports = []
    for port in re.findall(r":(\d+)\s*\(LISTEN\)", lsof_output):
        if port not in seen:
            seen.add(port)
            ports.append(int(port))
    return ports


def _fetch_via_bridge() -> dict | None:
    """Tier 2: preferred real numbers, via the local language-server bridge.
    Tries every discovered listening port since only one serves this RPC.
    TLS verification is disabled ONLY for this narrowly-scoped loopback
    client — the self-signed cert is expected here and nowhere else."""
    discovered = _discover_bridge()
    if discovered is None:
        return _fetch_via_headless_server()
    token, ports = discovered

    for port in ports:
        body = _try_bridge_port(token, port)
        if body is not None:
            return body
    return None


def _fetch_via_headless_server() -> dict | None:
    """Antigravity app isn't running, so start the app's own bundled
    language server headless, ask it, and always kill it again. It reads the
    same keychain credential itself. Its state dir is a throwaway so nothing
    is written into ~/.gemini."""
    if not LANGUAGE_SERVER_BIN.is_file():
        return None

    token = secrets.token_hex(16)
    with tempfile.TemporaryDirectory(prefix="quotacli-ag-") as gemini_dir:
        try:
            proc = subprocess.Popen(
                [
                    str(LANGUAGE_SERVER_BIN),
                    "-standalone",
                    "-csrf_token", token,
                    "-gemini_dir", gemini_dir,
                    "-cloud_code_endpoint", CLOUD_CODE_ENDPOINT,
                    "-disable_telemetry",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            return None

        try:
            deadline = time.monotonic() + HEADLESS_STARTUP_DEADLINE
            while time.monotonic() < deadline and proc.poll() is None:
                for port in _listening_ports(proc.pid):
                    body = _try_bridge_port(token, port)
                    if body is not None:
                        return body
                time.sleep(1.0)
            return None
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def _try_bridge_port(token: str, port: int) -> dict | None:
    try:
        with httpclient.make_client(timeout=10.0, verify=False) as client:
            resp = client.post(
                f"https://127.0.0.1:{port}{BRIDGE_RPC_PATH}",
                headers={"x-codeium-csrf-token": token},
                json={"forceRefresh": True},
            )
        if resp.status_code >= 400:
            return None
        return resp.json()
    except httpx.HTTPError:
        return None


def _fetch_direct_quota(access_token: str) -> dict | None:
    """Tier 3: fallback direct call, usually 403 for personal/unlicensed
    accounts. Body is deliberately empty — extra fields get rejected."""
    try:
        with httpclient.make_client() as client:
            resp = client.post(
                DIRECT_QUOTA_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                json={},
            )
        if resp.status_code >= 400:
            return None
        return resp.json()
    except httpx.HTTPError:
        return None


def _count_today_transcript_activity() -> int | None:
    """Tier 4: last resort, no percentage — count today's MODEL-authored
    transcript lines across all trajectories."""
    if not TRANSCRIPT_ROOT.is_dir():
        return None

    today = dt.datetime.now().date()
    count = 0
    found_any = False
    for transcript in TRANSCRIPT_ROOT.glob("*/.system_generated/logs/transcript.jsonl"):
        found_any = True
        try:
            for line in transcript.read_text(errors="ignore").splitlines():
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("source") != "MODEL":
                    continue
                created_at = entry.get("created_at")
                if not created_at:
                    continue
                try:
                    when = dt.datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
                except ValueError:
                    continue
                if when.astimezone().date() == today:
                    count += 1
        except OSError:
            continue
    return count if found_any else None


_WINDOW_SUFFIX = {"5h": "5h", "weekly": "Weekly"}


def parse_bridge_response(body: dict) -> list[LimitWindow]:
    """Tier 2 response is remainingFraction, not used — inverted here.

    A bucket's own displayName is generic ("Weekly Limit Remaining") and
    identical across groups — it's the group's displayName (e.g. "Gemini
    Models" vs "Claude and GPT models") that actually distinguishes model
    families sharing separate quota pools, so both must be combined into
    the label or same-named buckets collide (verified live: two groups,
    each with a "Weekly Limit Remaining" and "Five Hour Limit Remaining").
    """
    windows = []
    for group in (body.get("response") or {}).get("groups", []) or []:
        group_label = group.get("displayName", "Quota")
        for bucket in group.get("buckets", []) or []:
            remaining = bucket.get("remainingFraction")
            if remaining is None:
                continue
            suffix = _WINDOW_SUFFIX.get(bucket.get("window"), bucket.get("displayName", ""))
            windows.append(
                LimitWindow(
                    label=f"{group_label} ({suffix})" if suffix else group_label,
                    used_fraction=max(0.0, 1.0 - remaining),
                    resets_at=_parse_iso(bucket.get("resetTime")),
                )
            )
    return windows


def parse_direct_quota_response(body: dict) -> list[LimitWindow]:
    """Tier 3 response: only keep buckets with a sane limit (limit>0 and
    0<=used<=limit*1.5, a sanity filter against a reverse-engineered shape)."""
    windows = []
    groups = body.get("quotaGroups")
    buckets = []
    if groups:
        for group in groups:
            buckets.extend(group.get("buckets", []) or [])
    else:
        buckets = body.get("buckets", []) or []

    for bucket in buckets:
        limit = bucket.get("limit")
        used = bucket.get("used")
        if not limit or limit <= 0 or used is None:
            continue
        if not (0 <= used <= limit * 1.5):
            continue
        windows.append(
            LimitWindow(
                label=bucket.get("displayName", bucket.get("name", "Quota")),
                used_fraction=used / limit,
                resets_at=_parse_iso(bucket.get("resetTime")),
            )
        )
    return windows


def _parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


class AntigravityProvider(Provider):
    id = "antigravity"
    display_name = "Antigravity"

    def __init__(self) -> None:
        # Set during fetch_snapshot; exposed for `quotacli -v` to report
        # which of the 4 tiers actually answered, since this path can't be
        # meaningfully unit-tested end-to-end.
        self.last_tier: str | None = None

    def fetch_snapshot(self) -> Snapshot:
        self.last_tier = None
        credential = _read_credential()
        access_token = credential.get("access_token")
        if not access_token:
            raise ProviderError(ErrorKind.NEEDS_AUTH, "Antigravity: credential missing access_token")

        expiry = _parse_iso(credential.get("expiry"))
        if expiry is not None and expiry <= dt.datetime.now(dt.timezone.utc):
            raise ProviderError(ErrorKind.CREDENTIAL_EXPIRED, "Antigravity: token expired")

        plan_label = _fetch_plan_label(access_token)

        bridge_body = _fetch_via_bridge()
        if bridge_body is not None:
            windows = parse_bridge_response(bridge_body)
            if windows:
                self.last_tier = "bridge (local language server)"
                return self._snapshot(windows, plan_label)

        direct_body = _fetch_direct_quota(access_token)
        if direct_body is not None:
            windows = parse_direct_quota_response(direct_body)
            if windows:
                self.last_tier = "direct quota endpoint"
                return self._snapshot(windows, plan_label)

        count = _count_today_transcript_activity()
        if count is not None:
            self.last_tier = "transcript activity count (estimated)"
            return self._snapshot(
                [
                    LimitWindow(
                        label="Requests today · no limit published",
                        used_fraction=None,
                        resets_at=None,
                        detail=f"~{count} (estimated)",
                    )
                ],
                plan_label,
            )

        raise ProviderError(ErrorKind.NOTHING_METERED, "Antigravity: no usage source available")

    def _snapshot(self, windows: list[LimitWindow], plan_label: str | None) -> Snapshot:
        return Snapshot(
            provider_id=self.id,
            fetched_at=dt.datetime.now(dt.timezone.utc),
            windows=windows,
            plan_label=plan_label,
        )
