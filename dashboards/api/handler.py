"""Control API for the AI Cloud Lab control panel.

One Lambda function behind an API Gateway HTTP API with a Cognito JWT authorizer.
The page never reads DynamoDB or calls AWS itself; it calls these routes, and this
function is the only thing that can start an instance or change who may.

Rules, checked on every call (see README.md):
  * Every route needs a signed-in user. Group membership is read live from Cognito,
    not only from the token, so removing someone from a group takes effect at once.
  * Customers see and start only the instances they hold an active grant for, and only
    while they are in the operators group. Nobody here can stop an instance.
  * Admin routes need the user_mgrs group. Saving changes writes instance_entitlements
    and Cognito group membership only. It never changes IAM, tags or instances.

Times in responses are milliseconds since the epoch.
"""

import datetime
import json
import os
import re
import time
import uuid

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

GROUP_OPERATORS = "operators"
GROUP_USER_MGRS = "user_mgrs"
GROUP_LABELS = {GROUP_OPERATORS: "Operator group", GROUP_USER_MGRS: "User manager group"}

USER_POOL_ID = os.environ.get("USER_POOL_ID", "")
ENTITLEMENTS_TABLE = os.environ.get("ENTITLEMENTS_TABLE", "instance_entitlements")
EVENTS_TABLE = os.environ.get("EVENTS_TABLE", "control_panel_events")
INSTANCE_IDS = [i.strip() for i in os.environ.get("INSTANCE_IDS", "").split(",") if i.strip()]
TARGET_GROUP_ARN = os.environ.get("TARGET_GROUP_ARN", "")  # the ALB health check is the "HTTP" check
AUTO_STOP_PARAMETER = os.environ.get("AUTO_STOP_PARAMETER", "")
SERVICE_URL = os.environ.get("SERVICE_URL", "")  # where the Access button goes
EVENT_TTL_DAYS = int(os.environ.get("EVENT_TTL_DAYS", "90"))

SETUP_GRACE_MINUTES = 30  # same as the watchdog: idle rules wait this long after a start
SILENT_AFTER_MINUTES = 10  # no heartbeat for this long means the idle monitor is silent

ec2 = boto3.client("ec2")
elbv2 = boto3.client("elbv2")
ssm = boto3.client("ssm")
cloudwatch = boto3.client("cloudwatch")
cloudtrail = boto3.client("cloudtrail")
cognito = boto3.client("cognito-idp")
_dynamodb = boto3.resource("dynamodb")
entitlements = _dynamodb.Table(ENTITLEMENTS_TABLE)
events = _dynamodb.Table(EVENTS_TABLE)


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def now_ms():
    return int(time.time() * 1000)


def _num(value):
    return int(value) if value is not None else None


# ---- request helpers ----------------------------------------------------------------


def _parse_groups(raw):
    if isinstance(raw, (list, tuple, set)):
        return list(raw)
    text = str(raw or "").strip().strip("[]")
    return [g for g in re.split(r"[\s,]+", text) if g]


def _caller(event):
    claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("jwt", {}).get("claims") or {}
    sub = claims.get("sub")
    if not sub:
        raise ApiError(401, "Please sign in again.")
    http = (event.get("requestContext") or {}).get("http") or {}
    headers = event.get("headers") or {}
    return {
        "id": sub,
        "username": claims.get("cognito:username") or sub,
        "name": claims.get("name") or claims.get("email") or sub,
        "email": claims.get("email", ""),
        "groups": _parse_groups(claims.get("cognito:groups")),
        "auth_time": int(float(claims.get("auth_time", 0))) * 1000,
        "ip": http.get("sourceIp", ""),
        "user_agent": http.get("userAgent") or headers.get("user-agent", ""),
    }


def _live_groups(username):
    """Current groups from Cognito, so a removal does not wait for the token to expire."""
    try:
        out = cognito.admin_list_groups_for_user(UserPoolId=USER_POOL_ID, Username=username)
    except ClientError as err:
        print(json.dumps({"error": "admin_list_groups_for_user", "detail": str(err)}))
        raise ApiError(503, "Could not check your permissions right now. Try again shortly.")
    return [g["GroupName"] for g in out.get("Groups", [])]


def _require_group(caller, group):
    if group not in caller["groups"]:
        raise ApiError(403, "You do not have permission to do that.")


