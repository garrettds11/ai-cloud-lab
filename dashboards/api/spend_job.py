"""The spend caps job: runs every 5 minutes (EventBridge), and again when an administrator changes a cap.

What it does (docs/monitoring-spec.md, "Spend caps"):
1. While the lab runs, asks the instance for its usage (the "usage" action through Systems Manager)
   and keeps this month's per-session charges in a ledger row, so the month survives the instance
   being stopped.
2. Works out each person's spend and the lab's spend against their caps, sends the 50/80/98/100%
   emails (once each per month), blocks people at 100% (Cognito user disabled, Open WebUI role set
   to pending) and stops the instance when the lab cap is reached.
3. On a new month, or when a cap is raised above the spend, lets people back in.
4. Emails go through one Amazon SNS topic. Each person gets their own email subscription, filtered so only
   they receive their messages, and only when their identity (the Cognito user) has a verified email
   address. SNS emails them a confirmation link once; nobody is emailed before they click it.

Everything it does is written to the control panel's change history. State lives in the events table
(pk "spend": the settings and one ledger per month) and on the people's rows in the users table.
It only acts on the lab's managed instance and never on administrators (they are never blocked).
"""

import base64
import json
import os
import time
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

import costing
import handler as h
import spend_rules as rules

SPEND_TOPIC_ARN = os.environ.get("SPEND_TOPIC_ARN", "")  # empty = no cap emails (caps are still enforced)
SPEND_ACTOR = "Spend caps"
SSM_WAIT_SECONDS = 100
SSM_POLL_SECONDS = 3
CONFIRM_PER_RUN = 25

sns = boto3.client("sns")
cognito = boto3.client("cognito-idp")


def _d(value):
    return Decimal(str(round(float(value), 6)))


def _f(value, default=0.0):
    return float(value) if value is not None else default


# ---- state -----------------------------------------------------------------------------


def load_settings():
    item = h.events.get_item(Key={"pk": "spend", "sk": "settings"}).get("Item") or {}
    return {"labCapUsd": rules.cap_value(item.get("labCapUsd")), "defaultUserCapUsd": rules.cap_value(item.get("defaultUserCapUsd"))}


def load_ledger(period):
    item = h.events.get_item(Key={"pk": "spend", "sk": f"ledger#{period}"}).get("Item") or {}
    return {
        "sessions": dict(item.get("sessions") or {}),
        "webuiIds": dict(item.get("webuiIds") or {}),
        "labEmails": [int(t) for t in item.get("labEmails") or []],
        "labStopped": item.get("labStopped"),
    }


def save_ledger(period, ledger):
    h.events.put_item(Item={
        "pk": "spend", "sk": f"ledger#{period}", "t": h.now_ms(),
        "sessions": ledger["sessions"], "webuiIds": ledger["webuiIds"],
        "labEmails": ledger["labEmails"], "labStopped": ledger["labStopped"],
    })


def spend_by_user(ledger):
    totals = {}
    for entry in ledger["sessions"].values():
        for email, amount in (entry.get("charges") or {}).items():
            totals[email] = totals.get(email, 0.0) + _f(amount)
    return totals


def lab_spend(ledger):
    return sum(_f(e.get("cost")) for e in ledger["sessions"].values())


def _history(text, user_id):
    h._put_event("changes", "chg", adminId=SPEND_ACTOR, userId=user_id, action=text, result="Applied")


# ---- Systems Manager ---------------------------------------------------------------------


def run_webui(instance_id, action, **params):
    """Runs one action of the panel's document on the instance; returns its JSON result or None."""
    if not h.WEBUI_DOCUMENT:
        return None
    try:
        sent = h.ssm.send_command(
            DocumentName=h.WEBUI_DOCUMENT, InstanceIds=[instance_id],
            Parameters={"action": [action], "expectedVersion": [""], "sinceHour": [str(params.get("since_hour", ""))],
                        "userId": [params.get("user_id", "")], "role": [params.get("role", "")],
                        "payload": [params.get("payload", "")], "toolTokenPrefix": [params.get("tool_prefix", "")]},
            TimeoutSeconds=120, Comment=f"control panel: {action} by spend caps"[:100])
    except ClientError as err:
        print(json.dumps({"error": "send_command", "action": action, "detail": str(err)}))
        return None
    command_id = sent["Command"]["CommandId"]
    deadline = time.time() + SSM_WAIT_SECONDS
    time.sleep(1.5)
    while time.time() < deadline:
        try:
            inv = h.ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
        except ClientError as err:
            if err.response.get("Error", {}).get("Code") != "InvocationDoesNotExist":
                print(json.dumps({"error": "get_command_invocation", "detail": str(err)}))
                return None
            inv = {"Status": "Pending"}
        if inv.get("Status") in h._TERMINAL:
            return h._last_json_line(inv.get("StandardOutputContent"))
        time.sleep(SSM_POLL_SECONDS)
    return None


