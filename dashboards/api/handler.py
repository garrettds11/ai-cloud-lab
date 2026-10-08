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
    while they hold the operators role. Administrators hold every right: they see and start
    every managed instance without grants. Nobody here can stop an instance. Anyone who
    may start a running instance may also reset its auto-stop timer (extend the hard
    uptime limit by one full period); that writes one SSM parameter and a log line.
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
# Where the last auto-stop timer reset is kept (epoch seconds). The lab's instance monitor and
# watchdog read it. Defaults to the auto-stop parameter's "reset-at" child, which is what
# Terraform creates (auto_stop.tf), so there is no extra setting to copy by hand.
RESET_PARAMETER = os.environ.get("RESET_PARAMETER", f"{AUTO_STOP_PARAMETER}/reset-at" if AUTO_STOP_PARAMETER else "")
EVENT_TTL_DAYS = int(os.environ.get("EVENT_TTL_DAYS", "90"))
# The SSM document that runs Open WebUI admin actions on the lab instance (admin function only).
# It accepts only the action names below; the API stack's webui_admin.tf defines both lists.
WEBUI_DOCUMENT = os.environ.get("WEBUI_DOCUMENT", "")
WEBUI_ACTIONS = {"status": "Open WebUI health and version"}
OPEN_WEBUI_IMAGE = os.environ.get("OPEN_WEBUI_IMAGE", "")  # fallback when no lab prefix is set

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


def _can_operate(caller):
    """Operators, and administrators, who hold every right."""
    return ROLE_OPERATORS in caller["roles"] or ROLE_ADMIN in caller["roles"]


def _usable_ids(caller):
    """Instances this caller may see and start: all managed ones for an administrator,
    otherwise the ones with an applied grant."""
    ids = _instance_ids()
    if ROLE_ADMIN in caller["roles"]:
        return ids
    granted = set(_granted_ids(caller["id"]))
    return [i for i in ids if i in granted]


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
        return {"instance_ids": INSTANCE_IDS, "target_group_arn": TARGET_GROUP_ARN, "service_url": SERVICE_URL, "open_webui_image": OPEN_WEBUI_IMAGE}
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
        "open_webui_image": found.get("open-webui-image", ""),
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


def _reset_at_ms(launched_ms):
    """When the hard-limit timer was last reset during this run, in ms, or None.

    A reset older than this run's launch is left over from an earlier run and does not
    count. A time in the future is treated as now. If the parameter cannot be read the
    panel simply shows the countdown from launch, which is what the lab falls back to too."""
    if not RESET_PARAMETER:
        return None
    try:
        raw = ssm.get_parameter(Name=RESET_PARAMETER)["Parameter"]["Value"]
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") != "ParameterNotFound":
            print(json.dumps({"warning": "timer reset unreadable", "detail": str(err)}))
        return None
    try:
        reset_ms = int(str(raw).strip()) * 1000
    except ValueError:
        return None
    reset_ms = min(reset_ms, now_ms())
    return reset_ms if reset_ms > launched_ms else None


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
        "resetAt": _reset_at_ms(launched_ms) if rule["max"] > 0 else None,
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
    if not _can_operate(caller):
        return []
    wanted = _usable_ids(caller)
    raws = _describe(wanted)
    rule = _rule()
    return [_view(i, raws[i], rule) for i in wanted if i in raws]


def start_instance(caller, instance_id):
    if not _can_operate(caller):
        raise ApiError(403, "You do not have permission to do that.")
    if instance_id not in _instance_ids():
        raise ApiError(404, "Instance not found.")
    if instance_id not in _usable_ids(caller):
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


