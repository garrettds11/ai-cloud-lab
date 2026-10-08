"""Tests for the Control API. AWS is replaced with small in-memory fakes, so no account is needed.

Run from dashboards/api:   python -m pytest -q
"""

import datetime
import json
import os
import re
import sys

import pytest
from botocore.exceptions import ClientError

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.update(
    BOOTSTRAP_ADMINS="boot@x.test",
    INSTANCE_IDS="i-aaa,i-bbb",
    TARGET_GROUP_ARN="arn:tg",
    AUTO_STOP_PARAMETER="/lab/auto-stop",
    SERVICE_URL="https://example.test",
)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import handler  # noqa: E402


def client_error(code):
    return ClientError({"Error": {"Code": code, "Message": code}}, "op")


def _flat(cond):
    e = cond.get_expression()
    if e["operator"] == "AND":
        return _flat(e["values"][0]) + _flat(e["values"][1])
    return [(e["operator"], e["values"][0].name, e["values"][1])]


RESERVED = {"name", "sub", "status", "role", "user", "key", "value", "data", "comment", "count", "timestamp"}


class FakeTable:
    def __init__(self, key_names):
        self.keys = key_names
        self.items = {}
        self.down = False

    def _k(self, item):
        return tuple(item[k] for k in self.keys)

    def _check(self):
        if self.down:
            raise client_error("InternalServerError")

    def get_item(self, Key):
        self._check()
        item = self.items.get(tuple(Key[k] for k in self.keys))
        return {"Item": dict(item)} if item else {}

    def put_item(self, Item, ConditionExpression=None):
        self._check()
        if ConditionExpression and "attribute_not_exists" in ConditionExpression and self._k(Item) in self.items:
            raise client_error("ConditionalCheckFailedException")
        self.items[self._k(Item)] = dict(Item)

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues, ConditionExpression=None, ExpressionAttributeNames=None, **_):
        self._check()
        k = tuple(Key[n] for n in self.keys)
        if k not in self.items:
            if ConditionExpression:
                raise client_error("ConditionalCheckFailedException")
            self.items[k] = dict(Key)
        names = ExpressionAttributeNames or {}
        for target, _value in re.findall(r"(#?\w+) = (:\w+)", UpdateExpression):
            # DynamoDB rejects these words when they are not behind a #name placeholder
            assert target.startswith("#") or target.lower() not in RESERVED, f"{target} is a DynamoDB reserved word"
        for target, value in re.findall(r"(#?\w+) = (:\w+)", UpdateExpression):
            self.items[k][names.get(target, target)] = ExpressionAttributeValues[value]

    def scan(self, **_):
        self._check()
        return {"Items": [dict(v) for v in self.items.values()]}

    def query(self, KeyConditionExpression, ScanIndexForward=True, Limit=None):
        self._check()
        conds = _flat(KeyConditionExpression)
        rows = list(self.items.values())
        for op, name, value in conds:
            if op == "=":
                rows = [r for r in rows if r[name] == value]
            elif op == "begins_with":
                rows = [r for r in rows if str(r[name]).startswith(value)]
        rows.sort(key=lambda r: r[self.keys[1]], reverse=not ScanIndexForward)
        return {"Items": rows[:Limit] if Limit else rows}


class FakeEC2:
    def __init__(self):
        self.state = {"i-aaa": "stopped", "i-bbb": "stopped"}
        self.checks = {"i-aaa": "ok", "i-bbb": "ok"}
        self.launched = {}
        self.started = []
        self.start_error = None

    def describe_instances(self, InstanceIds):
        out = []
        for i in InstanceIds:
            launch = self.launched.get(i, datetime.datetime.now(datetime.timezone.utc))
            out.append({"InstanceId": i, "InstanceType": "t3.large", "LaunchTime": launch,
                        "State": {"Name": self.state[i]}, "Tags": [{"Key": "Name", "Value": "lab-" + i[-3:]}]})
        return {"Reservations": [{"Instances": out}]}

    def describe_instance_status(self, InstanceIds, IncludeAllInstances):
        return {"InstanceStatuses": [
            {"InstanceId": i, "InstanceStatus": {"Status": self.checks[i]}, "SystemStatus": {"Status": self.checks[i]}}
            for i in InstanceIds]}

    def start_instances(self, InstanceIds):
        if self.start_error:
            raise client_error(self.start_error)
        self.started += InstanceIds
        for i in InstanceIds:
            self.state[i] = "pending"


class FakeCloudWatch:
    def __init__(self):
        self.points = {}

    def get_metric_statistics(self, MetricName, Statistics, **_):
        return {"Datapoints": self.points.get(MetricName, [])}


OP, ADM, NONE, BOTH, BOOT = "olive@x.test", "ada@x.test", "nina@x.test", "bo@x.test", "boot@x.test"


@pytest.fixture(autouse=True)
def aws(monkeypatch):
    fakes = {
        "ec2": FakeEC2(),
        "cloudwatch": FakeCloudWatch(),
        "users": FakeTable(("email",)),
        "entitlements": FakeTable(("userId", "instanceId")),
        "events": FakeTable(("pk", "sk")),
    }
    for name, fake in fakes.items():
        monkeypatch.setattr(handler, name, fake)
    for email, name, roles in [(OP, "Olive Operator", ["operators"]), (ADM, "Ada Admin", ["admin"]),
                               (NONE, "Nina None", []), (BOTH, "Bo Both", ["operators", "user_mgrs"])]:
        fakes["users"].items[(email,)] = {"email": email, "name": name, "roles": roles, "source": "panel"}
    monkeypatch.setattr(handler, "elbv2", type("E", (), {"describe_target_health": lambda self, TargetGroupArn: {"TargetHealthDescriptions": []}})())
    monkeypatch.setattr(handler, "ssm", type("S", (), {"get_parameter": lambda self, Name: {"Parameter": {"Value": json.dumps({"enabled": True, "idle_minutes": 60, "max_uptime_minutes": 90})}}})())
    monkeypatch.setattr(handler, "cloudtrail", type("C", (), {"lookup_events": lambda self, **k: {"Events": []}})())
    handler._rule_cache.update(at=0, value=None)
    handler._lab_cache.update(at=0.0, value=None)
    return type("AWS", (), fakes)


