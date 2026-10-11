"""Timer policy through the Control API: the panel sets shorter limits inside the lab's.
Run from dashboards/api: python -m pytest -q tests/test_timer_policy.py"""

import json

import pytest

from test_handler import ADM, BOTH, NONE, OP, aws, call, client_error  # noqa: F401  (aws is an autouse fixture)

import handler  # noqa: E402

POLICY = "/lab/auto-stop/policy"


class PolicySSM:
    def __init__(self, ceiling=None, policy=None, write_error=None):
        self.ceiling = ceiling or {"enabled": True, "idle_minutes": 60, "max_uptime_minutes": 240}
        self.policy, self.write_error, self.writes = policy, write_error, []

    def get_parameter(self, Name):
        if Name == POLICY:
            if self.policy is None:
                raise client_error("ParameterNotFound")
            return {"Parameter": {"Value": self.policy}}
        return {"Parameter": {"Value": json.dumps(self.ceiling)}}

    def put_parameter(self, **kwargs):
        if self.write_error:
            raise client_error(self.write_error)
        self.writes.append(kwargs)
        self.policy = kwargs["Value"]


@pytest.fixture
def ssm(monkeypatch):
    fake = PolicySSM()
    monkeypatch.setattr(handler, "ssm", fake)
    monkeypatch.setattr(handler, "POLICY_PARAMETER", POLICY)
    return fake


def put(body, user=ADM):
    return call("PUT /admin/timer-policy", user, body=body)


def test_only_administrators_read_or_change_it(aws, ssm):
    for user in (OP, NONE, BOTH):
        assert call("GET /admin/timer-policy", user)[0] == 403
        assert put({"idleMinutes": 30}, user)[0] == 403
    assert ssm.writes == []
    assert call("GET /admin/timer-policy", ADM)[0] == 200


def test_with_no_policy_the_lab_values_apply(aws, ssm):
    body = call("GET /admin/timer-policy", ADM)[1]
    assert body["enabled"] is True
    assert body["ceiling"] == {"idleMinutes": 60, "maxUptimeMinutes": 240, "absoluteMaxMinutes": 0, "maxResets": 0}
    assert body["policy"] == {"idleMinutes": 0, "maxUptimeMinutes": 0, "maxResets": 0}
    assert body["effective"] == {"idleMinutes": 60, "maxUptimeMinutes": 240, "absoluteMaxMinutes": 0, "maxResets": 0}


def test_a_shorter_policy_is_saved_applied_and_recorded(aws, ssm):
    status, body = put({"idleMinutes": 20, "maxUptimeMinutes": 120})
    assert status == 200 and body["effective"] == {"idleMinutes": 20, "maxUptimeMinutes": 120, "absoluteMaxMinutes": 0, "maxResets": 0}
    write = ssm.writes[0]
    assert write["Name"] == POLICY and write["Overwrite"] is True and write["Type"] == "String"
    saved = json.loads(write["Value"])
    assert saved["idle_minutes"] == 20 and saved["max_uptime_minutes"] == 120 and saved["by"] == ADM
    assert any("timer policy" in item["action"] for item in aws.events.items.values() if item.get("pk") == "changes")


def test_the_reset_button_and_the_lab_view_use_the_policy(aws, ssm):
    put({"maxUptimeMinutes": 100})
    assert handler._rule()["max"] == 100


def test_a_value_longer_than_the_lab_limit_is_refused(aws, ssm):
    assert put({"idleMinutes": 61})[0] == 400
    assert put({"maxUptimeMinutes": 241})[0] == 400
    assert ssm.writes == []


@pytest.mark.parametrize("bad", [{"idleMinutes": 4}, {"maxUptimeMinutes": 29}, {"idleMinutes": -1}, {"idleMinutes": "30"},
                                 {"idleMinutes": 1.5}, {"idleMinutes": True}, {"maxUptimeMinutes": 10081}])
def test_out_of_range_or_wrong_type_values_are_refused(aws, ssm, bad):
    assert put(bad)[0] == 400 and ssm.writes == []


def test_zero_goes_back_to_the_lab_value(aws, ssm):
    put({"idleMinutes": 20})
    status, body = put({"idleMinutes": 0, "maxUptimeMinutes": 0})
    assert status == 200 and body["effective"] == {"idleMinutes": 60, "maxUptimeMinutes": 240, "absoluteMaxMinutes": 0, "maxResets": 0}


def test_a_limit_the_lab_leaves_off_can_be_switched_on(aws, monkeypatch):
    fake = PolicySSM(ceiling={"enabled": True, "idle_minutes": 0, "max_uptime_minutes": 90})
    monkeypatch.setattr(handler, "ssm", fake)
    monkeypatch.setattr(handler, "POLICY_PARAMETER", POLICY)
    status, body = put({"idleMinutes": 45, "maxUptimeMinutes": 60})
    assert status == 200 and body["effective"] == {"idleMinutes": 45, "maxUptimeMinutes": 60, "absoluteMaxMinutes": 0, "maxResets": 0}


