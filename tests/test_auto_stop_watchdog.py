"""Offline tests for lambda/auto_stop_watchdog.py. Run: python -m unittest discover -s tests"""

import datetime
import importlib.util
import os
import pathlib
import unittest
from unittest import mock

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.update(
    INSTANCE_ID="i-0123",
    IDLE_MINUTES="60",
    MAX_UPTIME_HOURS="8",
    CHECK_MINUTES="5",
    SNS_TOPIC_ARN="arn:aws:sns:us-east-1:111111111111:topic",
    ALB_DIMENSION="app/lab/abc",
)

path = pathlib.Path(__file__).resolve().parent.parent / "lambda" / "auto_stop_watchdog.py"
spec = importlib.util.spec_from_file_location("watchdog", path)
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)

NOW = datetime.datetime(2026, 10, 3, 20, 0, tzinfo=datetime.timezone.utc)


def point(value, stat):
    return {"Timestamp": NOW, stat: value}


class WatchdogTest(unittest.TestCase):
    def run_handler(self, *, state="running", uptime=120, agent=None, alb=None, alb_dimension="app/lab/abc", enforce=False):
        """agent: None for silent, else dict(idle=, users=). alb: request count, None for no data."""
        instance = {"State": {"Name": state}, "LaunchTime": NOW - datetime.timedelta(minutes=uptime)}
        ec2 = mock.Mock()
        ec2.describe_instances.return_value = {"Reservations": [{"Instances": [instance]}]}
        cloudwatch = mock.Mock()
        sns = mock.Mock()

        def stats(**kwargs):
            name = kwargs["MetricName"]
            if kwargs["Namespace"] == "AWS/ApplicationELB":
                return {"Datapoints": [] if alb is None else [{"Timestamp": NOW, "Sum": alb}]}
            if agent is None:
                return {"Datapoints": []}
            values = {
                "Heartbeat": point(1, "Sum"),
                "IdleMinutes": point(agent["idle"], "Maximum"),
                "ActiveUsers": point(agent["users"], "Maximum"),
            }
            return {"Datapoints": [values[name]]}

        cloudwatch.get_metric_statistics.side_effect = stats
        with mock.patch.multiple(watchdog, ec2=ec2, cloudwatch=cloudwatch, sns=sns, _now=lambda: NOW, ALB_DIMENSION=alb_dimension, ENFORCE_MAX_UPTIME=enforce):
            result = watchdog.lambda_handler({}, None)
        return result, ec2, sns

    def test_stopped_instance_is_left_alone(self):
        result, ec2, sns = self.run_handler(state="stopped")
        self.assertEqual(result["action"], "none")
        ec2.stop_instances.assert_not_called()

    def test_new_instance_gets_setup_grace(self):
        result, ec2, _ = self.run_handler(uptime=10, agent=None, alb=0)
        self.assertEqual(result["action"], "none")
        ec2.stop_instances.assert_not_called()

    def test_active_users_are_never_stopped(self):
        result, ec2, _ = self.run_handler(uptime=600, agent={"idle": 0, "users": 3})
        self.assertEqual(result["action"], "none")
        ec2.stop_instances.assert_not_called()

    def test_failed_agent_shutdown_is_stopped(self):
        result, ec2, _ = self.run_handler(uptime=200, agent={"idle": 65, "users": 0})
        self.assertEqual(result["action"], "stop")
        ec2.stop_instances.assert_called_once_with(InstanceIds=["i-0123"])

    def test_idle_within_limit_is_left_to_the_agent(self):
        result, ec2, _ = self.run_handler(uptime=200, agent={"idle": 59, "users": 0})
        self.assertEqual(result["action"], "none")
        ec2.stop_instances.assert_not_called()

    def test_silent_agent_and_quiet_alb_is_stopped(self):
        result, ec2, _ = self.run_handler(uptime=200, agent=None, alb=0)
        self.assertEqual(result["action"], "stop")
        ec2.stop_instances.assert_called_once()

    def test_silent_agent_with_alb_traffic_only_alerts(self):
        result, ec2, sns = self.run_handler(uptime=200, agent=None, alb=40)
        self.assertEqual(result["action"], "alert")
        ec2.stop_instances.assert_not_called()
        sns.publish.assert_called_once()

    def test_silent_agent_without_alb_metric_only_alerts(self):
        result, ec2, sns = self.run_handler(uptime=200, agent=None, alb_dimension="")
        self.assertEqual(result["action"], "alert")
        ec2.stop_instances.assert_not_called()

    def test_max_uptime_alerts_but_does_not_stop_active_users(self):
        result, ec2, sns = self.run_handler(uptime=8 * 60 + 2, agent={"idle": 0, "users": 2})
        self.assertEqual(result["action"], "none")
        self.assertTrue(result.get("alerted"))
        ec2.stop_instances.assert_not_called()
        sns.publish.assert_called_once()

    def test_max_uptime_alert_repeats_hourly_not_every_run(self):
        _, _, sns = self.run_handler(uptime=8 * 60 + 25, agent={"idle": 0, "users": 2})
        sns.publish.assert_not_called()
        _, _, sns = self.run_handler(uptime=9 * 60 + 1, agent={"idle": 0, "users": 2})
        sns.publish.assert_called_once()

    def test_enforced_max_uptime_is_left_to_the_agent_at_first(self):
        result, ec2, _ = self.run_handler(uptime=8 * 60 + 2, agent={"idle": 0, "users": 3}, enforce=True)
        self.assertEqual(result["action"], "none")
        ec2.stop_instances.assert_not_called()

    def test_enforced_max_uptime_is_stopped_even_with_active_users(self):
        result, ec2, _ = self.run_handler(uptime=8 * 60 + 6, agent={"idle": 0, "users": 3}, enforce=True)
        self.assertEqual(result["action"], "stop")
        ec2.stop_instances.assert_called_once_with(InstanceIds=["i-0123"])

    def test_enforced_max_uptime_warns_thirty_minutes_ahead(self):
        result, ec2, sns = self.run_handler(uptime=7 * 60 + 32, agent={"idle": 0, "users": 3}, enforce=True)
        self.assertEqual(result["action"], "none")
        self.assertTrue(result.get("alerted"))
        ec2.stop_instances.assert_not_called()
        sns.publish.assert_called_once()

    def test_enforced_max_uptime_does_not_warn_every_run(self):
        _, _, sns = self.run_handler(uptime=7 * 60 + 40, agent={"idle": 0, "users": 3}, enforce=True)
        sns.publish.assert_not_called()
        _, _, sns = self.run_handler(uptime=7 * 60, agent={"idle": 0, "users": 3}, enforce=True)
        sns.publish.assert_not_called()

    def test_enforced_max_uptime_does_not_send_hourly_alerts(self):
        _, _, sns = self.run_handler(uptime=9 * 60 + 1, agent={"idle": 0, "users": 3}, enforce=True)
        sns.publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