def call(route, user, path=None, query=None, body=None, extra_claims=None):
    claims = {"sub": "sub-" + user, "email": user, "name": user, "auth_time": "1790000000"}
    claims.update(extra_claims or {})
    event = {
        "routeKey": route,
        "pathParameters": path or {},
        "queryStringParameters": query or {},
        "body": json.dumps(body) if body is not None else None,
        "requestContext": {
            "http": {"sourceIp": "203.0.113.9", "userAgent": "Mozilla/5.0 (Windows NT 10.0) Chrome/120"},
            "authorizer": {"jwt": {"claims": claims}},
        },
    }
    entry = handler.admin_handler if route.split(" ", 1)[1].startswith("/admin") else handler.customer_handler
    out = entry(event, None)
    return out["statusCode"], json.loads(out["body"])


def grant(aws, user, instance, status="applied"):
    aws.entitlements.put_item({"userId": user, "instanceId": instance, "status": status})


def set_health(monkeypatch, state):
    monkeypatch.setattr(handler, "elbv2", type("E", (), {"describe_target_health": lambda self, TargetGroupArn: {
        "TargetHealthDescriptions": [{"Target": {"Id": "i-aaa"}, "TargetHealth": {"State": state}}]}})())


def test_missing_email_or_unverified_email_is_401():
    out = handler.customer_handler({"routeKey": "GET /instances", "requestContext": {}}, None)
    assert out["statusCode"] == 401
    assert call("GET /instances", OP, extra_claims={"email": ""})[0] == 401
    assert call("GET /instances", OP, extra_claims={"email_verified": "false"})[0] == 401


def test_email_case_does_not_matter(aws):
    grant(aws, OP, "i-aaa")
    assert call("GET /instances", "Olive@X.test")[0] == 200
    assert len(call("GET /instances", "Olive@X.test")[1]) == 1


def test_table_outage_fails_closed(aws):
    aws.users.down = True
    assert call("GET /instances", OP)[0] == 503


def test_non_operator_sees_no_instances(aws):
    grant(aws, NONE, "i-aaa")
    assert call("GET /instances", NONE) == (200, [])


def test_operator_sees_only_granted_instances(aws):
    grant(aws, OP, "i-aaa")
    grant(aws, OP, "i-bbb", status="revoked")
    status, body = call("GET /instances", OP)
    assert status == 200
    assert [i["id"] for i in body] == ["i-aaa"]
    assert body[0]["phase"] == "stopped" and body[0]["autoStop"] is None and body[0]["url"] == "https://example.test"
    assert body[0]["rule"] == {"enabled": True, "idleMinutes": 60, "maxUptimeMinutes": 90}


def test_admin_sees_and_starts_every_instance_without_grants(aws):
    status, body = call("GET /instances", ADM)
    assert status == 200
    assert sorted(i["id"] for i in body) == ["i-aaa", "i-bbb"]
    assert call("POST /instances/{instanceId}/start", ADM, path={"instanceId": "i-aaa"})[0] == 200
    assert [i["id"] for i in call("GET /instances", BOOT)[1]] == [i["id"] for i in body]


def test_phase_needs_status_checks_and_alb_health(aws, monkeypatch):
    grant(aws, OP, "i-aaa")
    aws.ec2.state["i-aaa"] = "running"
    set_health(monkeypatch, "initial")
    assert call("GET /instances", OP)[1][0]["phase"] == "initializing"
    set_health(monkeypatch, "healthy")
    aws.ec2.checks["i-aaa"] = "initializing"
    assert call("GET /instances", OP)[1][0]["phase"] == "initializing"
    aws.ec2.checks["i-aaa"] = "ok"
    view = call("GET /instances", OP)[1][0]
    assert view["phase"] == "ready" and view["checks"] == {"ec2": True, "http": True}


def test_auto_stop_setting_up_then_silent(aws, monkeypatch):
    grant(aws, OP, "i-aaa")
    aws.ec2.state["i-aaa"] = "running"
    now = datetime.datetime.now(datetime.timezone.utc)
    aws.ec2.launched["i-aaa"] = now - datetime.timedelta(minutes=10)
    assert call("GET /instances", OP)[1][0]["autoStop"]["setupDone"] is False
    aws.ec2.launched["i-aaa"] = now - datetime.timedelta(minutes=45)
    a = call("GET /instances", OP)[1][0]["autoStop"]
    assert a["setupDone"] is True and a["heartbeatAgeMinutes"] >= 10
    aws.cloudwatch.points = {
        "Heartbeat": [{"Timestamp": now - datetime.timedelta(seconds=30), "Sum": 1}],
        "IdleMinutes": [{"Timestamp": now, "Maximum": 7}],
        "ActiveUsers": [{"Timestamp": now, "Maximum": 0}],
    }
    a = call("GET /instances", OP)[1][0]["autoStop"]
    assert a["idleMinutes"] == 7 and a["activeUsers"] == 0 and a["heartbeatAgeMinutes"] < 2


