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

import base64
import datetime
import ipaddress
import json
import os
import re
import socket
import time
import urllib.parse
import uuid
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

import costing
import spend_rules

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
# The timer policy the panel sets (idle and hard-limit minutes). It can only tighten the lab's
# auto-stop setting, which stays the ceiling. The instance monitor reads it the same way.
POLICY_PARAMETER = os.environ.get("POLICY_PARAMETER", f"{AUTO_STOP_PARAMETER}/policy" if AUTO_STOP_PARAMETER else "")
# How many times the Reset button has been used in the current run: "<launch epoch seconds>:<count>".
RESET_COUNT_PARAMETER = os.environ.get("RESET_COUNT_PARAMETER", f"{AUTO_STOP_PARAMETER}/reset-count" if AUTO_STOP_PARAMETER else "")
EVENT_TTL_DAYS = int(os.environ.get("EVENT_TTL_DAYS", "90"))
# The SSM document that runs Open WebUI admin actions on the lab instance (admin function only).
# It accepts only the action names below; the API stack's webui_admin.tf defines both lists.
WEBUI_DOCUMENT = os.environ.get("WEBUI_DOCUMENT", "")
SPEND_FUNCTION = os.environ.get("SPEND_FUNCTION", "")  # the spend caps job; the admin function asks it to run after a cap change
WEBUI_ACTIONS = {"status": "Open WebUI health and version", "usage": "Hourly token usage and session log",
                 "chat-test": "One fixed test prompt to a model through the chat API",
                 "tool-servers": "Tool servers and a connection test of each (read only)",
                 "settings": "Settings in effect, marked Global or User default (read only)",
                 "export-config": "Configuration export with secrets left out (read only)",
                 "set-default-model": "Default model, chosen from the installed models",
                 "set-model-params": "Temperature and context length of one model",
                 "set-feature": "One feature switch or API key route list",
                 "upsert-tool-server": "Add or change an MCP or OpenAPI tool server",
                 "remove-tool-server": "Remove a tool server",
                 "import-skill": "Import one text skill and attach it to chosen models",
                 "apply-desired": "Put the saved settings back after an instance start"}
# Write actions (phase 2 of #55): the request is checked here, passed to the instance as base64 JSON,
# read back from Open WebUI there, and only then saved in the desired-state table.
WRITE_KINDS = {"set-default-model": "default-model", "set-model-params": "model-params", "set-feature": "feature",
               "upsert-tool-server": "tool-server", "remove-tool-server": "tool-server-remove", "import-skill": "skill"}
CONFIRMED_KINDS = {"feature", "tool-server", "tool-server-remove", "skill"}  # broad changes ask the person to confirm first
FEATURES = {"api_keys": "bool", "message_rating": "bool", "image_generation": "bool", "memory": "bool", "api_key_routes": "routes"}
RESERVED_MODELS = {"security-analyst"}  # set up by the lab at every start, so a change here would be overwritten
RESERVED_SERVERS = {"vuln-findings"}
WEBUI_DESIRED_TABLE = os.environ.get("WEBUI_DESIRED_TABLE", "")
TOOL_TOKEN_PREFIX = os.environ.get("TOOL_TOKEN_PREFIX", "")  # tool tokens are chosen only from secrets under this prefix
PRIVATE_TOOL_HOSTS = {x.strip().lower() for x in os.environ.get("PRIVATE_TOOL_HOSTS", "").split(",") if x.strip()}
PAYLOAD_LIMIT = 3900  # characters of base64 an SSM document parameter may carry here (the document allows 4000)
DESIRED_LIMIT = 2800  # bytes of saved settings that fit in one apply-desired command
MODEL_NAME = re.compile(r"[A-Za-z0-9._:/-]{1,100}")
OPEN_WEBUI_IMAGE = os.environ.get("OPEN_WEBUI_IMAGE", "")  # fallback when no lab prefix is set

SETUP_GRACE_MINUTES = 30  # same as the watchdog: idle rules wait this long after a start
SILENT_AFTER_MINUTES = 10  # no heartbeat for this long means the idle monitor is silent
RESET_SKEW_SECONDS = 300  # same allowance as the lab: a reset further in the future is ignored

ec2 = boto3.client("ec2")
elbv2 = boto3.client("elbv2")
ssm = boto3.client("ssm")
cloudwatch = boto3.client("cloudwatch")
cloudtrail = boto3.client("cloudtrail")
lambda_client = boto3.client("lambda")
_dynamodb = boto3.resource("dynamodb")
users = _dynamodb.Table(USERS_TABLE)
entitlements = _dynamodb.Table(ENTITLEMENTS_TABLE)
events = _dynamodb.Table(EVENTS_TABLE)
desired = _dynamodb.Table(WEBUI_DESIRED_TABLE) if WEBUI_DESIRED_TABLE else None
secretsmanager = boto3.client("secretsmanager")


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
        return {"instance_ids": INSTANCE_IDS, "target_group_arn": TARGET_GROUP_ARN, "service_url": SERVICE_URL, "open_webui_image": OPEN_WEBUI_IMAGE,
                "user_pool_id": os.environ.get("USER_POOL_ID", "")}
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
        "user_pool_id": found.get("user-pool-id", ""),
    }
    _lab_cache.update(at=now, value=value)
    return value


def _instance_ids():
    return _lab()["instance_ids"]


# ---- instances ------------------------------------------------------------------------

_rule_cache = {"at": 0, "value": None}


def _policy_minutes(value, ceiling):
    """The minutes in force: the panel's value when set, never above a ceiling that is on."""
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return ceiling
    return min(value, ceiling) if ceiling > 0 else value


