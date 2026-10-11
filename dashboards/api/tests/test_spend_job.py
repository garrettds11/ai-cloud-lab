"""The spend caps job (spend_job.py) with AWS replaced by fakes. The instance's usage answer and
the Open WebUI role change are scripted; nothing here proves the real Open WebUI or SES, which the
first live deploy checks. Run from dashboards/api: python -m pytest -q tests/test_spend_job.py"""

import base64
import datetime
import json
import time as real_time
import types
from decimal import Decimal

import pytest

from test_handler import ADM, BOTH, NONE, OP, FakeTable, aws, client_error  # noqa: F401  (aws is an autouse fixture)

import handler  # noqa: E402
import spend_job as job  # noqa: E402
import spend_rules  # noqa: E402

H = 3600
NOW = 1_792_000_000  # 2026-10-14, 18:13 UTC
HOUR0 = NOW // H * H
PERIOD = spend_rules.period_key(NOW)
UID = {OP: "wu-olive", ADM: "wu-ada", BOTH: "wu-bo", NONE: "wu-nina"}


class Clock:
    def time(self):
        return NOW

    def sleep(self, _):
        pass


class FakeSSM:
    """send_command/get_command_invocation with a scripted usage record and role changes."""

    def __init__(self):
        self.usage = None
        self.sent = []
        self.role_ok = True
        self.apply = None  # the scripted answer to apply-desired
        self.last = {}

    def send_command(self, **kw):
        cid = f"{len(self.sent):08d}-0000-0000-0000-000000000000"
        self.sent.append({k: (v[0] if isinstance(v, list) and len(v) == 1 else v) for k, v in kw["Parameters"].items()} | {"id": cid})
        self.last[cid] = self.sent[-1]
        return {"Command": {"CommandId": cid}}

    def get_command_invocation(self, CommandId, InstanceId):
        sent = self.last[CommandId]
        if sent["action"] == "usage":
            out = self.usage if self.usage is not None else {"ok": False}
        elif sent["action"] == "apply-desired":
            out = self.apply if self.apply is not None else {"ok": False, "action": "apply-desired", "error": "Open WebUI is not set up yet."}
        else:
            out = {"ok": self.role_ok, "userId": sent["userId"], "role": sent["role"], "changed": True}
        return {"Status": "Success", "DocumentName": handler.WEBUI_DOCUMENT, "StandardOutputContent": json.dumps(out)}

    def roles_set(self):
        return [(s["userId"], s["role"]) for s in self.sent if s["action"] == "set-role"]


class FakeSNS:
    """The spend topic: subscriptions by email (an ARN once confirmed, "PendingConfirmation" before) and what was published."""

    def __init__(self):
        self.subs = {}
        self.mail = []
        self.subscribed = []

    def subscribe(self, TopicArn, Protocol, Endpoint, Attributes, ReturnSubscriptionArn):
        assert Protocol == "email" and ReturnSubscriptionArn
        assert json.loads(Attributes["FilterPolicy"]) == {"recipient": [Endpoint]} and Attributes["FilterPolicyScope"] == "MessageAttributes"
        self.subs[Endpoint] = "PendingConfirmation"
        self.subscribed.append(Endpoint)
        return {"SubscriptionArn": "pending confirmation"}

    def publish(self, TopicArn, Subject, Message, MessageAttributes):
        assert len(Subject) < 100 and "\n" not in Subject
        self.mail.append((MessageAttributes["recipient"]["StringValue"], Subject, Message))

    def get_paginator(self, name):
        assert name == "list_subscriptions_by_topic"
        outer = self
        return types.SimpleNamespace(paginate=lambda TopicArn: [{"Subscriptions": [
            {"Protocol": "email", "Endpoint": e, "SubscriptionArn": a} for e, a in outer.subs.items()]}])

    def confirm(self, address):  # the person clicked the link
        self.subs[address] = "arn:aws:sns:us-east-1:111122223333:spend:" + address

    def to(self, address):
        return [m for m in self.mail if m[0] == address]


