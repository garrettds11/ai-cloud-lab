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
