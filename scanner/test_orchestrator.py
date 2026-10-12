"""Offline state-machine tests; no AWS resources are created."""

import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch


class Table:
    def __init__(self):
        self.items = {}

    def get_item(self, Key, **kwargs):
        item = self.items.get(Key["pk"])
        return {"Item": dict(item)} if item else {}

    def put_item(self, Item):
        self.items[Item["pk"]] = dict(Item)


class DDB:
    def __init__(self):
        self.tables = {}

    def Table(self, name):
        return self.tables.setdefault(name, Table())


class EC2:
    def __init__(self):
        self.states = {"i-lab": "stopped", "i-scanner": "stopped"}

    def describe_instances(self, InstanceIds):
        return {"Reservations": [{"Instances": [
            {"InstanceId": i, "State": {"Name": self.states[i]}} for i in InstanceIds
        ]}]}

    def start_instances(self, InstanceIds):
        for i in InstanceIds:
            self.states[i] = "running"

    def stop_instances(self, InstanceIds):
        for i in InstanceIds:
            self.states[i] = "stopped"


class SSM:
    def __init__(self):
        self.commands = {}

    def describe_instance_information(self, Filters):
        return {"InstanceInformationList": [{"PingStatus": "Online"}]}

    def send_command(self, **kwargs):
        command_id = f"cmd-{len(self.commands) + 1}"
        self.commands[command_id] = {"instance": kwargs["InstanceIds"][0],
                                     "script": kwargs["Parameters"]["commands"][0],
                                     "status": "Success"}
        return {"Command": {"CommandId": command_id}}

    def get_command_invocation(self, CommandId, InstanceId):
        return {"Status": self.commands[CommandId]["status"]}


class S3:
    def head_object(self, **kwargs):
        return {}


class SNS:
    def __init__(self):
        self.messages = []

    def publish(self, **kwargs):
        self.messages.append(kwargs)


class OrchestratorTest(unittest.TestCase):
    def setUp(self):
        self.ec2, self.ssm, self.s3, self.sns, self.ddb = EC2(), SSM(), S3(), SNS(), DDB()
        clients = {"ec2": self.ec2, "ssm": self.ssm, "s3": self.s3, "sns": self.sns}
        env = {
            "AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
            "AWS_DEFAULT_REGION": "us-east-1", "STATE_TABLE": "state", "EVENTS_TABLE": "events",
            "LAB_INSTANCE_ID": "i-lab", "SCANNER_INSTANCE_ID": "i-scanner",
            "REPORT_BUCKET": "reports", "RESULTS_TOPIC_ARN": "arn:aws:sns:us-east-1:1:results",
            "SCAN_INTERVAL_SECONDS": "604800", "SCAN_TIMEOUT_SECONDS": "43200",
            "SSH_PUBLIC_KEY": "ssh-rsa AAAA test", "IMAGE_DIGEST": "image@sha256:" + "a" * 64,
            "CONTAINER_NAME": "open-webui",
        }
        self.env_patch = patch.dict(os.environ, env)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        with patch("boto3.client", side_effect=lambda name: clients[name]), \
                patch("boto3.resource", return_value=self.ddb):
            spec = importlib.util.spec_from_file_location("scanner_orchestrator", Path(__file__).with_name("orchestrator.py"))
            self.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.module)
        self.clock = 1_800_000_000
        self.module.now = lambda: self.clock

    def tick(self, demand=False):
        return self.module.lambda_handler({"Records": [{"body": "{}"}]} if demand else {}, None)

    def test_due_run_wakes_both_hosts_and_records_scanner_identity(self):
        self.assertEqual(self.tick()["stage"], "waiting_online")
        self.assertEqual(self.ec2.states, {"i-lab": "running", "i-scanner": "running"})
        start = next(iter(self.ddb.Table("events").items.values()))
        self.assertEqual(start["userId"], "scanner:greenbone")
        self.assertEqual(self.tick()["stage"], "setup_lab")
        setup = self.ssm.commands["cmd-1"]["script"]
        self.assertIn("svc_vulnscan", setup)
        self.assertIn("docker inspect", setup)
        self.assertIn("last-activity", setup)
        self.assertEqual(self.tick()["stage"], "scanning")
        self.assertEqual(self.tick()["stage"], "idle")
        state = self.ddb.Table("state").items["scanner"]
        self.assertEqual(state["last_success"], self.clock)
        self.assertEqual(self.ec2.states["i-scanner"], "stopped")
        self.assertEqual(self.ec2.states["i-lab"], "running")
        self.assertEqual(len(self.sns.messages), 1)
        self.assertEqual(self.tick()["stage"], "idle")  # no duplicate weekly run

    def test_demand_during_active_run_queues_another_run(self):
        self.tick()
        self.tick(demand=True)
        self.assertTrue(self.ddb.Table("state").items["scanner"]["pending_force"])
        self.tick()
        self.tick()
        self.assertEqual(self.tick()["stage"], "waiting_online")

    def test_failed_command_does_not_advance_last_success(self):
        self.tick()
        self.tick()
        self.tick()
        scanner_command = self.ddb.Table("state").items["scanner"]["command_id"]
        self.ssm.commands[scanner_command]["status"] = "Failed"
        self.assertEqual(self.tick()["stage"], "idle")
        state = self.ddb.Table("state").items["scanner"]
        self.assertNotIn("last_success", state)
        self.assertIn("Scanner SSM command", state["last_error"])
        self.assertEqual(self.tick()["stage"], "idle")  # bounded retry, no five-minute cost loop
        self.clock += 6 * 3600
        self.assertEqual(self.tick()["stage"], "waiting_online")

    def test_restart_recovers_a_start_interrupted_before_ec2_call(self):
        self.ddb.Table("state").put_item(Item={
            "pk": "scanner", "stage": "starting", "run_id": "run-1",
            "started_at": self.clock, "lab_started": True,
            "scanner_started": True, "pending_force": False,
        })
        self.assertEqual(self.tick()["stage"], "waiting_online")
        self.assertEqual(self.ec2.states["i-scanner"], "running")
        self.assertEqual(len(self.ddb.Table("events").items), 1)


if __name__ == "__main__":
    unittest.main()