START = "POST /instances/{instanceId}/start"


def test_start_happy_path(aws):
    grant(aws, OP, "i-aaa")
    assert call(START, OP, path={"instanceId": "i-aaa"}) == (200, {"ok": True})
    assert aws.ec2.started == ["i-aaa"]
    assert any("Start requested" in e.get("event", "") for e in aws.events.items.values())


def test_start_needs_a_grant(aws):
    status, _ = call(START, OP, path={"instanceId": "i-aaa"})
    assert status == 403 and aws.ec2.started == []
    assert any("Start refused" in e.get("event", "") for e in aws.events.items.values())


def test_start_needs_operator_role_even_with_a_grant(aws):
    grant(aws, NONE, "i-aaa")
    assert call(START, NONE, path={"instanceId": "i-aaa"})[0] == 403
    assert aws.ec2.started == []


def test_roles_in_the_token_are_ignored(aws):
    grant(aws, NONE, "i-aaa")
    status, _ = call(START, NONE, path={"instanceId": "i-aaa"}, extra_claims={"cognito:groups": "[operators user_mgrs]", "groups": ["operators"]})
    assert status == 403 and aws.ec2.started == []
    assert call("GET /admin/users", NONE, extra_claims={"cognito:groups": "[user_mgrs]"})[0] == 403


def test_start_rejects_unmanaged_and_running_and_capacity(aws):
    grant(aws, OP, "i-aaa")
    assert call(START, OP, path={"instanceId": "i-zzz"})[0] == 404
    aws.ec2.state["i-aaa"] = "running"
    assert call(START, OP, path={"instanceId": "i-aaa"})[0] == 409
    aws.ec2.state["i-aaa"] = "stopped"
    aws.ec2.start_error = "InsufficientInstanceCapacity"
    status, body = call(START, OP, path={"instanceId": "i-aaa"})
    assert status == 503 and "capacity" in body["message"]


def test_no_route_can_stop_an_instance():
    assert call("POST /instances/{instanceId}/stop", BOTH, path={"instanceId": "i-aaa"})[0] == 404


def test_first_sign_in_adds_a_user_with_no_roles(aws):
    newcomer = "new@x.test"
    assert call("GET /instances", newcomer) == (200, [])
    status, me = call("POST /session", newcomer)
    assert status == 200 and me["roles"] == [] and me["email"] == newcomer
    row = aws.users.items[(newcomer,)]
    assert "roles" not in row and row["source"] == "panel" and row["sub"] == "sub-" + newcomer
    assert call("GET /admin/users", newcomer)[0] == 403


def test_sign_in_keeps_existing_roles(aws):
    call("POST /session", OP)
    assert aws.users.items[(OP,)]["roles"] == ["operators"]
    assert call("POST /session", OP)[1]["roles"] == ["operators"]


def test_bootstrap_admin_works_with_an_empty_table(aws):
    aws.users.items.clear()
    status, me = call("POST /session", BOOT)
    assert status == 200 and me["roles"] == ["admin"]
    assert call("GET /admin/users", BOOT)[0] == 200
    assert call("GET /admin/users", OP)[0] == 403


def test_bootstrap_admin_cannot_be_demoted(aws):
    status, body = call("PUT /admin/users/{userId}", ADM, path={"userId": BOOT}, body={"roles": [{"role": "admin", "member": False}]})
    assert status == 409 and "settings" in body["message"]


def test_login_recorded_once_and_marked_current(aws):
    call("POST /session", OP)
    call("POST /session", OP)
    status, rows = call("GET /logins", OP)
    assert len(rows) == 1 and rows[0]["current"] is True and rows[0]["browser"] == "Chrome on Windows"
    assert rows[0]["ip"] == "203.0.113.9"
    assert call("GET /logins", ADM)[1] == []


def test_logs_are_only_the_callers_own(aws):
    call("POST /session", OP)
    call("POST /session", ADM)
    status, rows = call("GET /logs", OP)
    assert status == 200 and all(r["userId"] == OP for r in rows) and len(rows) == 1


def test_admin_routes_need_user_mgrs(aws):
    for route, path in [("GET /admin/users", None), ("GET /admin/instances", None), ("GET /admin/changes", None),
                        ("GET /admin/users/{userId}/grants", {"userId": OP})]:
        assert call(route, OP, path=path)[0] == 403
    assert call("PUT /admin/users/{userId}", OP, path={"userId": NONE}, body={"roles": []})[0] == 403


def test_admin_lists_users_with_roles(aws):
    status, users = call("GET /admin/users", ADM)
    by = {u["id"]: u for u in users}
    assert status == 200 and by[OP]["roles"] == ["operators"] and by[NONE]["roles"] == [] and by[BOTH]["name"] == "Bo Both"
    assert by[BOOT]["roles"] == ["admin"] and by[BOOT]["source"] == "settings"
    assert by[ADM]["roles"] == ["admin"] and by[BOTH]["roles"] == ["operators", "user_mgrs"]
    assert [i["id"] for i in call("GET /admin/instances", ADM)[1]] == ["i-aaa", "i-bbb"]


def test_admin_makes_operator_and_grants_in_one_save(aws):
    status, body = call("PUT /admin/users/{userId}", ADM, path={"userId": NONE},
                        body={"roles": [{"role": "operators", "member": True}], "grants": [{"instanceId": "i-aaa", "grant": True}]})
    assert (status, body) == (200, {"applied": 2})
    assert aws.users.items[(NONE,)]["roles"] == ["operators"]
    assert call("GET /admin/users/{userId}/grants", ADM, path={"userId": NONE})[1] == ["i-aaa"]
    kinds = [(c["action"], c.get("role") or c.get("instanceId")) for c in call("GET /admin/changes", ADM)[1]]
    assert ("Added", "Operator role") in kinds and ("Granted", "i-aaa") in kinds
    assert aws.entitlements.items[(NONE, "i-aaa")]["status"] == "applied"
    assert call("GET /instances", NONE)[1][0]["id"] == "i-aaa"  # takes effect on the very next call