def reset_timer(caller, instance_id):
    """Give a running lab another full hard-limit period, counted from now.

    Does not start, stop or restart anything and does not touch the idle timer. It only
    records the time in the reset parameter, which the instance monitor and the watchdog
    both read. Allowed for exactly the people who may start this instance."""
    if not _can_operate(caller):
        raise ApiError(403, "You do not have permission to do that.")
    if instance_id not in _instance_ids():
        raise ApiError(404, "Instance not found.")
    if instance_id not in _usable_ids(caller):
        _log(caller["id"], "warning", f"Timer reset refused for {instance_id}: no active grant", instance_id)
        raise ApiError(403, "You do not have access to this instance.")
    if len(_instance_ids()) != 1:
        # The reset time is one value for the whole lab and every instance monitor reads it,
        # so with several managed instances a reset for one would extend all of them.
        raise ApiError(409, "Timer reset only works while the lab has a single instance, because the reset applies to the whole lab.")
    raws = _describe([instance_id])
    if instance_id not in raws:
        raise ApiError(404, "Instance not found.")
    name = _tag(raws[instance_id]["instance"], "Name") or instance_id
    if raws[instance_id]["instance"]["State"]["Name"] != "running":
        raise ApiError(409, "This instance is not running, so there is no auto-stop timer to reset.")
    rule = _rule()
    if not (rule["enabled"] and rule["max"] > 0):
        raise ApiError(409, "No hard time limit is set for this lab, so there is nothing to reset.")
    if not RESET_PARAMETER:
        print(json.dumps({"error": "timer reset parameter not configured"}))
        raise ApiError(503, "Timer reset is not set up on this control panel yet. Tell your administrator.")
    reset_s = int(time.time())
    try:
        ssm.put_parameter(Name=RESET_PARAMETER, Value=str(reset_s), Type="String", Overwrite=True)
    except ClientError as err:
        code = err.response.get("Error", {}).get("Code", "")
        _log(caller["id"], "error", f"{name}: auto-stop timer reset failed, {code}", instance_id)
        print(json.dumps({"error": "put_parameter", "detail": str(err)}))
        raise ApiError(502, "Could not reset the timer. Try again, and tell your administrator if it keeps failing.")
    stop_ms = reset_s * 1000 + rule["max"] * 60000
    stop_text = datetime.datetime.fromtimestamp(stop_ms / 1000, datetime.timezone.utc).strftime("%H:%M UTC")
    _log(caller["id"], "info", f"Reset the auto-stop timer for {name}: another {rule['max']} minutes, stopping at about {stop_text}", instance_id)
    return {
        "ok": True,
        "resetAt": reset_s * 1000,
        "stopAt": stop_ms,
        "maxUptimeMinutes": rule["max"],
        "message": f"Auto-stop timer reset. The lab has another {rule['max']} minutes.",
    }


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
        UpdateExpression="SET #n = :n, #sub = :s, lastSeenAt = :t",
        ExpressionAttributeNames={"#n": "name", "#sub": "sub"},
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


def _require_admin(caller):
    if ROLE_ADMIN not in caller["roles"]:
        raise ApiError(403, "Only an administrator can see everyone's activity.")


def _scan_events(prefix, since=None):
    """Every person's events whose sort key starts with prefix. The table is small and its
    rows expire, so a scan is fine."""
    rows, kwargs = [], {}
    while True:
        out = events.scan(**kwargs)
        rows += [i for i in out.get("Items", []) if str(i.get("sk", "")).startswith(prefix)
                 and (since is None or int(i["t"]) >= since)]
        if not out.get("LastEvaluatedKey"):
            return rows
        kwargs["ExclusiveStartKey"] = out["LastEvaluatedKey"]


def _owner(item):
    return str(item.get("pk", "")).removeprefix("user#")


def list_all_logins(caller):
    _require_admin(caller)
    names = {u["email"]: u.get("name", u["email"]) for u in _all_users()}
    rows = sorted(_scan_events("login#"), key=lambda r: int(r["t"]), reverse=True)[:200]
    return [
        {
            "id": f"{_owner(r)}|{r['sk']}",
            "t": _num(r["t"]),
            "userId": _owner(r),
            "userName": names.get(_owner(r), _owner(r)),
            "result": r.get("result", "success"),
            "detail": r.get("detail", ""),
            "ip": r.get("ip", ""),
            "browser": r.get("browser", ""),
            "current": _owner(r) == caller["id"] and _num(r["t"]) == caller["auth_time"],
        }
        for r in rows
    ]


