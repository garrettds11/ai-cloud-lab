"""Spend cap rules: periods, thresholds, projections and the emails. Pure functions, no AWS calls.

Design: docs/monitoring-spec.md ("Spend caps"). Caps are dollars per calendar month (UTC), the
month AWS bills by. A cap of 0 or none means unlimited.
"""

import datetime

THRESHOLDS = (50, 80, 98, 100)
DOMAIN = "AI Cloud Lab"


def period_key(now_s):
    d = datetime.datetime.fromtimestamp(now_s, datetime.timezone.utc)
    return f"{d.year:04d}-{d.month:02d}"


def period_bounds(period):
    """(start, end) epoch seconds of a "YYYY-MM" month; end is the first second of the next month."""
    year, month = int(period[:4]), int(period[5:7])
    start = datetime.datetime(year, month, 1, tzinfo=datetime.timezone.utc)
    end = datetime.datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=datetime.timezone.utc)
    return int(start.timestamp()), int(end.timestamp())


def reset_label(period):
    """"Nov 1" for the month after a "YYYY-MM" period."""
    end = datetime.datetime.fromtimestamp(period_bounds(period)[1], datetime.timezone.utc)
    return f"{end.strftime('%b')} {end.day}"


def cap_value(value):
    """A usable cap in dollars, or None for unlimited."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def effective_cap(user_cap, default_cap):
    """A person's own cap if one is set (0 there means unlimited for them), else the default."""
    if user_cap is not None:
        return cap_value(user_cap)
    return cap_value(default_cap)


def percent(spend, cap):
    return None if cap is None else spend / cap * 100


def due_email(pct, sent):
    """The one threshold to email now: the highest crossed one not sent yet, or None.

    Returns (threshold, thresholds to record as sent). Lower unsent thresholds are recorded as
    sent too, so a jump from 40% to 85% sends one email, not two."""
    if pct is None:
        return None, []
    crossed = [t for t in THRESHOLDS if pct >= t]
    unsent = [t for t in crossed if t not in sent]
    if not unsent:
        return None, []
    return max(unsent), unsent


def projected(spend, now_s, period):
    """Month-end spend at the current rate, or None when too early to say (under one day)."""
    start, end = period_bounds(period)
    elapsed = now_s - start
    if elapsed < 86400 or spend <= 0:
        return None
    return spend * (end - start) / elapsed


def money(value):
    return f"${value:,.2f}"


def user_email(threshold, cap, spend, period):
    reset = reset_label(period)
    if threshold >= 100:
        subject = f"{DOMAIN}: you reached your monthly spend cap"
        body = (f"Your {DOMAIN} spend this month reached your cap of {money(cap)} (spent so far: {money(spend)}).\n"
                f"Your sign-in is switched off for the rest of the month and returns on {reset}.\n"
                "An administrator can raise the cap if you need more.")
    else:
        lead = {50: "You have used half of", 80: "You have used 80% of", 98: "You are about to reach"}[threshold]
        subject = f"{DOMAIN}: {threshold}% of your monthly spend cap"
        body = (f"{lead} your monthly {DOMAIN} spend cap of {money(cap)} (spent so far: {money(spend)}).\n"
                f"The cap resets on {reset}. At 100% your sign-in is switched off until then.")
    return subject, body


def lab_email(threshold, cap, spend, period):
    reset = reset_label(period)
    if threshold >= 100:
        subject = f"{DOMAIN}: the lab reached its monthly budget and was stopped"
        body = (f"The lab's running cost this month reached the cap of {money(cap)} (spent so far: {money(spend)}).\n"
                f"The instance was stopped and cannot be started until the cap is raised or the month resets on {reset}.")
    else:
        subject = f"{DOMAIN}: the lab has used {threshold}% of its monthly budget"
        body = (f"The lab's running cost this month is {money(spend)} of the {money(cap)} cap.\n"
                f"At 100% the instance is stopped and cannot be started until the cap is raised or the month resets on {reset}.")
    return subject, body


def block_reason(cap, spend, blocked_ms, period):
    """The one sentence shown on the Blocked badge."""
    when = datetime.datetime.fromtimestamp(blocked_ms / 1000, datetime.timezone.utc)
    return (f"Blocked for the rest of {when.strftime('%B')}: monthly AI spend cap of {money(cap)} reached on "
            f"{when.strftime('%b')} {when.day}; sign-in returns {reset_label(period)}")