def test_cannot_grant_a_non_operator(aws):
    status, _ = call("PUT /admin/users/{userId}", ADM, path={"userId": NONE}, body={"grants": [{"instanceId": "i-aaa", "grant": True}]})
    assert status == 409 and (NONE, "i-aaa") not in aws.entitlements.items


def test_removing_operator_revokes_grants(aws):
    grant(aws, OP, "i-aaa")
    status, _ = call("PUT /admin/users/{userId}", ADM, path={"userId": OP}, body={"roles": [{"role": "operators", "member": False}]})
    assert status == 200 and aws.entitlements.items[(OP, "i-aaa")]["status"] == "revoked"
    assert call("GET /instances", OP) == (200, [])


def test_admin_cannot_remove_own_administrator_role(aws):
    status, _ = call("PUT /admin/users/{userId}", ADM, path={"userId": ADM}, body={"roles": [{"role": "admin", "member": False}]})
    assert status == 409 and "admin" in aws.users.items[(ADM,)]["roles"]
    assert call("PUT /admin/users/{userId}", ADM, path={"userId": BOTH}, body={"roles": [{"role": "user_mgrs", "member": False}]})[0] == 200
    assert "user_mgrs" not in aws.users.items[(BOTH,)]["roles"]


def test_user_manager_changes_grants_but_not_roles(aws):
    status, body = call("PUT /admin/users/{userId}", BOTH, path={"userId": OP}, body={"grants": [{"instanceId": "i-aaa", "grant": True}]})
    assert (status, body) == (200, {"applied": 1}) and aws.entitlements.items[(OP, "i-aaa")]["status"] == "applied"
    status, _ = call("PUT /admin/users/{userId}", BOTH, path={"userId": NONE}, body={"roles": [{"role": "operators", "member": True}]})
    assert status == 403 and aws.users.items[(NONE,)]["roles"] == []
    status, _ = call("PUT /admin/users/{userId}", BOTH, path={"userId": BOTH}, body={"roles": [{"role": "user_mgrs", "member": False}]})
    assert status == 403 and "user_mgrs" in aws.users.items[(BOTH,)]["roles"]
    assert call("GET /admin/users", BOTH)[0] == 200


def test_operator_only_cannot_use_admin_routes_or_launch_without_grant(aws):
    assert call("PUT /admin/users/{userId}", OP, path={"userId": NONE}, body={"grants": []})[0] == 403
    assert call("POST /instances/{instanceId}/start", OP, path={"instanceId": "i-aaa"})[0] in (403, 404)


def test_more_than_one_person_can_be_an_administrator(aws):
    assert call("PUT /admin/users/{userId}", ADM, path={"userId": NONE}, body={"roles": [{"role": "admin", "member": True}]})[0] == 200
    assert aws.users.items[(NONE,)]["roles"] == ["admin"]
    # the new administrator manages roles and grants, and can also remove another administrator
    assert call("POST /session", NONE)[1]["roles"] == ["admin"]
    assert call("GET /admin/users", NONE)[0] == 200
    assert call("PUT /admin/users/{userId}", NONE, path={"userId": OP}, body={"grants": [{"instanceId": "i-aaa", "grant": True}]})[0] == 200
    assert call("PUT /admin/users/{userId}", NONE, path={"userId": ADM}, body={"roles": [{"role": "admin", "member": False}]})[0] == 200
    assert call("GET /admin/users", ADM)[0] == 403  # ADM has no role left
    # the settings administrator can always get back in
    assert call("PUT /admin/users/{userId}", BOOT, path={"userId": ADM}, body={"roles": [{"role": "admin", "member": True}]})[0] == 200


def test_only_administrators_grant_the_administrator_role(aws):
    status, _ = call("PUT /admin/users/{userId}", BOTH, path={"userId": NONE}, body={"roles": [{"role": "admin", "member": True}]})
    assert status == 403 and aws.users.items[(NONE,)]["roles"] == []


def test_validation_happens_before_any_write(aws):
    status, _ = call("PUT /admin/users/{userId}", ADM, path={"userId": NONE},
                     body={"roles": [{"role": "operators", "member": True}], "grants": [{"instanceId": "i-nope", "grant": True}]})
    assert status == 404 and aws.users.items[(NONE,)]["roles"] == []
    assert call("PUT /admin/users/{userId}", ADM, path={"userId": NONE}, body={"roles": [{"role": "root", "member": True}]})[0] == 400
    assert call("PUT /admin/users/{userId}", ADM, path={"userId": "nobody@x.test"}, body={})[0] == 404


def test_revoking_a_never_granted_instance_adds_nothing(aws):
    call("PUT /admin/users/{userId}", ADM, path={"userId": OP}, body={"grants": [{"instanceId": "i-bbb", "grant": False}]})
    assert (OP, "i-bbb") not in aws.entitlements.items


def test_unexpected_errors_do_not_leak(aws, monkeypatch):
    monkeypatch.setattr(handler, "list_logins", lambda caller: 1 / 0)
    status, body = call("GET /logins", OP)
    assert status == 500 and "division" not in json.dumps(body)