def _body(event):
    raw = event.get("body")
    if not raw:
        return {}
    if event.get("isBase64Encoded"):
        import base64

        raw = base64.b64decode(raw).decode()
    try:
        data = json.loads(raw)
    except ValueError:
        raise ApiError(400, "The request body is not valid JSON.")
    if not isinstance(data, dict):
        raise ApiError(400, "The request body must be a JSON object.")
    return data


def _browser(user_agent):
    ua = user_agent or ""
    name = "Edge" if "Edg/" in ua else "Firefox" if "Firefox/" in ua else "Chrome" if "Chrome/" in ua else "Safari" if "Safari/" in ua else "Unknown browser"
    system = "Windows" if "Windows" in ua else "macOS" if "Mac OS X" in ua else "Android" if "Android" in ua else "iOS" if ("iPhone" in ua or "iPad" in ua) else "Linux" if "Linux" in ua else ""
    return name + (" on " + system if system else "")


# ---- events (logins, control panel log lines, change history) -------------------------


def _put_event(pk, kind, item_time=None, **fields):
    t = item_time or now_ms()
    item = {
        "pk": pk,
        "sk": f"{kind}#{t:013d}#{uuid.uuid4().hex[:8]}",
        "t": t,
        "expiresAt": int(t / 1000) + EVENT_TTL_DAYS * 86400,
    }
    item.update({k: v for k, v in fields.items() if v is not None})
    events.put_item(Item=item)


def _log(user_id, severity, text, instance_id=None):
    _put_event(f"user#{user_id}", "log", source="Control panel", severity=severity, event=text, instanceId=instance_id)


def _history(admin, user_id, action, instance_id=None, group=None):
    _put_event("changes", "chg", adminId=admin["id"], userId=user_id, instanceId=instance_id, group=group, action=action, result="Applied")


def _query_events(pk, prefix, limit, since=None):
    out = events.query(
        KeyConditionExpression=Key("pk").eq(pk) & Key("sk").begins_with(prefix),
        ScanIndexForward=False,
        Limit=limit,
    )
    items = out.get("Items", [])
    return [i for i in items if since is None or int(i["t"]) >= since]


# ---- grants ---------------------------------------------------------------------------


def _grant_items(user_id):
    out = entitlements.query(KeyConditionExpression=Key("userId").eq(user_id))
    return out.get("Items", [])


def _granted_ids(user_id):
    return [i["instanceId"] for i in _grant_items(user_id) if i.get("status") == "applied"]


# ---- instances ------------------------------------------------------------------------

_rule_cache = {"at": 0, "value": None}


def _rule():
    """The auto-stop setting, shared by the lab: enabled, idle_minutes, max_uptime_minutes."""
    if now_ms() - _rule_cache["at"] < 60000 and _rule_cache["value"] is not None:
        return _rule_cache["value"]
    rule = {"enabled": False, "idle": 0, "max": 0}
    if AUTO_STOP_PARAMETER:
        try:
            raw = ssm.get_parameter(Name=AUTO_STOP_PARAMETER)["Parameter"]["Value"]
            data = json.loads(raw)
            rule = {
                "enabled": bool(data.get("enabled")),
                "idle": int(data.get("idle_minutes", 0) or 0),
                "max": int(data.get("max_uptime_minutes", 0) or 0),
            }
        except (ClientError, ValueError, KeyError) as err:
            print(json.dumps({"warning": "auto-stop parameter unreadable", "detail": str(err)}))
    _rule_cache.update(at=now_ms(), value=rule)
    return rule


def _describe(instance_ids):
    """Raw EC2 facts for the managed instances: description, status checks, ALB health."""
    if not instance_ids:
        return {}
    try:
        reservations = ec2.describe_instances(InstanceIds=instance_ids).get("Reservations", [])
        statuses_raw = ec2.describe_instance_status(InstanceIds=instance_ids, IncludeAllInstances=True).get("InstanceStatuses", [])
    except ClientError as err:
        print(json.dumps({"error": "describe instances", "detail": str(err)}))
        raise ApiError(502, "Could not read the instances right now. Try again shortly.")
    found = {i["InstanceId"]: i for r in reservations for i in r.get("Instances", [])}
    statuses = {s["InstanceId"]: s for s in statuses_raw}
    target_health = {}
    if TARGET_GROUP_ARN:
        try:
            for d in elbv2.describe_target_health(TargetGroupArn=TARGET_GROUP_ARN).get("TargetHealthDescriptions", []):
                target_health[d["Target"]["Id"]] = d["TargetHealth"]["State"]
        except ClientError as err:
            print(json.dumps({"warning": "target health unreadable", "detail": str(err)}))
    return {iid: {"instance": inst, "status": statuses.get(iid), "target": target_health.get(iid)} for iid, inst in found.items()}


