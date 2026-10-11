"""Timer reset limits: resets per run, and the absolute limit no reset can extend.
Run from dashboards/api: python -m pytest -q tests/test_reset_limits.py"""

import datetime
import json

import pytest

from test_handler import ADM, OP, RESET, aws, call, grant, running, use_reset_ssm  # noqa: F401  (aws is an autouse fixture)

import handler  # noqa: E402

PATH = {"instanceId": "i-aaa"}


def launch_epoch(aws):
    return int(aws.ec2.launched["i-aaa"].timestamp())


@pytest.fixture
def ready(aws, monkeypatch):
    grant(aws, OP, "i-aaa")
    running(aws, minutes_up=40)
    return aws


def test_by_default_fifty_resets_are_allowed_and_counted(ready, monkeypatch):
    ssm = use_reset_ssm(monkeypatch)
    for n in (1, 2, 3):
        status, body = call(RESET, OP, path=PATH)
        assert status == 200 and body["resetsLeft"] == 50 - n
        assert ssm.count_writes[-1]["Value"] == f"{launch_epoch(ready)}:{n}"
    assert len(ssm.writes) == 3


def test_zero_resets_turns_the_button_off(ready, monkeypatch):
    ssm = use_reset_ssm(monkeypatch, rule={"max_resets": 0})
    status, body = call(RESET, OP, path=PATH)
    assert status == 409 and "turned off" in body["message"]
    assert ssm.writes == [] and ssm.count_writes == []
    view = call("GET /instances", OP)[1][0]["autoStop"]
    assert view["resetsAllowed"] == 0


def test_a_lab_limit_refuses_the_reset_after_the_last_one(ready, monkeypatch):
    ssm = use_reset_ssm(monkeypatch, rule={"max_resets": 2})
    first = call(RESET, OP, path=PATH)[1]
    assert first["resetsLeft"] == 1 and "1 reset left" in first["message"]
    assert call(RESET, OP, path=PATH)[1]["resetsLeft"] == 0
    status, body = call(RESET, OP, path=PATH)
    assert status == 409 and "all 2 timer resets" in body["message"]
    assert len(ssm.writes) == 2  # the refused reset wrote nothing
    assert ssm.count == f"{launch_epoch(ready)}:2"
    lines = [r["event"] for r in call("GET /logs", OP)[1]]
    assert any("refused" in e and "all 2 resets" in e for e in lines)


def test_the_panel_policy_sets_the_reset_limit(ready, monkeypatch):
    # The panel's saved reset count applies as entered, whether below or above the seed.
    lower = use_reset_ssm(monkeypatch, rule={"max_resets": 3}, policy=json.dumps({"max_resets": 1}))
    assert call(RESET, OP, path=PATH)[0] == 200
    assert call(RESET, OP, path=PATH)[0] == 409
    assert len(lower.writes) == 1
    handler._rule_cache.update(at=0, value=None)
    higher = use_reset_ssm(monkeypatch, rule={"max_resets": 1}, policy=json.dumps({"max_resets": 3}))
    assert call(RESET, OP, path=PATH)[0] == 200
    assert call(RESET, OP, path=PATH)[0] == 200
    assert call(RESET, OP, path=PATH)[0] == 200
    assert call(RESET, OP, path=PATH)[0] == 409
    assert len(higher.writes) == 3


def test_a_policy_can_lower_the_default_of_fifty(ready, monkeypatch):
    ssm = use_reset_ssm(monkeypatch, policy=json.dumps({"max_resets": 1}))
    assert call(RESET, OP, path=PATH)[0] == 200
    assert call(RESET, OP, path=PATH)[0] == 409
    assert len(ssm.writes) == 1


@pytest.mark.parametrize("policy", ["not json", "[]", "3", json.dumps({"max_resets": "1"}), json.dumps({"max_resets": True}), json.dumps({"max_resets": -2}), json.dumps({"max_resets": 1.5})])
def test_a_malformed_policy_never_loosens_the_maximum_allowed(ready, monkeypatch, policy):
    ssm = use_reset_ssm(monkeypatch, rule={"max_resets": 1}, policy=policy)
    assert call(RESET, OP, path=PATH)[0] == 200
    assert call(RESET, OP, path=PATH)[0] == 409
    assert len(ssm.writes) == 1


def test_a_count_from_an_earlier_run_does_not_count(ready, monkeypatch):
    ssm = use_reset_ssm(monkeypatch, rule={"max_resets": 1}, count=f"{launch_epoch(ready) - 86400}:5")
    assert call(RESET, OP, path=PATH)[0] == 200
    assert ssm.count == f"{launch_epoch(ready)}:1"