def _raw(entry, route, user, path=None):
    event = {"routeKey": route, "pathParameters": path or {}, "requestContext": {"authorizer": {"jwt": {"claims": {"email": user, "sub": "s"}}}}}
    out = entry(event, None)
    return out["statusCode"]


def test_customer_lambda_has_no_admin_routes(aws):
    for route in ("GET /admin/users", "GET /admin/changes", "PUT /admin/users/{userId}"):
        assert _raw(handler.customer_handler, route, ADM, {"userId": OP}) == 404


def test_admin_lambda_cannot_start_or_sign_in(aws):
    for route in ("POST /instances/{instanceId}/start", "POST /session", "GET /instances"):
        assert _raw(handler.admin_handler, route, BOTH, {"instanceId": "i-aaa"}) == 404


class FakeLabSSM:
    """Stands in for SSM: the parameters Terraform publishes with the lab, and the auto-stop rule."""

    def __init__(self, params, fail=False):
        self.params, self.fail, self.reads = params, fail, 0

    def get_parameter(self, Name):
        return {"Parameter": {"Value": json.dumps({"enabled": True, "idle_minutes": 60, "max_uptime_minutes": 90})}}

    def get_parameters_by_path(self, Path, Recursive, NextToken=None):
        self.reads += 1
        if self.fail:
            raise client_error("AccessDenied")
        return {"Parameters": [{"Name": Path + "/" + k, "Value": v} for k, v in self.params.items()]}


def use_lab(monkeypatch, params, fail=False):
    ssm = FakeLabSSM(params, fail)
    monkeypatch.setattr(handler, "LAB_PARAMETER_PREFIX", "/lab/control-panel")
    monkeypatch.setattr(handler, "ssm", ssm)
    handler._lab_cache.update(at=0.0, value=None)
    return ssm


def test_no_lab_parameters_means_no_instances(aws, monkeypatch):
    use_lab(monkeypatch, {})
    grant(aws, OP, "i-aaa")
    assert call("GET /instances", OP) == (200, [])
    assert call("POST /instances/{instanceId}/start", OP, path={"instanceId": "i-aaa"})[0] == 404
    assert call("GET /admin/instances", ADM) == (200, [])
    assert aws.ec2.started == []


def test_lab_parameters_decide_the_instance_and_address(aws, monkeypatch):
    use_lab(monkeypatch, {"instance-ids": "i-aaa", "service-url": "https://lab.test", "target-group-arn": "arn:tg2"})
    grant(aws, OP, "i-aaa")
    grant(aws, OP, "i-bbb")
    status, body = call("GET /instances", OP)
    assert [i["id"] for i in body] == ["i-aaa"] and body[0]["url"] == "https://lab.test"
    assert call("POST /instances/{instanceId}/start", OP, path={"instanceId": "i-aaa"})[0] == 200
    assert call("POST /instances/{instanceId}/start", OP, path={"instanceId": "i-bbb"})[0] == 404


def test_lab_parameters_are_cached_then_follow_the_lab(aws, monkeypatch):
    ssm = use_lab(monkeypatch, {"instance-ids": "i-aaa"})
    grant(aws, OP, "i-aaa")
    call("GET /instances", OP)
    call("GET /instances", OP)
    assert ssm.reads == 1
    ssm.params.clear()
    handler._lab_cache.update(at=0.0, value=None)
    assert call("GET /instances", OP) == (200, [])


def test_unreadable_lab_parameters_fail_closed(aws, monkeypatch):
    use_lab(monkeypatch, {"instance-ids": "i-aaa"}, fail=True)
    grant(aws, OP, "i-aaa")
    assert call("GET /instances", OP)[0] == 503


def test_only_admin_sees_everyones_logins_and_logs(aws):
    for who in (OP, NONE, BOTH):
        call("POST /session", who)
    call("POST /session", ADM)
    for route in ("GET /admin/logins", "GET /admin/logs"):
        assert call(route, OP)[0] == 403
        assert call(route, BOTH)[0] == 403  # a user manager is not an administrator
    status, rows = call("GET /admin/logins", ADM)
    assert status == 200
    assert {r["userId"] for r in rows} >= {OP, NONE, BOTH, ADM}
    assert all(r["userName"] for r in rows)
    status, logs = call("GET /admin/logs", ADM)
    assert status == 200
    assert {OP, ADM} <= {r["userId"] for r in logs if r["source"] == "Control panel"}


# ---- auto-stop timer reset ---------------------------------------------------------------

RESET = "POST /instances/{instanceId}/reset-timer"
RESET_NAME = "/lab/auto-stop/reset-at"


class FakeResetSSM:
    """The auto-stop rule plus the reset parameter, which can be read, written or made to fail."""

    def __init__(self, max_minutes=90, reset=None, read_error=None, write_error=None):
        self.rule = {"enabled": True, "idle_minutes": 60, "max_uptime_minutes": max_minutes}
        self.value = reset  # epoch seconds as a string, or None when the parameter does not exist
        self.read_error, self.write_error, self.writes = read_error, write_error, []

    def get_parameter(self, Name):
        if Name == RESET_NAME:
            if self.read_error:
                raise client_error(self.read_error)
            if self.value is None:
                raise client_error("ParameterNotFound")
            return {"Parameter": {"Value": self.value}}
        return {"Parameter": {"Value": json.dumps(self.rule)}}

    def put_parameter(self, **kwargs):
        if self.write_error:
            raise client_error(self.write_error)
        self.writes.append(kwargs)
        self.value = kwargs["Value"]


def running(aws, minutes_up=40):
    aws.ec2.state["i-aaa"] = "running"
    aws.ec2.launched["i-aaa"] = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes_up)