def list_all_logs(caller, params):
    _require_admin(caller)
    source = params.get("source", "all")
    severity = params.get("severity", "all")
    try:
        window = max(1, min(int(params.get("windowMinutes", 60)), 1440))
    except ValueError:
        window = 60
    since = now_ms() - window * 60000
    names = {u["email"]: u.get("name", u["email"]) for u in _all_users()}
    rows = [
        {
            "id": f"{_owner(r)}|{r['sk']}",
            "t": _num(r["t"]),
            "source": r.get("source", "Control panel"),
            "severity": r.get("severity", "info"),
            "event": r.get("event", ""),
            "userId": _owner(r),
            "userName": names.get(_owner(r), _owner(r)),
            "instanceId": r.get("instanceId"),
        }
        for r in _scan_events("log#", since)
    ]
    if source in ("all", "EC2"):
        ids = _usable_ids(caller)
        if ids:
            raws = _describe(ids)
            tag_names = {i: (_tag(r["instance"], "Name") or i) for i, r in raws.items()}
            rows += _instance_trail(ids, tag_names, since)
    rows = [r for r in rows if source in ("all", r["source"]) and severity in ("all", r["severity"])]
    rows.sort(key=lambda r: r["t"], reverse=True)
    return rows[:200]


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
    if _can_operate(caller) and (source in ("all", "EC2")):
        ids = _usable_ids(caller)
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


# ---- admin: Open WebUI actions --------------------------------------------------------
#
# The panel never talks to Open WebUI itself. An administrator asks for one named action, the
# admin function asks SSM to run the panel's document on the managed instance, and the result
# is read back later (SSM runs commands asynchronously). Each request and each result is
# recorded under pk "webui-actions" in the events table, and as a log line for the person.

_TERMINAL = {"Success", "Failed", "Cancelled", "TimedOut"}
_COMMAND_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _require_admin_role(caller):
    if ROLE_ADMIN not in caller["roles"]:
        raise ApiError(403, "Only an administrator can run Open WebUI actions.")


def _expected_version():
    """The Open WebUI version the lab pins, from its image tag, or "" when it is not a version."""
    match = re.search(r":v?(\d{1,4}\.\d{1,4}\.\d{1,4})$", _lab().get("open_webui_image", ""))
    return match.group(1) if match else ""


_PENDING_GRACE_MS = 10 * 60000  # SSM may not list a new command for a moment; after this it is gone


def _action_records(command_id):
    """The request row and, once the action has finished, its result row."""
    rows = events.query(KeyConditionExpression=Key("pk").eq("webui-actions") & Key("sk").begins_with(command_id)).get("Items", [])
    by_sk = {r["sk"]: r for r in rows}
    return by_sk.get(command_id), by_sk.get(f"{command_id}#result")


def start_webui_action(caller, payload):
    _require_admin_role(caller)
    action = payload.get("action")
    if action not in WEBUI_ACTIONS:
        raise ApiError(400, "Unknown action.")
    instance_id = payload.get("instanceId")
    if instance_id not in _instance_ids():
        raise ApiError(404, "Instance not found.")
    if not WEBUI_DOCUMENT:
        raise ApiError(503, "Open WebUI actions are not set up on this control panel yet.")
    raws = _describe([instance_id])
    if instance_id not in raws:
        raise ApiError(404, "Instance not found.")
    name = _tag(raws[instance_id]["instance"], "Name") or instance_id
    if raws[instance_id]["instance"]["State"]["Name"] != "running":
        raise ApiError(409, "The instance is not running, so Open WebUI cannot be reached.")
    try:
        out = ssm.send_command(
            DocumentName=WEBUI_DOCUMENT,
            InstanceIds=[instance_id],
            Parameters={"action": [action], "expectedVersion": [_expected_version()]},
            TimeoutSeconds=120,
            Comment=f"control panel: {action} by {caller['id']}"[:100],
        )
    except ClientError as err:
        code = err.response.get("Error", {}).get("Code", "")
        _log(caller["id"], "error", f"{name}: Open WebUI action '{action}' could not start, {code}", instance_id)
        print(json.dumps({"error": "send_command", "detail": str(err)}))
        if code in ("InvalidInstanceId", "InvalidInstanceInformationFilterValue"):
            raise ApiError(409, "The instance is not connected to Systems Manager yet. Try again in a minute.")
        raise ApiError(502, "Could not start the action. Try again shortly.")
    command_id = out["Command"]["CommandId"]
    t = now_ms()
    events.put_item(Item={
        "pk": "webui-actions", "sk": command_id, "t": t, "expiresAt": int(t / 1000) + EVENT_TTL_DAYS * 86400,
        "action": action, "instanceId": instance_id, "requestedBy": caller["id"], "status": "Pending",
    })
    _log(caller["id"], "info", f"Requested Open WebUI action '{action}' on {name}", instance_id)
    return {"commandId": command_id, "instanceId": instance_id, "action": action, "status": "Pending", "requestedAt": t}


