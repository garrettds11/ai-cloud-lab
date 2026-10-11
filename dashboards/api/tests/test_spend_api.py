"""Spend caps through the Control API: reading, setting, the start refusal and the blocked badge.
The scheduled job is in test_spend_job.py. Run from dashboards/api: python -m pytest -q tests/test_spend_api.py"""

import json
import time
from decimal import Decimal

import pytest

from test_handler import ADM, BOTH, NONE, OP, aws, call, grant  # noqa: F401  (aws is an autouse fixture; this import also sets the test environment)

import handler  # noqa: E402
import spend_rules  # noqa: E402

PERIOD = spend_rules.period_key(time.time())


def seed(aws, labcap=None, default=None, sessions=None, lab_emails=()):
    aws.events.put_item({"pk": "spend", "sk": "settings", "labCapUsd": None if labcap is None else Decimal(str(labcap)),
                         "defaultUserCapUsd": None if default is None else Decimal(str(default))})
    aws.events.put_item({"pk": "spend", "sk": f"ledger#{PERIOD}", "sessions": sessions or {}, "labEmails": list(lab_emails)})


def session(cost, **charges):
    return {"cost": Decimal(str(cost)), "charges": {k.replace("_", "@") + ".test": Decimal(str(v)) for k, v in charges.items()}, "final": True}


def test_only_managers_and_admins_read_the_overview_and_only_admins_change_it(aws):
    assert call("GET /admin/spend-caps", OP)[0] == 403
    assert call("GET /admin/spend-caps", NONE)[0] == 403
    assert call("GET /admin/spend-caps", BOTH)[0] == 200
    status, body = call("GET /admin/spend-caps", ADM)
    assert status == 200 and body["canEdit"] is True and call("GET /admin/spend-caps", BOTH)[1]["canEdit"] is False
    assert call("PUT /admin/spend-caps", BOTH, body={"labCapUsd": 50})[0] == 403
    assert call("POST /admin/spend-caps/{userId}/confirm-email", BOTH, path={"userId": OP})[0] == 403


def test_the_overview_shows_caps_spend_percent_and_the_total_of_user_caps(aws):
    seed(aws, labcap=100, default=5, sessions={"1": session(40, olive_x=2.5, ada_x=1.0)})
    aws.users.items[(OP,)]["capUsd"] = Decimal("10")
    body = call("GET /admin/spend-caps", ADM)[1]
    assert body["period"] == PERIOD and body["lab"]["capUsd"] == 100 and body["lab"]["spendUsd"] == 40 and body["lab"]["percent"] == 40
    people = {p["id"]: p for p in body["users"]}
    assert people[OP]["capUsd"] == 10 and people[OP]["effectiveCapUsd"] == 10 and people[OP]["spendUsd"] == 2.5 and people[OP]["percent"] == 25
    assert people[NONE]["capUsd"] is None and people[NONE]["effectiveCapUsd"] == 5
    assert body["defaultUserCapUsd"] == 5
    assert body["userCapsTotalUsd"] == 10 + 5 * 3 + 5  # olive 10; ada, nina, bo and the bootstrap admin at the default 5


def test_an_administrator_sets_the_caps_and_it_is_logged_and_the_job_is_started(aws, monkeypatch):
    started = []
    monkeypatch.setattr(handler, "SPEND_FUNCTION", "spend-fn")
    monkeypatch.setattr(handler, "lambda_client", type("L", (), {"invoke": lambda self, **kw: started.append(kw)})())
    status, body = call("PUT /admin/spend-caps", ADM, body={"labCapUsd": 80, "defaultUserCapUsd": 5, "userCaps": [{"userId": OP, "capUsd": 12.5}]})
    assert status == 200 and body["lab"]["capUsd"] == 80 and body["defaultUserCapUsd"] == 5
    assert {p["id"]: p["capUsd"] for p in body["users"]}[OP] == 12.5
    texts = [r["action"] for r in aws.events.items.values() if r.get("pk") == "changes"]
    assert any("lab's monthly cap to $80.00" in t for t in texts) and any("default monthly cap" in t for t in texts) and any("$12.50" in t for t in texts)
    assert len(started) == 1 and started[0]["FunctionName"] == "spend-fn" and started[0]["InvocationType"] == "Event"
    # a later change leaves the fields it does not mention alone
    call("PUT /admin/spend-caps", ADM, body={"labCapUsd": 90})
    assert call("GET /admin/spend-caps", ADM)[1]["defaultUserCapUsd"] == 5