def _rule_parts():
    """(ceiling, policy, effective): the lab's setting, the panel's override, and what applies."""
    ceiling = {"enabled": False, "idle": 0, "max": 0, "absolute": 0, "resets": 0}
    policy = {"idle": 0, "max": 0, "resets": 0}
    if AUTO_STOP_PARAMETER:
        try:
            data = json.loads(ssm.get_parameter(Name=AUTO_STOP_PARAMETER)["Parameter"]["Value"])
            ceiling = {
                "enabled": bool(data.get("enabled")),
                "idle": int(data.get("idle_minutes", 0) or 0),
                "max": int(data.get("max_uptime_minutes", 0) or 0),
                "absolute": int(data.get("absolute_max_minutes", 0) or 0),
                "resets": int(data.get("max_resets", 0) or 0),
            }
        except (ClientError, ValueError, KeyError) as err:
            print(json.dumps({"warning": "auto-stop parameter unreadable", "detail": str(err)}))
    if POLICY_PARAMETER and ceiling["enabled"]:
        try:
            data = json.loads(ssm.get_parameter(Name=POLICY_PARAMETER)["Parameter"]["Value"])
            policy = {"idle": data.get("idle_minutes", 0), "max": data.get("max_uptime_minutes", 0), "resets": data.get("max_resets", 0)}
        except ClientError as err:
            if err.response.get("Error", {}).get("Code") != "ParameterNotFound":
                print(json.dumps({"warning": "timer policy unreadable", "detail": str(err)}))
        except (ValueError, AttributeError) as err:
            print(json.dumps({"warning": "timer policy is not valid JSON; ignoring it", "detail": str(err)}))
    effective = {
        "enabled": ceiling["enabled"],
        "idle": _policy_minutes(policy["idle"], ceiling["idle"]),
        "max": _policy_minutes(policy["max"], ceiling["max"]),
        "absolute": ceiling["absolute"],  # the panel cannot change it
        "resets": _policy_minutes(policy["resets"], ceiling["resets"]),
    }
    return ceiling, policy, effective