def test_a_hand_written_longer_policy_cannot_loosen_the_lab(aws, monkeypatch):
    fake = PolicySSM(policy=json.dumps({"idle_minutes": 600, "max_uptime_minutes": 9999}))
    monkeypatch.setattr(handler, "ssm", fake)
    monkeypatch.setattr(handler, "POLICY_PARAMETER", POLICY)
    assert call("GET /admin/timer-policy", ADM)[1]["effective"] == {"idleMinutes": 60, "maxUptimeMinutes": 240, "absoluteMaxMinutes": 0, "maxResets": 0}


@pytest.mark.parametrize("raw", ["not json", "[]", "3", json.dumps({"idle_minutes": "5"}), json.dumps({"idle_minutes": True})])
def test_a_malformed_policy_is_ignored(aws, monkeypatch, raw):
    fake = PolicySSM(policy=raw)
    monkeypatch.setattr(handler, "ssm", fake)
    monkeypatch.setattr(handler, "POLICY_PARAMETER", POLICY)
    assert call("GET /admin/timer-policy", ADM)[1]["effective"] == {"idleMinutes": 60, "maxUptimeMinutes": 240, "absoluteMaxMinutes": 0, "maxResets": 0}


def test_when_auto_stop_is_off_for_the_lab_there_is_nothing_to_set(aws, monkeypatch):
    fake = PolicySSM(ceiling={"enabled": False, "idle_minutes": 0, "max_uptime_minutes": 0}, policy=json.dumps({"idle_minutes": 5}))
    monkeypatch.setattr(handler, "ssm", fake)
    monkeypatch.setattr(handler, "POLICY_PARAMETER", POLICY)
    body = call("GET /admin/timer-policy", ADM)[1]
    assert body["enabled"] is False and body["effective"] == {"idleMinutes": 0, "maxUptimeMinutes": 0, "absoluteMaxMinutes": 0, "maxResets": 0}
    assert put({"idleMinutes": 20})[0] == 409


def test_a_failed_write_is_a_clear_error(aws, monkeypatch):
    fake = PolicySSM(write_error="AccessDeniedException")
    monkeypatch.setattr(handler, "ssm", fake)
    monkeypatch.setattr(handler, "POLICY_PARAMETER", POLICY)
    status, body = put({"idleMinutes": 20})
    assert status == 502 and "Could not save" in body["message"]


def test_without_the_parameter_configured_it_says_so(aws, ssm, monkeypatch):
    monkeypatch.setattr(handler, "POLICY_PARAMETER", "")
    assert put({"idleMinutes": 20})[0] == 503


# ---- resets per run and the absolute limit --------------------------------------------------


def test_resets_per_run_are_saved_and_shown(aws, ssm):
    status, body = put({"idleMinutes": 0, "maxUptimeMinutes": 0, "maxResets": 2})
    assert status == 200
    assert json.loads(ssm.writes[0]["Value"])["max_resets"] == 2
    assert body["policy"]["maxResets"] == 2 and body["effective"]["maxResets"] == 2 and body["ceiling"]["maxResets"] == 0


def test_zero_resets_means_the_lab_value(aws, ssm):
    ssm.ceiling = {**ssm.ceiling, "max_resets": 4}
    assert put({"maxResets": 2})[1]["effective"]["maxResets"] == 2
    body = put({"maxResets": 0})[1]
    assert body["policy"]["maxResets"] == 0 and body["effective"]["maxResets"] == 4


@pytest.mark.parametrize("value", [-1, 51, 1.5, "3", True])
def test_bad_reset_counts_are_refused(aws, ssm, value):
    status, body = put({"maxResets": value})
    assert status == 400 and "Resets per run" in body["message"]
    assert ssm.writes == []


def test_the_panel_cannot_allow_more_resets_than_the_lab(aws, ssm):
    ssm.ceiling = {**ssm.ceiling, "max_resets": 3}
    status, body = put({"maxResets": 4})
    assert status == 400 and "lab's own limit of 3" in body["message"]
    assert put({"maxResets": 3})[0] == 200


def test_a_hand_written_higher_reset_count_cannot_loosen_the_lab(aws, ssm):
    ssm.ceiling = {**ssm.ceiling, "max_resets": 2}
    ssm.policy = json.dumps({"max_resets": 40})
    assert call("GET /admin/timer-policy", ADM)[1]["effective"]["maxResets"] == 2


def test_the_absolute_limit_is_shown_and_is_not_changeable_here(aws, ssm):
    ssm.ceiling = {**ssm.ceiling, "absolute_max_minutes": 600}
    ssm.policy = json.dumps({"absolute_max_minutes": 5000})
    body = call("GET /admin/timer-policy", ADM)[1]
    assert body["ceiling"]["absoluteMaxMinutes"] == 600 and body["effective"]["absoluteMaxMinutes"] == 600
    put({"absoluteMaxMinutes": 9000, "idleMinutes": 30})
    assert "absolute_max_minutes" not in json.loads(ssm.writes[-1]["Value"])
