"""Independent watchdog for the AI lab auto-stop.

Runs every few minutes from EventBridge and backs up the on-instance monitor. It has
two independent controls, each off when set to 0:

* Hard time limit (MAX_UPTIME_MINUTES). Warn shortly before the limit, and stop the
  instance just after it if the instance has not stopped itself. People being active
  does not prevent this. The limit counts from the later of the instance's launch time
  and the last timer reset (the control panel's reset button writes the time to the SSM
  parameter RESET_PARAMETER). A reset from an earlier run is older than the launch time,
  so it is ignored.
* Idle shutdown (IDLE_MINUTES). It never stops an instance that people are using.
  If the agent reports the lab idle well past the limit but the instance is still
  running, the shutdown failed, so stop it. If the agent has gone silent, activity
  cannot be read from the instance: stop it only when the load balancer also shows
  no requests for the whole idle window, otherwise send an alert.
"""

import datetime
import json
import os

import boto3
from botocore.exceptions import BotoCoreError, ClientError

INSTANCE_ID = os.environ.get("INSTANCE_ID", "")
IDLE_MINUTES = int(os.environ.get("IDLE_MINUTES", "0"))  # 0 = idle shutdown off
MAX_UPTIME_MINUTES = int(os.environ.get("MAX_UPTIME_MINUTES", "0"))  # 0 = no hard limit
CHECK_MINUTES = int(os.environ.get("CHECK_MINUTES", "5"))
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "")
ALB_DIMENSION = os.environ.get("ALB_DIMENSION", "")  # e.g. app/name/1234567890abcdef
RESET_PARAMETER = os.environ.get("RESET_PARAMETER", "")  # SSM parameter holding the last timer reset, epoch seconds

STOP_GRACE_MINUTES = 5  # extra time before the watchdog steps in after the agent
SETUP_GRACE_MINUTES = 30  # leave a freshly started instance alone for idle checks while it sets up
SILENT_AFTER_MINUTES = 10  # no heartbeat for this long means the agent is silent

ec2 = boto3.client("ec2")
cloudwatch = boto3.client("cloudwatch")
sns = boto3.client("sns")
ssm = boto3.client("ssm")


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _warn_before_minutes():
    """How long before the hard limit the warning email goes out."""
    return 30 if MAX_UPTIME_MINUTES >= 60 else 10


def _agent_points(metric, statistic, now, minutes):
    response = cloudwatch.get_metric_statistics(
        Namespace="AILab",
        MetricName=metric,
        Dimensions=[{"Name": "InstanceId", "Value": INSTANCE_ID}],
        StartTime=now - datetime.timedelta(minutes=minutes),
        EndTime=now,
        Period=60,
        Statistics=[statistic],
    )
    return sorted(response["Datapoints"], key=lambda point: point["Timestamp"])


def _alb_requests(now, minutes):
    """Total ALB requests over the window, or None when it cannot be measured."""
    if not ALB_DIMENSION:
        return None
    response = cloudwatch.get_metric_statistics(
        Namespace="AWS/ApplicationELB",
        MetricName="RequestCount",
        Dimensions=[{"Name": "LoadBalancer", "Value": ALB_DIMENSION}],
        StartTime=now - datetime.timedelta(minutes=minutes),
        EndTime=now,
        Period=300,
        Statistics=["Sum"],
    )
    return sum(point["Sum"] for point in response["Datapoints"])


RESET_SKEW_SECONDS = 300  # a reset a little in the future is clock skew; further is invalid
RESET_MAX_DIGITS = 10  # epoch seconds stay 10 digits until the year 2286


def _reset_time(now):
    """When the hard-limit timer was last reset, as a datetime, or None when it never was.

    Returns False when the parameter cannot be read, so the caller can fall back to the
    launch time. That fails toward stopping, which is the safe direction for a cost limit.
    Only a plain decimal of at most 10 digits counts. A value more than RESET_SKEW_SECONDS
    in the future is ignored (the limit then counts from launch), so a bad or hand-written
    value can never switch the hard limit off. A slightly future value is treated as now."""
    if not RESET_PARAMETER:
        return None
    try:
        raw = ssm.get_parameter(Name=RESET_PARAMETER)["Parameter"]["Value"]
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") == "ParameterNotFound":
            return None
        print(json.dumps({"warning": "reset parameter unreadable; using the launch time", "detail": str(err)}))
        return False
    except BotoCoreError as err:  # timeouts and connection errors
        print(json.dumps({"warning": "reset parameter unreadable; using the launch time", "detail": str(err)}))
        return False
    text = str(raw).strip()
    if not (text.isascii() and text.isdigit() and len(text) <= RESET_MAX_DIGITS):
        print(json.dumps({"warning": "reset parameter is not a valid time; ignoring it", "value": text[:40]}))
        return None
    seconds = int(text)
    if seconds <= 0:
        return None
    if seconds > now.timestamp() + RESET_SKEW_SECONDS:
        print(json.dumps({"warning": "reset parameter is in the future; ignoring it", "value": text}))
        return None
    try:
        reset = datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None
    return min(reset, now)