# ---- putting the saved Open WebUI settings back after a start ----------------------------

REAPPLY_AFTER_SECONDS = 150  # Open WebUI needs a moment after a start before it can take changes
REAPPLY_TRIES = 3  # after this many runs that get no usable answer, the start is given up on (the admin can reapply by hand)


def _mark_reapplied(launch_ms, now, failed, tries=0):
    h.desired.put_item(Item={"setting": "_reapply", "launchMs": launch_ms, "appliedAt": int(now * 1000), "failed": failed, "tries": tries})


def reapply_desired(instance_id, running, launch_ms, now):
    """Once per start, replays the settings the panel keeps for Open WebUI (default model, model settings,
    feature switches, tool servers) so an instance that was replaced comes back as it was configured.
    Returns a short word for the run's log line, or None when there was nothing to do."""
    if h.desired is None or not (instance_id and running and launch_ms and h.WEBUI_DOCUMENT):
        return None
    marker = h.desired.get_item(Key={"setting": "_reapply"}).get("Item") or {}
    if int(marker.get("launchMs", 0)) >= launch_ms:
        return None  # already done for this start
    if now * 1000 - launch_ms < REAPPLY_AFTER_SECONDS * 1000:
        return "waiting"
    items = h._desired_items()
    if not items:
        _mark_reapplied(launch_ms, now, 0)
        return "nothing saved"
    encoded = base64.b64encode(json.dumps({"items": items}, separators=(",", ":")).encode()).decode()
    tries = int(marker.get("tries", 0)) + 1 if int(marker.get("tryLaunchMs", 0)) == launch_ms else 1
    result = None
    if len(encoded) <= h.PAYLOAD_LIMIT:
        result = run_webui(instance_id, "apply-desired", payload=encoded, tool_prefix=h.TOOL_TOKEN_PREFIX)
    if result is not None and isinstance(result.get("results"), list):
        failed = int(result.get("failed", 0))
        _mark_reapplied(launch_ms, now, failed)
        _history(f"Put back {int(result.get('applied', 0))} saved Open WebUI settings after the instance started"
                 + (f"; {failed} could not be applied (see the Open WebUI page)" if failed else ""), "lab")
        return "applied" if not failed else "applied with failures"
    if tries >= REAPPLY_TRIES:
        _mark_reapplied(launch_ms, now, len(items), tries)
        _history("Could not put back the saved Open WebUI settings after the instance started; use Reapply saved settings on the Open WebUI page", "lab")
        return "gave up"
    h.desired.put_item(Item={"setting": "_reapply", "launchMs": int(marker.get("launchMs", 0)), "appliedAt": int(marker.get("appliedAt", 0)),
                             "failed": int(marker.get("failed", 0)), "tryLaunchMs": launch_ms, "tries": tries})
    return "retry"


# ---- the ledger --------------------------------------------------------------------------