def test_zero_means_no_cap_and_null_clears_a_personal_cap(aws):
    seed(aws, default=5)
    body = call("PUT /admin/spend-caps", ADM, body={"userCaps": [{"userId": OP, "capUsd": 0}]})[1]
    mine = {p["id"]: p for p in body["users"]}[OP]
    assert mine["capUsd"] == 0 and mine["effectiveCapUsd"] is None
    body = call("PUT /admin/spend-caps", ADM, body={"userCaps": [{"userId": OP, "capUsd": None}]})[1]
    mine = {p["id"]: p for p in body["users"]}[OP]
    assert mine["capUsd"] is None and mine["effectiveCapUsd"] == 5


@pytest.mark.parametrize("bad", [-1, 1_000_001, "5", True, float("nan")])
def test_a_bad_cap_is_refused_and_nothing_is_written(aws, bad):
    assert call("PUT /admin/spend-caps", ADM, body={"labCapUsd": bad})[0] == 400
    assert ("spend", "settings") not in aws.events.items


def test_unknown_users_and_incomplete_entries_are_refused(aws):
    assert call("PUT /admin/spend-caps", ADM, body={"userCaps": [{"userId": "who@x.test", "capUsd": 1}]})[0] == 404
    assert call("PUT /admin/spend-caps", ADM, body={"userCaps": [{"userId": OP}]})[0] == 400
    assert call("PUT /admin/spend-caps", ADM, body={"userCaps": "x"})[0] == 400


def test_anyone_reads_their_own_cap_spend_and_emails(aws):
    seed(aws, default=4, sessions={"1": session(3, olive_x=3.5)})
    aws.users.items[(OP,)]["capEmails"] = {PERIOD: [50, 80]}
    aws.users.items[(OP,)]["emailStatus"] = "confirmed"
    status, body = call("GET /spend", OP)
    assert status == 200 and body["spendUsd"] == 3.5 and body["percent"] == 87.5 and body["emailsSent"] == [50, 80]
    assert body["emailStatus"] == "confirmed" and body["blocked"] is None and body["period"] == PERIOD
    assert "users" not in body and "lab" not in body  # nothing about anyone else
    assert call("GET /spend", NONE)[1]["effectiveCapUsd"] == 4


def test_start_is_refused_and_flagged_when_the_lab_budget_is_reached(aws):
    grant(aws, OP, "i-aaa")
    seed(aws, labcap=10, sessions={"1": session(10)})
    assert call("GET /instances", OP)[1][0]["budgetReached"] is True
    status, body = call("POST /instances/{instanceId}/start", OP, path={"instanceId": "i-aaa"})
    assert status == 409 and "Monthly budget reached" in body["message"] and aws.ec2.started == []
    seed(aws, labcap=11, sessions={"1": session(10)})
    assert call("GET /instances", OP)[1][0]["budgetReached"] is False
    assert call("POST /instances/{instanceId}/start", OP, path={"instanceId": "i-aaa"})[0] == 200


def test_no_cap_never_blocks_a_start(aws):
    grant(aws, OP, "i-aaa")
    seed(aws, sessions={"1": session(9999)})
    assert call("POST /instances/{instanceId}/start", OP, path={"instanceId": "i-aaa"})[0] == 200