def use_reset_ssm(monkeypatch, **kwargs):
    fake = FakeResetSSM(**kwargs)
    monkeypatch.setattr(handler, "ssm", fake)
    monkeypatch.setattr(handler, "RESET_PARAMETER", RESET_NAME)
    monkeypatch.setattr(handler, "INSTANCE_IDS", ["i-aaa"])  # a reset is only allowed for a single-instance lab
    return fake


def ago_s(minutes):
    return str(int(datetime.datetime.now(datetime.timezone.utc).timestamp()) - minutes * 60)


def test_reset_gives_another_full_period_and_is_logged(aws, monkeypatch):
    grant(aws, OP, "i-aaa")
    running(aws)
    ssm = use_reset_ssm(monkeypatch)
    before = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    status, body = call(RESET, OP, path={"instanceId": "i-aaa"})
    assert status == 200 and body["ok"] is True
    assert len(ssm.writes) == 1
    write = ssm.writes[0]
    assert write["Name"] == RESET_NAME and write["Overwrite"] is True and write["Type"] == "String"
    assert before <= int(write["Value"]) <= before + 5
    assert body["resetAt"] == int(write["Value"]) * 1000
    assert body["stopAt"] == body["resetAt"] + 90 * 60000
    assert body["maxUptimeMinutes"] == 90
    assert body["message"] == "Auto-stop timer reset. The lab has another 90 minutes."
    # One log line for the person who pressed it, visible in their own log.
    status, logs = call("GET /logs", OP)
    lines = [r for r in logs if "Reset the auto-stop timer" in r["event"]]
    assert len(lines) == 1
    assert lines[0]["severity"] == "info" and lines[0]["instanceId"] == "i-aaa" and "another 90 minutes" in lines[0]["event"]
    assert lines[0]["userId"] == OP
    # The administrator sees it, with the name of the person.
    admin_lines = [r for r in call("GET /admin/logs", ADM)[1] if "Reset the auto-stop timer" in r["event"]]
    assert [r["userName"] for r in admin_lines] == ["Olive Operator"]


def test_reset_changes_nothing_else(aws, monkeypatch):
    grant(aws, OP, "i-aaa")
    running(aws)
    use_reset_ssm(monkeypatch)
    call(RESET, OP, path={"instanceId": "i-aaa"})
    assert aws.ec2.started == [] and aws.ec2.state["i-aaa"] == "running"


def test_reset_needs_a_grant(aws, monkeypatch):
    running(aws)
    ssm = use_reset_ssm(monkeypatch)
    assert call(RESET, OP, path={"instanceId": "i-aaa"})[0] == 403
    grant(aws, OP, "i-aaa", status="revoked")
    assert call(RESET, OP, path={"instanceId": "i-aaa"})[0] == 403
    assert ssm.writes == []
    assert any("refused" in r["event"] for r in call("GET /logs", OP)[1])


def test_reset_needs_the_operator_role_even_with_a_grant(aws, monkeypatch):
    grant(aws, NONE, "i-aaa")
    running(aws)
    ssm = use_reset_ssm(monkeypatch)
    assert call(RESET, NONE, path={"instanceId": "i-aaa"})[0] == 403
    assert ssm.writes == []


def test_administrator_may_reset_without_a_grant(aws, monkeypatch):
    running(aws)
    ssm = use_reset_ssm(monkeypatch)
    assert call(RESET, ADM, path={"instanceId": "i-aaa"})[0] == 200
    assert len(ssm.writes) == 1


def test_reset_is_refused_when_the_lab_has_several_instances(aws, monkeypatch):
    # One reset value is shared by the whole lab, so a reset for A would extend B too.
    grant(aws, OP, "i-aaa")
    running(aws)
    ssm = use_reset_ssm(monkeypatch)
    monkeypatch.setattr(handler, "INSTANCE_IDS", ["i-aaa", "i-bbb"])
    status, body = call(RESET, OP, path={"instanceId": "i-aaa"})
    assert status == 409 and "single instance" in body["message"]
    assert ssm.writes == []


def test_reset_rejects_unmanaged_and_stopped_instances(aws, monkeypatch):
    grant(aws, OP, "i-aaa")
    ssm = use_reset_ssm(monkeypatch)
    assert call(RESET, OP, path={"instanceId": "i-zzz"})[0] == 404
    status, body = call(RESET, OP, path={"instanceId": "i-aaa"})  # stopped
    assert status == 409 and "not running" in body["message"]
    assert ssm.writes == []


def test_reset_with_no_hard_limit_has_nothing_to_reset(aws, monkeypatch):
    grant(aws, OP, "i-aaa")
    running(aws)
    ssm = use_reset_ssm(monkeypatch, max_minutes=0)
    status, body = call(RESET, OP, path={"instanceId": "i-aaa"})
    assert status == 409 and "no hard time limit" in body["message"].lower()
    assert ssm.writes == []


def test_failed_write_is_reported_and_logged_not_claimed(aws, monkeypatch):
    grant(aws, OP, "i-aaa")
    running(aws)
    use_reset_ssm(monkeypatch, write_error="AccessDeniedException")
    status, body = call(RESET, OP, path={"instanceId": "i-aaa"})
    assert status == 502 and "Could not reset the timer" in body["message"] and "AccessDenied" not in body["message"]
    lines = call("GET /logs", OP)[1]
    assert any(r["severity"] == "error" and "reset failed" in r["event"] for r in lines)
    assert not any("Reset the auto-stop timer" in r["event"] for r in lines)


