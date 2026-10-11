"""Offline tests for the lab's usage and metrics scripts:
scripts/ai-lab-session-log, ai-lab-usage, ai-lab-metrics and ai-lab-webui-lib.sh.

MOCKED: the real scripts run under bash. Everything outside them is a stub on PATH: curl is a small
fake Open WebUI / Ollama / metadata service that answers from a list of chat messages (it filters
by the start_date/end_date window the way Open WebUI's analytics does), aws returns a password,
sqlite3 runs the SQL with Python's sqlite3 on a real temporary database, and nvidia-smi prints a
fixed line. Nothing here proves the real Open WebUI v0.11.x responses, systemd ordering at shutdown
or IMDS; that is for the first live deploy (issue #76).

Run: python -m unittest discover -s tests
"""

import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
SESSION_LOG = SCRIPTS / "ai-lab-session-log"
USAGE = SCRIPTS / "ai-lab-usage"
METRICS = SCRIPTS / "ai-lab-metrics"
LIB = SCRIPTS / "ai-lab-webui-lib.sh"

CURL = r'''#!/usr/bin/env python3
import json, os, re, sys
args = sys.argv[1:]
cfg = json.load(open(os.environ["STUB_CONFIG"]))
out = None; fmt = None; method = "GET"; url = next(a for a in args if a.startswith("http"))
i = 0
while i < len(args):
    if args[i] == "-o": out = args[i + 1]; i += 1
    elif args[i] == "-w": fmt = args[i + 1]; i += 1
    elif args[i] == "-X": method = args[i + 1]; i += 1
    i += 1
open(os.environ["STUB_DIR"] + "/curl-calls", "a").write(f"{method} {url}\n")
def reply(status, body=""):
    if out: open(out, "w").write(body if isinstance(body, str) else json.dumps(body))
    elif status == 200: sys.stdout.write(body if isinstance(body, str) else json.dumps(body))
    if fmt: sys.stdout.write(str(status))
    sys.exit(0 if status in (200, 403, 401) or not fmt else 0)
if "169.254.169.254" in url:
    sys.stdout.write("i-0abc123" if "instance-id" in url else "token"); sys.exit(0)
if "11434" in url:
    if cfg.get("ollama") is None: sys.exit(7)
    sys.stdout.write(json.dumps(cfg["ollama"])); sys.exit(0)
path, _, query = url.split("127.0.0.1:8080", 1)[1].partition("?")
q = dict(p.split("=") for p in query.split("&") if p)
if path == "/api/v1/auths/signin":
    sys.stdin.read()
    if cfg.get("login") == 403: reply(403, {"detail": "no"})
    if cfg.get("login") == 401: reply(401, {})
    open(os.environ["WEBUI_DB_TOUCH"], "w").write("1")
    reply(200, {"token": "tok-1"})
if cfg.get("webui_down"): reply(503, "")
touch = os.environ.get("WEBUI_DB_TOUCH")
if touch and os.environ.get("WEBUI_DB"):
    import sqlite3, time
    c = sqlite3.connect(os.environ["WEBUI_DB"]); c.execute("update user set last_active_at=? where email='admin@lab.test'", (int(time.time()),)); c.commit()
if path == "/api/v1/auths/": reply(200, {"id": "u-admin"})
msgs = [m for m in cfg["messages"] if int(q.get("start_date", 0)) <= m["t"] <= int(q.get("end_date", 1 << 40))]
if path == "/api/v1/analytics/users":
    if cfg.get("bad_shape"): reply(200, {"oops": []})
    users = {}
    for m in msgs:
        u = users.setdefault(m["user_id"], {"user_id": m["user_id"], "name": m["name"], "email": m["email"], "count": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
        u["count"] += 1; u["input_tokens"] += m["in"]; u["output_tokens"] += m["out"]; u["total_tokens"] += m["in"] + m["out"]
    reply(200, {"users": list(users.values())})
if path == "/api/v1/analytics/tokens":
    models = {}
    for m in msgs:
        e = models.setdefault(m["model"], {"model_id": m["model"], "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "message_count": 0})
        e["input_tokens"] += m["in"]; e["output_tokens"] += m["out"]; e["total_tokens"] += m["in"] + m["out"]; e["message_count"] += 1
    reply(200, {"models": list(models.values()), "total_input_tokens": 0, "total_output_tokens": 0, "total_tokens": 0})
if path == "/api/v1/models":
    reply(200, {"data": cfg.get("model_list", [])})
reply(404, "")
'''

