"""Offline tests for lambda/auto_stop_watchdog.py. Run: python -m unittest discover -s tests"""

import datetime
import importlib.util
import os
import pathlib
import unittest
from unittest import mock

from botocore.exceptions import ClientError, EndpointConnectionError

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
    def run_handler(self, *, state="running", uptime=120, agent=None, alb=None, alb_dimension="app/lab/abc", idle=60, cap=0,
                    reset_minutes_ago=None, reset_value=None, reset_error=None):
        """agent: None for silent, else dict(idle=, users=). alb: request count, None for no data.
        idle: idle shutdown minutes (0 = off). cap: hard limit minutes (0 = none).
        reset_minutes_ago: the control panel's timer reset happened that many minutes ago
        (None = never reset, the parameter does not exist). reset_value: a raw parameter
        value instead. reset_error: an AWS error code the parameter read fails with."""
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

        ssm = mock.Mock()
        if reset_error or (reset_minutes_ago is None and reset_value is None):
            code = reset_error or "ParameterNotFound"
            if code == "network":
                ssm.get_parameter.side_effect = EndpointConnectionError(endpoint_url="https://ssm.example")
            else:
                ssm.get_parameter.side_effect = ClientError({"Error": {"Code": code, "Message": "x"}}, "GetParameter")
        else:
            value = reset_value if reset_value is not None else str(int((NOW - datetime.timedelta(minutes=reset_minutes_ago)).timestamp()))
            ssm.get_parameter.return_value = {"Parameter": {"Value": value}}

        with mock.patch.multiple(
            watchdog,
            ec2=ec2,
            cloudwatch=cloudwatch,
            sns=sns,
            ssm=ssm,
            RESET_PARAMETER="/proj/auto-stop/reset-at",
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

    # --- timer reset from the control panel
    def test_a_reset_gives_another_full_period(self):
        # Started 130 minutes ago with a 90 minute limit, but reset 10 minutes ago.
        result, ec2, _ = self.run_handler(uptime=130, agent={"idle": 0, "users": 3}, idle=0, cap=90, reset_minutes_ago=10)
        self.assertEqual(result["action"], "none")
        self.assertEqual(result["hard_limit_elapsed_minutes"], 10)
        ec2.stop_instances.assert_not_called()

    def test_the_extended_period_also_ends(self):
        result, ec2, _ = self.run_handler(uptime=400, agent={"idle": 0, "users": 3}, idle=0, cap=90, reset_minutes_ago=96)
        self.assertEqual(result["action"], "stop")
        ec2.stop_instances.assert_called_once_with(InstanceIds=["i-0123"])

    def test_a_reset_from_an_earlier_run_is_ignored(self):
        # Reset 500 minutes ago, but this instance launched 96 minutes ago: launch time wins.
        result, ec2, _ = self.run_handler(uptime=96, agent={"idle": 0, "users": 3}, idle=0, cap=90, reset_minutes_ago=500)
        self.assertEqual(result["action"], "stop")
        ec2.stop_instances.assert_called_once()

    def test_the_warning_moves_with_the_new_deadline(self):
        # 62 minutes since the reset is the 30 minute warning point for a 90 minute limit,
        # even though the instance has been up for 200 minutes.
        result, ec2, sns = self.run_handler(uptime=200, agent={"idle": 0, "users": 3}, idle=0, cap=90, reset_minutes_ago=62)
        self.assertTrue(result.get("alerted"))
        ec2.stop_instances.assert_not_called()
        sns.publish.assert_called_once()

    def test_no_warning_at_the_old_deadline_after_a_reset(self):
        # Without the reset the 62 minute warning point would be now; with it, no warning.
        result, _, sns = self.run_handler(uptime=62, agent={"idle": 0, "users": 3}, idle=0, cap=90, reset_minutes_ago=5)
        self.assertFalse(result.get("alerted"))
        sns.publish.assert_not_called()

    def test_a_slightly_future_reset_counts_as_now(self):
        soon = str(int((NOW + datetime.timedelta(minutes=2)).timestamp()))
        result, ec2, _ = self.run_handler(uptime=200, agent={"idle": 0, "users": 3}, idle=0, cap=90, reset_value=soon)
        self.assertEqual(result["hard_limit_elapsed_minutes"], 0)
        ec2.stop_instances.assert_not_called()

    def test_a_far_future_reset_is_ignored_and_the_limit_still_applies(self):
        future = str(int((NOW + datetime.timedelta(days=30)).timestamp()))
        result, ec2, _ = self.run_handler(uptime=200, agent={"idle": 0, "users": 3}, idle=0, cap=90, reset_value=future)
        self.assertEqual(result["action"], "stop")
        ec2.stop_instances.assert_called_once()

    def test_huge_reset_values_do_not_crash_the_watchdog(self):
        for value in ("253402300800", "99999999999999999999", "9" * 400, "-5", "1e12", "\u0661\u0662"):
            with self.subTest(value=value):
                result, ec2, _ = self.run_handler(uptime=96, agent={"idle": 0, "users": 3}, idle=0, cap=90, reset_value=value)
                self.assertEqual(result["action"], "stop")
                ec2.stop_instances.assert_called_once()

    def test_a_network_error_reading_the_reset_falls_back_to_the_launch_time(self):
        result, ec2, _ = self.run_handler(uptime=96, agent={"idle": 0, "users": 3}, idle=0, cap=90, reset_error="network")
        self.assertEqual(result["action"], "stop")
        ec2.stop_instances.assert_called_once()

    def test_an_unreadable_reset_falls_back_to_the_launch_time(self):
        result, ec2, _ = self.run_handler(uptime=96, agent={"idle": 0, "users": 3}, idle=0, cap=90, reset_error="AccessDeniedException")
        self.assertEqual(result["action"], "stop")
        ec2.stop_instances.assert_called_once()

    def test_a_garbage_reset_value_is_ignored(self):
        result, ec2, _ = self.run_handler(uptime=96, agent={"idle": 0, "users": 3}, idle=0, cap=90, reset_value="soon")
        self.assertEqual(result["action"], "stop")
        ec2.stop_instances.assert_called_once()

    def test_a_reset_does_not_change_idle_shutdown(self):
        result, ec2, _ = self.run_handler(uptime=200, agent={"idle": 65, "users": 0}, idle=60, cap=0, reset_minutes_ago=1)
        self.assertEqual(result["action"], "stop")
        ec2.stop_instances.assert_called_once()

    def test_no_limit_means_the_reset_is_never_read(self):
        with mock.patch.object(watchdog, "ssm") as ssm:
            ssm.get_parameter.side_effect = AssertionError("must not be read")
            result, _, _ = self.run_handler(uptime=200, agent={"idle": 0, "users": 3}, idle=0, cap=0)
        self.assertEqual(result["action"], "none")


if __name__ == "__main__":
    unittest.main()