def _alert(subject, message):
    print(json.dumps({"alert": subject, "message": message}))
    if SNS_TOPIC_ARN:
        sns.publish(TopicArn=SNS_TOPIC_ARN, Subject=subject[:100], Message=message)


def _stop(reason):
    print(json.dumps({"action": "stop", "reason": reason}))
    ec2.stop_instances(InstanceIds=[INSTANCE_ID])


def lambda_handler(event, context):
    instance = ec2.describe_instances(InstanceIds=[INSTANCE_ID])["Reservations"][0]["Instances"][0]
    state = instance["State"]["Name"]
    if state != "running":
        return {"state": state, "action": "none"}

    now = _now()
    uptime = (now - instance["LaunchTime"]).total_seconds() / 60
    result = {"state": state, "uptime_minutes": round(uptime), "action": "none"}

    # The hard limit counts from the later of the launch and the last reset.
    hard_start = instance["LaunchTime"]
    if MAX_UPTIME_MINUTES > 0:
        reset = _reset_time(now)
        if reset and reset > hard_start:
            hard_start = reset
            result["timer_reset_at"] = reset.isoformat()
    hard_elapsed = (now - hard_start).total_seconds() / 60
    result["hard_limit_elapsed_minutes"] = round(hard_elapsed)

    heartbeats = _agent_points("Heartbeat", "Sum", now, SILENT_AFTER_MINUTES)
    idle_points = _agent_points("IdleMinutes", "Maximum", now, SILENT_AFTER_MINUTES)
    user_points = _agent_points("ActiveUsers", "Maximum", now, SILENT_AFTER_MINUTES)
    agent_alive = bool(heartbeats)
    idle = idle_points[-1]["Maximum"] if idle_points else None
    active_users = user_points[-1]["Maximum"] if user_points else None
    result.update({"agent_alive": agent_alive, "idle_minutes": idle, "active_users": active_users})

    # Hard time limit: applies from boot (or the last reset), including while the instance
    # is still setting up.
    if MAX_UPTIME_MINUTES > 0:
        warn_before = _warn_before_minutes()
        if hard_elapsed >= MAX_UPTIME_MINUTES + STOP_GRACE_MINUTES:
            # The agent should have powered off at the limit; this is the backstop.
            _stop(f"hard limit of {MAX_UPTIME_MINUTES} minutes reached ({hard_elapsed:.0f} minutes since the "
                  f"{'last timer reset' if hard_start != instance['LaunchTime'] else 'instance started'})")
            result["action"] = "stop"
        elif 0 <= hard_elapsed - (MAX_UPTIME_MINUTES - warn_before) < CHECK_MINUTES:
            who = "unknown" if active_users is None else f"{active_users:.0f}"
            _alert(
                f"AI lab will stop in about {warn_before} minutes",
                f"Instance {INSTANCE_ID} reaches its hard time limit of {MAX_UPTIME_MINUTES} minutes in about "
                f"{warn_before} minutes and will be stopped even if people are using it "
                f"(active Open WebUI users: {who}). Start it again afterwards to continue.",
            )
            result["alerted"] = True

    # Idle shutdown: leave a freshly started instance alone while it sets up.
    if result["action"] != "stop" and IDLE_MINUTES > 0 and uptime >= SETUP_GRACE_MINUTES:
        if agent_alive and idle is not None and idle >= IDLE_MINUTES + STOP_GRACE_MINUTES:
            # The agent says nobody is active but the instance is still up: its own
            # shutdown failed. Safe to stop, because the idle count already excludes
            # active users.
            _stop(f"agent reports {idle:.0f} idle minutes (limit {IDLE_MINUTES}) but the instance is still running")
            result["action"] = "stop"
        elif not agent_alive and uptime >= IDLE_MINUTES:
            requests = _alb_requests(now, IDLE_MINUTES)
            if requests == 0:
                _stop(f"agent silent and no load balancer requests for {IDLE_MINUTES} minutes")
                result["action"] = "stop"
            else:
                reason = "no load balancer metric to confirm it is unused" if requests is None else "load balancer still shows requests"
                _alert(
                    "AI lab idle monitor is not reporting",
                    f"Instance {INSTANCE_ID} has run {uptime:.0f} minutes and its idle monitor has been silent for "
                    f"{SILENT_AFTER_MINUTES}+ minutes. It was not stopped because {reason}. Check the instance.",
                )
                result["action"] = "alert"

    print(json.dumps(result))
    return result