AWS = '#!/usr/bin/env bash\necho "s3cret-password"\n'
SQLITE = r'''#!/usr/bin/env python3
import sqlite3, sys
a = sys.argv[1:]; db = None; sql = None; i = 0; ro = False
while i < len(a):
    if a[i] == "-readonly": ro = True
    elif a[i] == "-cmd": i += 1
    elif db is None: db = a[i]
    else: sql = a[i]
    i += 1
c = sqlite3.connect(db)
cur = c.execute(sql); rows = cur.fetchall(); c.commit()
for r in rows: print("|".join(str(x) for x in r))
'''
NVIDIA = '#!/usr/bin/env bash\necho "0, 61, 37, 9120, 23034, 71.5, 72.0, 0x0000000000000004"\n'
DOCKER = '#!/usr/bin/env bash\nexit 1\n'
JOURNALCTL = r'''#!/usr/bin/env bash
[[ -e "$STUB_DIR/journal-fails" ]] && exit 1
[[ -r "$STUB_DIR/journal.txt" ]] && cat "$STUB_DIR/journal.txt"
exit 0
'''
LOGGER = '#!/usr/bin/env bash\necho "$*" >>"$STUB_DIR/logger-calls"\n'


def bash_ok():
    return shutil.which("bash") and shutil.which("jq")


class Base(unittest.TestCase):
    def setUp(self):
        if not bash_ok():
            self.skipTest("needs bash and jq")
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.data = self.tmp / "data"
        self.state = self.tmp / "run"
        self.state.mkdir()
        for name, text in (("curl", CURL), ("aws", AWS), ("sqlite3", SQLITE), ("docker", DOCKER), ("logger", LOGGER)):
            (self.bin / name).write_text(text)
            (self.bin / name).chmod(0o755)
        (self.bin / "nvidia-smi").write_text(NVIDIA)
        (self.bin / "nvidia-smi").chmod(0o755)
        self.settings = self.tmp / "vuln-mcp.env"
        self.settings.write_text("PORT='8080'\nREGION='us-east-1'\nADMIN_ARN='arn:aws:secretsmanager:us-east-1:1:secret:x'\n"
                                 "ADMIN_EMAIL='admin@lab.test'\nLOCAL_LOGIN='true'\n")
        self.autostop = self.tmp / "auto-stop.env"
        self.autostop.write_text("OPEN_WEBUI_VOLUME=vol\n")
        self.db = self.tmp / "webui.db"
        con = sqlite3.connect(self.db)
        con.execute("create table user (id text, email text, last_active_at integer)")
        self.admin_baseline = int(time.time()) - 3000
        con.execute("insert into user values ('u-admin','admin@lab.test',?)", (self.admin_baseline,))
        con.commit()
        con.close()
        (self.data).mkdir()
        (self.data / "ready").write_text("")
        self.cfg = {"messages": [], "ollama": None}
        self.write_cfg()

    def write_cfg(self):
        (self.tmp / "cfg.json").write_text(json.dumps(self.cfg))

    def env(self, **extra):
        env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", STUB_CONFIG=str(self.tmp / "cfg.json"),
                   STUB_DIR=str(self.tmp), WEBUI_DB=str(self.db), WEBUI_DB_TOUCH=str(self.tmp / "touch"),
                   AI_LAB_DATA_DIR=str(self.data), AI_LAB_STATE_DIR=str(self.state), AI_LAB_READY_FILE=str(self.data / "ready"),
                   AI_LAB_SETTINGS_FILE=str(self.settings), AI_LAB_AUTO_STOP_ENV=str(self.autostop),
                   AI_LAB_WEBUI_DB=str(self.db), AI_LAB_WEBUI_LIB=str(LIB),
                   AI_LAB_SESSION_LOG=str(SESSION_LOG), AI_LAB_BOOT_ID_FILE=str(self.tmp / "boot_id"),
                   AI_LAB_METRICS_DIR=str(self.tmp / "metrics"), AI_LAB_METRICS_ENV=str(self.tmp / "metrics.env"),
                   AI_LAB_OLLAMA_URL="http://127.0.0.1:11434")
        env.update(extra)
        return env

    def run_script(self, script, *args, **env):
        return subprocess.run(["bash", str(script), *args], env=self.env(**env), capture_output=True, text=True, timeout=60)

    def admin_last_active(self):
        con = sqlite3.connect(self.db)
        value = con.execute("select last_active_at from user where email='admin@lab.test'").fetchone()[0]
        con.close()
        return value