def _last_json_line(text):
    for line in reversed((text or "").strip().splitlines()):
        try:
            value = json.loads(line)
        except ValueError:
            continue
        return value if isinstance(value, dict) else None
    return None


def get_webui_action(caller, command_id):
    _require_admin_role(caller)
    if not _COMMAND_ID.match(command_id or ""):
        raise ApiError(400, "That is not an action ID.")
    record, finished = _action_records(command_id)
    if record is None or not WEBUI_DOCUMENT:
        raise ApiError(404, "No such action.")
    instance_id = record["instanceId"]
    view = {
        "commandId": command_id,
        "instanceId": instance_id,
        "action": record["action"],
        "requestedBy": record["requestedBy"],
        "requestedAt": _num(record["t"]),
    }
    # A finished action is answered from its stored result, so it stays readable after SSM
    # drops the command from its history (about 30 days; the record lives EVENT_TTL_DAYS).
    if finished is not None:
        stored = finished.get("result")
        return {**view, "status": finished["status"], "result": json.loads(stored) if stored else None}
    try:
        inv = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") != "InvocationDoesNotExist":
            print(json.dumps({"error": "get_command_invocation", "detail": str(err)}))
            raise ApiError(502, "Could not read the action's result. Try again shortly.")
        recent = now_ms() - int(record["t"]) < _PENDING_GRACE_MS
        # Just started: SSM has not listed it yet. Long ago: SSM no longer knows it, and no
        # result was ever read back, so the outcome is unknown.
        return {**view, "status": "Pending" if recent else "Expired", "result": None}
    if inv.get("DocumentName") != WEBUI_DOCUMENT:
        raise ApiError(404, "No such action.")  # never show the output of anything else
    status = inv.get("Status", "Pending")
    if status not in _TERMINAL:
        return {**view, "status": status, "result": None}
    result = _last_json_line(inv.get("StandardOutputContent"))
    item = {"pk": "webui-actions", "sk": f"{command_id}#result", "t": now_ms(),
            "expiresAt": int(record["expiresAt"]), "status": status}
    if result is not None:
        item["result"] = json.dumps(result)
    try:
        events.put_item(Item=item, ConditionExpression="attribute_not_exists(sk)")
        ok = status == "Success" and bool((result or {}).get("ok"))
        _log(caller["id"], "info" if ok else "error",
             f"Open WebUI action '{record['action']}' on {instance_id}: {'done' if ok else 'failed (' + status + ')'}", instance_id)
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
    return {**view, "status": status, "result": result}


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
    if key == "POST /instances/{instanceId}/reset-timer":
        return reset_timer(caller, path["instanceId"])
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
    if key == "GET /admin/logins":
        return list_all_logins(caller)
    if key == "GET /admin/logs":
        return list_all_logs(caller, event.get("queryStringParameters") or {})
    if key == "PUT /admin/users/{userId}":
        return save_user(caller, path["userId"], _body(event))
    if key == "POST /admin/webui/actions":
        return start_webui_action(caller, _body(event))
    if key == "GET /admin/webui/actions/{commandId}":
        return get_webui_action(caller, path["commandId"])
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
