"""Session cost allocation for the control panel's Usage and cost page and the spend caps.

Pure functions, no AWS calls. The input is the record the instance returns for the "usage"
action (sessions, hourly token usage per user, the hour in progress); the rules are in
docs/monitoring-spec.md ("Cost allocation"):

- A session is one run of the instance. Its cost is the hourly price times its running hours.
- Each clock hour of a session is shared by the people who used tokens in that hour, by their
  tokens (input plus output). Nobody pays for hours after they stopped using the lab.
- An hour with no tokens (start-up, the idle tail) goes to whoever used tokens in the nearest
  earlier hour of the session, or, for start-up, the nearest later hour.
- A session with no tokens at all is charged to whoever started the instance (the start log), and
  is unallocated when that is unknown.
- When two sessions touch the same clock hour, that hour's tokens are split between them by
  how long each overlapped it, because the record is per clock hour.
"""

HOUR = 3600


def _tokens(user):
    return int(user.get("in") or 0) + int(user.get("out") or 0)


def _round(value, places=4):
    return None if value is None else round(value, places)


def _hour_table(usage):
    """{hour start: {user id: user dict}} for recorded hours plus the hour in progress."""
    table = {}
    for entry in usage.get("hours") or []:
        table[int(entry["hour"])] = {u["id"]: u for u in entry.get("users") or [] if _tokens(u) > 0}
    live = usage.get("provisional")
    if live and live.get("hour") is not None:
        table[int(live["hour"])] = {u["id"]: u for u in live.get("users") or [] if _tokens(u) > 0}
    return table


def _bounds(session, now_s):
    start = int(session["start"])
    running = session.get("stop") is None
    stop = int(now_s) if running else int(session["stop"])
    return start, stop, running


# A start request is recorded just before the instance launches, so a start counts for a session
# when it falls in this window around the session's real start.
STARTER_BEFORE = 1800
STARTER_AFTER = 300


def _starter(starts, real_start):
    """The person whose start request opened a session, or None. Closest request at or before it wins."""
    best = None
    for s in starts or []:
        t = s.get("t")
        if isinstance(t, (int, float)) and not isinstance(t, bool) and real_start - STARTER_BEFORE <= t <= real_start + STARTER_AFTER and (best is None or abs(real_start - t) < abs(real_start - best["t"])):
            best = s
    return best


def allocate(usage, now_s, since=None, until=None, starts=None):
    """Cost, tokens and per-user charges for every session in the usage record.

    since / until (epoch seconds, on hour boundaries) count only the part of each session inside
    that window, as the spend caps do for a calendar month. A session's "start" stays its real
    start, so it can be recognised again."""
    price = usage.get("hourlyCostUsd")
    price = float(price) if isinstance(price, (int, float)) and not isinstance(price, bool) else None
    hours = _hour_table(usage)
    current_hour = usage.get("currentHour")
    live_hour = (usage.get("provisional") or {}).get("hour")
    next_hour = usage.get("nextHour")
    truncated = bool(usage.get("truncated"))
    earliest = min(hours) if hours else None

    spans = []
    for session in usage.get("sessions") or []:
        start, stop, running = _bounds(session, now_s)
        real_start = start
        if since is not None:
            start = max(start, int(since))
        if until is not None:
            stop = min(stop, int(until))
            running = running and stop >= int(now_s)
        if stop > start:
            spans.append((start, stop, running, session, real_start))

    # Seconds of each clock hour that all sessions together cover, to split shared hours.
    covered = {}
    for start, stop, _, _, _ in spans:
        h = start // HOUR * HOUR
        while h < stop:
            covered[h] = covered.get(h, 0) + min(stop, h + HOUR) - max(start, h)
            h += HOUR

    sessions = []
    totals = {}
    for start, stop, running, session, real_start in spans:
        cells = []  # (hour, seconds, {user id: tokens in this session's share of the hour})
        h = start // HOUR * HOUR
        while h < stop:
            seconds = min(stop, h + HOUR) - max(start, h)
            fraction = seconds / covered[h]
            cells.append((h, seconds, {uid: _tokens(u) * fraction for uid, u in hours.get(h, {}).items()}))
            h += HOUR

        charges, tokens, unallocated, starters = {}, {}, 0.0, {}
        for i, (h, seconds, shares) in enumerate(cells):
            cost = None if price is None else price * seconds / HOUR
            donor = shares
            if not donor:
                earlier = [c[2] for c in cells[:i] if c[2]]
                later = [c[2] for c in cells[i + 1:] if c[2]]
                donor = earlier[-1] if earlier else (later[0] if later else {})
            for uid, t in shares.items():
                tokens[uid] = tokens.get(uid, 0) + t
            total = sum(donor.values())
            if cost is None:
                continue
            if total <= 0:
                starter = _starter(starts, real_start)
                if starter and starter.get("id"):
                    charges[starter["id"]] = charges.get(starter["id"], 0.0) + cost
                    starters[starter["id"]] = starter
                else:
                    unallocated += cost
                continue
            for uid, t in donor.items():
                charges[uid] = charges.get(uid, 0.0) + cost * t / total

        names = dict(starters)
        for h, _, _ in cells:
            for uid, u in hours.get(h, {}).items():
                names[uid] = u
        session_tokens = sum(tokens.values())
        session_cost = None if price is None else price * (stop - start) / HOUR
        pending = next_hour is not None and current_hour is not None and any(next_hour <= h < current_hour for h, _, _ in cells)
        incomplete = truncated and (earliest is None or start // HOUR * HOUR < earliest)
        provisional = running or pending or any(h == live_hour for h, _, _ in cells)
        user_rows = []
        for uid in sorted(set(tokens) | set(charges), key=lambda x: -charges.get(x, 0)):
            u = names.get(uid, {})
            row = {
                "id": uid, "name": u.get("name"), "email": u.get("email"),
                "tokens": round(tokens.get(uid, 0)),
                "share": _round(tokens.get(uid, 0) / session_tokens) if session_tokens else 0,
                "charge": _round(charges.get(uid, 0.0)) if price is not None else None,
            }
            user_rows.append(row)
            agg = totals.setdefault(uid, {"id": uid, "name": u.get("name"), "email": u.get("email"), "tokens": 0, "charge": 0.0 if price is not None else None, "sessions": 0})
            agg["tokens"] += row["tokens"]
            agg["sessions"] += 1
            if price is not None:
                agg["charge"] += charges.get(uid, 0.0)
        sessions.append({
            "start": real_start, "stop": None if running else stop, "running": running,
            "endedBy": session.get("endedBy"),
            "hours": _round((stop - start) / HOUR, 3),
            "cost": _round(session_cost),
            "tokens": round(session_tokens),
            "pricePerMillionTokens": _round(session_cost / session_tokens * 1_000_000, 2) if session_cost is not None and session_tokens else None,
            "unallocatedCost": _round(unallocated) if price is not None else None,
            "provisional": provisional,
            "incomplete": incomplete,
            "users": user_rows,
        })

    sessions.sort(key=lambda s: -s["start"])
    user_totals = sorted(totals.values(), key=lambda u: (-(u["charge"] or 0), -u["tokens"]))
    for u in user_totals:
        u["charge"] = _round(u["charge"])
    return {
        "priced": price is not None,
        "hourlyCostUsd": price,
        "instanceType": usage.get("instanceType"),
        "truncated": truncated,
        "sessions": sessions,
        "users": user_totals,
    }
