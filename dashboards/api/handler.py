"""Control API for the AI Cloud Lab control panel.

One Lambda function behind an API Gateway HTTP API with a JWT authorizer (any OIDC
sign-in provider; Cognito is only the first one used).
The page never reads DynamoDB or calls AWS itself; it calls these routes, and this
function is the only thing that can start an instance or change who may.

Rules, checked on every call (see README.md):
  * Every route needs a signed-in user. The sign-in provider only proves who they are
    (an OIDC token with a verified email). What they may do comes from this panel's own
    table, read on every call, so a role change takes effect at once.
  * Customers see and start only the instances they hold an active grant for, and only
    while they hold the operators role. Nobody here can stop an instance.
  * Admin routes need the user_mgrs or the admin role. User managers and administrators
    change who may start which instance (grants). Only an administrator changes anyone's
    roles, including who else is an administrator. Saving changes writes the panel's users
    and instance_entitlements tables only. It never changes IAM, tags, Cognito or instances.
  * Any number of people can hold the admin role (it is stored in the table like the other
    roles). People named in BOOTSTRAP_ADMINS are always administrators too, whatever the
    table says, so the panel is never locked out and is unreachable by anyone else until a
    real person is added.

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

ROLE_OPERATORS = "operators"
ROLE_USER_MGRS = "user_mgrs"
ROLE_ADMIN = "admin"
ROLE_LABELS = {ROLE_OPERATORS: "Operator role", ROLE_USER_MGRS: "User manager role", ROLE_ADMIN: "Administrator role"}

USERS_TABLE = os.environ.get("USERS_TABLE", "panel_users")
BOOTSTRAP_ADMINS = {e.strip().lower() for e in os.environ.get("BOOTSTRAP_ADMINS", "").split(",") if e.strip()}
ENTITLEMENTS_TABLE = os.environ.get("ENTITLEMENTS_TABLE", "instance_entitlements")
EVENTS_TABLE = os.environ.get("EVENTS_TABLE", "control_panel_events")
# The lab publishes what the panel manages as SSM parameters under LAB_PARAMETER_PREFIX
# (Terraform writes them, so they follow the lab: gone when the lab is gone). The three
# settings below are only a fallback for when no prefix is set, such as in tests.
LAB_PARAMETER_PREFIX = os.environ.get("LAB_PARAMETER_PREFIX", "").rstrip("/")
LAB_CACHE_SECONDS = 60
INSTANCE_IDS = [i.strip() for i in os.environ.get("INSTANCE_IDS", "").split(",") if i.strip()]
TARGET_GROUP_ARN = os.environ.get("TARGET_GROUP_ARN", "")  # the ALB health check is the "HTTP" check
SERVICE_URL = os.environ.get("SERVICE_URL", "")  # where the Access button goes
AUTO_STOP_PARAMETER = os.environ.get("AUTO_STOP_PARAMETER", "")
EVENT_TTL_DAYS = int(os.environ.get("EVENT_TTL_DAYS", "90"))

SETUP_GRACE_MINUTES = 30  # same as the watchdog: idle rules wait this long after a start
SILENT_AFTER_MINUTES = 10  # no heartbeat for this long means the idle monitor is silent

ec2 = boto3.client("ec2")
elbv2 = boto3.client("elbv2")
ssm = boto3.client("ssm")
cloudwatch = boto3.client("cloudwatch")
cloudtrail = boto3.client("cloudtrail")
_dynamodb = boto3.resource("dynamodb")
users = _dynamodb.Table(USERS_TABLE)
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


def _caller(event):
    claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("jwt", {}).get("claims") or {}
    email = str(claims.get("email", "")).strip().lower()
    if not email:
        raise ApiError(401, "Your sign-in has no email address, so the panel cannot tell who you are.")
    if str(claims.get("email_verified", "true")).lower() == "false":
        raise ApiError(401, "Your email address is not verified with your sign-in provider.")
    http = (event.get("requestContext") or {}).get("http") or {}
    headers = event.get("headers") or {}
    return {
        "id": email,  # the panel identifies people by email, whichever provider signed them in
        "sub": claims.get("sub", ""),
        "name": claims.get("name") or email,
        "email": email,
        "roles": [],
        "auth_time": int(float(claims.get("auth_time", 0))) * 1000,
        "ip": http.get("sourceIp", ""),
        "user_agent": http.get("userAgent") or headers.get("user-agent", ""),
    }


def _user_row(email):
    try:
        return users.get_item(Key={"email": email}).get("Item")
    except ClientError as err:
        print(json.dumps({"error": "read users table", "detail": str(err)}))
        raise ApiError(503, "Could not check your permissions right now. Try again shortly.")


def _roles_of(email, row=None):
    """The person's current roles from the panel's table, read on every call."""
    row = row if row is not None else _user_row(email)
    roles = [r for r in (row or {}).get("roles", []) if r in ROLE_LABELS]
    if email in BOOTSTRAP_ADMINS and ROLE_ADMIN not in roles:
        roles.append(ROLE_ADMIN)
    return roles


def _require_role(caller, role):
    if role not in caller["roles"]:
        raise ApiError(403, "You do not have permission to do that.")


def _require_manager(caller):
    """User managers and administrators may open User management and change grants."""
    if ROLE_USER_MGRS not in caller["roles"] and ROLE_ADMIN not in caller["roles"]:
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


def _history(admin, user_id, action, instance_id=None, role=None):
    _put_event("changes", "chg", adminId=admin["id"], userId=user_id, instanceId=instance_id, role=role, action=action, result="Applied")


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


# ---- the lab the panel manages ----------------------------------------------------------

_lab_cache = {"at": 0.0, "value": None}


def _lab():
    """The instances, ALB target group and service address of the lab, read live.

    Parameters under LAB_PARAMETER_PREFIX: instance-ids (comma separated), target-group-arn,
    service-url. With no lab deployed they do not exist, so the panel manages nothing.
    Cached for a minute. If they cannot be read the call fails closed."""
    if not LAB_PARAMETER_PREFIX:
        return {"instance_ids": INSTANCE_IDS, "target_group_arn": TARGET_GROUP_ARN, "service_url": SERVICE_URL}
    now = time.time()
    if _lab_cache["value"] is not None and now - _lab_cache["at"] < LAB_CACHE_SECONDS:
        return _lab_cache["value"]
    found, kwargs = {}, {"Path": LAB_PARAMETER_PREFIX, "Recursive": False}
    try:
        while True:
            out = ssm.get_parameters_by_path(**kwargs)
            for p in out.get("Parameters", []):
                found[p["Name"].rsplit("/", 1)[-1]] = p["Value"]
            if not out.get("NextToken"):
                break
            kwargs["NextToken"] = out["NextToken"]
    except ClientError as err:
        print(json.dumps({"error": "read lab parameters", "detail": str(err)}))
        raise ApiError(503, "Could not read which instances the lab provides. Try again shortly.")
    value = {
        "instance_ids": [i.strip() for i in found.get("instance-ids", "").split(",") if i.strip()],
        "target_group_arn": found.get("target-group-arn", ""),
        "service_url": found.get("service-url", ""),
    }
    _lab_cache.update(at=now, value=value)
    return value


def _instance_ids():
    return _lab()["instance_ids"]


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
    target_group = _lab()["target_group_arn"]
    if target_group:
        try:
            for d in elbv2.describe_target_health(TargetGroupArn=target_group).get("TargetHealthDescriptions", []):
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
    http_ok = (raw["target"] == "healthy") if _lab()["target_group_arn"] else ec2_ok
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
        "url": _lab()["service_url"],
        "phase": phase,
        "message": None,
        "launchedAt": launched,
        "checks": {"ec2": bool(ec2_ok and state == "running"), "http": bool(http_ok and state == "running")},
        "rule": {"enabled": rule["enabled"], "idleMinutes": rule["idle"], "maxUptimeMinutes": rule["max"]},
        "autoStop": _auto_stop(instance_id, launched, rule) if phase in ("ready", "initializing", "stopping") and launched else None,
    }


def list_instances(caller):
    if ROLE_OPERATORS not in caller["roles"]:
        return []
    granted = set(_granted_ids(caller["id"]))
    wanted = [i for i in _instance_ids() if i in granted]
    raws = _describe(wanted)
    rule = _rule()
    return [_view(i, raws[i], rule) for i in wanted if i in raws]


def start_instance(caller, instance_id):
    _require_role(caller, ROLE_OPERATORS)
    if instance_id not in _instance_ids():
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
    """Called when the page loads after sign-in. Adds the person to the panel's users table
    the first time (with no roles) and records one login per sign-in."""
    t = now_ms()
    try:
        users.put_item(
            Item={"email": caller["email"], "source": "panel", "firstSeenAt": t},  # no roles attribute: the customer role cannot write one
            ConditionExpression="attribute_not_exists(email)",
        )
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
    users.update_item(
        Key={"email": caller["email"]},
        UpdateExpression="SET #n = :n, sub = :s, lastSeenAt = :t",
        ExpressionAttributeNames={"#n": "name"},
        ExpressionAttributeValues={":n": caller["name"], ":s": caller["sub"], ":t": t},
    )
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
        "roles": caller["roles"],
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
    if ROLE_OPERATORS in caller["roles"] and (source in ("all", "EC2")):
        granted = set(_granted_ids(caller["id"]))
        ids = [i for i in _instance_ids() if i in granted]
        if ids:
            raws = _describe(ids)
            names = {i: (_tag(r["instance"], "Name") or i) for i, r in raws.items()}
            rows += _instance_trail(ids, names, since)
    rows = [r for r in rows if source in ("all", r["source"]) and severity in ("all", r["severity"])]
    rows.sort(key=lambda r: r["t"], reverse=True)
    return rows[:200]


# ---- admin: users, grants, roles ------------------------------------------------------


def _all_users():
    rows, kwargs = [], {}
    while True:
        out = users.scan(**kwargs)
        rows += out.get("Items", [])
        if not out.get("LastEvaluatedKey"):
            return rows
        kwargs["ExclusiveStartKey"] = out["LastEvaluatedKey"]


def list_users(caller):
    _require_manager(caller)
    rows = {r["email"]: r for r in _all_users()}
    for email in BOOTSTRAP_ADMINS:
        rows.setdefault(email, {"email": email, "roles": [], "source": "settings"})
    return sorted(
        (
            {
                "id": email,
                "name": r.get("name") or email,
                "email": email,
                "roles": _roles_of(email, r),
                "source": r.get("source", "panel"),
                "lastSeenAt": _num(r.get("lastSeenAt")),
            }
            for email, r in rows.items()
        ),
        key=lambda p: p["name"].lower(),
    )


def list_all_instances(caller):
    _require_manager(caller)
    raws = _describe(_instance_ids())
    return [
        {"id": i, "name": _tag(raws[i]["instance"], "Name") or i, "type": raws[i]["instance"].get("InstanceType", "")}
        for i in _instance_ids()
        if i in raws
    ]


def get_grants(caller, user_id):
    _require_manager(caller)
    return [i for i in _granted_ids(user_id.lower()) if i in _instance_ids()]


def list_changes(caller):
    _require_manager(caller)
    return [
        {
            "t": _num(r["t"]),
            "adminId": r["adminId"],
            "userId": r["userId"],
            "instanceId": r.get("instanceId"),
            "role": r.get("role"),
            "action": r["action"],
            "result": r.get("result", "Applied"),
        }
        for r in _query_events("changes", "chg#", 12)
    ]


def _role_label(roles):
    parts = []
    if ROLE_ADMIN in roles:
        parts.append("Administrator")
    if ROLE_OPERATORS in roles:
        parts.append("Operator")
    if ROLE_USER_MGRS in roles:
        parts.append("User manager")
    return ", ".join(parts) if parts else "No role"


def save_user(caller, user_id, payload):
    """Role changes first, then grants, so a user can be made an operator and granted
    instances in one save. Everything is validated before anything is written."""
    _require_manager(caller)
    user_id = user_id.lower()
    role_changes = payload.get("roles") or []
    grant_changes = payload.get("grants") or []
    if role_changes and ROLE_ADMIN not in caller["roles"]:
        raise ApiError(403, "Only an administrator can change roles.")
    row = _user_row(user_id)
    if row is None and user_id not in BOOTSTRAP_ADMINS:
        raise ApiError(404, "User not found.")
    target_name = (row or {}).get("name") or user_id
    stored = [r for r in (row or {}).get("roles", []) if r in ROLE_LABELS]
    final = list(stored)

    for change in role_changes:
        role, member = change.get("role"), bool(change.get("member"))
        if role not in ROLE_LABELS:
            raise ApiError(400, "Unknown role.")
        if role == ROLE_ADMIN and not member:
            if user_id == caller["id"]:
                raise ApiError(409, "You cannot remove your own administrator role. Ask another administrator to do it.")
            if user_id in BOOTSTRAP_ADMINS:
                raise ApiError(409, f"{target_name} is an administrator set in the panel's settings, so this cannot be changed here.")
        if member and role not in final:
            final.append(role)
        if not member and role in final:
            final.remove(role)
    for change in grant_changes:
        if change.get("instanceId") not in _instance_ids():
            raise ApiError(404, "Instance not found.")
        if change.get("grant") and ROLE_OPERATORS not in final:
            raise ApiError(409, f"{target_name} does not have the operator role, so cannot be granted instances.")

    losing_operator = ROLE_OPERATORS in stored and ROLE_OPERATORS not in final
    raws = _describe(_instance_ids()) if (grant_changes or losing_operator) else {}
    names = {i: (_tag(r["instance"], "Name") or i) for i, r in raws.items()}
    applied = 0

    if sorted(final) != sorted(stored):
        t = now_ms()
        users.update_item(
            Key={"email": user_id},
            UpdateExpression="SET #r = :r, updatedAt = :t, updatedBy = :by",
            ExpressionAttributeNames={"#r": "roles"},
            ExpressionAttributeValues={":r": final, ":t": t, ":by": caller["id"]},
        )
        for role in ROLE_LABELS:
            if (role in final) != (role in stored):
                added = role in final
                _history(caller, user_id, "Added" if added else "Removed", role=ROLE_LABELS[role])
                _log(caller["id"], "info", f"{'Added' if added else 'Removed'} {target_name} {'to' if added else 'from'} the {ROLE_LABELS[role]}")
                applied += 1
        if losing_operator:
            for item in _grant_items(user_id):
                if item.get("status") == "applied":
                    _set_grant(caller, user_id, item["instanceId"], False, target_name, final, names)

    for change in grant_changes:
        _set_grant(caller, user_id, change["instanceId"], bool(change.get("grant")), target_name, final, names)
        applied += 1
    return {"applied": applied}


def _set_grant(caller, user_id, instance_id, grant, user_name, roles, names):
    t = now_ms()
    instance_name = names.get(instance_id, instance_id)
    if grant:
        entitlements.put_item(
            Item={
                "userId": user_id,
                "instanceId": instance_id,
                "userName": user_name,
                "role": _role_label(roles),
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


def _customer_routes(event, caller):
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
    return None


def _admin_routes(event, caller):
    key = event.get("routeKey", "")
    path = event.get("pathParameters") or {}
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
    return None


def _response(status, body):
    return {"statusCode": status, "headers": {"content-type": "application/json", "cache-control": "no-store"}, "body": json.dumps(body)}


def make_handler(routes):
    """One Lambda entry point that answers only the routes it is given."""

    def lambda_handler(event, context):
        try:
            caller = _caller(event)
            caller["roles"] = _roles_of(caller["id"])
            result = routes(event, caller)
            if result is None:
                raise ApiError(404, "Not found.")
            return _response(200, result)
        except ApiError as err:
            return _response(err.status, {"message": err.message})
        except Exception as err:  # never leak internals to the browser
            print(json.dumps({"error": "unhandled", "detail": repr(err), "route": event.get("routeKey")}))
            return _response(500, {"message": "Something went wrong. Try again."})

    return lambda_handler


customer_handler = make_handler(_customer_routes)
admin_handler = make_handler(_admin_routes)