def _tag(instance, key):
    for tag in instance.get("Tags", []):
        if tag["Key"] == key:
            return tag["Value"]
    return None


def _points(metric, statistic, instance_id, minutes):
    now = datetime.datetime.now(datetime.timezone.utc)
    out = cloudwatch.get_metric_statistics(
        Namespace="AILab",
        MetricName=metric,
        Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
        StartTime=now - datetime.timedelta(minutes=minutes),
        EndTime=now,
        Period=60,
        Statistics=[statistic],
    )
    return sorted(out.get("Datapoints", []), key=lambda p: p["Timestamp"])


def _auto_stop(instance_id, launched_ms, rule):
    uptime = (now_ms() - launched_ms) / 60000
    heartbeat_age = None
    idle = None
    active = -1
    try:
        beats = _points("Heartbeat", "Sum", instance_id, SILENT_AFTER_MINUTES)
        idle_points = _points("IdleMinutes", "Maximum", instance_id, SILENT_AFTER_MINUTES)
        user_points = _points("ActiveUsers", "Maximum", instance_id, SILENT_AFTER_MINUTES)
        if beats:
            last = beats[-1]["Timestamp"]
            heartbeat_age = max(0.0, (datetime.datetime.now(datetime.timezone.utc) - last).total_seconds() / 60)
        if idle_points:
            idle = int(idle_points[-1]["Maximum"])
        if user_points:
            active = int(user_points[-1]["Maximum"])
    except ClientError as err:
        print(json.dumps({"warning": "metrics unreadable", "detail": str(err)}))
    setup_done = heartbeat_age is not None or uptime >= SETUP_GRACE_MINUTES
    if heartbeat_age is None:
        heartbeat_age = uptime if uptime >= SETUP_GRACE_MINUTES else 0.0
    return {
        "enabled": rule["enabled"] and (rule["idle"] > 0 or rule["max"] > 0),
        "idleLimitMinutes": rule["idle"],
        "maxUptimeMinutes": rule["max"],
        "launchedAt": launched_ms,
        "setupDone": setup_done,
        "idleMinutes": idle if idle is not None else 0,
        "activeUsers": active,
        "heartbeatAgeMinutes": heartbeat_age,
    }


def _view(instance_id, raw, rule):
    inst = raw["instance"]
    state = inst["State"]["Name"]
    status = raw["status"] or {}
    ec2_ok = (status.get("InstanceStatus", {}).get("Status") == "ok") and (status.get("SystemStatus", {}).get("Status") == "ok")
    http_ok = (raw["target"] == "healthy") if TARGET_GROUP_ARN else ec2_ok
    if state == "pending":
        phase = "pending"
    elif state == "running":
        phase = "ready" if (ec2_ok and http_ok) else "initializing"
    elif state in ("stopping", "shutting-down"):
        phase = "stopping"
    else:
        phase = "stopped"
    launched = int(inst["LaunchTime"].timestamp() * 1000) if state in ("pending", "running", "stopping") and inst.get("LaunchTime") else None
    return {
        "id": instance_id,
        "name": _tag(inst, "Name") or instance_id,
        "type": inst.get("InstanceType", ""),
        "region": os.environ.get("AWS_REGION", ""),
        "url": SERVICE_URL,
        "phase": phase,
        "message": None,
        "launchedAt": launched,
        "checks": {"ec2": bool(ec2_ok and state == "running"), "http": bool(http_ok and state == "running")},
        "rule": {"enabled": rule["enabled"], "idleMinutes": rule["idle"], "maxUptimeMinutes": rule["max"]},
        "autoStop": _auto_stop(instance_id, launched, rule) if phase in ("ready", "initializing", "stopping") and launched else None,
    }


def list_instances(caller):
    if GROUP_OPERATORS not in caller["groups"]:
        return []
    granted = set(_granted_ids(caller["id"]))
    wanted = [i for i in INSTANCE_IDS if i in granted]
    raws = _describe(wanted)
    rule = _rule()
    return [_view(i, raws[i], rule) for i in wanted if i in raws]