def test_reset_without_a_configured_parameter_is_a_clear_503(aws, monkeypatch):
    grant(aws, OP, "i-aaa")
    running(aws)
    use_reset_ssm(monkeypatch)
    monkeypatch.setattr(handler, "RESET_PARAMETER", "")
    assert call(RESET, OP, path={"instanceId": "i-aaa"})[0] == 503


def test_the_admin_lambda_has_no_reset_route(aws):
    assert _raw(handler.admin_handler, RESET, BOTH, {"instanceId": "i-aaa"}) == 404


def test_instance_view_carries_the_reset_for_this_run_only(aws, monkeypatch):
    grant(aws, OP, "i-aaa")
    running(aws, minutes_up=120)
    use_reset_ssm(monkeypatch)
    assert call("GET /instances", OP)[1][0]["autoStop"]["resetAt"] is None  # never reset
    ssm = use_reset_ssm(monkeypatch, reset=ago_s(10))
    view = call("GET /instances", OP)[1][0]["autoStop"]
    assert abs(view["resetAt"] - (int(ago_s(10)) * 1000)) < 5000
    assert view["launchedAt"] < view["resetAt"]
    ssm.value = ago_s(500)  # left over from an earlier run: older than this launch
    assert call("GET /instances", OP)[1][0]["autoStop"]["resetAt"] is None


def test_a_future_reset_time_is_shown_as_now(aws, monkeypatch):
    grant(aws, OP, "i-aaa")
    running(aws)
    use_reset_ssm(monkeypatch, reset=str(int(datetime.datetime.now(datetime.timezone.utc).timestamp()) + 86400))
    reset_at = call("GET /instances", OP)[1][0]["autoStop"]["resetAt"]
    assert reset_at <= handler.now_ms()


def test_unreadable_or_garbage_reset_shows_the_launch_countdown(aws, monkeypatch):
    grant(aws, OP, "i-aaa")
    running(aws)
    use_reset_ssm(monkeypatch, read_error="AccessDeniedException")
    assert call("GET /instances", OP)[1][0]["autoStop"]["resetAt"] is None
    use_reset_ssm(monkeypatch, reset="soon")
    assert call("GET /instances", OP)[1][0]["autoStop"]["resetAt"] is None


def test_no_hard_limit_means_the_reset_is_not_read(aws, monkeypatch):
    grant(aws, OP, "i-aaa")
    running(aws)
    ssm = use_reset_ssm(monkeypatch, max_minutes=0, reset=ago_s(1))
    ssm.get_parameter = lambda Name: (_ for _ in ()).throw(AssertionError("read")) if Name == RESET_NAME else {"Parameter": {"Value": json.dumps(ssm.rule)}}
    assert call("GET /instances", OP)[1][0]["autoStop"]["resetAt"] is None


# ---- Open WebUI admin actions ----------------------------------------------------------

ACTIONS = "POST /admin/webui/actions"
ACTION = "GET /admin/webui/actions/{commandId}"
CMD = "0123abcd-4567-89ab-cdef-0123456789ab"


class FakeSSMCommands:
    def __init__(self, rule):
        self.rule = rule
        self.sent = []
        self.send_error = None
        self.invocation = {"Status": "InProgress", "DocumentName": "panel-webui-admin", "StandardOutputContent": ""}
        self.invocation_error = None

    def get_parameter(self, Name):
        return {"Parameter": {"Value": json.dumps(self.rule)}}

    def send_command(self, **kwargs):
        if self.send_error:
            raise client_error(self.send_error)
        self.sent.append(kwargs)
        return {"Command": {"CommandId": CMD}}

    def get_command_invocation(self, CommandId, InstanceId):
        if self.invocation_error:
            raise client_error(self.invocation_error)
        return dict(self.invocation)


@pytest.fixture
def webui(aws, monkeypatch):
    fake = FakeSSMCommands({"enabled": True, "idle_minutes": 60, "max_uptime_minutes": 90})
    monkeypatch.setattr(handler, "ssm", fake)
    monkeypatch.setattr(handler, "WEBUI_DOCUMENT", "panel-webui-admin")
    monkeypatch.setattr(handler, "OPEN_WEBUI_IMAGE", "ghcr.io/open-webui/open-webui:v0.11.4")
    aws.ec2.state["i-aaa"] = "running"
    return fake


def test_only_administrators_can_run_open_webui_actions(aws, webui):
    for user in (OP, BOTH, NONE):
        assert call(ACTIONS, user, body={"action": "status", "instanceId": "i-aaa"})[0] == 403
        assert call(ACTION, user, path={"commandId": CMD})[0] == 403
    assert webui.sent == []


def test_the_customer_function_has_no_open_webui_routes(aws, webui):
    assert _raw(handler.customer_handler, ACTIONS, ADM) == 404


def test_an_action_runs_the_panel_document_with_the_pinned_version(aws, webui):
    status, body = call(ACTIONS, ADM, body={"action": "status", "instanceId": "i-aaa"})
    assert status == 200 and body["commandId"] == CMD and body["status"] == "Pending"
    sent = webui.sent[0]
    assert sent["DocumentName"] == "panel-webui-admin" and sent["InstanceIds"] == ["i-aaa"]
    assert sent["Parameters"] == {"action": ["status"], "expectedVersion": ["0.11.4"]}
    record = aws.events.items[("webui-actions", CMD)]
    assert record["requestedBy"] == ADM and record["action"] == "status"
    assert any("Requested Open WebUI action 'status'" in r.get("event", "") for r in aws.events.items.values())


