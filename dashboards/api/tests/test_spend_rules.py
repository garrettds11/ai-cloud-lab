"""Spend cap rules. Run from dashboards/api: python -m pytest -q tests/test_spend_rules.py"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

import spend_rules as r  # noqa: E402

OCT_15 = 1_792_000_000  # 2026-10-14 UTC, inside October 2026


def test_periods_are_calendar_months_in_utc():
    assert r.period_key(OCT_15) == "2026-10"
    start, end = r.period_bounds("2026-10")
    assert r.period_key(start) == "2026-10" and r.period_key(end) == "2026-11" and r.period_key(end - 1) == "2026-10"
    assert r.period_key(r.period_bounds("2026-12")[1]) == "2027-01"
    assert r.reset_label("2026-10") == "Nov 1" and r.reset_label("2026-12") == "Jan 1"


def test_no_or_zero_cap_means_unlimited_and_a_users_own_cap_beats_the_default():
    assert r.effective_cap(None, None) is None and r.effective_cap(None, 0) is None
    assert r.effective_cap(None, 5) == 5 and r.effective_cap(8, 5) == 8
    assert r.effective_cap(0, 5) is None  # an explicit 0 for one person lifts the default for them
    assert r.percent(1, None) is None and r.percent(2.5, 5) == 50


def test_one_email_per_threshold_and_a_jump_sends_only_the_highest():
    assert r.due_email(40, []) == (None, [])
    assert r.due_email(50, []) == (50, [50])
    assert r.due_email(55, [50]) == (None, [])
    assert r.due_email(85, [50]) == (80, [80])
    assert r.due_email(85, []) == (80, [50, 80])
    assert r.due_email(100, [50, 80, 98]) == (100, [100])
    assert r.due_email(None, []) == (None, [])


def test_projection_waits_a_day_then_extrapolates():
    start, end = r.period_bounds("2026-10")
    assert r.projected(5, start + 3600, "2026-10") is None
    assert r.projected(5, start + 86400, "2026-10") == pytest.approx(5 * 31)
    assert r.projected(0, start + 5 * 86400, "2026-10") is None


def test_emails_state_the_cap_the_spend_and_the_reset_date():
    for t in r.THRESHOLDS:
        subject, body = r.user_email(t, 5, 4.1, "2026-10")
        assert "$5.00" in body and "$4.10" in body and "Nov 1" in body and subject
        subject, body = r.lab_email(t, 100, 98.5, "2026-10")
        assert "$100.00" in body and "$98.50" in body and "Nov 1" in body
    assert "switched off" in r.user_email(100, 5, 5, "2026-10")[1]
    assert "stopped" in r.lab_email(100, 5, 5, "2026-10")[1]


def test_the_blocked_sentence_matches_the_spec():
    # 2026-10-24 12:00 UTC
    ms = 1_792_843_200_000
    assert r.block_reason(5, 5.02, ms, "2026-10") == "Blocked for the rest of October: monthly AI spend cap of $5.00 reached on Oct 24; sign-in returns Nov 1"