class SessionLogTest(Base):
    def boot(self, ident):
        (self.tmp / "boot_id").write_text(ident + "\n")

    def show(self):
        done = self.run_script(SESSION_LOG, "show")
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_a_clean_session_has_a_start_and_a_stop(self):
        self.boot("boot-a")
        self.assertEqual(self.show(), [])
        self.run_script(SESSION_LOG, "start")
        running = self.show()
        self.assertEqual(len(running), 1)
        self.assertEqual((running[0]["endedBy"], running[0]["stop"]), ("running", None))
        self.run_script(SESSION_LOG, "stop")
        done = self.show()
        self.assertEqual((done[0]["endedBy"], done[0]["boot"]), ("shutdown", "boot-a"))
        self.assertGreaterEqual(done[0]["stop"], done[0]["start"])

    def test_a_crash_is_closed_at_the_last_heartbeat_by_the_next_start(self):
        self.boot("boot-a")
        self.run_script(SESSION_LOG, "start")
        start = self.show()[0]["start"]
        (self.data / "session-heartbeat").write_text(f"{start + 600}\n")  # last sign of life
        self.boot("boot-b")  # power loss: no stop line, then a new boot
        self.assertEqual(self.show()[0]["endedBy"], "lost")  # visible even before the next start runs
        self.run_script(SESSION_LOG, "start")
        sessions = self.show()
        self.assertEqual([s["endedBy"] for s in sessions], ["lost", "running"])
        self.assertEqual(sessions[0]["stop"], start + 600)

    def test_stop_from_another_boot_or_with_nothing_open_changes_nothing(self):
        self.boot("boot-a")
        self.run_script(SESSION_LOG, "stop")
        self.assertEqual(self.show(), [])
        self.run_script(SESSION_LOG, "start")
        self.boot("boot-b")
        self.run_script(SESSION_LOG, "stop")
        self.assertEqual(self.show()[0]["endedBy"], "lost")

    def test_heartbeat_notes_the_time_and_unknown_commands_are_refused(self):
        self.run_script(SESSION_LOG, "heartbeat")
        self.assertTrue((self.data / "session-heartbeat").read_text().strip().isdigit())
        self.assertEqual(self.run_script(SESSION_LOG, "bogus").returncode, 2)


def msg(user, t, tin, tout, model="qwen3:14b"):
    names = {"u1": ("Ann", "ann@lab.test"), "u2": ("Bo", "bo@lab.test")}
    name, email = names.get(user, (f"User {user}", f"{user}@lab.test"))
    return {"user_id": user, "name": name, "email": email, "t": t, "in": tin, "out": tout, "model": model}


