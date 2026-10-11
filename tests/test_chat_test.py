"""Offline tests for scripts/ai-lab-chat-test (the control panel's Chat API test). Same harness
and limits as test_usage_monitoring.py: the real script runs under bash and curl is a stub Open
WebUI. The real /api/chat/completions answer is checked at the first live deploy.

Run: python -m unittest discover -s tests
"""

import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import test_usage_monitoring as base  # noqa: E402

CHAT_TEST = base.SCRIPTS / "ai-lab-chat-test"

CURL = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
url = next(a for a in args if a.startswith("http"))
out = fmt = None; body = None; timeout = None; i = 0
while i < len(args):
    if args[i] == "-o": out = args[i + 1]; i += 1
    elif args[i] == "-w": fmt = args[i + 1]; i += 1
    elif args[i] == "-m": timeout = args[i + 1]; i += 1
    elif args[i] == "--data-binary":
        src = args[i + 1]; body = sys.stdin.read() if src == "@-" else open(src[1:]).read(); i += 1
    i += 1
path = url.split("127.0.0.1:8080", 1)[1]
def reply(status, payload=""):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    if out: open(out, "w").write(text)
    elif status == 200: sys.stdout.write(text)
    if fmt: sys.stdout.write(str(status))
    sys.exit(0)
if path == "/api/v1/auths/signin":
    sys.stdin.read(); open(os.environ["WEBUI_DB_TOUCH"], "w").write("1"); reply(200, {"token": "tok-1"})
if path == "/api/v1/auths/": reply(200, {"id": "u-admin"})
if path == "/api/chat/completions":
    open(os.environ["STUB_DIR"] + "/chat-body", "w").write(body)
    open(os.environ["STUB_DIR"] + "/chat-timeout", "w").write(str(timeout))
    mode = os.environ.get("STUB_CHAT", "ok")
    if mode == "fail": reply(500, {"detail": "boom"})
    if mode == "odd": reply(200, {"unexpected": True})
    content = os.environ.get("STUB_REPLY", "ready")
    reply(200, {"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 14, "completion_tokens": 3}})
reply(404, "")
'''


class ChatTestTest(base.Base):
    def setUp(self):
        super().setUp()
        (self.bin / "curl").write_text(CURL)
        (self.bin / "curl").chmod(0o755)

    def chat(self, model="qwen3:14b", **env):
        done = self.run_script(CHAT_TEST, model, **env)
        line = [x for x in done.stdout.splitlines() if x.strip()][-1]
        return done.returncode, json.loads(line)

    def test_it_sends_the_fixed_prompt_and_reports_the_reply(self):
        code, out = self.chat()
        self.assertEqual(code, 0)
        self.assertTrue(out["ok"])
        self.assertEqual((out["action"], out["model"], out["reply"]), ("chat-test", "qwen3:14b", "ready"))
        self.assertEqual((out["inputTokens"], out["outputTokens"]), (14, 3))
        self.assertIsInstance(out["latencyMs"], int)
        sent = json.loads((self.tmp / "chat-body").read_text())
        self.assertEqual(sent["model"], "qwen3:14b")
        self.assertIs(sent["stream"], False)
        self.assertEqual(sent["messages"], [{"role": "user", "content": "Reply with the single word: ready."}])

    def test_it_allows_a_long_first_call_while_the_model_loads(self):
        self.chat()
        self.assertEqual((self.tmp / "chat-timeout").read_text(), "120")

    def test_a_think_block_is_removed_and_a_long_reply_is_shortened(self):
        _, out = self.chat(STUB_REPLY="<think>\nhmm\nlet me see\n</think>\n\nready")
        self.assertEqual(out["reply"], "ready")
        _, out = self.chat(STUB_REPLY="x" * 1000)
        self.assertEqual(len(out["reply"]), 300)

    def test_bad_model_names_are_refused_before_anything_is_sent(self):
        for name in ("", "a b", "x;rm -rf /", "$(id)", "a" * 101, "q\"w"):
            with self.subTest(name=name):
                code, out = self.chat(name)
                self.assertEqual(code, 2)
                self.assertFalse(out["ok"])
        self.assertFalse((self.tmp / "chat-body").exists())

    def test_a_failed_or_odd_answer_is_a_clear_failure(self):
        code, out = self.chat(STUB_CHAT="fail")
        self.assertEqual(code, 1)
        self.assertIn("did not return a reply", out["error"])
        code, out = self.chat(STUB_CHAT="odd")
        self.assertEqual(code, 1)
        self.assertIn("not with a chat reply", out["error"])

    def test_password_login_off_is_reported(self):
        self.settings.write_text(self.settings.read_text().replace("LOCAL_LOGIN='true'", "LOCAL_LOGIN='false'"))
        code, out = self.chat()
        self.assertEqual(code, 3)
        self.assertFalse(out["ok"])

    def test_it_leaves_the_admins_idle_clock_as_it_found_it(self):
        self.chat()
        self.assertEqual(self.admin_last_active(), self.admin_baseline)


if __name__ == "__main__":
    unittest.main()