def _rule():
    """The auto-stop setting in force: enabled, idle minutes, hard-limit minutes."""
    if now_ms() - _rule_cache["at"] < 60000 and _rule_cache["value"] is not None:
        return _rule_cache["value"]
    rule = _rule_parts()[2]
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
    count. A time slightly in the future is treated as now; one further ahead, or a value
    that is not a plain number, does not count, matching the lab. If the parameter cannot be read the
    panel simply shows the countdown from launch, which is what the lab falls back to too."""
    if not RESET_PARAMETER:
        return None
    try:
        raw = ssm.get_parameter(Name=RESET_PARAMETER)["Parameter"]["Value"]
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") != "ParameterNotFound":
            print(json.dumps({"warning": "timer reset unreadable", "detail": str(err)}))
        return None
    text = str(raw).strip()
    # Same rule as the instance monitor and the watchdog: a plain decimal of at most 10 digits,
    # and nothing more than 5 minutes in the future, or the reset does not count.
    if not (text.isascii() and text.isdigit() and len(text) <= 10):
        return None
    reset_ms = int(text) * 1000
    if reset_ms > now_ms() + RESET_SKEW_SECONDS * 1000:
        return None
    reset_ms = min(reset_ms, now_ms())
    return reset_ms if reset_ms > launched_ms else None


def _resets_used(launched_ms):
    """Timer resets used in the run that launched at launched_ms: a number, or None when it cannot be read.

    The stored value is "<launch epoch seconds>:<count>". A count from another run, or one that is
    not in that form, counts as none; the absolute limit is what bounds a run either way."""
    if not RESET_COUNT_PARAMETER:
        return 0
    try:
        raw = ssm.get_parameter(Name=RESET_COUNT_PARAMETER)["Parameter"]["Value"]
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") == "ParameterNotFound":
            return 0
        print(json.dumps({"warning": "reset count unreadable", "detail": str(err)}))
        return None
    match = re.fullmatch(r"([0-9]{1,10}):([0-9]{1,4})", str(raw).strip())
    if not match:
        print(json.dumps({"warning": "reset count is not in the expected form; counting none", "value": str(raw)[:40]}))
        return 0
    return int(match.group(2)) if int(match.group(1)) == launched_ms // 1000 else 0


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
        "enabled": rule["enabled"] and (rule["idle"] > 0 or rule["max"] > 0 or rule["absolute"] > 0),
        "idleLimitMinutes": rule["idle"],
        "maxUptimeMinutes": rule["max"],
        "absoluteMaxMinutes": rule["absolute"],
        "resetsAllowed": rule["resets"] if rule["max"] > 0 else 0,
        "resetsUsed": _resets_used(launched_ms) if rule["max"] > 0 and rule["resets"] > 0 else None,
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
        "rule": {"enabled": rule["enabled"], "idleMinutes": rule["idle"], "maxUptimeMinutes": rule["max"], "absoluteMaxMinutes": rule["absolute"]},
        "autoStop": _auto_stop(instance_id, launched, rule) if phase in ("ready", "initializing", "stopping") and launched else None,
    }


def list_instances(caller):
    if not _can_operate(caller):
        return []
    wanted = _usable_ids(caller)
    raws = _describe(wanted)
    rule = _rule()
    reached = _start_blocked_by_budget()
    return [{**_view(i, raws[i], rule), "budgetReached": reached} for i in wanted if i in raws]


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
    if _lab_budget_reached() and not _use_override(caller):
        _log(caller["id"], "warning", f"Start refused for {name}: monthly budget reached", instance_id)
        raise ApiError(409, "Monthly budget reached. The lab cannot be started until an administrator raises the cap or the month resets.")
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
    try:  # who started it, for charging a run nobody chatted in (see costing.py); the start has happened either way
        _put_event("starts", "start", userId=caller["id"], userName=caller.get("name"), instanceId=instance_id)
    except ClientError as err:
        print(json.dumps({"warning": "start not recorded", "detail": str(err)}))
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
    launch = raws[instance_id]["instance"].get("LaunchTime")
    launched_ms = int(launch.timestamp() * 1000) if launch else now_ms()
    limit = rule["resets"]
    used = _resets_used(launched_ms)
    if limit > 0:
        if used is None:
            raise ApiError(502, "Could not check how many timer resets are left. Try again shortly.")
        if used >= limit:
            _log(caller["id"], "warning", f"Timer reset refused for {name}: all {limit} resets of this run are used", instance_id)
            raise ApiError(409, f"This run has used all {limit} timer resets, so the timer cannot be reset again. An administrator can change the limit in the timer policy.")
    reset_s = int(time.time())
    stop_ms = reset_s * 1000 + rule["max"] * 60000
    capped = False
    if rule["absolute"] > 0:
        # The absolute limit counts from launch and no reset moves it.
        absolute_ms = launched_ms + rule["absolute"] * 60000
        current_ms = (_reset_at_ms(launched_ms) or launched_ms) + rule["max"] * 60000
        if min(stop_ms, absolute_ms) <= current_ms:
            at = datetime.datetime.fromtimestamp(absolute_ms / 1000, datetime.timezone.utc).strftime("%H:%M UTC")
            raise ApiError(409, f"A reset would add no time: the lab's absolute limit stops it at {at} whatever the timer says.")
        if stop_ms > absolute_ms:
            stop_ms, capped = absolute_ms, True
    try:
        if RESET_COUNT_PARAMETER:
            try:
                ssm.put_parameter(Name=RESET_COUNT_PARAMETER, Value=f"{launched_ms // 1000}:{(used or 0) + 1}", Type="String", Overwrite=True)
            except ClientError:
                if limit > 0:
                    raise  # a counted limit must not be bypassed by a count that did not save
                print(json.dumps({"warning": "reset count not saved"}))
        ssm.put_parameter(Name=RESET_PARAMETER, Value=str(reset_s), Type="String", Overwrite=True)
    except ClientError as err:
        code = err.response.get("Error", {}).get("Code", "")
        _log(caller["id"], "error", f"{name}: auto-stop timer reset failed, {code}", instance_id)
        print(json.dumps({"error": "put_parameter", "detail": str(err)}))
        raise ApiError(502, "Could not reset the timer. Try again, and tell your administrator if it keeps failing.")
    stop_text = datetime.datetime.fromtimestamp(stop_ms / 1000, datetime.timezone.utc).strftime("%H:%M UTC")
    left = limit - (used or 0) - 1 if limit > 0 else None
    _log(caller["id"], "info", f"Reset the auto-stop timer for {name}: another {rule['max']} minutes, stopping at about {stop_text}" + (" (cut short by the absolute limit)" if capped else "") + (f", {left} resets left" if left is not None else ""), instance_id)
    message = f"Auto-stop timer reset. The lab will stop at about {stop_text}." if capped else f"Auto-stop timer reset. The lab has another {rule['max']} minutes."
    return {
        "ok": True,
        "resetAt": reset_s * 1000,
        "stopAt": stop_ms,
        "maxUptimeMinutes": rule["max"],
        "cappedByAbsoluteLimit": capped,
        "resetsLeft": left,
        "message": message + (f" {left} reset{'s' if left != 1 else ''} left in this run." if left is not None else ""),
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


def _require_admin(caller, what="see everyone's activity"):
    if ROLE_ADMIN not in caller["roles"]:
        raise ApiError(403, f"Only an administrator can {what}.")


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
    period = spend_rules.period_key(time.time())
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
                "spendBlock": _block_view(r, period),
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


# ---- spend caps ---------------------------------------------------------------------------
#
# Caps and this month's spend. The scheduled job (spend_job.py) keeps the ledger and enforces the
# caps; these functions only read that state and let an administrator set the caps. The rules are
# in spend_rules.py and docs/monitoring-spec.md ("Spend caps").

MAX_CAP_USD = 1_000_000


def _spend_item(sk):
    try:
        return events.get_item(Key={"pk": "spend", "sk": sk}).get("Item") or {}
    except ClientError as err:
        print(json.dumps({"error": "read spend state", "detail": str(err)}))
        raise ApiError(503, "Could not read the spend caps right now. Try again shortly.")


def _spend_state():
    """Settings and this month's ledger as plain numbers: per-person spend, lab spend, emails."""
    period = spend_rules.period_key(time.time())
    settings = _spend_item("settings")
    ledger = _spend_item(f"ledger#{period}")
    by_user, lab = {}, 0.0
    for entry in (ledger.get("sessions") or {}).values():
        lab += float(entry.get("cost") or 0)
        for email, amount in (entry.get("charges") or {}).items():
            by_user[email] = by_user.get(email, 0.0) + float(amount)
    return {
        "period": period,
        "labCap": spend_rules.cap_value(settings.get("labCapUsd")),
        "defaultCap": spend_rules.cap_value(settings.get("defaultUserCapUsd")),
        "labSpend": lab,
        "labEmails": sorted(int(t) for t in ledger.get("labEmails") or []),
        "labStopped": bool(ledger.get("labStopped")),
        "byUser": by_user,
    }


