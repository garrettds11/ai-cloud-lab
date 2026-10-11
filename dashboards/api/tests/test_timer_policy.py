"""Timer policy through the Control API: the panel owns the values; they apply as entered.
Terraform only seeds the initial values and keeps the on/off gate and the absolute backstop.
Run from dashboards/api: python -m pytest -q tests/test_timer_policy.py"""

import json

import pytest

from test_handler import ADM, BOTH, NONE, OP, aws, call, client_error  # noqa: F401  (aws is an autouse fixture)

import handler  # noqa: E402

POLICY = "/lab/auto-stop/policy"


class PolicySSM:
    """Stands in for SSM: `seed` is the deployment's auto-stop parameter, `policy` the saved panel policy."""

    def __init__(self, seed=None, policy=None, write_error=None):
        self.seed = seed or {"enabled": True, "idle_minutes": 60, "max_uptime_minutes": 240}
        self.policy, self.write_error, self.writes = policy, write_error, []

    def get_parameter(self, Name):
        if Name == POLICY:
            if self.policy is None:
                raise client_error("ParameterNotFound")
            return {"Parameter": {"Value": self.policy}}
        return {"Parameter": {"Value": json.dumps(self.seed)}}

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
    """Saves a policy. Every field is required, so a test that cares about one still sends the rest."""
    return call("PUT /admin/timer-policy", user, body={"idleMinutes": 60, "maxUptimeMinutes": 240, "maxResets": 50, **body})


def test_only_administrators_read_or_change_it(aws, ssm):
    for user in (OP, NONE, BOTH):
        assert call("GET /admin/timer-policy", user)[0] == 403
        assert put({"idleMinutes": 30}, user)[0] == 403
    assert ssm.writes == []
    assert call("GET /admin/timer-policy", ADM)[0] == 200


def test_with_no_policy_the_seeded_values_apply(aws, ssm):
    body = call("GET /admin/timer-policy", ADM)[1]
    assert body["enabled"] is True
    assert body["custom"] is False
    assert body["values"] == {"idleMinutes": 60, "maxUptimeMinutes": 240, "maxResets": 50}
    assert body["absoluteMaxMinutes"] == 0
    assert body["bounds"] == {"minMinutes": 5, "maxMinutes": 10080, "maxResets": 500}


def test_saved_values_are_applied_and_recorded(aws, ssm):
    status, body = put({"idleMinutes": 20, "maxUptimeMinutes": 120})
    assert status == 200 and body["custom"] is True
    assert body["values"] == {"idleMinutes": 20, "maxUptimeMinutes": 120, "maxResets": 50}
    write = ssm.writes[0]
    assert write["Name"] == POLICY and write["Overwrite"] is True and write["Type"] == "String"
    saved = json.loads(write["Value"])
    assert saved["idle_minutes"] == 20 and saved["max_uptime_minutes"] == 120 and saved["by"] == ADM
    assert any("timer policy" in item["action"] for item in aws.events.items.values() if item.get("pk") == "changes")


def test_the_reset_button_and_the_lab_view_use_the_policy(aws, ssm):
    put({"maxUptimeMinutes": 100})
    assert handler._rule()["max"] == 100


def test_values_longer_than_the_seed_are_allowed(aws, ssm):
    # The panel is authoritative: it is not capped by the deployment's seeded values.
    status, body = put({"idleMinutes": 600, "maxUptimeMinutes": 9999})
    assert status == 200
    assert body["values"]["idleMinutes"] == 600 and body["values"]["maxUptimeMinutes"] == 9999


def test_a_seed_far_below_the_bounds_does_not_limit_the_panel(aws, monkeypatch):
    # The old 20-minute "ceiling" problem: a small seed must not cap what the panel can set.
    fake = PolicySSM(seed={"enabled": True, "idle_minutes": 0, "max_uptime_minutes": 20})
    monkeypatch.setattr(handler, "ssm", fake)
    monkeypatch.setattr(handler, "POLICY_PARAMETER", POLICY)
    status, body = put({"maxUptimeMinutes": 240})
    assert status == 200 and body["values"]["maxUptimeMinutes"] == 240


@pytest.mark.parametrize("bad", [{"idleMinutes": 4}, {"maxUptimeMinutes": 4}, {"idleMinutes": -1}, {"idleMinutes": "30"},
                                 {"idleMinutes": 1.5}, {"idleMinutes": True}, {"maxUptimeMinutes": 10081}])
def test_out_of_range_or_wrong_type_values_are_refused(aws, ssm, bad):
    assert put(bad)[0] == 400 and ssm.writes == []


