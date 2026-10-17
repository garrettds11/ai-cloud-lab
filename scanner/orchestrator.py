"""Five-minute Greenbone controller and SQS on-demand entry point.

Only one Lambda invocation is allowed at a time. DynamoDB holds the last successful
scan and the active SSM command, so a restart or missed tick resumes safely.
"""

import json
import os
import shlex
import time
import uuid

import boto3
from botocore.exceptions import ClientError


ec2 = boto3.client("ec2")
ssm = boto3.client("ssm")
s3 = boto3.client("s3")
sns = boto3.client("sns")
ddb = boto3.resource("dynamodb")
TABLE = ddb.Table(os.environ["STATE_TABLE"])
LAB = os.environ["LAB_INSTANCE_ID"]
SCANNER = os.environ["SCANNER_INSTANCE_ID"]
BUCKET = os.environ["REPORT_BUCKET"]
INTERVAL = int(os.environ["SCAN_INTERVAL_SECONDS"])
TIMEOUT = int(os.environ["SCAN_TIMEOUT_SECONDS"])


def now():
    return int(time.time())


def load():
    return TABLE.get_item(Key={"pk": "scanner"}, ConsistentRead=True).get("Item", {"pk": "scanner"})


def save(state):
    TABLE.put_item(Item=state)


def instance_states():
    out = ec2.describe_instances(InstanceIds=[LAB, SCANNER])
    return {inst["InstanceId"]: inst["State"]["Name"]
            for reservation in out["Reservations"] for inst in reservation["Instances"]}


def online(instance_id):
    out = ssm.describe_instance_information(Filters=[{"Key": "InstanceIds", "Values": [instance_id]}])
    return any(i.get("PingStatus") == "Online" for i in out.get("InstanceInformationList", []))


def command(instance_id, script, timeout=3600):
    out = ssm.send_command(
        InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
        Parameters={"commands": [script], "executionTimeout": [str(timeout)]},
        TimeoutSeconds=600,
        Comment="AI Cloud Lab vulnerability scan",
    )
    return out["Command"]["CommandId"]


def command_status(instance_id, command_id):
    try:
        return ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)["Status"]
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "InvocationDoesNotExist":
            return "Pending"
        raise


def starter_event():
    table_name = os.environ.get("EVENTS_TABLE")
    if not table_name:
        return
    millis = now() * 1000
    ddb.Table(table_name).put_item(Item={
        "pk": "starts", "sk": f"start#{millis:013d}#{uuid.uuid4().hex[:8]}",
        "t": millis, "expiresAt": now() + 90 * 86400,
        "userId": "scanner:greenbone", "userName": "Security scanner",
        "instanceId": LAB,
    })


def notify(status, state, detail=""):
    message = {
        "status": status, "run_id": state.get("run_id"),
        "report_prefix": f"s3://{BUCKET}/runs/{state.get('run_id')}/" if state.get("run_id") else None,
        "detail": detail[:500], "at": now(),
        "note": "Success means assessment execution completed. Review reports for vulnerabilities and configuration findings.",
    }
    sns.publish(TopicArn=os.environ["RESULTS_TOPIC_ARN"],
                Subject=f"AI Cloud Lab vulnerability scan {status}",
                Message=json.dumps(message))


def finish(state, success, detail=""):
    # The controller stops touching the lab's idle clock. Its existing policy
    # handles a scan-initiated start without cutting off a user who signed in.
    try:
        if state.get("scanner_started") and instance_states().get(SCANNER) == "running":
            ec2.stop_instances(InstanceIds=[SCANNER])
    except ClientError as exc:
        detail += f"; scanner stop failed: {exc.response.get('Error', {}).get('Code', 'AWS error')}"

    if success:
        state["last_success"] = now()
        state.pop("last_error", None)
        state.pop("next_retry", None)
    else:
        state["last_error"] = detail[:500]
        state["next_retry"] = now() + 6 * 3600
    state["last_finished"] = now()
    state.pop("stage", None)
    state.pop("command_id", None)
    state.pop("setup_command_id", None)
    notify("succeeded" if success else "failed", state, detail)
    state.pop("run_id", None)
    state.pop("started_at", None)
    state.pop("scanner_started", None)
    state.pop("lab_started", None)
    state.pop("starter_logged", None)
    save(state)
    return state


def start_stage(state):
    if state["lab_started"] and not state.get("starter_logged"):
        # Duplicate delivery after a crash would still identify the same
        # scanner, so recording before StartInstances is the safe order.
        starter_event()
        state["starter_logged"] = True
        save(state)
    states = instance_states()
    for instance_id in (LAB, SCANNER):
        if states[instance_id] == "stopped":
            ec2.start_instances(InstanceIds=[instance_id])
    state["stage"] = "waiting_online"
    save(state)
    return state


