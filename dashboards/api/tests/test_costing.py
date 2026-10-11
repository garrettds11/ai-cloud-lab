"""Cost allocation rules (docs/monitoring-spec.md, Cost allocation). Run from dashboards/api:
python -m pytest -q tests/test_costing.py"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

import costing  # noqa: E402

H = 3600
T0 = 1_700_000_000 // H * H  # an hour boundary


def user(uid, tokens, name=None):
    return {"id": uid, "email": f"{uid}@x", "name": name or uid, "in": tokens // 2, "out": tokens - tokens // 2, "messages": 1}


def usage(sessions, hours, price=1.0, **extra):
    return {"hourlyCostUsd": price, "instanceType": "g6.xlarge", "sessions": sessions, "hours": hours, "provisional": None,
            "truncated": False, "nextHour": None, "currentHour": None, **extra}


def charges(session):
    return {u["id"]: u["charge"] for u in session["users"]}


def test_one_user_alone_pays_the_whole_session():
    out = costing.allocate(usage([{"start": T0, "stop": T0 + 2 * H, "endedBy": "shutdown"}],
                                 [{"hour": T0, "users": [user("a", 1000)]}, {"hour": T0 + H, "users": [user("a", 1000)]}], price=0.8), T0 + 5 * H)
    s = out["sessions"][0]
    assert s["cost"] == pytest.approx(1.6) and charges(s) == {"a": pytest.approx(1.6)}
    assert s["pricePerMillionTokens"] == pytest.approx(1.6 / 2000 * 1e6)
    assert out["users"][0]["charge"] == pytest.approx(1.6)


def test_each_hour_is_shared_only_by_people_who_used_it():
    hours = [{"hour": T0, "users": [user("a", 300), user("b", 100)]}, {"hour": T0 + H, "users": [user("a", 500)]}]
    s = costing.allocate(usage([{"start": T0, "stop": T0 + 2 * H}], hours, price=2.0), T0 + 9 * H)["sessions"][0]
    assert charges(s) == {"a": pytest.approx(1.5 + 2.0), "b": pytest.approx(0.5)}
    assert sum(charges(s).values()) == pytest.approx(s["cost"])


def test_an_idle_tail_goes_to_the_last_users_and_start_up_to_the_first():
    hours = [{"hour": T0 + H, "users": [user("a", 100)]}, {"hour": T0 + 2 * H, "users": [user("b", 100)]}]
    s = costing.allocate(usage([{"start": T0, "stop": T0 + 4 * H}], hours, price=1.0), T0 + 9 * H)["sessions"][0]
    # hour 0 (start-up) -> a; hour 1 -> a; hour 2 -> b; hour 3 (idle tail) -> b
    assert charges(s) == {"a": pytest.approx(2.0), "b": pytest.approx(2.0)}


def test_a_session_with_no_tokens_is_unallocated():
    s = costing.allocate(usage([{"start": T0, "stop": T0 + H}], []), T0 + 9 * H)["sessions"][0]
    assert s["users"] == [] and s["unallocatedCost"] == pytest.approx(1.0) and s["pricePerMillionTokens"] is None


def test_partial_hours_cost_by_the_minute():
    s = costing.allocate(usage([{"start": T0 + 1800, "stop": T0 + 3600 + 900}], [{"hour": T0, "users": [user("a", 10)]}], price=4.0), T0 + 9 * H)["sessions"][0]
    assert s["cost"] == pytest.approx(3.0) and s["hours"] == 0.75
    assert charges(s) == {"a": pytest.approx(3.0)}


def test_two_sessions_in_one_clock_hour_split_its_tokens_by_overlap():
    sessions = [{"start": T0, "stop": T0 + 1800}, {"start": T0 + 2400, "stop": T0 + H}]  # 1800 s and 1200 s
    out = costing.allocate(usage(sessions, [{"hour": T0, "users": [user("a", 3000)]}], price=6.0), T0 + 9 * H)
    first, second = sorted(out["sessions"], key=lambda s: s["start"])
    assert first["tokens"] == 1800 and second["tokens"] == 1200
    assert sum(s["tokens"] for s in out["sessions"]) == 3000


def test_a_running_session_is_provisional_and_uses_the_live_hour():
    data = usage([{"start": T0, "stop": None, "endedBy": "running"}], [], currentHour=T0, nextHour=T0)
    data["provisional"] = {"hour": T0, "users": [user("a", 200)]}
    s = costing.allocate(data, T0 + 1800)["sessions"][0]
    assert s["running"] and s["provisional"] and s["stop"] is None
    assert s["cost"] == pytest.approx(0.5) and charges(s) == {"a": pytest.approx(0.5)}


def test_uncollected_hours_mark_the_session_provisional():
    data = usage([{"start": T0, "stop": T0 + 2 * H}], [{"hour": T0, "users": [user("a", 10)]}], nextHour=T0 + H, currentHour=T0 + 3 * H)
    assert costing.allocate(data, T0 + 3 * H)["sessions"][0]["provisional"] is True


def test_truncated_history_marks_older_sessions_incomplete():
    data = usage([{"start": T0, "stop": T0 + H}, {"start": T0 + 5 * H, "stop": T0 + 6 * H}], [{"hour": T0 + 5 * H, "users": [user("a", 10)]}], truncated=True)
    by_start = {s["start"]: s for s in costing.allocate(data, T0 + 9 * H)["sessions"]}
    assert by_start[T0]["incomplete"] is True and by_start[T0 + 5 * H]["incomplete"] is False


def test_without_a_price_tokens_are_still_counted_but_nothing_is_charged():
    out = costing.allocate(usage([{"start": T0, "stop": T0 + H}], [{"hour": T0, "users": [user("a", 10)]}], price=None), T0 + 9 * H)
    s = out["sessions"][0]
    assert out["priced"] is False and s["cost"] is None and s["tokens"] == 10 and s["users"][0]["charge"] is None


def test_newest_session_first_and_totals_sum_across_sessions():
    sessions = [{"start": T0, "stop": T0 + H}, {"start": T0 + 4 * H, "stop": T0 + 5 * H}]
    hours = [{"hour": T0, "users": [user("a", 10)]}, {"hour": T0 + 4 * H, "users": [user("a", 10), user("b", 10)]}]
    out = costing.allocate(usage(sessions, hours, price=2.0), T0 + 9 * H)
    assert [s["start"] for s in out["sessions"]] == [T0 + 4 * H, T0]
    totals = {u["id"]: u for u in out["users"]}
    assert totals["a"]["charge"] == pytest.approx(3.0) and totals["a"]["sessions"] == 2 and totals["b"]["charge"] == pytest.approx(1.0)


def test_a_window_counts_only_the_part_of_a_session_inside_it():
    hours = [{"hour": T0, "users": [user("a", 10)]}, {"hour": T0 + H, "users": [user("b", 10)]}]
    data = usage([{"start": T0, "stop": T0 + 2 * H}], hours, price=1.0)
    s = costing.allocate(data, T0 + 9 * H, since=T0 + H)["sessions"][0]
    assert s["start"] == T0 and s["cost"] == pytest.approx(1.0) and charges(s) == {"b": pytest.approx(1.0)}
    assert costing.allocate(data, T0 + 9 * H, since=T0 + 2 * H)["sessions"] == []
    until = costing.allocate(data, T0 + 9 * H, until=T0 + H)["sessions"][0]
    assert charges(until) == {"a": pytest.approx(1.0)}


# ---- a run nobody chatted in goes to whoever started it ----------------------------------------


def start(uid, at, name=None):
    return {"t": at, "id": uid, "email": uid, "name": name or uid}


def test_a_session_with_no_tokens_is_charged_to_whoever_started_it():
    s = costing.allocate(usage([{"start": T0 + 60, "stop": T0 + H + 60}], [], price=2.0), T0 + 5 * H, starts=[start("sam@x", T0 + 30, "Sam")])["sessions"][0]
    assert s["unallocatedCost"] == pytest.approx(0)
    assert charges(s) == {"sam@x": pytest.approx(2.0)} and s["users"][0]["name"] == "Sam" and s["users"][0]["tokens"] == 0
    assert s["cost"] == pytest.approx(2.0)


def test_the_closest_start_request_wins():
    starts = [start("early@x", T0 - 1200), start("near@x", T0 + 20), start("late@x", T0 + 200)]
    s = costing.allocate(usage([{"start": T0, "stop": T0 + H}], [], price=1.0), T0 + 5 * H, starts=starts)["sessions"][0]
    assert list(charges(s)) == ["near@x"]


def test_a_start_too_far_from_the_session_does_not_count():
    for at in (T0 - 3600, T0 + 3600, T0 + 400):
        s = costing.allocate(usage([{"start": T0, "stop": T0 + H}], [], price=1.0), T0 + 5 * H, starts=[start("sam@x", at)])["sessions"][0]
        assert s["users"] == [] and s["unallocatedCost"] == pytest.approx(1.0)


def test_people_who_chatted_still_pay_and_the_starter_does_not():
    hours = [{"hour": T0, "users": [user("a", 100)]}]
    s = costing.allocate(usage([{"start": T0, "stop": T0 + H}], hours, price=1.0), T0 + 5 * H, starts=[start("sam@x", T0)])["sessions"][0]
    assert charges(s) == {"a": pytest.approx(1.0)}


def test_unknown_or_malformed_starts_change_nothing():
    for starts in (None, [], [{"t": "x", "id": "sam@x"}], [{"t": T0, "id": ""}], [{"id": "sam@x"}], [{"t": True, "id": "sam@x"}]):
        s = costing.allocate(usage([{"start": T0, "stop": T0 + H}], [], price=1.0), T0 + 5 * H, starts=starts)["sessions"][0]
        assert s["users"] == [] and s["unallocatedCost"] == pytest.approx(1.0)


def test_the_starter_appears_in_the_totals_and_without_a_price_nothing_is_charged():
    out = costing.allocate(usage([{"start": T0, "stop": T0 + H}], [], price=1.0), T0 + 5 * H, starts=[start("sam@x", T0)])
    assert out["users"][0]["id"] == "sam@x" and out["users"][0]["charge"] == pytest.approx(1.0)
    priceless = costing.allocate(usage([{"start": T0, "stop": T0 + H}], [], price=None), T0 + 5 * H, starts=[start("sam@x", T0)])
    assert priceless["sessions"][0]["unallocatedCost"] is None