class UsageTest(Base):
    def setUp(self):
        super().setUp()
        now = int(time.time())
        self.this_hour = now // 3600 * 3600
        h = self.this_hour
        self.cfg["messages"] = [
            msg("u1", h - 7200 + 10, 100, 400), msg("u2", h - 7200 + 20, 50, 150),
            msg("u1", h - 3600 + 5, 10, 90),
            msg("u1", h + 1, 7, 3),  # the hour in progress
        ]
        self.write_cfg()
        # A session that began three hours ago, so the catch-up reaches the first message.
        (self.data / "sessions.jsonl").write_text(json.dumps({"event": "start", "t": h - 3 * 3600, "boot": "b"}) + "\n")
        (self.tmp / "boot_id").write_text("b\n")

    def show(self, *args):
        done = self.run_script(USAGE, "show", *args)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout.strip().splitlines()[-1])

    def test_collect_records_every_finished_hour_per_user_and_model(self):
        done = self.run_script(USAGE, "collect")
        self.assertEqual(done.returncode, 0, done.stderr)
        out = self.show()
        hours = {x["hour"]: x for x in out["hours"]}
        h = self.this_hour
        self.assertEqual(sorted(hours), [h - 7200, h - 3600])  # not the hour in progress
        first = {u["id"]: u for u in hours[h - 7200]["users"]}
        self.assertEqual((first["u1"]["in"], first["u1"]["out"], first["u1"]["messages"]), (100, 400, 1))
        self.assertEqual((first["u2"]["email"], first["u2"]["out"]), ("bo@lab.test", 150))
        self.assertEqual(hours[h - 7200]["models"][0]["id"], "qwen3:14b")
        self.assertEqual((hours[h - 7200]["models"][0]["in"], hours[h - 7200]["models"][0]["out"]), (150, 550))
        self.assertEqual([u["id"] for u in hours[h - 3600]["users"]], ["u1"])
        self.assertEqual(out["nextHour"], h)
        self.assertEqual(len(out["sessions"]), 1)
        self.assertIsNone(out["provisional"])

    def test_collecting_twice_does_not_duplicate_anything(self):
        self.run_script(USAGE, "collect")
        before = (self.data / "usage.jsonl").read_text()
        self.run_script(USAGE, "collect")
        self.assertEqual((self.data / "usage.jsonl").read_text(), before)

    def test_final_collection_also_records_the_hour_in_progress_and_keeps_it_repeatable(self):
        self.run_script(USAGE, "collect", "--final")
        hours = {x["hour"]: x for x in self.show()["hours"]}
        self.assertIn(self.this_hour, hours)
        self.assertEqual(hours[self.this_hour]["users"][0]["in"], 7)
        # More chat later in the same hour (the instance restarted): re-collecting replaces the hour.
        self.cfg["messages"].append(msg("u2", self.this_hour + 2, 1, 1))
        self.write_cfg()
        self.run_script(USAGE, "collect", "--final")
        hours = {x["hour"]: x for x in self.show()["hours"]}
        self.assertEqual(sorted(u["id"] for u in hours[self.this_hour]["users"]), ["u1", "u2"])
        lines = [json.loads(line) for line in (self.data / "usage.jsonl").read_text().splitlines()]
        self.assertEqual(len([x for x in lines if x["hour"] == self.this_hour and x["kind"] == "user"]), 2)

    def test_show_can_add_the_live_hour_and_filter_by_start(self):
        self.run_script(USAGE, "collect")
        out = self.show("--since", str(self.this_hour - 3600), "--provisional")
        self.assertEqual([x["hour"] for x in out["hours"]], [self.this_hour - 3600])
        self.assertEqual(out["provisional"]["hour"], self.this_hour)
        self.assertEqual(out["provisional"]["users"][0]["out"], 3)
        self.assertEqual(self.run_script(USAGE, "show", "--since", "x").returncode, 2)

    def test_show_carries_the_price_and_instance_type_for_the_cost_page(self):
        (self.tmp / "metrics.env").write_text("INSTANCE_TYPE='g6.xlarge'\nINSTANCE_HOURLY_COST_USD='0.8048'\n")
        out = self.show()
        self.assertEqual((out["hourlyCostUsd"], out["instanceType"]), (0.8048, "g6.xlarge"))
        (self.tmp / "metrics.env").write_text("INSTANCE_TYPE='g6.xlarge'\nINSTANCE_HOURLY_COST_USD=''\n")
        self.assertIsNone(self.show()["hourlyCostUsd"])

    def test_an_unexpected_api_answer_stores_nothing_and_asks_for_a_retry(self):
        self.cfg["bad_shape"] = True
        self.write_cfg()
        done = self.run_script(USAGE, "collect")
        self.assertEqual(done.returncode, 1)
        self.assertFalse((self.data / "usage.jsonl").exists())
        self.assertFalse((self.data / "usage-next-hour").exists())

    def test_a_failed_hour_does_not_advance_past_it(self):
        self.cfg["webui_down"] = True
        self.write_cfg()
        self.assertEqual(self.run_script(USAGE, "collect").returncode, 1)
        self.assertFalse((self.data / "usage-next-hour").exists())

    def test_password_login_off_is_reported_not_crashed(self):
        self.settings.write_text(self.settings.read_text().replace("LOCAL_LOGIN='true'", "LOCAL_LOGIN='false'"))
        done = self.run_script(USAGE, "collect")
        self.assertEqual(done.returncode, 3)
        self.assertIn("password login is off", done.stderr)
        self.cfg["login"] = 403
        self.settings.write_text(self.settings.read_text().replace("LOCAL_LOGIN='false'", "LOCAL_LOGIN='true'"))
        self.write_cfg()
        self.assertEqual(self.run_script(USAGE, "collect").returncode, 3)

    def test_nothing_runs_before_first_boot_setup_is_finished(self):
        (self.data / "ready").unlink()
        self.assertEqual(self.run_script(USAGE, "collect").returncode, 0)
        self.assertFalse((self.tmp / "curl-calls").exists())

    def test_the_admin_is_not_left_looking_active(self):
        # The fake API marks the admin active on every request, as Open WebUI does. Without the
        # restore, the idle monitor would see an active user every hour and never stop the lab.
        self.run_script(USAGE, "collect")
        self.assertEqual(self.admin_last_active(), self.admin_baseline)

    def test_a_person_active_since_before_the_run_is_not_rolled_back(self):
        recent = int(time.time()) - 30
        con = sqlite3.connect(self.db)
        con.execute("update user set last_active_at=? where email='admin@lab.test'", (recent,))
        con.commit()
        con.close()
        self.run_script(USAGE, "collect")
        self.assertEqual(self.admin_last_active(), recent)

    def test_output_is_trimmed_to_fit_ssm_and_says_so(self):
        h = self.this_hour
        rows = []
        for hour in range(70):
            rows += [msg(f"user-{n}", h - 3600 * (hour + 1) + 5 + n, 1, 1) for n in range(30)]
        self.cfg["messages"] = rows
        self.write_cfg()
        (self.data / "sessions.jsonl").write_text(json.dumps({"event": "start", "t": h - 80 * 3600, "boot": "b"}) + "\n")
        self.run_script(USAGE, "collect")
        out = self.show()
        raw = self.run_script(USAGE, "show").stdout.strip()
        self.assertLessEqual(len(raw), 20000)
        self.assertTrue(out["truncated"])
        self.assertEqual(out["hours"][-1]["hour"], h - 3600)  # newest hours are kept