@pytest.mark.parametrize("stored", ["", "garbage", "12:x", "1:2:3", "99999999999:1", "0:0"])
def test_a_malformed_count_counts_as_none(ready, monkeypatch, stored):
    ssm = use_reset_ssm(monkeypatch, rule={"max_resets": 1}, count=stored)
    assert call(RESET, OP, path=PATH)[0] == 200
    assert ssm.count == f"{launch_epoch(ready)}:1"


def test_an_unreadable_count_stops_the_reset(ready, monkeypatch):
    ssm = use_reset_ssm(monkeypatch, count_error="ThrottlingException")
    status, body = call(RESET, OP, path=PATH)
    assert status == 502 and "how many timer resets" in body["message"] and ssm.writes == []


def test_a_count_that_cannot_be_saved_does_not_bypass_the_limit(ready, monkeypatch):
    ssm = use_reset_ssm(monkeypatch, count_write_error="AccessDeniedException")
    status, body = call(RESET, OP, path=PATH)
    assert status == 502 and ssm.writes == []


def test_a_reset_is_capped_at_the_absolute_limit(ready, monkeypatch):
    running(ready, minutes_up=85)  # the current stop is at 90 minutes; a full reset would reach 175
    ssm = use_reset_ssm(monkeypatch, rule={"absolute_max_minutes": 150})
    status, body = call(RESET, OP, path=PATH)
    assert status == 200 and body["cappedByAbsoluteLimit"] is True
    assert body["stopAt"] == int(ready.ec2.launched['i-aaa'].timestamp() * 1000) + 150 * 60000
    assert "will stop at about" in body["message"]
    assert len(ssm.writes) == 1


def test_a_second_reset_that_would_add_no_time_is_refused(ready, monkeypatch):
    running(ready, minutes_up=85)
    ssm = use_reset_ssm(monkeypatch, rule={"absolute_max_minutes": 150})
    assert call(RESET, OP, path=PATH)[0] == 200
    status, body = call(RESET, OP, path=PATH)
    assert status == 409 and "absolute limit" in body["message"]
    assert len(ssm.writes) == 1 and len(ssm.count_writes) == 1


def test_a_far_absolute_limit_changes_nothing(ready, monkeypatch):
    use_reset_ssm(monkeypatch, rule={"absolute_max_minutes": 1000})
    status, body = call(RESET, OP, path=PATH)
    assert status == 200 and body["cappedByAbsoluteLimit"] is False
    assert body["stopAt"] == body["resetAt"] + 90 * 60000


def test_the_absolute_limit_is_only_ever_read_from_the_lab_setting(ready, monkeypatch):
    # A panel policy that names it is ignored.
    running(ready, minutes_up=85)
    use_reset_ssm(monkeypatch, rule={"absolute_max_minutes": 150}, policy=json.dumps({"absolute_max_minutes": 5000}))
    assert call(RESET, OP, path=PATH)[1]["stopAt"] == int(ready.ec2.launched['i-aaa'].timestamp() * 1000) + 150 * 60000


def test_an_unreadable_lab_setting_is_a_clear_refusal_not_a_crash(ready, monkeypatch):
    use_reset_ssm(monkeypatch, rule={"absolute_max_minutes": "x"})
    status, body = call(RESET, OP, path=PATH)
    assert status == 409 and "message" in body


def test_the_instance_view_shows_the_limits_and_the_count(ready, monkeypatch):
    use_reset_ssm(monkeypatch, rule={"max_resets": 3, "absolute_max_minutes": 600}, count=f"{launch_epoch(ready)}:2")
    view = call("GET /instances", OP)[1][0]["autoStop"]
    assert view["absoluteMaxMinutes"] == 600 and view["resetsAllowed"] == 3 and view["resetsUsed"] == 2
    handler._rule_cache.update(at=0, value=None)
    use_reset_ssm(monkeypatch)
    view = call("GET /instances", OP)[1][0]["autoStop"]
    assert view["absoluteMaxMinutes"] == 0 and view["resetsAllowed"] == 50 and view["resetsUsed"] == 0


def test_an_absolute_limit_alone_still_counts_as_auto_stop_on(ready, monkeypatch):
    use_reset_ssm(monkeypatch, max_minutes=0, rule={"idle_minutes": 0, "absolute_max_minutes": 120})
    view = call("GET /instances", OP)[1][0]
    assert view["autoStop"]["enabled"] is True and view["rule"]["absoluteMaxMinutes"] == 120
