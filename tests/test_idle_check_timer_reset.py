"""Offline tests for the hard-limit timer reset in scripts/ai-lab-idle-check.sh.

MOCKED: the real script runs under bash, but every program that would touch AWS or the
instance (aws, curl for the metadata service, docker, ss, systemctl, logger) is a stub on
PATH, and /proc/uptime, the settings file and the state directory are temp files. Nothing
here proves the real SSM, IMDS or systemd behaviour; that is for the manual smoke test.

Run: python -m unittest discover -s tests
"""

import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "ai-lab-idle-check.sh"
RESET_PARAMETER = "/proj/auto-stop/reset-at"

STUBS = {
    # Instance metadata service: a token, then the instance id and region.
    "curl": """#!/usr/bin/env bash
case "$*" in
  *instance-id*) echo i-0123 ;;
  *placement/region*) echo us-east-1 ;;
  *) echo token ;;
esac
""",
    "aws": """#!/usr/bin/env bash
echo "$*" >>"$STUB_DIR/aws-calls"
case "$*" in
  *"ssm get-parameter"*"/proj/auto-stop/reset-at"*)
    case "$RESET_MODE" in
      value) echo "$RESET_VALUE" ;;
      missing) echo "An error occurred (ParameterNotFound) when calling the GetParameter operation" >&2; exit 254 ;;
      *) echo "An error occurred (AccessDeniedException) when calling the GetParameter operation" >&2; exit 254 ;;
    esac ;;
  *"ssm get-parameter"*) echo "$CONFIG_JSON" ;;
  *) exit 0 ;;
esac
""",
    "docker": "#!/usr/bin/env bash\nexit 1\n",
    "ss": "#!/usr/bin/env bash\nexit 0\n",
    "systemctl": '#!/usr/bin/env bash\necho "$*" >>"$STUB_DIR/systemctl-calls"\n',
    "logger": '#!/usr/bin/env bash\necho "$*" >>"$STUB_DIR/logger-calls"\n',
}