class MetricsTest(Base):
    def setUp(self):
        super().setUp()
        (self.tmp / "metrics.env").write_text("INSTANCE_TYPE='g6.xlarge'\nINSTANCE_HOURLY_COST_USD='0.8048'\n")
        self.cfg.update({
            "ollama": {"models": [{"name": "qwen3:14b", "size": 12000000000, "size_vram": 9000000000}]},
            "messages": [msg("u1", 1000, 100, 400), msg("u2", 1001, 50, 150, "security-analyst")],
            "model_list": [{"id": "security-analyst", "info": {"params": {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "system": "never exported"}}}],
        })
        self.write_cfg()
        (self.state / "status.json").write_text(json.dumps({"idle_seconds_remaining": 1800, "hard_limit_seconds_remaining": -1}))
        (self.bin / "journalctl").write_text(JOURNALCTL)
        (self.bin / "journalctl").chmod(0o755)
        (self.tmp / "journal.txt").write_text(
            '[GIN] 2026/10/10 - 12:00:00 | 200 |  2.5s |  127.0.0.1 | POST     "/api/chat"\n'
            '[GIN] 2026/10/10 - 12:00:05 | 200 |  500ms |  127.0.0.1 | POST     "/v1/chat/completions"\n'
            '[GIN] 2026/10/10 - 12:00:06 | 200 |  1m3.5s |  127.0.0.1 | POST     "/api/generate"\n'
            '[GIN] 2026/10/10 - 12:00:07 | 500 |  10ms |  127.0.0.1 | POST     "/api/chat"\n'
            '[GIN] 2026/10/10 - 12:00:08 | 404 |  80\u00b5s |  127.0.0.1 | POST     "/api/chat"\n'
            '[GIN] 2026/10/10 - 12:00:09 | 200 |  900ms |  127.0.0.1 | GET      "/api/tags"\n'
            'time=2026-10-10T12:00:00 level=INFO msg="llama runner started in 4.25 seconds"\n')
        con = sqlite3.connect(self.db)
        con.execute("create table chat (id text, title text)")
        con.execute("insert into chat values ('c1','secret title'),('c2','other'),('c3','x')")
        con.execute("create table feedback (id text, data text)")
        con.executemany("insert into feedback values (?,?)", [("f1", '{"rating": 1}'), ("f2", '{"rating": 1}'), ("f3", '{"rating": -1}'), ("f4", '{"reason": "none"}')])
        con.commit()
        con.close()

    def prom(self):
        return (self.tmp / "metrics" / "ai_lab.prom").read_text()

    def run_metrics(self):
        done = self.run_script(METRICS)
        self.assertEqual(done.returncode, 0, done.stderr)
        return self.prom()

    def test_writes_the_documented_metrics(self):
        text = self.run_metrics()
        for expected in (
            'ai_lab_gpu_temperature_celsius{gpu="0",instance_id="i-0abc123"} 61',
            'ai_lab_gpu_utilization_ratio{gpu="0",instance_id="i-0abc123"} 0.3700',
            'ai_lab_gpu_memory_used_bytes{gpu="0",instance_id="i-0abc123"} 9563013120',
            'ai_lab_gpu_power_watts{gpu="0",instance_id="i-0abc123"} 71.5',
            'ai_lab_gpu_throttled{gpu="0",reason="sw_power_cap",instance_id="i-0abc123"} 1',
            'ai_lab_gpu_throttled{gpu="0",reason="hw_thermal_slowdown",instance_id="i-0abc123"} 0',
            'ai_lab_ollama_model_loaded{model="qwen3:14b",instance_id="i-0abc123"} 1',
            'ai_lab_ollama_model_vram_ratio{model="qwen3:14b",instance_id="i-0abc123"} 0.7500',
            'ai_lab_tokens_total{model="qwen3:14b",direction="input",instance_id="i-0abc123"} 100',
            'ai_lab_tokens_total{model="security-analyst",direction="output",instance_id="i-0abc123"} 150',
            'ai_lab_messages_total{model="security-analyst",instance_id="i-0abc123"} 1',
            'ai_lab_model_param{model="security-analyst",param="temperature",instance_id="i-0abc123"} 0.6',
            'ai_lab_model_param{model="security-analyst",param="top_k",instance_id="i-0abc123"} 20',
            'ai_lab_idle_seconds_remaining{instance_id="i-0abc123"} 1800',
            'ai_lab_hard_limit_seconds_remaining{instance_id="i-0abc123"} -1',
            'ai_lab_instance_hourly_cost_usd{instance_type="g6.xlarge",instance_id="i-0abc123"} 0.8048',
            'ai_lab_exporter_source_up{source="gpu",instance_id="i-0abc123"} 1',
            'ai_lab_exporter_source_up{source="open_webui",instance_id="i-0abc123"} 1',
        ):
            self.assertIn(expected, text)

    def test_every_metric_is_grouped_under_one_help_and_type_line(self):
        text = self.run_metrics()
        names = [line.split()[2] for line in text.splitlines() if line.startswith("# TYPE")]
        self.assertEqual(len(names), len(set(names)))
        seen_closed = set()
        current = None
        for line in text.splitlines():
            if line.startswith("# TYPE"):
                current = line.split()[2]
            elif not line.startswith("#"):
                name = line.split("{")[0]
                self.assertEqual(name, current, "samples of a metric must be contiguous")
                self.assertNotIn(name, seen_closed)

    def test_no_user_or_prompt_data_reaches_the_file(self):
        text = self.run_metrics()
        for private in ("ann@lab.test", "bo@lab.test", "Ann", "never exported", "u1", "u2", "admin@lab.test"):
            self.assertNotIn(private, text)

    def test_a_failing_source_is_left_out_and_marked_down_while_the_rest_is_written(self):
        self.cfg["ollama"] = None
        self.cfg["webui_down"] = True
        self.write_cfg()
        text = self.run_metrics()
        self.assertIn('ai_lab_exporter_source_up{source="ollama",instance_id="i-0abc123"} 0', text)
        self.assertIn('ai_lab_exporter_source_up{source="open_webui",instance_id="i-0abc123"} 0', text)
        self.assertNotIn("ai_lab_tokens_total", text)
        self.assertIn("ai_lab_gpu_temperature_celsius", text)
        self.assertIn("ai_lab_exporter_last_success_timestamp_seconds", text)

    def test_ollama_requests_and_model_load_come_from_the_journal(self):
        text = self.run_metrics()
        for expected in (
            'ai_lab_ollama_chat_requests_5m{status="2xx",instance_id="i-0abc123"} 3',
            'ai_lab_ollama_chat_requests_5m{status="4xx",instance_id="i-0abc123"} 1',
            'ai_lab_ollama_chat_requests_5m{status="5xx",instance_id="i-0abc123"} 1',
            'ai_lab_ollama_chat_request_seconds_avg_5m{instance_id="i-0abc123"} 22.167',
            'ai_lab_ollama_chat_request_seconds_max_5m{instance_id="i-0abc123"} 63.500',
            'ai_lab_ollama_last_model_load_seconds{instance_id="i-0abc123"} 4.25',
            'ai_lab_exporter_source_up{source="ollama_log",instance_id="i-0abc123"} 1',
        ):
            self.assertIn(expected, text)

    def test_a_quiet_journal_gives_zero_requests_and_no_averages(self):
        (self.tmp / "journal.txt").write_text("")
        text = self.run_metrics()
        self.assertIn('ai_lab_ollama_chat_requests_5m{status="2xx",instance_id="i-0abc123"} 0', text)
        self.assertNotIn("request_seconds_avg", text)
        self.assertNotIn("last_model_load", text)

    def test_an_unreadable_journal_marks_the_source_down_only(self):
        (self.tmp / "journal-fails").write_text("")
        text = self.run_metrics()
        self.assertIn('ai_lab_exporter_source_up{source="ollama_log",instance_id="i-0abc123"} 0', text)
        self.assertNotIn("ai_lab_ollama_chat_requests_5m", text)
        self.assertIn("ai_lab_tokens_total", text)

    def test_chat_count_and_feedback_come_from_the_database_without_any_text(self):
        text = self.run_metrics()
        for expected in (
            'ai_lab_chats{instance_id="i-0abc123"} 3',
            'ai_lab_feedback{rating="up",instance_id="i-0abc123"} 2',
            'ai_lab_feedback{rating="down",instance_id="i-0abc123"} 1',
            'ai_lab_exporter_source_up{source="webui_db",instance_id="i-0abc123"} 1',
        ):
            self.assertIn(expected, text)
        self.assertNotIn("secret title", text)

    def test_a_database_without_the_expected_tables_marks_only_that_source_down(self):
        con = sqlite3.connect(self.db)
        con.execute("drop table chat")
        con.commit()
        con.close()
        text = self.run_metrics()
        self.assertIn('ai_lab_exporter_source_up{source="webui_db",instance_id="i-0abc123"} 0', text)
        self.assertNotIn("ai_lab_chats", text)
        self.assertNotIn("ai_lab_feedback", text)
        self.assertIn("ai_lab_gpu_temperature_celsius", text)

    def test_a_missing_feedback_table_keeps_the_chat_count(self):
        con = sqlite3.connect(self.db)
        con.execute("drop table feedback")
        con.commit()
        con.close()
        text = self.run_metrics()
        self.assertIn("ai_lab_chats{", text)
        self.assertNotIn("ai_lab_feedback", text)

    def test_shapes_mode_prints_keys_and_types_and_no_values(self):
        done = self.run_script(METRICS, "--shapes")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("== GET /api/v1/analytics/messages", done.stdout)
        self.assertIn("== table chat", done.stdout)
        self.assertIn("title TEXT", done.stdout)
        self.assertIn("(no such table)", done.stdout)
        self.assertNotIn("secret title", done.stdout)
        self.assertFalse((self.tmp / "metrics" / "ai_lab.prom").exists())

    def test_missing_price_and_no_gpu_are_simply_omitted(self):
        (self.tmp / "metrics.env").write_text("INSTANCE_TYPE='c7i.4xlarge'\nINSTANCE_HOURLY_COST_USD=''\n")
        (self.bin / "nvidia-smi").unlink()
        text = self.run_metrics()
        self.assertNotIn("ai_lab_instance_hourly_cost_usd", text)
        self.assertNotIn("ai_lab_gpu_", text)

    def test_nothing_is_written_before_first_boot_setup_is_finished(self):
        (self.data / "ready").unlink()
        self.assertEqual(self.run_script(METRICS).returncode, 0)
        self.assertFalse((self.tmp / "metrics" / "ai_lab.prom").exists())

    def test_the_admin_is_not_left_looking_active(self):
        self.run_metrics()
        self.assertEqual(self.admin_last_active(), self.admin_baseline)


if __name__ == "__main__":
    unittest.main()