def start_instance(caller, instance_id):
    _require_group(caller, GROUP_OPERATORS)
    if instance_id not in INSTANCE_IDS:
        raise ApiError(404, "Instance not found.")
    if instance_id not in _granted_ids(caller["id"]):
        _log(caller["id"], "warning", f"Start refused for {instance_id}: no active grant", instance_id)
        raise ApiError(403, "You do not have access to this instance.")
    raws = _describe([instance_id])
    if instance_id not in raws:
        raise ApiError(404, "Instance not found.")
    name = _tag(raws[instance_id]["instance"], "Name") or instance_id
    if raws[instance_id]["instance"]["State"]["Name"] != "stopped":
        raise ApiError(409, "This instance is not stopped.")
    try:
        ec2.start_instances(InstanceIds=[instance_id])
    except ClientError as err:
        code = err.response.get("Error", {}).get("Code", "")
        _log(caller["id"], "error", f"{name}: start failed, {code}", instance_id)
        if code == "InsufficientInstanceCapacity":
            raise ApiError(503, "Not enough capacity right now. Try again in a few minutes.")
        print(json.dumps({"error": "start_instances", "detail": str(err)}))
        raise ApiError(502, "AWS could not start the instance. Try again, and tell your administrator if it keeps failing.")
    _log(caller["id"], "info", f"Start requested for {name}", instance_id)
    return {"ok": True}


# ---- session, logins, logs ------------------------------------------------------------


def record_session(caller):
    """Called when the page loads after sign-in. Records one login per sign-in."""
    if caller["auth_time"]:
        item = {
            "pk": f"user#{caller['id']}",
            "sk": f"login#{caller['auth_time']:013d}",
            "t": caller["auth_time"],
            "result": "success",
            "detail": "",
            "ip": caller["ip"],
            "browser": _browser(caller["user_agent"]),
            "expiresAt": int(caller["auth_time"] / 1000) + EVENT_TTL_DAYS * 86400,
        }
        try:
            events.put_item(Item=item, ConditionExpression="attribute_not_exists(pk)")
            _log(caller["id"], "info", "Signed in")
        except ClientError as err:
            if err.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
    return {
        "userId": caller["id"],
        "name": caller["name"],
        "email": caller["email"],
        "groups": caller["groups"],
        "signedInAt": caller["auth_time"] or now_ms(),
    }


def list_logins(caller):
    rows = _query_events(f"user#{caller['id']}", "login#", 10)
    return [
        {
            "id": r["sk"],
            "t": _num(r["t"]),
            "result": r.get("result", "success"),
            "detail": r.get("detail", ""),
            "ip": r.get("ip", ""),
            "browser": r.get("browser", ""),
            "current": _num(r["t"]) == caller["auth_time"],
        }
        for r in rows
    ]


_TRAIL_TEXT = {
    "StartInstances": "start requested",
    "StopInstances": "stop requested",
    "RebootInstances": "reboot requested",
    "RunInstances": "launched",
    "TerminateInstances": "terminated",
}


def _instance_trail(instance_ids, names, since_ms):
    """EC2 start/stop activity from CloudTrail. Carries no user names, only the instance."""
    out = []
    for iid in instance_ids:
        try:
            resp = cloudtrail.lookup_events(
                LookupAttributes=[{"AttributeKey": "ResourceName", "AttributeValue": iid}],
                StartTime=datetime.datetime.fromtimestamp(since_ms / 1000, datetime.timezone.utc),
                EndTime=datetime.datetime.now(datetime.timezone.utc),
                MaxResults=50,
            )
        except ClientError as err:
            print(json.dumps({"warning": "cloudtrail unavailable", "detail": str(err)}))
            continue
        for e in resp.get("Events", []):
            text = _TRAIL_TEXT.get(e.get("EventName"))
            if not text:
                continue
            failed = bool(json.loads(e.get("CloudTrailEvent", "{}")).get("errorCode"))
            out.append(
                {
                    "id": e["EventId"],
                    "t": int(e["EventTime"].timestamp() * 1000),
                    "source": "EC2",
                    "severity": "error" if failed else "info",
                    "event": f"{names.get(iid, iid)}: {text}" + (" (failed)" if failed else ""),
                    "instanceId": iid,
                }
            )
    return out


