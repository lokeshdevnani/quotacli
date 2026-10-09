import subprocess

from quotacli import keychain


def _fake_run(returncode: int, stdout: bytes = b""):
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=b"")

    return run


def test_security_cli_returns_secret_without_trailing_newline(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(0, b"s3cret\n"))
    assert keychain._read_via_security_cli("svc", "acct") == b"s3cret"


def test_security_cli_failure_returns_none_so_caller_falls_back(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(44))
    assert keychain._read_via_security_cli("svc", None) is None


def test_security_cli_timeout_returns_none(monkeypatch):
    def run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 5)

    monkeypatch.setattr(subprocess, "run", run)
    assert keychain._read_via_security_cli("svc", "acct") is None