def _lab_budget_reached():
    state = _spend_state()
    return state["labCap"] is not None and state["labSpend"] >= state["labCap"]


def _override_item():
    """The administrator's one-time permission to run past the lab cap, if it is for this month."""
    item = _spend_item("override")
    return item if item and item.get("period") == spend_rules.period_key(time.time()) else None


def _start_blocked_by_budget():
    """True when the lab cap is reached and no unused one-time override is waiting."""
    if not _lab_budget_reached():
        return False
    item = _override_item()
    return not (item and not item.get("used"))


def _use_override(caller):
    """Spends the one-time override on this start. False when there is none or someone else just used it."""
    item = _override_item()
    if not item or item.get("used"):
        return False
    try:
        events.update_item(Key={"pk": "spend", "sk": "override"}, UpdateExpression="SET #u = :t",
                           ConditionExpression="attribute_exists(pk) AND (attribute_not_exists(#u) OR #u = :f)",
                           ExpressionAttributeNames={"#u": "used"}, ExpressionAttributeValues={":t": True, ":f": False})
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False
        print(json.dumps({"error": "use override", "detail": str(err)}))
        raise ApiError(503, "Could not check the one-time override right now. Try again shortly.")
    _log(caller["id"], "warning", "Started the lab past its monthly cap with the administrator's one-time override")
    return True


def _block_view(row, period):
    block = (row or {}).get("spendBlock")
    if not block or block.get("period") != period:
        return None
    return {
        "reason": spend_rules.block_reason(float(block["cap"]), float(block["spend"]), int(block["at"]), period),
        "capUsd": float(block["cap"]),
        "spendUsd": float(block["spend"]),
        "blockedAt": int(block["at"]),
        "liftsOn": spend_rules.reset_label(period),
    }


def _person_spend(row, email, state):
    cap = spend_rules.effective_cap(row.get("capUsd"), state["defaultCap"])
    spent = state["byUser"].get(email, 0.0)
    pct = spend_rules.percent(spent, cap)
    return {
        "id": email,
        "name": row.get("name") or email,
        "email": email,
        "roles": _roles_of(email, row),
        "capUsd": None if row.get("capUsd") is None else float(row["capUsd"]),
        "effectiveCapUsd": cap,
        "spendUsd": round(spent, 4),
        "percent": None if pct is None else round(pct, 1),
        "emailsSent": sorted(int(t) for t in (row.get("capEmails") or {}).get(state["period"], [])),
        "emailStatus": row.get("emailStatus") or "unconfirmed",
        "blocked": _block_view(row, state["period"]),
    }


def _spend_overview(caller):
    state = _spend_state()
    rows = {r["email"]: r for r in _all_users()}
    for email in BOOTSTRAP_ADMINS:
        rows.setdefault(email, {"email": email})
    people = sorted((_person_spend(r, e, state) for e, r in rows.items()), key=lambda p: p["name"].lower())
    caps = [p["effectiveCapUsd"] for p in people if p["effectiveCapUsd"] is not None]
    lab_pct = spend_rules.percent(state["labSpend"], state["labCap"])
    return {
        "period": state["period"],
        "resetsOn": spend_rules.reset_label(state["period"]),
        "canEdit": ROLE_ADMIN in caller["roles"],
        "lab": {
            "capUsd": state["labCap"],
            "spendUsd": round(state["labSpend"], 4),
            "percent": None if lab_pct is None else round(lab_pct, 1),
            "projectedUsd": spend_rules.projected(state["labSpend"], time.time(), state["period"]),
            "emailsSent": state["labEmails"],
            "stopped": state["labStopped"],
            "override": _override_view(),
        },
        "defaultUserCapUsd": state["defaultCap"],
        "userCapsTotalUsd": round(sum(caps), 2),
        "users": people,
    }


def _override_view():
    item = _override_item()
    if not item:
        return None
    return {"by": item.get("by", ""), "at": int(item.get("at") or 0), "used": bool(item.get("used"))}


def allow_lab_start_once(caller):
    """An administrator lets the lab run once past its monthly cap, without changing the cap.

    If the lab is stopped, the next start is allowed. If it is still running (the job has not stopped it
    yet), that run is allowed. The permission ends when the lab next stops; the cap itself is untouched."""
    _require_admin(caller, "let the lab run past its cap")
    if not _lab_budget_reached():
        raise ApiError(409, "The lab is under its monthly cap, so there is nothing to override.")
    launch, running = 0, False
    for instance_id in _instance_ids()[:1]:
        raw = _describe([instance_id]).get(instance_id)
        if raw and raw["instance"]["State"]["Name"] in ("running", "pending"):
            running = True
            launched = raw["instance"].get("LaunchTime")
            launch = int(launched.timestamp() * 1000) if launched else 0
    events.put_item(Item={"pk": "spend", "sk": "override", "t": now_ms(), "period": spend_rules.period_key(time.time()),
                          "at": now_ms(), "by": caller["id"], "used": running, "launch": launch})
    _history(caller, "lab", "Allowed the lab to run once past its monthly cap" + (" (it is running now)" if running else " (the next start)"))
    _log(caller["id"], "warning", "Allowed the lab to run once past its monthly cap")
    return _spend_overview(caller)


def get_spend_caps(caller):
    """Everyone's caps and spend. Administrators and user managers (read only for managers)."""
    _require_manager(caller)
    return _spend_overview(caller)