def list_logs(caller, params):
    source = params.get("source", "all")
    severity = params.get("severity", "all")
    try:
        window = max(1, min(int(params.get("windowMinutes", 60)), 1440))
    except ValueError:
        window = 60
    since = now_ms() - window * 60000
    rows = [
        {
            "id": r["sk"],
            "t": _num(r["t"]),
            "source": r.get("source", "Control panel"),
            "severity": r.get("severity", "info"),
            "event": r.get("event", ""),
            "userId": caller["id"],
            "instanceId": r.get("instanceId"),
        }
        for r in _query_events(f"user#{caller['id']}", "log#", 200, since)
    ]
    if GROUP_OPERATORS in caller["groups"] and (source in ("all", "EC2")):
        granted = set(_granted_ids(caller["id"]))
        ids = [i for i in INSTANCE_IDS if i in granted]
        if ids:
            raws = _describe(ids)
            names = {i: (_tag(r["instance"], "Name") or i) for i, r in raws.items()}
            rows += _instance_trail(ids, names, since)
    rows = [r for r in rows if source in ("all", r["source"]) and severity in ("all", r["severity"])]
    rows.sort(key=lambda r: r["t"], reverse=True)
    return rows[:200]


# ---- admin: users, grants, groups ------------------------------------------------------


def _attr(user, name):
    for a in user.get("Attributes", user.get("UserAttributes", [])):
        if a["Name"] == name:
            return a["Value"]
    return ""


def _pool_users():
    users, token = [], None
    while True:
        kwargs = {"UserPoolId": USER_POOL_ID}
        if token:
            kwargs["NextToken"] = token
        out = cognito.list_users(**kwargs)
        users += out.get("Users", [])
        token = out.get("NextToken")
        if not token:
            return users


def _group_members(group):
    names, token = set(), None
    while True:
        kwargs = {"UserPoolId": USER_POOL_ID, "GroupName": group}
        if token:
            kwargs["NextToken"] = token
        out = cognito.list_users_in_group(**kwargs)
        names |= {u["Username"] for u in out.get("Users", [])}
        token = out.get("NextToken")
        if not token:
            return names


def list_users(caller):
    _require_group(caller, GROUP_USER_MGRS)
    members = {g: _group_members(g) for g in (GROUP_OPERATORS, GROUP_USER_MGRS)}
    people = []
    for u in _pool_users():
        sub = _attr(u, "sub") or u["Username"]
        people.append(
            {
                "id": sub,
                "name": _attr(u, "name") or _attr(u, "email") or u["Username"],
                "email": _attr(u, "email"),
                "groups": [g for g in (GROUP_OPERATORS, GROUP_USER_MGRS) if u["Username"] in members[g]],
            }
        )
    return sorted(people, key=lambda p: p["name"].lower())


def _find_user(sub):
    out = cognito.list_users(UserPoolId=USER_POOL_ID, Filter=f'sub = "{sub}"', Limit=1)
    users = out.get("Users", [])
    if not users:
        raise ApiError(404, "User not found.")
    return users[0]


def list_all_instances(caller):
    _require_group(caller, GROUP_USER_MGRS)
    raws = _describe(INSTANCE_IDS)
    return [
        {"id": i, "name": _tag(raws[i]["instance"], "Name") or i, "type": raws[i]["instance"].get("InstanceType", "")}
        for i in INSTANCE_IDS
        if i in raws
    ]


def get_grants(caller, user_id):
    _require_group(caller, GROUP_USER_MGRS)
    return [i for i in _granted_ids(user_id) if i in INSTANCE_IDS]


def list_changes(caller):
    _require_group(caller, GROUP_USER_MGRS)
    return [
        {
            "t": _num(r["t"]),
            "adminId": r["adminId"],
            "userId": r["userId"],
            "instanceId": r.get("instanceId"),
            "group": r.get("group"),
            "action": r["action"],
            "result": r.get("result", "Applied"),
        }
        for r in _query_events("changes", "chg#", 12)
    ]


def _role_label(groups):
    parts = []
    if GROUP_OPERATORS in groups:
        parts.append("Operator")
    if GROUP_USER_MGRS in groups:
        parts.append("User manager")
    return ", ".join(parts) if parts else "No role"


