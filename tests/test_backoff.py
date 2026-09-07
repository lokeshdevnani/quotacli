import datetime as dt

import pytest

from quotacli.backoff import (
    claude_code_backoff_seconds,
    codex_backoff_seconds,
    parse_retry_after,
)


@pytest.mark.parametrize(
    "attempt,retry_after,expected",
    [
        (0, 0, 60),
        (1, 0, 120),
        (2, 0, 240),
        (3, 0, 480),
        (4, 0, 900),  # 60*2^4=960, clamped to the 900s ceiling
        (5, 0, 900),  # attempt beyond 4 stays capped at the attempt-4 value
        (0, 500, 500),  # retry_after raises the floor
        (2, 100, 240),  # retry_after lower than the floor is ignored
    ],
)
def test_claude_code_backoff_seconds(attempt, retry_after, expected):
    assert claude_code_backoff_seconds(attempt, retry_after) == expected


@pytest.mark.parametrize(
    "retry_after,expected",
    [
        (0, 60),
        (30, 60),
        (120, 120),
    ],
)
def test_codex_backoff_seconds(retry_after, expected):
    assert codex_backoff_seconds(retry_after) == expected


def test_parse_retry_after_none_or_empty():
    assert parse_retry_after(None) == 0.0
    assert parse_retry_after("") == 0.0


def test_parse_retry_after_integer_seconds():
    assert parse_retry_after("120") == 120.0
    assert parse_retry_after("0") == 0.0


def test_parse_retry_after_http_date_in_future():
    future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=90)
    header = future.strftime("%a, %d %b %Y %H:%M:%S GMT")
    seconds = parse_retry_after(header)
    assert 80 <= seconds <= 100


def test_parse_retry_after_http_date_in_past_clamped_to_zero():
    past = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=90)
    header = past.strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert parse_retry_after(header) == 0.0


def test_parse_retry_after_garbage():
    assert parse_retry_after("not a date") == 0.0