def get_my_spend(caller):
    """The caller's own cap, spend and emails."""
    state = _spend_state()
    row = _user_row(caller["id"]) or {"email": caller["id"]}
    mine = _person_spend(row, caller["id"], state)
    mine.update(period=state["period"], resetsOn=spend_rules.reset_label(state["period"]))
    return mine


def _cap_field(payload, key):
    """A cap from the request: a number from 0 to the maximum (0 = unlimited), None to clear, or missing."""
    if key not in payload:
        return "missing"
    value = payload[key]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or not 0 <= value <= MAX_CAP_USD:
        raise ApiError(400, "A cap must be a dollar amount from 0 to 1,000,000. Use 0 for no cap.")
    return round(float(value), 2)


def _run_spend_job(payload):
    """Asks the spend caps job to run now, so a raised cap lets people back in at once. Best effort:
    the job also runs every 5 minutes."""
    if not SPEND_FUNCTION:
        return
    try:
        lambda_client.invoke(FunctionName=SPEND_FUNCTION, InvocationType="Event", Payload=json.dumps(payload).encode())
    except ClientError as err:
        print(json.dumps({"warning": "could not start the spend job", "detail": str(err)}))


def _cap_text(value):
    return "no cap" if not value else spend_rules.money(value)


def save_spend_caps(caller, payload):
    """Administrators set the lab cap, the default per-person cap and per-person overrides."""
    _require_admin(caller, "change spend caps")
    lab, default = _cap_field(payload, "labCapUsd"), _cap_field(payload, "defaultUserCapUsd")
    people = payload.get("userCaps") or []
    if not isinstance(people, list):
        raise ApiError(400, "userCaps must be a list.")
    parsed = []
    for entry in people:
        if not isinstance(entry, dict) or not isinstance(entry.get("userId"), str):
            raise ApiError(400, "Each entry in userCaps needs a userId.")
        user_id = entry["userId"].strip().lower()
        if _user_row(user_id) is None:
            raise ApiError(404, f"{user_id} is not a known user.")
        parsed.append((user_id, _cap_field(entry, "capUsd")))
    if "missing" in [v for _, v in parsed]:
        raise ApiError(400, "Each entry in userCaps needs a capUsd (a number, or null to use the default).")
    current = _spend_item("settings")
    if lab != "missing" or default != "missing":
        updated = {"pk": "spend", "sk": "settings", "t": now_ms(),
                   "labCapUsd": current.get("labCapUsd"), "defaultUserCapUsd": current.get("defaultUserCapUsd")}
        if lab != "missing":
            updated["labCapUsd"] = None if lab is None else Decimal(str(lab))
            _history(caller, "lab", f"Set the lab's monthly cap to {_cap_text(lab)}")
        if default != "missing":
            updated["defaultUserCapUsd"] = None if default is None else Decimal(str(default))
            _history(caller, "everyone", f"Set the default monthly cap per person to {_cap_text(default)}")
        events.put_item(Item=updated)
    for user_id, cap in parsed:
        users.update_item(Key={"email": user_id}, UpdateExpression="SET capUsd = :c",
                          ExpressionAttributeValues={":c": None if cap is None else Decimal(str(cap))})
        _history(caller, user_id, "Cleared the monthly cap (the default applies)" if cap is None else f"Set the monthly cap to {_cap_text(cap)}")
    _log(caller["id"], "info", "Changed spend caps")
    _run_spend_job({"reason": "caps changed"})
    return _spend_overview(caller)


def resend_confirmation(caller, user_id):
    _require_admin(caller, "change spend caps")
    user_id = user_id.lower()
    if _user_row(user_id) is None:
        raise ApiError(404, "User not found.")
    _run_spend_job({"confirm": [user_id]})
    _history(caller, user_id, "Asked for the email address to be confirmed again")
    return {"ok": True}


# ---- admin: timer policy ----------------------------------------------------------------
#
# The lab's auto-stop setting (Terraform) is the ceiling. The panel may set shorter idle and
# hard-limit minutes inside it, never longer, and cannot switch a limit that is on off. 0 means
# "use the lab's value". The instance monitor applies the same rule from the same parameter.

IDLE_MIN, HARD_MIN, POLICY_MAX, RESETS_MAX = 5, 30, 10080, 50


def _policy_view():
    ceiling, policy, effective = _rule_parts()

    def own(value):
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0

    return {
        "enabled": ceiling["enabled"],
        "ceiling": {"idleMinutes": ceiling["idle"], "maxUptimeMinutes": ceiling["max"], "absoluteMaxMinutes": ceiling["absolute"], "maxResets": ceiling["resets"]},
        "policy": {"idleMinutes": own(policy["idle"]), "maxUptimeMinutes": own(policy["max"]), "maxResets": own(policy["resets"])},
        "effective": {"idleMinutes": effective["idle"], "maxUptimeMinutes": effective["max"], "absoluteMaxMinutes": effective["absolute"], "maxResets": effective["resets"]},
        "minimums": {"idleMinutes": IDLE_MIN, "maxUptimeMinutes": HARD_MIN},
        "maximum": POLICY_MAX,
        "maximumResets": RESETS_MAX,
    }


def get_timer_policy(caller):
    _require_admin(caller, "see the timer policy")
    return _policy_view()


def _policy_field(payload, key, low, label, ceiling):
    value = payload.get(key, 0)
    if value is None:
        value = 0
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApiError(400, f"{label} must be a whole number of minutes (0 to use the lab's value).")
    if value == 0:
        return 0
    if value < low or value > POLICY_MAX:
        raise ApiError(400, f"{label} must be 0 or between {low} and {POLICY_MAX} minutes.")
    if ceiling > 0 and value > ceiling:
        raise ApiError(400, f"{label} cannot be longer than the lab's own limit of {ceiling} minutes.")
    return value