def refresh_ledger(instance_id, period, now, ledger):
    """Brings this month's sessions up to date from the instance. True when the instance answered."""
    start, end = rules.period_bounds(period)
    sessions = ledger["sessions"]
    marks = [int(k) // 3600 * 3600 for k, e in sessions.items() if not e.get("final")]
    if sessions:
        marks.append(max(int(k) for k in sessions) // 3600 * 3600)
    since = max(start, min(marks)) if marks else start
    result = run_webui(instance_id, "usage", since_hour=since)
    if not result or not result.get("ok"):
        return False
    cost = costing.allocate(result, now, since=start, until=end, starts=h.recent_starts())
    if not cost["priced"]:
        return True
    for s in cost["sessions"]:
        charges = {}
        for u in s["users"]:
            email = (u.get("email") or "").strip().lower()
            if not email:
                continue
            charges[email] = _d(u.get("charge") or 0)
            ledger["webuiIds"][email] = u["id"]
        sessions[str(s["start"])] = {
            "cost": _d(s["cost"] or 0), "tokens": s["tokens"], "charges": charges,
            "final": bool(s["stop"] is not None and not s["provisional"] and not s["incomplete"]),
        }
    return True


# ---- email -------------------------------------------------------------------------------


def send_email(to, subject, body):
    """Publishes one message that only `to` receives: their subscription filters on the recipient attribute."""
    if not SPEND_TOPIC_ARN:
        return False
    try:
        sns.publish(TopicArn=SPEND_TOPIC_ARN, Subject=" ".join(subject.split())[:99], Message=body,
                    MessageAttributes={"recipient": {"DataType": "String", "StringValue": to}})
        return True
    except ClientError as err:
        print(json.dumps({"error": "send_email", "detail": str(err)}))
        return False


def subscriptions():
    """{email: subscription ARN, or "PendingConfirmation"} for the topic, or None when it cannot be read."""
    found = {}
    try:
        for page in sns.get_paginator("list_subscriptions_by_topic").paginate(TopicArn=SPEND_TOPIC_ARN):
            for sub in page.get("Subscriptions", []):
                if sub.get("Protocol") == "email":
                    found[sub["Endpoint"].lower()] = sub["SubscriptionArn"]
    except ClientError as err:
        print(json.dumps({"error": "list_subscriptions", "detail": str(err)}))
        return None
    return found


def identity_verified(pool, email):
    """True when the person's identity (their Cognito user) has this email address marked verified, False when
    it does not, None when that cannot be told right now."""
    if not pool:
        return None
    try:
        attrs = cognito.admin_get_user(UserPoolId=pool, Username=email).get("UserAttributes", [])
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") == "UserNotFoundException":
            return False
        print(json.dumps({"error": "admin_get_user", "detail": str(err)}))
        return None
    values = {a["Name"]: a.get("Value", "") for a in attrs}
    return values.get("email_verified", "").lower() == "true" and values.get("email", "").lower() == email.lower()


def request_subscription(pool, email):
    """Subscribes a verified address; SNS emails the person a confirmation link. Returns the new status:
    pending, unverified (identity's email not verified) or failed (try again next run)."""
    verified = identity_verified(pool, email)
    if verified is None:
        return "failed"
    if not verified:
        return "unverified"
    try:
        sns.subscribe(TopicArn=SPEND_TOPIC_ARN, Protocol="email", Endpoint=email, ReturnSubscriptionArn=True,
                      Attributes={"FilterPolicy": json.dumps({"recipient": [email]}), "FilterPolicyScope": "MessageAttributes"})
        return "pending"
    except ClientError as err:
        print(json.dumps({"error": "subscribe", "detail": str(err)}))
        return "failed"


# In sweep_addresses, expired (the link was not clicked within SNS's 48 hours) and unsubscribed (the person used
# the link in an email) wait for an administrator's "send again"; the other states are retried by the job itself.


def sweep_addresses(rows, force=()):
    """Brings each person's email status up to date from the topic's subscriptions, and asks verified people who
    have no subscription yet to confirm one. A bounded number of requests per run."""
    if not SPEND_TOPIC_ARN:
        return
    subs = subscriptions()
    if subs is None:
        return
    pool = h._lab().get("user_pool_id", "")
    budget = CONFIRM_PER_RUN
    for row in rows:
        email = row["email"]
        previous = row.get("emailStatus") if row.get("emailChecked") == email else None
        arn = subs.get(email.lower())
        forced = email in force
        if arn and arn != "PendingConfirmation":
            new = "confirmed"
        elif arn and not forced:
            new = "pending"
        elif previous == "confirmed" and not forced:
            new = "unsubscribed"
        elif previous == "pending" and not forced:
            new = "expired"
        elif previous in ("expired", "unsubscribed") and not forced:
            new = previous
        elif budget > 0:
            budget -= 1
            new = request_subscription(pool, email)
        else:
            continue
        if (new, email) != (row.get("emailStatus"), row.get("emailChecked")):
            h.users.update_item(Key={"email": email}, UpdateExpression="SET emailStatus = :s, emailChecked = :e",
                                ExpressionAttributeValues={":s": new, ":e": email})
            row["emailStatus"], row["emailChecked"] = new, email


# ---- people ------------------------------------------------------------------------------


def _admins(rows):
    return [r for r in rows if h.ROLE_ADMIN in h._roles_of(r["email"], r)]


def _enable(pool, email):
    cognito.admin_enable_user(UserPoolId=pool, Username=email)


def _disable(pool, email):
    cognito.admin_disable_user(UserPoolId=pool, Username=email)


def _cognito(fn, pool, email):
    """Runs a Cognito call. True on success or when the user is not in the pool (nothing to do)."""
    if not pool:
        return False
    try:
        fn(pool, email)
        return True
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") == "UserNotFoundException":
            return True
        print(json.dumps({"error": "cognito", "detail": str(err)}))
        return False


def _save_block(email, block):
    h.users.update_item(Key={"email": email}, UpdateExpression="SET spendBlock = :b", ExpressionAttributeValues={":b": block})


def _unblock(row, pool, instance_id, running):
    """Lets a blocked person back in: Cognito first, then the Open WebUI role. Returns the new block (None when done)."""
    email, block = row["email"], dict(row["spendBlock"])
    if block.get("cognitoDisabled") and _cognito(_enable, pool, email):
        block["cognitoDisabled"] = False
    if block.get("roleSet") and running and block.get("webuiId"):
        result = run_webui(instance_id, "set-role", user_id=block["webuiId"], role="user")
        if result and result.get("ok"):
            block["roleSet"] = False
    done = not block.get("cognitoDisabled") and not block.get("roleSet")
    if done:
        _history("Spend cap block lifted", email)
        h._log(email, "info", "Your sign-in is back: the spend cap block was lifted")
    return None if done else block


def _block(row, spend, cap, period, ledger, pool, instance_id, running, now_ms):
    email = row["email"]
    block = {"period": period, "at": now_ms, "cap": _d(cap), "spend": _d(spend),
             "webuiId": ledger["webuiIds"].get(email), "cognitoDisabled": False, "roleSet": False}
    block["cognitoDisabled"] = _cognito(_disable, pool, email)
    _history(f"Blocked: monthly spend cap of {rules.money(cap)} reached", email)
    h._log(email, "warning", f"Blocked: monthly spend cap of {rules.money(cap)} reached; sign-in returns {rules.reset_label(period)}")
    return block


def _set_pending(row, block, instance_id):
    result = run_webui(instance_id, "set-role", user_id=block["webuiId"], role="pending")
    if result and result.get("ok"):
        block["roleSet"] = True
        return True
    return False


def process_people(rows, spend, settings, ledger, period, now, instance_id, running, pool):
    now_ms = int(now * 1000)
    newly_blocked = []
    for row in rows:
        email = row["email"]
        roles = h._roles_of(email, row)
        is_admin = h.ROLE_ADMIN in roles
        cap = rules.effective_cap(row.get("capUsd"), settings["defaultUserCapUsd"])
        amount = spend.get(email, 0.0)
        pct = rules.percent(amount, cap)
        block = row.get("spendBlock")

        # Let back in: a new month, a removed or raised cap, or still finishing a lift.
        if block and (block.get("period") != period or cap is None or amount < cap or is_admin):
            new = _unblock(row, pool, instance_id, running)
            _save_block(email, new)
            row["spendBlock"] = new
            block = new
            if new is None:
                continue
        elif block:
            # Still blocked: finish the Open WebUI half when the instance is up.
            if running and not block.get("roleSet") and block.get("webuiId") and _set_pending(row, dict(block), instance_id):
                block = {**block, "roleSet": True}
                _save_block(email, block)
                row["spendBlock"] = block
        elif cap is not None and amount >= cap and not is_admin:
            block = _block(row, amount, cap, period, ledger, pool, instance_id, running, now_ms)
            if running and block.get("webuiId"):
                _set_pending(row, block, instance_id)
            _save_block(email, block)
            row["spendBlock"] = block
            newly_blocked.append((email, cap, amount))

        # Emails: the highest threshold crossed and not yet sent, once the address is confirmed.
        if cap is None or row.get("emailStatus") != "confirmed":
            continue
        sent = {int(t) for t in (row.get("capEmails") or {}).get(period, [])}
        threshold, record = rules.due_email(pct, sent)
        if threshold is None:
            continue
        subject, body = rules.user_email(threshold, cap, amount, period)
        if not send_email(email, subject, body):
            continue
        h.users.update_item(Key={"email": email}, UpdateExpression="SET capEmails = :m",
                            ExpressionAttributeValues={":m": {period: sorted(sent | set(record))}})
        row["capEmails"] = {period: sorted(sent | set(record))}
        h._log(email, "info", f"Spend cap email sent: {threshold}% of {rules.money(cap)}")
    return newly_blocked


def tell_admins(rows, blocked, period):
    """The admins are copied when someone is stopped by their cap (whether or not the person's own email went out)."""
    recipients = [r["email"] for r in _admins(rows) if r.get("emailStatus") == "confirmed"]
    for email, cap, amount in blocked:
        subject = f"{rules.DOMAIN}: {email} reached their monthly spend cap"
        body = (f"{email} reached their monthly spend cap of {rules.money(cap)} (spent so far: {rules.money(amount)}).\n"
                f"Their sign-in is switched off until {rules.reset_label(period)}. Raise their cap on the Spend caps page to let them back in.")
        for to in recipients:
            send_email(to, subject, body)


def judge_override(pct, running, launch_ms, period):
    """The administrator's one-time permission to run past the lab cap (docs/monitoring-spec.md, "Spend caps").
    Returns True when it covers the running instance, so it must not be stopped. The permission is dropped when
    it is for another month, when the lab is back under its cap, or once the lab it covered has stopped."""
    item = h.events.get_item(Key={"pk": "spend", "sk": "override"}).get("Item")
    if not item:
        return False
    used = bool(item.get("used"))
    if item.get("period") != period or pct is None or pct < 100 or (not running and used):
        h.events.delete_item(Key={"pk": "spend", "sk": "override"})
        _history("Lab cap override ended", "lab")
        return False
    if not running:
        return False  # waiting for the next start
    covers = launch_ms >= int(item.get("at") or 0) or (int(item.get("launch") or 0) > 0 and launch_ms == int(item["launch"]))
    if covers and not used:
        h.events.update_item(Key={"pk": "spend", "sk": "override"}, UpdateExpression="SET #u = :t",
                             ExpressionAttributeNames={"#u": "used"}, ExpressionAttributeValues={":t": True})
    return covers


def process_lab(rows, settings, ledger, period, instance_id, running, now, launch_ms=0):
    cap = settings["labCapUsd"]
    amount = lab_spend(ledger)
    pct = rules.percent(amount, cap)
    overridden = judge_override(pct, running, launch_ms, period)
    if cap is None:
        return
    threshold, record = rules.due_email(pct, set(ledger["labEmails"]))
    if threshold is not None:
        subject, body = rules.lab_email(threshold, cap, amount, period)
        recipients = [r["email"] for r in _admins(rows) if r.get("emailStatus") == "confirmed"]
        if recipients and all(send_email(to, subject, body) for to in recipients):
            ledger["labEmails"] = sorted(set(ledger["labEmails"]) | set(record))
            _history(f"Lab budget email sent: {threshold}% of {rules.money(cap)}", "lab")
    if pct >= 100 and running and instance_id and not overridden:
        try:
            h.ec2.stop_instances(InstanceIds=[instance_id])
        except ClientError as err:
            print(json.dumps({"error": "stop_instances", "detail": str(err)}))
            return
        ledger["labStopped"] = {"period": period, "at": int(now * 1000)}
        _history(f"Lab stopped: monthly budget of {rules.money(cap)} reached", "lab")


# ---- entry point -------------------------------------------------------------------------


def lambda_handler(event, context):
    now = time.time()
    period = rules.period_key(now)
    settings = load_settings()
    lab = h._lab()
    instance_id = (lab.get("instance_ids") or [None])[0]
    pool = lab.get("user_pool_id", "")
    running, launch_ms = False, 0
    if instance_id:
        raws = h._describe([instance_id])
        running = instance_id in raws and raws[instance_id]["instance"]["State"]["Name"] == "running"
        launched = raws[instance_id]["instance"].get("LaunchTime") if instance_id in raws else None
        launch_ms = int(launched.timestamp() * 1000) if launched else 0
    ledger = load_ledger(period)
    answered = refresh_ledger(instance_id, period, now, ledger) if running else False
    rows = [r for r in h._all_users()]
    sweep_addresses(rows, force=set((event or {}).get("confirm") or []))
    process_lab(rows, settings, ledger, period, instance_id, running, now, launch_ms)
    blocked = process_people(rows, spend_by_user(ledger), settings, ledger, period, now, instance_id, running, pool)
    tell_admins(rows, blocked, period)
    save_ledger(period, ledger)
    try:
        left = context.get_remaining_time_in_millis() if hasattr(context, "get_remaining_time_in_millis") else 10**9
        # The replay can wait up to SSM_WAIT_SECONDS for the instance; skip it when this run has used most of its time.
        reapplied = reapply_desired(instance_id, running, launch_ms, now) if left > (SSM_WAIT_SECONDS + 15) * 1000 else "no time left"
    except Exception as err:  # the caps above are what this job is for; a settings replay problem must not stop them
        print(json.dumps({"error": "reapply_desired", "detail": str(err)}))
        reapplied = "error"
    result = {"period": period, "running": running, "usageRead": answered, "labSpend": round(lab_spend(ledger), 4), "settingsReapplied": reapplied}
    print(json.dumps(result))
    return result