def test_user_management_shows_a_block_for_this_month_only(aws):
    now_ms = int(time.time() * 1000)
    aws.users.items[(OP,)]["spendBlock"] = {"period": PERIOD, "at": now_ms, "cap": Decimal("5"), "spend": Decimal("5.02")}
    aws.users.items[(BOTH,)]["spendBlock"] = {"period": "2020-01", "at": now_ms, "cap": Decimal("5"), "spend": Decimal("6")}
    people = {u["id"]: u for u in call("GET /admin/users", ADM)[1]}
    assert people[OP]["spendBlock"]["reason"].startswith("Blocked for the rest of") and "$5.00" in people[OP]["spendBlock"]["reason"]
    assert people[BOTH]["spendBlock"] is None and people[NONE]["spendBlock"] is None


def test_resend_confirmation_is_admin_only_and_passes_the_address_on(aws, monkeypatch):
    sent = []
    monkeypatch.setattr(handler, "SPEND_FUNCTION", "spend-fn")
    monkeypatch.setattr(handler, "lambda_client", type("L", (), {"invoke": lambda self, **kw: sent.append(json.loads(kw["Payload"]))})())
    assert call("POST /admin/spend-caps/{userId}/confirm-email", ADM, path={"userId": OP.upper()})[0] == 200
    assert sent == [{"confirm": [OP]}]
    assert call("POST /admin/spend-caps/{userId}/confirm-email", ADM, path={"userId": "who@x.test"})[0] == 404


# ---- the administrator's one-time override of the lab cap ---------------------------------


def over(aws):
    seed(aws, labcap=10, sessions={"1": session(10)})


def test_only_an_administrator_can_give_the_override_and_only_when_the_lab_is_over_its_cap(aws):
    seed(aws, labcap=10, sessions={"1": session(3)})
    assert call("POST /admin/spend-caps/lab-override", ADM)[0] == 409
    over(aws)
    for who in (OP, NONE, BOTH):
        assert call("POST /admin/spend-caps/lab-override", who)[0] == 403
    assert aws.events.items.get(("spend", "override")) is None


def test_the_override_allows_exactly_one_start_and_is_in_the_change_history(aws):
    grant(aws, OP, "i-aaa")
    over(aws)
    assert call("POST /instances/{instanceId}/start", OP, path={"instanceId": "i-aaa"})[0] == 409
    status, body = call("POST /admin/spend-caps/lab-override", ADM)
    assert status == 200 and body["lab"]["override"]["by"] == ADM and body["lab"]["override"]["used"] is False
    assert call("GET /instances", OP)[1][0]["budgetReached"] is False
    assert call("POST /instances/{instanceId}/start", OP, path={"instanceId": "i-aaa"})[0] == 200
    assert call("GET /admin/spend-caps", ADM)[1]["lab"]["override"]["used"] is True
    aws.ec2.state["i-aaa"] = "stopped"  # it stopped again
    assert call("GET /instances", OP)[1][0]["budgetReached"] is True
    assert call("POST /instances/{instanceId}/start", OP, path={"instanceId": "i-aaa"})[0] == 409
    assert any("run once past its monthly cap" in str(i.get("action", "")) for i in aws.events.items.values())
    assert call("GET /admin/spend-caps", ADM)[1]["lab"]["capUsd"] == 10  # the cap itself is untouched


def test_given_while_the_lab_is_running_it_is_used_at_once(aws):
    over(aws)
    aws.ec2.state["i-aaa"] = "running"
    body = call("POST /admin/spend-caps/lab-override", ADM)[1]
    assert body["lab"]["override"]["used"] is True
    assert aws.events.items[("spend", "override")]["launch"] > 0


def test_an_override_from_another_month_does_nothing(aws):
    grant(aws, OP, "i-aaa")
    over(aws)
    aws.events.put_item({"pk": "spend", "sk": "override", "period": "2020-01", "at": 1, "by": ADM, "used": False})
    assert call("POST /instances/{instanceId}/start", OP, path={"instanceId": "i-aaa"})[0] == 409
    assert call("GET /admin/spend-caps", ADM)[1]["lab"]["override"] is None