def save_timer_policy(caller, payload):
    _require_admin(caller, "change the timer policy")
    if not POLICY_PARAMETER:
        raise ApiError(503, "The timer policy is not set up on this control panel yet.")
    ceiling, _, _ = _rule_parts()
    if not ceiling["enabled"]:
        raise ApiError(409, "Auto-stop is switched off for this lab, so there is no timer policy to set.")
    idle = _policy_field(payload, "idleMinutes", IDLE_MIN, "Idle time", ceiling["idle"])
    hard = _policy_field(payload, "maxUptimeMinutes", HARD_MIN, "Session length", ceiling["max"])
    resets = payload.get("maxResets", 0)
    if resets is None:
        resets = 0
    if isinstance(resets, bool) or not isinstance(resets, int) or resets < 0 or resets > RESETS_MAX:
        raise ApiError(400, f"Resets per run must be a whole number from 0 (use the lab's value) to {RESETS_MAX}.")
    if ceiling["resets"] > 0 and resets > ceiling["resets"]:
        raise ApiError(400, f"Resets per run cannot be more than the lab's own limit of {ceiling['resets']}.")
    value = json.dumps({"idle_minutes": idle, "max_uptime_minutes": hard, "max_resets": resets, "by": caller["id"], "at": now_ms()})
    try:
        ssm.put_parameter(Name=POLICY_PARAMETER, Value=value, Type="String", Overwrite=True)
    except ClientError as err:
        print(json.dumps({"error": "put_parameter", "detail": str(err)}))
        raise ApiError(502, "Could not save the timer policy. Try again, and check the logs if it keeps failing.")
    _rule_cache.update(at=0, value=None)
    text = (f"idle {idle} min" if idle else "idle: lab value") + ", " + (f"session {hard} min" if hard else "session: lab value") + ", " + (f"{resets} resets per run" if resets else "resets: lab value")
    _history(caller, "everyone", f"Set the timer policy: {text}")
    _log(caller["id"], "info", f"Changed the timer policy ({text})")
    return _policy_view()


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


def _bad(message):
    raise ApiError(400, message)


def _model_name(value):
    if not (isinstance(value, str) and MODEL_NAME.fullmatch(value)):
        _bad("Choose one of the installed models.")
    return value