def begin(state):
    states = instance_states()
    if states.get(LAB) not in {"running", "stopped"} or states.get(SCANNER) not in {"running", "stopped"}:
        return state
    state.update({
        "run_id": str(uuid.uuid4()), "started_at": now(),
        "stage": "starting", "lab_started": states[LAB] == "stopped",
        # The first Terraform boot provisions the scanner before any successful
        # run exists. The controller owns and stops that first run as well.
        "scanner_started": states[SCANNER] == "stopped" or "last_success" not in state,
        "pending_force": False,
    })
    save(state)
    return start_stage(state)


def setup_script():
    key = shlex.quote(os.environ["SSH_PUBLIC_KEY"])
    digest = shlex.quote(os.environ["IMAGE_DIGEST"])
    container = shlex.quote(os.environ["CONTAINER_NAME"])
    return "\n".join([
        "set -eu",
        "test -x /usr/sbin/sshd || { apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y openssh-server; }",
        "systemctl is-active --quiet ssh || systemctl enable --now ssh",
        "id svc_vulnscan >/dev/null 2>&1 || useradd --create-home --shell /bin/bash svc_vulnscan",
        "passwd -l svc_vulnscan >/dev/null 2>&1 || true",
        "install -d -m 0700 -o svc_vulnscan -g svc_vulnscan /home/svc_vulnscan/.ssh",
        f"printf '%s\\n' {key} >/home/svc_vulnscan/.ssh/authorized_keys",
        "chown svc_vulnscan:svc_vulnscan /home/svc_vulnscan/.ssh/authorized_keys",
        "chmod 0600 /home/svc_vulnscan/.ssh/authorized_keys",
        f"test \"$(docker inspect --format '{{{{.Config.Image}}}}' {container})\" = {digest}",
        "install -d -m 0755 /run/ai-lab",
        "date +%s >/run/ai-lab/last-activity",
    ])


def advance(state):
    if now() - int(state["started_at"]) > TIMEOUT + 3 * 3600:
        return finish(state, False, "Scan exceeded the controller time limit")
    stage = state["stage"]
    if stage == "starting":
        return start_stage(state)
    states = instance_states()
    if states.get(LAB) == "stopped" or states.get(SCANNER) == "stopped":
        # A stop during an active scan is a failed scan and must not be hidden.
        return finish(state, False, "A scan target or the scanner stopped during the scan")

    # The lab's idle monitor owns its policy. Refresh only its existing activity
    # clock while an active scan is running; if the controller stops, the lab
    # returns to its normal bounded idle stop without a persistent override.
    if stage in {"setup_lab", "scanning"} and online(LAB):
        command(LAB, "date +%s >/run/ai-lab/last-activity", timeout=60)

    if stage == "waiting_online":
        if not (states.get(LAB) == states.get(SCANNER) == "running" and online(LAB) and online(SCANNER)):
            return state
        state["setup_command_id"] = command(LAB, setup_script())
        state["stage"] = "setup_lab"
        save(state)
        return state

    if stage == "setup_lab":
        status = command_status(LAB, state["setup_command_id"])
        if status in {"Pending", "InProgress", "Delayed"}:
            return state
        if status != "Success":
            return finish(state, False, f"Lab SSH account or image digest verification failed: {status}")
        run_id = state["run_id"]
        script = ("set -eu\n"
                  "for n in $(seq 1 360); do test -f /opt/ai-scanner/ready && break; sleep 10; done\n"
                  "test -f /opt/ai-scanner/ready\n"
                  f"python3 /opt/ai-scanner/run_scan.py {run_id}")
        state["command_id"] = command(SCANNER, script, timeout=TIMEOUT + 3 * 3600)
        state["stage"] = "scanning"
        save(state)
        return state

    if stage == "scanning":
        status = command_status(SCANNER, state["command_id"])
        if status in {"Pending", "InProgress", "Delayed"}:
            return state
        if status != "Success":
            return finish(state, False, f"Scanner SSM command: {status}")
        try:
            s3.head_object(Bucket=BUCKET, Key=f"runs/{state['run_id']}/manifest.json")
        except ClientError:
            return finish(state, False, "The scanner command completed without a report manifest")
        return finish(state, True)
    return finish(state, False, f"Unknown controller stage: {stage}")


def lambda_handler(event, context):
    demand = bool(event.get("Records"))
    state = load()
    if demand and state.get("stage"):
        state["pending_force"] = True
        save(state)
    if state.get("stage"):
        state = advance(state)
    elif (demand or state.get("pending_force") or
          (now() - int(state.get("last_success", 0)) >= INTERVAL
           and now() >= int(state.get("next_retry", 0)))):
        state = begin(state)
    return {"stage": state.get("stage", "idle"), "run_id": state.get("run_id"),
            "last_success": state.get("last_success")}