def test_the_refusal_names_the_fixed_bounds_not_the_deployment(aws, monkeypatch):
    fake = PolicySSM(seed={"enabled": True, "idle_minutes": 0, "max_uptime_minutes": 20})
    monkeypatch.setattr(handler, "ssm", fake)
    monkeypatch.setattr(handler, "POLICY_PARAMETER", POLICY)
    status, body = put({"maxUptimeMinutes": 4})
    assert status == 400 and "5" in body["message"] and "10080" in body["message"]


def test_every_field_is_required(aws, ssm):
    for body in ({}, {"idleMinutes": 20}, {"idleMinutes": 20, "maxUptimeMinutes": 30}, {"maxResets": None}, {"idleMinutes": None}):
        status, answer = call("PUT /admin/timer-policy", ADM, body=body)
        assert status == 400 and "required" in answer["message"]
    assert ssm.writes == []


def test_zero_turns_a_timer_off(aws, ssm):
    status, body = put({"idleMinutes": 0, "maxUptimeMinutes": 0})
    assert status == 200
    assert body["values"]["idleMinutes"] == 0 and body["values"]["maxUptimeMinutes"] == 0


def test_a_timer_off_in_the_seed_can_be_switched_on(aws, monkeypatch):
    fake = PolicySSM(seed={"enabled": True, "idle_minutes": 0, "max_uptime_minutes": 90})
    monkeypatch.setattr(handler, "ssm", fake)
    monkeypatch.setattr(handler, "POLICY_PARAMETER", POLICY)
    status, body = put({"idleMinutes": 45, "maxUptimeMinutes": 60})
    assert status == 200 and body["values"] == {"idleMinutes": 45, "maxUptimeMinutes": 60, "maxResets": 50}


def test_a_saved_policy_is_authoritative(aws, monkeypatch):
    fake = PolicySSM(policy=json.dumps({"idle_minutes": 600, "max_uptime_minutes": 9999, "max_resets": 40}))
    monkeypatch.setattr(handler, "ssm", fake)
    monkeypatch.setattr(handler, "POLICY_PARAMETER", POLICY)
    body = call("GET /admin/timer-policy", ADM)[1]
    assert body["custom"] is True
    assert body["values"] == {"idleMinutes": 600, "maxUptimeMinutes": 9999, "maxResets": 40}


@pytest.mark.parametrize("raw", ["not json", "[]", "3", json.dumps({"idle_minutes": "5"}), json.dumps({"idle_minutes": True})])
def test_a_malformed_policy_falls_back_to_the_seed(aws, monkeypatch, raw):
    fake = PolicySSM(policy=raw)
    monkeypatch.setattr(handler, "ssm", fake)
    monkeypatch.setattr(handler, "POLICY_PARAMETER", POLICY)
    assert call("GET /admin/timer-policy", ADM)[1]["values"] == {"idleMinutes": 60, "maxUptimeMinutes": 240, "maxResets": 50}


def test_when_auto_stop_is_off_for_the_lab_there_is_nothing_to_set(aws, monkeypatch):
    fake = PolicySSM(seed={"enabled": False, "idle_minutes": 0, "max_uptime_minutes": 0}, policy=json.dumps({"idle_minutes": 5}))
    monkeypatch.setattr(handler, "ssm", fake)
    monkeypatch.setattr(handler, "POLICY_PARAMETER", POLICY)
    body = call("GET /admin/timer-policy", ADM)[1]
    assert body["enabled"] is False
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
    assert body["values"]["maxResets"] == 2


def test_zero_resets_turns_resets_off(aws, ssm):
    body = put({"maxResets": 0})[1]
    assert body["values"]["maxResets"] == 0


@pytest.mark.parametrize("value", [-1, 501, 1.5, "3", True])
def test_bad_reset_counts_are_refused(aws, ssm, value):
    status, body = put({"maxResets": value})
    assert status == 400 and "Resets per run" in body["message"]
    assert ssm.writes == []


def test_resets_up_to_the_maximum_are_allowed(aws, ssm):
    assert put({"maxResets": 500})[0] == 200
    assert json.loads(ssm.writes[-1]["Value"])["max_resets"] == 500


def test_a_saved_reset_count_above_the_seed_is_authoritative(aws, ssm):
    ssm.seed = {**ssm.seed, "max_resets": 2}
    ssm.policy = json.dumps({"max_resets": 40})
    assert call("GET /admin/timer-policy", ADM)[1]["values"]["maxResets"] == 40


def test_the_absolute_limit_is_shown_and_is_not_changeable_here(aws, ssm):
    ssm.seed = {**ssm.seed, "absolute_max_minutes": 600}
    ssm.policy = json.dumps({"absolute_max_minutes": 5000})
    body = call("GET /admin/timer-policy", ADM)[1]
    assert body["absoluteMaxMinutes"] == 600
    put({"idleMinutes": 30})
    assert "absolute_max_minutes" not in json.loads(ssm.writes[-1]["Value"])
