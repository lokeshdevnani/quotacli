"""macOS Keychain access via pyobjc's Security.framework binding.

Deliberately not the `security` CLI or the `keyring` package: both return a
single item chosen by an undocumented tie-break and have no way to enumerate
duplicates and pick the newest by modification date. That enumeration is
required for Claude Code, which writes a brand-new keychain item on every
token rotation instead of updating one in place.

Reading the *secret* is a different matter: Claude Code and Antigravity both
create their items through /usr/bin/security, so that binary is already on
the items' access lists and reads them silently, whereas our own (pyobjc)
Python binary is not and triggers an "Allow / Always Allow" dialog on every
run — fatal for unattended callers. So the secret is fetched via `security`
whenever that is unambiguous, and via Security.framework only as a fallback.
"""

from __future__ import annotations

import subprocess

from Security import (
    SecItemCopyMatching,
    kSecAttrAccount,
    kSecAttrModificationDate,
    kSecAttrService,
    kSecClass,
    kSecClassGenericPassword,
    kSecMatchLimit,
    kSecMatchLimitAll,
    kSecMatchLimitOne,
    kSecReturnAttributes,
    kSecReturnData,
    kSecReturnRef,
    kSecValueRef,
    errSecItemNotFound,
    errSecSuccess,
)


class KeychainError(Exception):
    """No matching keychain item exists at all. Callers should translate
    this into ErrorKind.NEEDS_AUTH."""


def read_newest_generic_password(service: str, account: str | None = None) -> bytes:
    """Find all generic-password items matching service(+account), pick the
    one with the max kSecAttrModificationDate, then fetch its secret data via
    a single targeted lookup by persistent ref.
    """
    query = {
        kSecClass: kSecClassGenericPassword,
        kSecAttrService: service,
        kSecMatchLimit: kSecMatchLimitAll,
        kSecReturnAttributes: True,
        kSecReturnRef: True,
    }
    if account is not None:
        query[kSecAttrAccount] = account

    status, results = SecItemCopyMatching(query, None)
    if status == errSecItemNotFound:
        raise KeychainError(f"no keychain item for service={service!r}")
    if status != errSecSuccess:
        raise KeychainError(f"SecItemCopyMatching attrs failed: OSStatus {status}")

    # kSecMatchLimitAll always returns an NSArray (even for a single match) —
    # it's already iterable and works directly with max(), no wrapping needed.
    newest = max(results, key=lambda item: item[kSecAttrModificationDate])

    # Exactly one candidate -> `security` can't pick the wrong one. Any
    # failure (not trusted, timeout, ...) falls through to the framework path.
    if len(results) == 1:
        secret = _read_via_security_cli(service, newest.get(kSecAttrAccount))
        if secret is not None:
            return secret

    value_ref = newest[kSecValueRef]

    fetch_query = {
        kSecClass: kSecClassGenericPassword,
        kSecValueRef: value_ref,
        kSecMatchLimit: kSecMatchLimitOne,
        kSecReturnData: True,
    }
    status, data = SecItemCopyMatching(fetch_query, None)
    if status != errSecSuccess:
        raise KeychainError(f"SecItemCopyMatching data failed: OSStatus {status}")
    return bytes(data)


def _read_via_security_cli(service: str, account: str | None) -> bytes | None:
    cmd = ["/usr/bin/security", "find-generic-password", "-s", service]
    if account:
        cmd += ["-a", str(account)]
    cmd.append("-w")
    try:
        # Short timeout: if `security` isn't trusted it blocks on a GUI
        # dialog, which an unattended caller must not wait on.
        proc = subprocess.run(cmd, capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    secret = proc.stdout.rstrip(b"\n")
    return secret or None