def _server_id(value):
    if not (isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,40}", value)):
        _bad("The server ID may use letters, digits, - and _ (up to 40).")
    if value in RESERVED_SERVERS:
        _bad("That server is set up by the lab at every start and is not changed here.")
    return value


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_tool_url(url):
    """HTTPS only, no login, query or fragment, and no private, loopback, link-local or metadata address
    unless the host is listed in PRIVATE_TOOL_HOSTS. A name is resolved and every address it gives is checked."""
    if not (isinstance(url, str) and len(url) <= 300):
        _bad("Give the server's https:// address.")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        _bad("The address must start with https:// and have no login, query or fragment.")
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~%/+-]*)?", url):
        _bad("The address may use letters, digits and . - only in its host name (no IPv6 literal).")
    try:
        port = parts.port or 443
    except ValueError:
        _bad("The address has an invalid port.")
    if not 1 <= port <= 65535:
        _bad("The address has an invalid port.")
    host = parts.hostname.lower()
    if host in PRIVATE_TOOL_HOSTS:
        return
    try:
        found = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            found = {ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
        except (socket.gaierror, ValueError):
            _bad("That address could not be found. Check the spelling.")
    for ip in found:
        if not ip.is_global or ip.is_multicast:
            _bad("That address is private or internal, so the panel will not add it. An administrator can list the host in the lab's settings.")


SKILL_CONTENT_LIMIT = 1800  # characters; the whole request must fit one SSM parameter (see PAYLOAD_LIMIT)
_SKILL_ID = re.compile(r"[a-z0-9_-]{1,40}")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _skill_request(body):
    """A text skill (a SKILL.md body) and the models to attach it to. Plain text only: it is stored, never run."""
    sid = body.get("id")
    if not (isinstance(sid, str) and _SKILL_ID.fullmatch(sid)):
        _bad("The skill ID may use lower-case letters, digits, - and _ (up to 40).")
    name, description, content = body.get("name"), body.get("description") or "", body.get("content")
    if not (isinstance(name, str) and 1 <= len(name) <= 80):
        _bad("Give the skill a name of up to 80 characters.")
    if not (isinstance(description, str) and len(description) <= 300):
        _bad("The description may be up to 300 characters.")
    if not (isinstance(content, str) and 1 <= len(content) <= SKILL_CONTENT_LIMIT):
        _bad(f"The skill text must be 1 to {SKILL_CONTENT_LIMIT} characters. Import longer skills in Open WebUI itself (Workspace, Skills).")
    if _CONTROL.search(name + description + content):
        _bad("The skill may contain text only.")
    enabled = body.get("enabled", True)
    if not isinstance(enabled, bool):
        _bad("Enabled is on or off.")
    models = body.get("models") or []
    if not (isinstance(models, list) and len(models) <= 10):
        _bad("Choose up to 10 models.")
    checked = []
    for m in models:
        m = _model_name(m)
        if m in RESERVED_MODELS:
            _bad("That model is set up by the lab at every start, so a change here would be overwritten. Change it in Terraform.")
        if m not in checked:
            checked.append(m)
    return {"id": sid, "name": name, "description": description, "content": content, "enabled": enabled, "models": checked}


def _write_request(kind, body):
    """The checked request for a write action, or a 400 saying what is wrong."""
    if kind == "default-model":
        return {"model": _model_name(body.get("model"))}
    if kind == "model-params":
        out = {"model": _model_name(body.get("model"))}
        if out["model"] in RESERVED_MODELS:
            _bad("That model is set up by the lab at every start, so a change here would be overwritten. Change it in Terraform.")
        if body.get("temperature") is not None:
            t = body["temperature"]
            if not (_is_number(t) and 0 <= t <= 2):
                _bad("Temperature must be between 0 and 2.")
            out["temperature"] = t
        if body.get("numCtx") is not None:
            n = body["numCtx"]
            if not (_is_number(n) and n == int(n) and 512 <= n <= 131072):
                _bad("Context length must be a whole number from 512 to 131072.")
            out["numCtx"] = int(n)
        if len(out) == 1:
            _bad("Give a temperature or a context length.")
        return out
    if kind == "feature":
        feature = body.get("feature")
        if feature not in FEATURES:
            _bad("Unknown feature.")
        value = body.get("value")
        if FEATURES[feature] == "bool":
            if not isinstance(value, bool):
                _bad("This switch is on or off.")
        elif not (isinstance(value, list) and len(value) <= 20 and all(isinstance(v, str) and re.fullmatch(r"/[A-Za-z0-9/_.-]{1,100}", v) for v in value)):
            _bad("Routes must be paths such as /api/chat/completions, at most 20.")
        return {"feature": feature, "value": value}
    if kind == "tool-server":
        out = {"id": _server_id(body.get("id")), "name": body.get("name"), "type": body.get("type"), "url": body.get("url"),
               "path": body.get("path") or "", "tokenSecret": body.get("tokenSecret") or "", "enabled": body.get("enabled") is not False}
        if not (isinstance(out["name"], str) and 1 <= len(out["name"]) <= 60 and re.fullmatch(r"[^\x00-\x1f]+", out["name"])):
            _bad("Give the server a name of up to 60 characters.")
        if out["type"] not in ("mcp", "openapi"):
            _bad("The type must be MCP or OpenAPI.")
        if not (isinstance(out["path"], str) and re.fullmatch(r"[A-Za-z0-9._/-]{0,100}", out["path"]) and ".." not in out["path"] and not out["path"].startswith("/")):
            _bad("The path may use letters, digits and . _ / - only, with no .. and no leading /.")
        _check_tool_url(out["url"])
        token = out["tokenSecret"]
        if token:
            if not (TOOL_TOKEN_PREFIX and isinstance(token, str) and token.startswith(TOOL_TOKEN_PREFIX) and ".." not in token and re.fullmatch(r"[A-Za-z0-9/_.+=@-]{1,200}", token)):
                _bad("Choose a token from the list of lab tool tokens.")
        return out
    if kind == "tool-server-remove":
        return {"id": _server_id(body.get("id"))}
    if kind == "skill":
        return _skill_request(body)
    raise ApiError(400, "Unknown action.")


def _desired_key(kind, request):
    if kind == "default-model":
        return "default-model"
    if kind == "model-params":
        return "model-params#" + request["model"]
    if kind == "feature":
        return "feature#" + request["feature"]
    return "tool-server#" + request["id"]


def _desired_items():
    """The saved settings, as the requests apply-desired replays (never anything secret: a token is a secret's name)."""
    if desired is None:
        return []
    rows, kwargs = [], {}
    while True:
        page = desired.scan(**kwargs)
        rows += page.get("Items", [])
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    items = []
    for row in sorted(rows, key=lambda r: r["setting"]):
        if row["setting"].startswith("_"):
            continue
        try:
            items.append({"kind": row["kind"], **json.loads(row["payload"])})
        except (KeyError, ValueError):
            continue
    return items


def _check_desired_fits(kind, request):
    """Refuse a change before it is made if the saved settings would no longer fit in one apply-desired command."""
    if desired is None or kind in ("tool-server-remove", "skill"):  # a skill is too large to keep for the next start
        return
    key = _desired_key(kind, request)
    kept = [i for i in _desired_items() if _desired_key(i["kind"], i) != key]
    size = len(json.dumps({"items": kept + [{"kind": kind, **request}]}, separators=(",", ":")))
    if size > DESIRED_LIMIT:
        raise ApiError(409, "Too many settings are saved to keep another one. Remove a tool server or reset a model setting first.")


def _record_desired(record, result):
    """After a write action is confirmed by the instance, keep it so it can be put back after the instance is replaced."""
    kind = WRITE_KINDS.get(record.get("action"))
    if desired is None or not kind or kind == "skill" or not (result or {}).get("ok"):
        return
    request = json.loads(record.get("payload") or "{}")
    if kind == "tool-server-remove":
        desired.delete_item(Key={"setting": _desired_key(kind, request)})
        return
    key = _desired_key(kind, request)
    if kind == "model-params":
        old = desired.get_item(Key={"setting": key}).get("Item")
        if old:
            request = {**json.loads(old["payload"]), **request}
    desired.put_item(Item={"setting": key, "kind": kind, "payload": json.dumps(request), "updatedAt": now_ms(), "updatedBy": record.get("requestedBy", "")})


def list_tool_tokens(caller):
    """Names (never values) of the secrets a tool server's token may be chosen from."""
    _require_admin_role(caller)
    if not TOOL_TOKEN_PREFIX:
        return {"prefix": "", "names": []}
    names, kwargs = [], {"Filters": [{"Key": "name", "Values": [TOOL_TOKEN_PREFIX]}], "MaxResults": 100}
    try:
        while len(names) < 200:
            page = secretsmanager.list_secrets(**kwargs)
            names += [s["Name"] for s in page.get("SecretList", []) if s["Name"].startswith(TOOL_TOKEN_PREFIX)]
            if not page.get("NextToken"):
                break
            kwargs["NextToken"] = page["NextToken"]
    except ClientError as err:
        print(json.dumps({"error": "list_secrets", "detail": str(err)}))
        raise ApiError(502, "Could not list the tool tokens. Try again shortly.")
    return {"prefix": TOOL_TOKEN_PREFIX, "names": sorted(names)}


def get_desired_state(caller):
    _require_admin_role(caller)
    items = _desired_items()
    marker = (desired.get_item(Key={"setting": "_reapply"}).get("Item") or {}) if desired is not None else {}
    return {"items": items, "configured": desired is not None,
            "lastApplied": {"launchMs": _num(marker.get("launchMs", 0)), "at": _num(marker.get("appliedAt", 0)),
                            "failed": int(marker.get("failed", 0))} if marker else None}


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
    since_hour = payload.get("sinceHour", "")
    if since_hour in (None, ""):
        since_hour = ""
    elif not (isinstance(since_hour, (int, str)) and not isinstance(since_hour, bool) and re.fullmatch(r"[0-9]{1,10}", str(since_hour))):
        raise ApiError(400, "sinceHour must be an epoch time in seconds.")
    model = payload.get("model", "")
    if action == "chat-test":
        if not (isinstance(model, str) and MODEL_NAME.fullmatch(model)):
            raise ApiError(400, "Choose a model to test.")
    else:
        model = ""
    write = None  # the canonical request of a write action, saved with the record
    if action in WRITE_KINDS:
        if WRITE_KINDS[action] in CONFIRMED_KINDS and payload.get("confirmed") is not True:
            raise ApiError(400, "Confirm this change first.")
        write = _write_request(WRITE_KINDS[action], payload)
    elif action == "apply-desired":
        write = {"items": _desired_items()}
        if not write["items"]:
            raise ApiError(409, "No settings are saved yet, so there is nothing to put back.")
    encoded = base64.b64encode(json.dumps(write, separators=(",", ":")).encode()).decode() if write is not None else ""
    if len(encoded) > PAYLOAD_LIMIT:
        raise ApiError(400, "That request is too large.")
    if write is not None and action in WRITE_KINDS:
        _check_desired_fits(WRITE_KINDS[action], write)
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
            Parameters={"action": [action], "expectedVersion": [_expected_version()], "sinceHour": [str(since_hour)], "model": [model],
                        "payload": [encoded], "toolTokenPrefix": [TOOL_TOKEN_PREFIX if action in ("upsert-tool-server", "apply-desired") else ""]},
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
        **({"payload": json.dumps(write)} if write is not None and action in WRITE_KINDS else {}),
    })
    _log(caller["id"], "info", f"Requested Open WebUI action '{action}'" + (f" for {model}" if model else "") + f" on {name}", instance_id)
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
        return _with_cost({**view, "status": finished["status"], "result": json.loads(stored) if stored else None})
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
        # The outcome belongs in the log of whoever asked, whichever administrator reads it first.
        _log(record["requestedBy"], "info" if ok else "error",
             f"Open WebUI action '{record['action']}' on {instance_id}: {'done' if ok else 'failed (' + status + ')'}", instance_id)
        if ok:
            try:
                _record_desired(record, result)
            except Exception as err:  # the change is made; failing to save it for later must not hide that
                print(json.dumps({"error": "record_desired", "detail": str(err)}))
                _log(record["requestedBy"], "warning", f"Open WebUI action '{record['action']}' worked but could not be saved for the next start", instance_id)
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
    return _with_cost({**view, "status": status, "result": result})