class FakeCognito:
    def __init__(self):
        self.calls = []
        self.missing = set()  # not in the pool
        self.unverified = set()  # email_verified is false

    def admin_disable_user(self, UserPoolId, Username):
        self.calls.append(("disable", UserPoolId, Username))

    def admin_enable_user(self, UserPoolId, Username):
        self.calls.append(("enable", UserPoolId, Username))

    def admin_get_user(self, UserPoolId, Username):
        if Username in self.missing:
            raise client_error("UserNotFoundException")
        verified = "false" if Username in self.unverified else "true"
        return {"UserAttributes": [{"Name": "email", "Value": Username}, {"Name": "email_verified", "Value": verified}]}


class World:
    pass


@pytest.fixture
def world(aws, monkeypatch):
    w = World()
    w.aws, w.ssm, w.sns, w.cognito = aws, FakeSSM(), FakeSNS(), FakeCognito()
    monkeypatch.setattr(handler, "ssm", w.ssm)
    monkeypatch.setattr(handler, "WEBUI_DOCUMENT", "panel-webui-admin")
    monkeypatch.setattr(job, "time", Clock())
    monkeypatch.setattr(job, "sns", w.sns)
    monkeypatch.setattr(job, "cognito", w.cognito)
    monkeypatch.setattr(job, "SPEND_TOPIC_ARN", "arn:aws:sns:us-east-1:111122223333:spend")
    monkeypatch.setattr(handler, "_lab", lambda: {"instance_ids": ["i-aaa"], "target_group_arn": "arn:tg", "service_url": "https://example.test", "open_webui_image": "", "user_pool_id": "pool-1"})
    stopped = []
    monkeypatch.setattr(aws.ec2, "stop_instances", lambda InstanceIds: stopped.append(InstanceIds), raising=False)
    w.stopped = stopped
    aws.ec2.state["i-aaa"] = "running"
    for email in (OP, ADM, BOTH, NONE):
        aws.users.items[(email,)]["emailStatus"] = "confirmed"
        aws.users.items[(email,)]["emailChecked"] = email
        w.sns.confirm(email)
    w.set_caps(default=5)
    return w


def _set_caps(self, labcap=None, default=None):
    self.aws.events.put_item({"pk": "spend", "sk": "settings", "labCapUsd": None if labcap is None else Decimal(str(labcap)),
                              "defaultUserCapUsd": None if default is None else Decimal(str(default))})


World.set_caps = _set_caps