class IdleCheckResetTest(unittest.TestCase):
    def setUp(self):
        if not shutil.which("bash") or not shutil.which("jq"):
            self.skipTest("needs bash and jq")
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name, body in STUBS.items():
            path = self.bin / name
            path.write_text(body)
            path.chmod(0o755)
        (self.tmp / "auto-stop.env").write_text(
            "OPEN_WEBUI_VOLUME=open-webui\nOLLAMA_PORT=11434\nAUTO_STOP_PARAMETER=/proj/auto-stop\n"
        )
        (self.tmp / "ready").write_text("")
        (self.tmp / "state").mkdir()

    def run_script(self, *, uptime_minutes, max_minutes=120, reset=None):
        """reset: None = parameter missing, an int = epoch seconds, 'error' = unreadable."""
        (self.tmp / "uptime").write_text(f"{uptime_minutes * 60 + 5}.00 1.00\n")
        for stale in ("systemctl-calls", "logger-calls", "aws-calls"):
            (self.tmp / stale).unlink(missing_ok=True)
        env = dict(
            os.environ,
            PATH=f"{self.bin}:{os.environ['PATH']}",
            STUB_DIR=str(self.tmp),
            AI_LAB_AUTO_STOP_ENV=str(self.tmp / "auto-stop.env"),
            AI_LAB_READY_FILE=str(self.tmp / "ready"),
            AI_LAB_STATE_DIR=str(self.tmp / "state"),
            AI_LAB_UPTIME_FILE=str(self.tmp / "uptime"),
            CONFIG_JSON=json.dumps({"enabled": True, "idle_minutes": 0, "max_uptime_minutes": max_minutes}),
            RESET_MODE="missing" if reset is None else "error" if reset == "error" else "value",
            RESET_VALUE="" if reset in (None, "error") else str(reset),
        )
        done = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        powered_off = (self.tmp / "systemctl-calls").exists() and "poweroff" in (self.tmp / "systemctl-calls").read_text()
        logged = (self.tmp / "logger-calls").read_text() if (self.tmp / "logger-calls").exists() else ""
        aws_calls = (self.tmp / "aws-calls").read_text() if (self.tmp / "aws-calls").exists() else ""
        return powered_off, done.stdout, logged, aws_calls

    @staticmethod
    def minutes_ago(minutes):
        return int(time.time()) - minutes * 60

    def test_without_a_reset_the_limit_counts_from_boot(self):
        off, out, _, _ = self.run_script(uptime_minutes=130, reset=None)
        self.assertTrue(off)
        self.assertIn("hard_limit_minutes=130/120", out)

    def test_below_the_limit_nothing_happens(self):
        off, _, _, _ = self.run_script(uptime_minutes=90, reset=None)
        self.assertFalse(off)

    def test_a_reset_gives_another_full_period(self):
        off, out, _, _ = self.run_script(uptime_minutes=130, reset=self.minutes_ago(10))
        self.assertFalse(off)
        self.assertIn("hard_limit_minutes=10/120", out)

    def test_the_extended_period_also_ends(self):
        off, out, _, _ = self.run_script(uptime_minutes=400, reset=self.minutes_ago(121))
        self.assertTrue(off)
        self.assertIn("hard_limit_minutes=121/120", out)

    def test_a_reset_from_an_earlier_run_is_ignored(self):
        # The reset is older than this boot (uptime 130 minutes), so boot time wins.
        off, out, _, _ = self.run_script(uptime_minutes=130, reset=self.minutes_ago(500))
        self.assertTrue(off)
        self.assertIn("hard_limit_minutes=130/120", out)

    def test_a_slightly_future_reset_counts_as_now(self):
        # Within the clock-skew allowance (5 minutes).
        off, out, _, _ = self.run_script(uptime_minutes=130, reset=int(time.time()) + 120)
        self.assertFalse(off)
        self.assertIn("hard_limit_minutes=0/120", out)

    def test_a_far_future_reset_is_ignored_and_the_limit_still_applies(self):
        off, out, logged, _ = self.run_script(uptime_minutes=130, reset=int(time.time()) + 100000)
        self.assertTrue(off)
        self.assertIn("hard_limit_minutes=130/120", out)
        self.assertIn("ignoring an invalid or future timer reset", logged)

    def test_a_value_too_large_for_bash_cannot_disable_the_limit(self):
        off, out, _, _ = self.run_script(uptime_minutes=300, reset="99999999999999999999")
        self.assertTrue(off)
        self.assertIn("hard_limit_minutes=300/120", out)

    def test_leading_zeros_are_read_as_decimal_not_octal(self):
        # "089" is not valid octal; bash would error on it without the 10# prefix.
        off, out, _, _ = self.run_script(uptime_minutes=130, reset="089")
        self.assertTrue(off)
        self.assertIn("hard_limit_minutes=130/120", out)
        padded = str(self.minutes_ago(10)).zfill(10)
        off, out, _, _ = self.run_script(uptime_minutes=130, reset=padded)
        self.assertFalse(off)

    def test_a_reset_of_zero_is_not_logged_as_invalid(self):
        off, _, logged, _ = self.run_script(uptime_minutes=130, reset=0)
        self.assertTrue(off)
        self.assertNotIn("ignoring", logged)

    def test_an_unreadable_reset_skips_the_hard_limit_for_that_minute(self):
        off, _, logged, _ = self.run_script(uptime_minutes=300, reset="error")
        self.assertFalse(off)
        self.assertIn("could not read the timer reset", logged)

    def test_a_garbage_reset_value_is_ignored(self):
        off, _, _, _ = self.run_script(uptime_minutes=130, reset="notanumber")
        self.assertTrue(off)

    def test_no_hard_limit_means_no_reset_lookup_and_no_poweroff(self):
        off, _, _, aws_calls = self.run_script(uptime_minutes=500, max_minutes=0, reset=self.minutes_ago(1))
        self.assertFalse(off)
        self.assertNotIn("reset-at", aws_calls)


if __name__ == "__main__":
    unittest.main()