def save_user(caller, user_id, payload):
    """Group membership first, then grants, so a user can be made an operator and granted
    instances in one save. Everything is validated before anything is written."""
    _require_group(caller, GROUP_USER_MGRS)
    group_changes = payload.get("groups") or []
    grant_changes = payload.get("grants") or []
    target = _find_user(user_id)
    username = target["Username"]
    target_name = _attr(target, "name") or _attr(target, "email") or username
    current = set(_live_groups(username))

    final = set(current)
    for change in group_changes:
        group, member = change.get("group"), bool(change.get("member"))
        if group not in GROUP_LABELS:
            raise ApiError(400, "Unknown group.")
        if group == GROUP_USER_MGRS and not member and user_id == caller["id"]:
            raise ApiError(409, "You cannot remove yourself from the user_mgrs group. Ask another user manager to do it.")
        (final.add if member else final.discard)(group)
    for change in grant_changes:
        if change.get("instanceId") not in INSTANCE_IDS:
            raise ApiError(404, "Instance not found.")
        if change.get("grant") and GROUP_OPERATORS not in final:
            raise ApiError(409, f"{target_name} is not in the operators group, so cannot be granted instances.")

    raws = _describe(INSTANCE_IDS) if (grant_changes or GROUP_OPERATORS in (current - final)) else {}
    names = {i: (_tag(r["instance"], "Name") or i) for i, r in raws.items()}
    applied = 0

    for change in group_changes:
        group, member = change["group"], bool(change["member"])
        if member == (group in current):
            continue
        try:
            if member:
                cognito.admin_add_user_to_group(UserPoolId=USER_POOL_ID, Username=username, GroupName=group)
            else:
                cognito.admin_remove_user_from_group(UserPoolId=USER_POOL_ID, Username=username, GroupName=group)
        except ClientError as err:
            print(json.dumps({"error": "group change", "detail": str(err)}))
            raise ApiError(502, "Cognito could not change the group. Nothing further was saved.")
        _history(caller, user_id, "Added" if member else "Removed", group=GROUP_LABELS[group])
        _log(caller["id"], "info", f"{'Added' if member else 'Removed'} {target_name} {'to' if member else 'from'} {GROUP_LABELS[group]}")
        applied += 1
        if group == GROUP_OPERATORS and not member:
            for item in _grant_items(user_id):
                if item.get("status") == "applied":
                    _set_grant(caller, user_id, item["instanceId"], False, target_name, final, names)

    for change in grant_changes:
        _set_grant(caller, user_id, change["instanceId"], bool(change.get("grant")), target_name, final, names)
        applied += 1
    return {"applied": applied}


def _set_grant(caller, user_id, instance_id, grant, user_name, groups, names):
    t = now_ms()
    instance_name = names.get(instance_id, instance_id)
    if grant:
        entitlements.put_item(
            Item={
                "userId": user_id,
                "instanceId": instance_id,
                "userName": user_name,
                "role": _role_label(groups),
                "instanceName": instance_name,
                "status": "applied",
                "grantedBy": caller["id"],
                "grantedAt": t,
            }
        )
    else:
        # Kept for the history, never deleted. Revoking something never granted adds nothing.
        try:
            entitlements.update_item(
                Key={"userId": user_id, "instanceId": instance_id},
                UpdateExpression="SET #s = :revoked, revokedAt = :t, grantedBy = :by",
                ConditionExpression="attribute_exists(userId)",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":revoked": "revoked", ":t": t, ":by": caller["id"]},
            )
        except ClientError as err:
            if err.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
    _history(caller, user_id, "Granted" if grant else "Revoked", instance_id=instance_id)
    _log(caller["id"], "info", f"{'Granted' if grant else 'Revoked'} {user_name} access to {instance_name}")


# ---- routing -------------------------------------------------------------------------


def _route(event, caller):
    key = event.get("routeKey", "")
    path = event.get("pathParameters") or {}
    query = event.get("queryStringParameters") or {}
    if key == "POST /session":
        return record_session(caller)
    if key == "GET /instances":
        return list_instances(caller)
    if key == "POST /instances/{instanceId}/start":
        return start_instance(caller, path["instanceId"])
    if key == "GET /logins":
        return list_logins(caller)
    if key == "GET /logs":
        return list_logs(caller, query)
    if key == "GET /admin/users":
        return list_users(caller)
    if key == "GET /admin/instances":
        return list_all_instances(caller)
    if key == "GET /admin/users/{userId}/grants":
        return get_grants(caller, path["userId"])
    if key == "GET /admin/changes":
        return list_changes(caller)
    if key == "PUT /admin/users/{userId}":
        return save_user(caller, path["userId"], _body(event))
    raise ApiError(404, "Not found.")


def _response(status, body):
    return {"statusCode": status, "headers": {"content-type": "application/json", "cache-control": "no-store"}, "body": json.dumps(body)}


def lambda_handler(event, context):
    try:
        caller = _caller(event)
        caller["groups"] = _live_groups(caller["username"])
        return _response(200, _route(event, caller))
    except ApiError as err:
        return _response(err.status, {"message": err.message})
    except Exception as err:  # never leak internals to the browser
        print(json.dumps({"error": "unhandled", "detail": repr(err), "route": event.get("routeKey")}))
        return _response(500, {"message": "Something went wrong. Try again."})