def usage_record(users, price=1.0, running=False, hours=1):
    """One session of `hours` hours starting at HOUR0, in which `users` {email: tokens} chatted in the first hour."""
    stop = None if running else HOUR0 + hours * H
    return {"ok": True, "action": "usage", "hourlyCostUsd": price, "instanceType": "g6.xlarge", "truncated": False,
            "currentHour": HOUR0 + hours * H, "nextHour": HOUR0 + hours * H,
            "sessions": [{"start": HOUR0, "stop": stop, "endedBy": "shutdown" if stop else "running"}],
            "hours": [{"hour": HOUR0, "users": [{"id": UID[e], "email": e, "name": e, "in": t // 2, "out": t - t // 2, "messages": 1} for e, t in users.items()]}],
            "provisional": None}


def run(world, event=None):
    return job.lambda_handler(event or {}, None)


def row(world, email):
    return world.aws.users.items[(email,)]


def test_the_ledger_takes_the_instances_usage_and_splits_the_hour_by_tokens(world):
    world.ssm.usage = usage_record({OP: 3000, BOTH: 1000}, price=4.0)
    out = run(world)
    assert out["usageRead"] is True and out["labSpend"] == 4.0
    ledger = job.load_ledger(PERIOD)
    entry = ledger["sessions"][str(HOUR0)]
    assert entry["final"] is True and {k: float(v) for k, v in entry["charges"].items()} == {OP: 3.0, BOTH: 1.0}
    assert ledger["webuiIds"][OP] == "wu-olive"
    usage_call = world.ssm.sent[0]
    assert usage_call["action"] == "usage" and usage_call["sinceHour"] == str(spend_rules.period_bounds(PERIOD)[0])


def test_a_running_session_stays_provisional_and_is_read_again_from_its_start(world):
    world.ssm.usage = usage_record({OP: 100}, price=2.0, running=True)
    run(world)
    assert job.load_ledger(PERIOD)["sessions"][str(HOUR0)]["final"] is False
    run(world)
    assert world.ssm.sent[-1]["sinceHour"] == str(HOUR0)  # asks from where it left off


def test_with_the_instance_stopped_the_month_is_judged_from_the_ledger_alone(world):
    world.aws.ec2.state["i-aaa"] = "stopped"
    world.aws.events.put_item({"pk": "spend", "sk": f"ledger#{PERIOD}", "sessions": {"1": {"cost": Decimal("2"), "charges": {OP: Decimal("4.1")}, "final": True}}})
    out = run(world)
    assert out["running"] is False and world.ssm.sent == []
    assert [m[1] for m in world.sns.to(OP)] == ["AI Cloud Lab: 80% of your monthly spend cap"]


def test_each_threshold_is_emailed_once_and_a_jump_sends_only_the_highest(world):
    world.ssm.usage = usage_record({OP: 100}, price=2.6)  # 2.6 of 5 = 52%
    run(world)
    assert [m[1] for m in world.sns.to(OP)] == ["AI Cloud Lab: 50% of your monthly spend cap"]
    run(world)
    assert len(world.sns.to(OP)) == 1
    world.ssm.usage = usage_record({OP: 100}, price=4.2)  # 84%: only the 80% email, not 50 again
    run(world)
    assert [m[1] for m in world.sns.to(OP)][-1] == "AI Cloud Lab: 80% of your monthly spend cap" and len(world.sns.to(OP)) == 2
    assert row(world, OP)["capEmails"] == {PERIOD: [50, 80]}


def test_nobody_is_emailed_before_their_address_is_confirmed(world):
    row(world, OP)["emailStatus"] = "pending"
    world.sns.subs[OP] = "PendingConfirmation"
    world.ssm.usage = usage_record({OP: 100}, price=2.6)
    run(world)
    assert world.sns.to(OP) == [] and "capEmails" not in row(world, OP) and row(world, OP)["emailStatus"] == "pending"
    world.sns.confirm(OP)  # they clicked the link
    run(world)
    assert row(world, OP)["emailStatus"] == "confirmed" and len(world.sns.to(OP)) == 1


def test_a_new_or_changed_address_is_asked_to_confirm_once(world):
    world.aws.users.items[("new@x.test",)] = {"email": "new@x.test", "name": "New", "roles": []}
    row(world, OP)["emailChecked"] = "old@x.test"
    world.sns.confirm(OP)
    world.ssm.usage = usage_record({})
    run(world)
    assert world.sns.subscribed == ["new@x.test"] and row(world, "new@x.test")["emailStatus"] == "pending"
    assert row(world, OP)["emailChecked"] == OP and row(world, OP)["emailStatus"] == "confirmed"
    run(world)
    run(world, {"confirm": [OP]})
    assert world.sns.subscribed == ["new@x.test"]  # a confirmed person is never asked again, and a pending one only once


def test_only_people_whose_identity_has_a_verified_email_are_asked_and_emailed(world):
    world.aws.users.items[("unv@x.test",)] = {"email": "unv@x.test", "name": "Unverified", "roles": []}
    world.aws.users.items[("gone@x.test",)] = {"email": "gone@x.test", "name": "No identity", "roles": []}
    world.cognito.unverified.add("unv@x.test")
    world.cognito.missing.add("gone@x.test")
    world.ssm.usage = usage_record({})
    run(world)
    assert world.sns.subscribed == []
    assert row(world, "unv@x.test")["emailStatus"] == "unverified" and row(world, "gone@x.test")["emailStatus"] == "unverified"
    world.cognito.unverified.clear()  # the identity provider now vouches for the address
    run(world)
    assert world.sns.subscribed == ["unv@x.test"] and row(world, "unv@x.test")["emailStatus"] == "pending"


def test_an_unreadable_identity_is_retried_not_trusted(world):
    world.aws.users.items[("late@x.test",)] = {"email": "late@x.test", "name": "Late", "roles": []}
    world.cognito.admin_get_user = lambda UserPoolId, Username: (_ for _ in ()).throw(client_error("InternalErrorException"))
    run(world)
    assert world.sns.subscribed == [] and row(world, "late@x.test")["emailStatus"] == "failed"


def test_an_unclicked_link_expires_and_is_not_sent_again_until_an_administrator_asks(world):
    world.aws.users.items[("slow@x.test",)] = {"email": "slow@x.test", "name": "Slow", "roles": []}
    run(world)
    assert row(world, "slow@x.test")["emailStatus"] == "pending"
    world.sns.subs.pop("slow@x.test")  # SNS drops a subscription that is not confirmed within 48 hours
    run(world)
    assert row(world, "slow@x.test")["emailStatus"] == "expired" and world.sns.subscribed == ["slow@x.test"]
    run(world)
    assert world.sns.subscribed == ["slow@x.test"]
    run(world, {"confirm": ["slow@x.test"]})
    assert world.sns.subscribed == ["slow@x.test", "slow@x.test"] and row(world, "slow@x.test")["emailStatus"] == "pending"


def test_someone_who_unsubscribes_is_not_emailed_or_asked_again(world):
    world.sns.subs.pop(OP, None)
    world.ssm.usage = usage_record({OP: 100}, price=2.6)
    run(world)
    assert row(world, OP)["emailStatus"] == "unsubscribed" and world.sns.to(OP) == [] and world.sns.subscribed.count(OP) == 0
    run(world)
    assert world.sns.subscribed.count(OP) == 0


def test_each_message_names_one_recipient_and_an_email_goes_to_nobody_else(world):
    world.ssm.usage = usage_record({OP: 100}, price=2.6)
    run(world)
    assert [m[0] for m in world.sns.mail] == [OP]


def test_without_a_topic_there_are_no_emails_but_the_caps_still_block(world, monkeypatch):
    monkeypatch.setattr(job, "SPEND_TOPIC_ARN", "")
    world.ssm.usage = usage_record({OP: 100}, price=5.5)
    run(world)
    assert world.sns.mail == [] and world.sns.subscribed == [] and row(world, OP).get("spendBlock")


def test_reaching_the_cap_blocks_in_cognito_and_open_webui_and_says_so(world):
    world.ssm.usage = usage_record({OP: 100}, price=5.01)
    run(world)
    block = row(world, OP)["spendBlock"]
    assert block["period"] == PERIOD and block["cognitoDisabled"] is True and block["roleSet"] is True and block["webuiId"] == "wu-olive"
    assert ("disable", "pool-1", OP) in world.cognito.calls and world.ssm.roles_set() == [("wu-olive", "pending")]
    assert "reached your monthly spend cap" in world.sns.to(OP)[0][1]
    assert any("Blocked" in r.get("action", "") and r.get("userId") == OP for r in world.aws.events.items.values() if r.get("pk") == "changes")
    run(world)  # still blocked: nothing is repeated
    assert world.ssm.roles_set() == [("wu-olive", "pending")] and world.cognito.calls.count(("disable", "pool-1", OP)) == 1
    assert len(world.sns.to(OP)) == 1


def test_an_administrator_is_never_blocked_but_is_still_emailed(world):
    world.ssm.usage = usage_record({ADM: 100}, price=6.0)
    run(world)
    assert "spendBlock" not in row(world, ADM) and world.cognito.calls == [] and world.ssm.roles_set() == []
    assert len(world.sns.to(ADM)) >= 1


def test_the_admins_get_a_copy_when_someone_is_stopped(world):
    world.ssm.usage = usage_record({OP: 100}, price=5.5)
    run(world)
    copies = [m for m in world.sns.to(ADM) if OP in m[1] or OP in m[2]]
    assert len(copies) == 1


def test_a_block_made_while_the_instance_is_off_finishes_when_it_runs_again(world):
    world.aws.ec2.state["i-aaa"] = "stopped"
    world.aws.events.put_item({"pk": "spend", "sk": f"ledger#{PERIOD}", "webuiIds": {OP: "wu-olive"},
                               "sessions": {"1": {"cost": Decimal("6"), "charges": {OP: Decimal("6")}, "final": True}}})
    run(world)
    block = row(world, OP)["spendBlock"]
    assert block["cognitoDisabled"] is True and block["roleSet"] is False and world.ssm.sent == []
    world.aws.ec2.state["i-aaa"] = "running"
    world.ssm.usage = None  # the instance does not answer for usage; the role is still set
    run(world)
    assert row(world, OP)["spendBlock"]["roleSet"] is True and world.ssm.roles_set() == [("wu-olive", "pending")]


def test_raising_the_cap_lets_the_person_back_in_at_once(world):
    world.ssm.usage = usage_record({OP: 100}, price=5.01)
    run(world)
    assert row(world, OP)["spendBlock"]
    row(world, OP)["capUsd"] = Decimal("20")
    run(world)
    assert row(world, OP)["spendBlock"] is None
    assert ("enable", "pool-1", OP) in world.cognito.calls and world.ssm.roles_set()[-1] == ("wu-olive", "user")


def test_a_new_month_resets_blocks_and_emails(world):
    old = "2026-09"
    row(world, OP)["spendBlock"] = {"period": old, "at": 1, "cap": Decimal("5"), "spend": Decimal("5"), "webuiId": "wu-olive",
                                    "cognitoDisabled": True, "roleSet": True}
    row(world, OP)["capEmails"] = {old: [50, 80, 98, 100]}
    world.ssm.usage = usage_record({})
    run(world)
    assert row(world, OP)["spendBlock"] is None and ("enable", "pool-1", OP) in world.cognito.calls
    assert world.ssm.roles_set() == [("wu-olive", "user")]


def test_a_block_is_kept_if_cognito_cannot_be_reached(world, monkeypatch):
    monkeypatch.setattr(world.cognito, "admin_enable_user", lambda **kw: (_ for _ in ()).throw(client_error("InternalErrorException")))
    row(world, OP)["spendBlock"] = {"period": "2026-09", "at": 1, "cap": Decimal("5"), "spend": Decimal("5"), "webuiId": "wu-olive",
                                    "cognitoDisabled": True, "roleSet": False}
    world.ssm.usage = usage_record({})
    run(world)
    assert row(world, OP)["spendBlock"]["cognitoDisabled"] is True  # tried again next run


def test_the_lab_cap_emails_the_admins_and_stops_the_instance_at_100_percent(world):
    world.set_caps(labcap=10, default=None)
    world.ssm.usage = usage_record({OP: 100}, price=5.5)
    run(world)
    assert [m[1] for m in world.sns.to(ADM)] == ["AI Cloud Lab: the lab has used 50% of its monthly budget"] and world.stopped == []
    assert world.sns.to(OP) == []  # the lab cap emails go to the admins only
    world.ssm.usage = usage_record({OP: 100}, price=10.2)
    run(world)
    assert world.stopped == [["i-aaa"]]
    assert "stopped" in world.sns.to(ADM)[-1][1]
    assert job.load_ledger(PERIOD)["labStopped"]["period"] == PERIOD
    assert job.load_ledger(PERIOD)["labEmails"] == [50, 80, 98, 100]  # a jump records the lower ones as covered


def test_a_lab_without_a_price_changes_nothing(world):
    world.ssm.usage = usage_record({OP: 100}, price=None)
    out = run(world)
    assert out["labSpend"] == 0 and world.cognito.calls == [] and world.stopped == []


def test_an_unanswered_usage_request_leaves_the_ledger_alone(world):
    world.ssm.usage = None
    out = run(world)
    assert out["usageRead"] is False and out["labSpend"] == 0


# ---- the administrator's one-time override of the lab cap ---------------------------------


def over_the_lab_cap(world):
    world.set_caps(labcap=10, default=None)
    world.aws.events.put_item({"pk": "spend", "sk": f"ledger#{PERIOD}", "sessions": {"1": {"cost": Decimal("12"), "charges": {OP: Decimal("12")}, "final": True}}})
    world.ssm.usage = usage_record({})


def put_override(world, at_ms, used=False, launch=0, period=PERIOD):
    world.aws.events.put_item({"pk": "spend", "sk": "override", "period": period, "at": at_ms, "by": ADM, "used": used, "launch": launch})


def launched(world, at_ms):
    import datetime
    world.aws.ec2.launched["i-aaa"] = datetime.datetime.fromtimestamp(at_ms / 1000, datetime.timezone.utc)


def override(world):
    return world.aws.events.items.get(("spend", "override"))


def test_a_lab_started_after_the_override_is_not_stopped_at_the_cap(world):
    over_the_lab_cap(world)
    put_override(world, 1_000_000)
    launched(world, 2_000_000)
    run(world)
    assert world.stopped == [] and override(world)["used"] is True


def test_a_lab_started_before_the_override_is_still_stopped(world):
    over_the_lab_cap(world)
    put_override(world, 2_000_000)
    launched(world, 1_000_000)
    run(world)
    assert world.stopped == [["i-aaa"]]


def test_the_run_that_was_going_when_the_override_was_given_is_allowed(world):
    over_the_lab_cap(world)
    put_override(world, 2_000_000, used=True, launch=1_000_000)
    launched(world, 1_000_000)
    run(world)
    assert world.stopped == []


def test_the_override_ends_when_the_lab_it_covered_stops_but_not_before_it_is_used(world):
    over_the_lab_cap(world)
    world.aws.ec2.state["i-aaa"] = "stopped"
    put_override(world, 1_000_000, used=False)
    run(world)
    assert override(world) is not None  # still waiting for the next start
    put_override(world, 1_000_000, used=True)
    run(world)
    assert override(world) is None


def test_the_override_is_dropped_when_the_cap_is_raised_or_the_month_changes(world):
    over_the_lab_cap(world)
    launched(world, 2_000_000)
    put_override(world, 1_000_000, period="2026-01")
    run(world)
    assert override(world) is None and world.stopped == [["i-aaa"]]
    put_override(world, 1_000_000)
    world.set_caps(labcap=50, default=None)
    run(world)
    assert override(world) is None



# ---- putting the saved Open WebUI settings back after a start ----

@pytest.fixture
def saved(world, monkeypatch):
    table = FakeTable(("setting",))
    monkeypatch.setattr(handler, "desired", table)
    monkeypatch.setattr(handler, "TOOL_TOKEN_PREFIX", "aiwebdemo/tool-tokens/")
    table.put_item(Item={"setting": "default-model", "kind": "default-model", "payload": json.dumps({"model": "qwen3:14b"})})
    table.put_item(Item={"setting": "feature#memory", "kind": "feature", "payload": json.dumps({"feature": "memory", "value": False})})
    return table


def started_ago(world, seconds_ago):
    world.aws.ec2.launched["i-aaa"] = datetime.datetime.fromtimestamp(NOW - seconds_ago, datetime.timezone.utc)


def replays(world):
    return [s for s in world.ssm.sent if s["action"] == "apply-desired"]


def ok_apply(n=2, failed=0):
    return {"ok": failed == 0, "action": "apply-desired", "applied": n - failed, "failed": failed, "results": [{"ok": True}] * n}


def test_the_saved_settings_wait_for_open_webui_then_are_put_back_once_per_start(world, saved):
    world.ssm.apply = ok_apply()
    started_ago(world, 100)
    assert run(world)["settingsReapplied"] == "waiting" and replays(world) == []
    started_ago(world, 200)
    assert run(world)["settingsReapplied"] == "applied"
    sent = replays(world)[0]
    assert json.loads(base64.b64decode(sent["payload"])) == {"items": [{"kind": "default-model", "model": "qwen3:14b"}, {"kind": "feature", "feature": "memory", "value": False}]}
    assert sent["toolTokenPrefix"] == "aiwebdemo/tool-tokens/"
    assert saved.items[("_reapply",)]["launchMs"] == (NOW - 200) * 1000 and saved.items[("_reapply",)]["failed"] == 0
    assert run(world)["settingsReapplied"] is None and len(replays(world)) == 1  # the same start is never replayed twice
    assert any("Put back 2 saved Open WebUI settings" in e["action"] for e in world.aws.events.items.values() if e.get("action"))
    started_ago(world, 90)  # the instance was stopped and started again: a new launch time
    world.aws.events.items.clear()
    assert run(world)["settingsReapplied"] == "waiting"
    started_ago(world, 160)
    saved.items[("_reapply",)]["launchMs"] = (NOW - 5000) * 1000  # the earlier start
    assert run(world)["settingsReapplied"] == "applied" and len(replays(world)) == 2


def test_failures_are_counted_and_shown_to_the_admin(world, saved):
    world.ssm.apply = ok_apply(2, failed=1)
    started_ago(world, 300)
    assert run(world)["settingsReapplied"] == "applied with failures"
    assert saved.items[("_reapply",)]["failed"] == 1
    assert any("1 could not be applied" in e["action"] for e in world.aws.events.items.values() if e.get("action"))


def test_open_webui_not_ready_is_retried_a_few_times_then_given_up_on(world, saved):
    started_ago(world, 300)  # the scripted answer is "not set up yet", which has no results
    assert [run(world)["settingsReapplied"] for _ in range(3)] == ["retry", "retry", "gave up"]
    assert len(replays(world)) == 3 and saved.items[("_reapply",)]["failed"] == 2
    assert run(world)["settingsReapplied"] is None  # given up for this start
    assert any("Could not put back" in e["action"] for e in world.aws.events.items.values() if e.get("action"))


def test_nothing_saved_means_no_command_and_a_stopped_instance_means_nothing_to_do(world, saved):
    saved.items.clear()
    started_ago(world, 300)
    assert run(world)["settingsReapplied"] == "nothing saved" and replays(world) == []
    saved.items.clear()
    world.aws.ec2.state["i-aaa"] = "stopped"
    assert run(world)["settingsReapplied"] is None and replays(world) == []


def test_a_problem_with_the_replay_never_stops_the_caps_work(world, saved):
    started_ago(world, 300)
    saved.down = True
    result = run(world)
    assert result["settingsReapplied"] == "error" and result["running"] is True and "labSpend" in result


def test_the_replay_is_skipped_when_the_run_has_little_time_left(world, saved):
    started_ago(world, 300)
    world.ssm.apply = ok_apply()
    short = types.SimpleNamespace(get_remaining_time_in_millis=lambda: 60_000)
    assert job.lambda_handler({}, short)["settingsReapplied"] == "no time left" and replays(world) == []
    roomy = types.SimpleNamespace(get_remaining_time_in_millis=lambda: 170_000)
    assert job.lambda_handler({}, roomy)["settingsReapplied"] == "applied"


def test_without_the_desired_state_table_the_job_does_nothing_extra(world):
    started_ago(world, 300)
    assert run(world)["settingsReapplied"] is None