def test_unknown_actions_unmanaged_instances_and_stopped_instances_are_refused(aws, webui):
    assert call(ACTIONS, ADM, body={"action": "rm -rf /", "instanceId": "i-aaa"})[0] == 400
    assert call(ACTIONS, ADM, body={"action": "status", "instanceId": "i-zzz"})[0] == 404
    aws.ec2.state["i-aaa"] = "stopped"
    assert call(ACTIONS, ADM, body={"action": "status", "instanceId": "i-aaa"})[0] == 409
    assert webui.sent == []


def test_an_unpinned_image_sends_no_expected_version(aws, webui, monkeypatch):
    monkeypatch.setattr(handler, "OPEN_WEBUI_IMAGE", "ghcr.io/open-webui/open-webui:main")
    call(ACTIONS, ADM, body={"action": "status", "instanceId": "i-aaa"})
    assert webui.sent[0]["Parameters"]["expectedVersion"] == [""]


def test_without_the_document_setting_actions_are_unavailable(aws, webui, monkeypatch):
    monkeypatch.setattr(handler, "WEBUI_DOCUMENT", "")
    assert call(ACTIONS, ADM, body={"action": "status", "instanceId": "i-aaa"})[0] == 503


def test_an_instance_not_yet_in_ssm_is_a_clear_conflict(aws, webui):
    webui.send_error = "InvalidInstanceId"
    assert call(ACTIONS, ADM, body={"action": "status", "instanceId": "i-aaa"})[0] == 409


def test_the_result_is_read_back_and_recorded_once(aws, webui):
    call(ACTIONS, ADM, body={"action": "status", "instanceId": "i-aaa"})
    assert call(ACTION, ADM, path={"commandId": CMD})[1]["status"] == "InProgress"
    webui.invocation = {"Status": "Success", "DocumentName": "panel-webui-admin",
                        "StandardOutputContent": "noise\n" + json.dumps({"ok": True, "action": "status", "healthy": True, "version": "0.11.4"})}
    status, body = call(ACTION, ADM, path={"commandId": CMD})
    assert status == 200 and body["status"] == "Success" and body["result"]["version"] == "0.11.4"
    call(ACTION, ADM, path={"commandId": CMD})
    done = [r for r in aws.events.items.values() if "Open WebUI action 'status' on i-aaa: done" in r.get("event", "")]
    assert len(done) == 1
    assert ("webui-actions", CMD + "#result") in aws.events.items


def test_a_command_from_another_document_is_never_shown(aws, webui):
    call(ACTIONS, ADM, body={"action": "status", "instanceId": "i-aaa"})
    webui.invocation = {"Status": "Success", "DocumentName": "AWS-RunShellScript", "StandardOutputContent": "secret"}
    assert call(ACTION, ADM, path={"commandId": CMD})[0] == 404


def test_unknown_or_malformed_action_ids_are_refused(aws, webui):
    assert call(ACTION, ADM, path={"commandId": "not-an-id"})[0] == 400
    assert call(ACTION, ADM, path={"commandId": "ffffffff-ffff-ffff-ffff-ffffffffffff"})[0] == 404


def test_an_invocation_ssm_has_not_registered_yet_reads_as_pending(aws, webui):
    call(ACTIONS, ADM, body={"action": "status", "instanceId": "i-aaa"})
    webui.invocation_error = "InvocationDoesNotExist"
    assert call(ACTION, ADM, path={"commandId": CMD})[1]["status"] == "Pending"


def test_a_failed_action_without_json_output_reports_no_result(aws, webui):
    call(ACTIONS, ADM, body={"action": "status", "instanceId": "i-aaa"})
    webui.invocation = {"Status": "Failed", "DocumentName": "panel-webui-admin", "StandardOutputContent": "bash: error"}
    body = call(ACTION, ADM, path={"commandId": CMD})[1]
    assert body["status"] == "Failed" and body["result"] is None


def test_a_finished_action_stays_readable_after_ssm_forgets_it(aws, webui):
    call(ACTIONS, ADM, body={"action": "status", "instanceId": "i-aaa"})
    webui.invocation = {"Status": "Success", "DocumentName": "panel-webui-admin",
                        "StandardOutputContent": json.dumps({"ok": True, "action": "status", "version": "0.11.4"})}
    call(ACTION, ADM, path={"commandId": CMD})
    webui.invocation_error = "InvocationDoesNotExist"  # SSM history has expired
    body = call(ACTION, ADM, path={"commandId": CMD})[1]
    assert body["status"] == "Success" and body["result"]["version"] == "0.11.4"


def test_an_action_ssm_forgot_before_anyone_read_it_is_expired_not_pending(aws, webui):
    call(ACTIONS, ADM, body={"action": "status", "instanceId": "i-aaa"})
    aws.events.items[("webui-actions", CMD)]["t"] = handler.now_ms() - 3600 * 1000
    webui.invocation_error = "InvocationDoesNotExist"
    body = call(ACTION, ADM, path={"commandId": CMD})[1]
    assert body["status"] == "Expired" and body["result"] is None


def test_the_outcome_is_logged_for_the_administrator_who_asked(aws, webui):
    aws.users.items[(BOOT,)] = {"email": BOOT, "name": "Boot", "roles": ["admin"], "source": "panel"}
    call(ACTIONS, ADM, body={"action": "status", "instanceId": "i-aaa"})
    webui.invocation = {"Status": "Success", "DocumentName": "panel-webui-admin",
                        "StandardOutputContent": json.dumps({"ok": True, "action": "status"})}
    call(ACTION, BOOT, path={"commandId": CMD})  # another administrator reads it first
    done = [r for r in aws.events.items.values() if ": done" in r.get("event", "")]
    assert len(done) == 1 and done[0]["pk"] == f"user#{ADM}"
