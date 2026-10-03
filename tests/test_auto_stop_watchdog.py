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
    MAX_UPTIME_MINUTES="0",
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
    def run_handler(self, *, state="running", uptime=120, agent=None, alb=None, alb_dimension="app/lab/abc", idle=60, cap=0):
        """agent: None for silent, else dict(idle=, users=). alb: request count, None for no data.
        idle: idle shutdown minutes (0 = off). cap: hard limit minutes (0 = none)."""
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
        with mock.patch.multiple(
            watchdog,
            ec2=ec2,
            cloudwatch=cloudwatch,
            sns=sns,
            _now=lambda: NOW,
            ALB_DIMENSION=alb_dimension,
            IDLE_MINUTES=idle,
            MAX_UPTIME_MINUTES=cap,
        ):
            result = watchdog.lambda_handler({}, None)
        return result, ec2, sns

    # --- idle shutdown
    def test_stopped_instance_is_left_alone(self):
        result, ec2, _ = self.run_handler(state="stopped")
        self.assertEqual(result["action"], "none")
        ec2.stop_instances.assert_not_called()

    def test_new_instance_gets_setup_grace(self):
        result, ec2, _ = self.run_handler(uptime=10, agent=None, alb=0)
        self.assertEqual(result["action"], "none")
        ec2.stop_instances.assert_not_called()

    def test_active_users_are_never_stopped_by_idle_shutdown(self):
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
        result, ec2, _ = self.run_handler(uptime=200, agent=None, alb_dimension="")
        self.assertEqual(result["action"], "alert")
        ec2.stop_instances.assert_not_called()

    # --- both controls off
    def test_nothing_happens_when_idle_shutdown_and_limit_are_both_off(self):
        result, ec2, sns = self.run_handler(uptime=900, agent={"idle": 500, "users": 0}, idle=0, cap=0)
        self.assertEqual(result["action"], "none")
        ec2.stop_instances.assert_not_called()
        sns.publish.assert_not_called()

    # --- hard time limit
    def test_limit_is_left_to_the_agent_at_first(self):
        result, ec2, _ = self.run_handler(uptime=92, agent={"idle": 0, "users": 3}, idle=0, cap=90)
        self.assertEqual(result["action"], "none")
        ec2.stop_instances.assert_not_called()

    def test_limit_stops_even_with_active_users(self):
        result, ec2, _ = self.run_handler(uptime=96, agent={"idle": 0, "users": 3}, idle=0, cap=90)
        self.assertEqual(result["action"], "stop")
        ec2.stop_instances.assert_called_once_with(InstanceIds=["i-0123"])

    def test_limit_warns_thirty_minutes_ahead(self):
        result, ec2, sns = self.run_handler(uptime=62, agent={"idle": 0, "users": 3}, idle=0, cap=90)
        self.assertEqual(result["action"], "none")
        self.assertTrue(result.get("alerted"))
        ec2.stop_instances.assert_not_called()
        sns.publish.assert_called_once()

    def test_limit_does_not_warn_every_run(self):
        _, _, sns = self.run_handler(uptime=70, agent={"idle": 0, "users": 3}, idle=0, cap=90)
        sns.publish.assert_not_called()
        _, _, sns = self.run_handler(uptime=50, agent={"idle": 0, "users": 3}, idle=0, cap=90)
        sns.publish.assert_not_called()

    def test_short_limit_warns_ten_minutes_ahead(self):
        result, _, sns = self.run_handler(uptime=11, agent={"idle": 0, "users": 1}, idle=0, cap=20)
        self.assertTrue(result.get("alerted"))
        sns.publish.assert_called_once()

    def test_limit_applies_during_setup_grace(self):
        result, ec2, _ = self.run_handler(uptime=26, agent=None, idle=0, cap=20)
        self.assertEqual(result["action"], "stop")
        ec2.stop_instances.assert_called_once()

    def test_limit_only_mode_ignores_a_silent_agent(self):
        result, ec2, sns = self.run_handler(uptime=40, agent=None, alb=0, idle=0, cap=90)
        self.assertEqual(result["action"], "none")
        ec2.stop_instances.assert_not_called()
        sns.publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