def recent_starts():
    """Who started the lab and when, newest first: [{"t": epoch seconds, "id", "name", "email"}]."""
    try:
        rows = _query_events("starts", "start#", 200)
    except ClientError as err:
        print(json.dumps({"warning": "starts unreadable", "detail": str(err)}))
        return []
    return [{"t": int(r["t"]) // 1000, "id": r["userId"], "name": r.get("userName"), "email": r["userId"]} for r in rows if r.get("userId")]


def _with_cost(answer):
    """A finished usage action also carries the session costs worked out from its record."""
    result = answer.get("result")
    if answer.get("action") == "usage" and isinstance(result, dict) and result.get("ok"):
        answer = {**answer, "cost": costing.allocate(result, time.time(), starts=recent_starts())}
    return answer


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
    if key == "GET /spend":
        return get_my_spend(caller)
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
    if key == "GET /admin/spend-caps":
        return get_spend_caps(caller)
    if key == "PUT /admin/spend-caps":
        return save_spend_caps(caller, _body(event))
    if key == "POST /admin/spend-caps/lab-override":
        return allow_lab_start_once(caller)
    if key == "POST /admin/spend-caps/{userId}/confirm-email":
        return resend_confirmation(caller, path["userId"])
    if key == "GET /admin/timer-policy":
        return get_timer_policy(caller)
    if key == "PUT /admin/timer-policy":
        return save_timer_policy(caller, _body(event))
    if key == "POST /admin/webui/actions":
        return start_webui_action(caller, _body(event))
    if key == "GET /admin/webui/actions/{commandId}":
        return get_webui_action(caller, path["commandId"])
    if key == "GET /admin/webui/tool-tokens":
        return list_tool_tokens(caller)
    if key == "GET /admin/webui/desired-state":
        return get_desired_state(caller)
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
