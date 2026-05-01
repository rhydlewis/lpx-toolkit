"""Tests for `_relative_time(then, now)` — formatter for the modified-time
chip on `proj-card` (#46).

The chip is the strongest single signal in the library-browse surface
("when did I last touch this?"). The formatter goes for *dense and
scannable*, not absolute precision — actual timestamps live on the
project page.

Ladder: < 60s → "just now"; minutes → "Nm"; hours → "Nh"; days < 14 → "Nd";
weeks < 8 → "Nw"; otherwise → "Mon 'YY".
"""
from datetime import datetime, timedelta

from lpx_inspect import _relative_time


NOW = datetime(2026, 5, 1, 12, 0, 0)


def test_just_now_under_a_minute():
    assert _relative_time(NOW - timedelta(seconds=10), now=NOW) == "just now"
    assert _relative_time(NOW - timedelta(seconds=59), now=NOW) == "just now"


def test_minutes_below_an_hour():
    assert _relative_time(NOW - timedelta(minutes=1), now=NOW) == "1m"
    assert _relative_time(NOW - timedelta(minutes=42), now=NOW) == "42m"
    assert _relative_time(NOW - timedelta(minutes=59), now=NOW) == "59m"


def test_hours_below_a_day():
    assert _relative_time(NOW - timedelta(hours=1), now=NOW) == "1h"
    assert _relative_time(NOW - timedelta(hours=23), now=NOW) == "23h"


def test_days_under_two_weeks():
    assert _relative_time(NOW - timedelta(days=1), now=NOW) == "1d"
    assert _relative_time(NOW - timedelta(days=13), now=NOW) == "13d"


def test_weeks_under_two_months():
    """14 days+ rolls over to weeks; 8 weeks (~56 days) rolls over to month."""
    assert _relative_time(NOW - timedelta(days=14), now=NOW) == "2w"
    assert _relative_time(NOW - timedelta(days=49), now=NOW) == "7w"


def test_long_ago_uses_month_year_abbreviation():
    """Beyond ~8 weeks, fall back to "Mon 'YY". Year is always 2 digits."""
    older = NOW - timedelta(days=120)  # ~17 weeks
    out = _relative_time(older, now=NOW)
    # Whatever the exact month, it's the abbreviated form with apostrophe-year.
    assert "'" in out
    assert len(out.split()) == 2
    month, year = out.split()
    assert len(month) == 3
    assert year.startswith("'") and len(year) == 3


def test_long_ago_uses_actual_month():
    """Calendar-month exact: 4 months ago from May → Jan."""
    jan_2026 = datetime(2026, 1, 15, 12, 0, 0)
    assert _relative_time(jan_2026, now=NOW) == "Jan '26"


def test_more_than_a_year_ago():
    apr_2024 = datetime(2024, 4, 5, 12, 0, 0)
    assert _relative_time(apr_2024, now=NOW) == "Apr '24"


def test_future_timestamps_render_as_just_now():
    """A timestamp in the future shouldn't crash the dashboard. Treat as
    'just now' rather than producing negative durations or '-1d'."""
    assert _relative_time(NOW + timedelta(hours=1), now=NOW) == "just now"


def test_now_defaults_to_current_time():
    """Omitting `now` uses the wall clock — must not crash."""
    out = _relative_time(datetime.now() - timedelta(seconds=5))
    assert out == "just now"
