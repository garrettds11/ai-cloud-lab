"""Independent watchdog for the AI lab auto-stop.

Runs every few minutes from EventBridge. It never stops an instance that people
are using, and it flags an instance that has been left running:

* The instance-side agent normally powers the instance off when nobody is active.
  If the agent reports the lab idle well past the limit but the instance is still
  running, the shutdown failed, so stop it here.
* If the agent has gone silent, activity cannot be read from the instance. Stop it
  only when the load balancer also shows no requests for the whole idle window;
  otherwise send an alert and leave it running.
* If the instance has been up longer than the maximum uptime, send an alert (never
  a forced stop), repeating hourly.
"""

import datetime
import json
import os

import boto3

INSTANCE_ID = os.environ.get("INSTANCE_ID", "")
IDLE_MINUTES = int(os.environ.get("IDLE_MINUTES", "60"))
MAX_UPTIME_MINUTES = int(os.environ.get("MAX_UPTIME_HOURS", "8")) * 60
CHECK_MINUTES = int(os.environ.get("CHECK_MINUTES", "5"))
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "")
ALB_DIMENSION = os.environ.get("ALB_DIMENSION", "")  # e.g. app/name/1234567890abcdef

STOP_GRACE_MINUTES = 5  # extra idle time before the watchdog steps in after the agent
SETUP_GRACE_MINUTES = 30  # leave a freshly started instance alone while it sets up
SILENT_AFTER_MINUTES = 10  # no heartbeat for this long means the agent is silent

ec2 = boto3.client("ec2")
cloudwatch = boto3.client("cloudwatch")
sns = boto3.client("sns")


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


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

    if uptime < SETUP_GRACE_MINUTES:
        result["note"] = "within setup grace period"
        print(json.dumps(result))
        return result

    heartbeats = _agent_points("Heartbeat", "Sum", now, SILENT_AFTER_MINUTES)
    idle_points = _agent_points("IdleMinutes", "Maximum", now, SILENT_AFTER_MINUTES)
    user_points = _agent_points("ActiveUsers", "Maximum", now, SILENT_AFTER_MINUTES)
    agent_alive = bool(heartbeats)
    idle = idle_points[-1]["Maximum"] if idle_points else None
    active_users = user_points[-1]["Maximum"] if user_points else None
    result.update({"agent_alive": agent_alive, "idle_minutes": idle, "active_users": active_users})

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

    if result["action"] != "stop" and uptime >= MAX_UPTIME_MINUTES:
        # Repeat roughly once an hour.
        if (uptime - MAX_UPTIME_MINUTES) % 60 < CHECK_MINUTES:
            who = "unknown" if active_users is None else f"{active_users:.0f}"
            _alert(
                "AI lab instance has been running a long time",
                f"Instance {INSTANCE_ID} has run {uptime / 60:.1f} hours (alert threshold {MAX_UPTIME_MINUTES // 60}h). "
                f"Active Open WebUI users: {who}. It is not stopped while anyone is active and stops "
                f"after {IDLE_MINUTES} idle minutes.",
            )
            result["alerted"] = True

    print(json.dumps(result))
    return result
