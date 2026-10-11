"""Offline tests for scripts/ai-lab-set-role (the spend caps' block and restore of an Open WebUI
user). Same harness and limits as test_usage_monitoring.py: the real script runs under bash, and
curl is a stub Open WebUI that keeps users in a JSON file. The real v0.11.x user endpoints are
checked at the first live deploy.

Run: python -m unittest discover -s tests
"""

import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import test_usage_monitoring as base  # noqa: E402

SET_ROLE = base.SCRIPTS / "ai-lab-set-role"

CURL = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
url = next(a for a in args if a.startswith("http"))
out = fmt = None; method = "GET"; body = None; i = 0
while i < len(args):
    if args[i] == "-o": out = args[i + 1]; i += 1
    elif args[i] == "-w": fmt = args[i + 1]; i += 1
    elif args[i] == "-X": method = args[i + 1]; i += 1
    elif args[i] == "--data-binary":
        src = args[i + 1]; body = sys.stdin.read() if src == "@-" else open(src[1:]).read(); i += 1
    i += 1
store = os.environ["STUB_DIR"] + "/users.json"
users = json.load(open(store))
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
parts = path.strip("/").split("/")  # api v1 users ID [update]
if parts[:3] == ["api", "v1", "users"] and len(parts) >= 4:
    user = users.get(parts[3])
    if user is None: reply(404, {"detail": "not found"})
    if len(parts) == 4 and method == "GET": reply(200, user)
    if len(parts) == 5 and parts[4] == "update" and method == "POST":
        if os.environ.get("STUB_REFUSE"): reply(400, {"detail": "no"})
        form = json.loads(body)
        user.update(form); json.dump(users, open(store, "w"))
        open(os.environ["STUB_DIR"] + "/update-bodies", "a").write(body + "\n")
        reply(200, user)
reply(404, "")
'''


class SetRoleTest(base.Base):
    def setUp(self):
        super().setUp()
        (self.bin / "curl").write_text(CURL)
        (self.bin / "curl").chmod(0o755)
        self.users = {
            "u1": {"id": "u1", "name": "Ann", "email": "ann@x", "role": "user", "profile_image_url": "/p.png"},
            "u2": {"id": "u2", "name": "Bo", "email": "bo@x", "role": "pending", "profile_image_url": "/user.png"},
            "adm": {"id": "adm", "name": "Root", "email": "root@x", "role": "admin", "profile_image_url": "/user.png"},
        }
        (self.tmp / "users.json").write_text(json.dumps(self.users))

    def stored(self):
        return json.loads((self.tmp / "users.json").read_text())

    def set_role(self, user, role, **env):
        done = self.run_script(SET_ROLE, user, role, **env)
        line = [x for x in done.stdout.splitlines() if x.strip()][-1]
        return done.returncode, json.loads(line)

    def test_blocking_sets_pending_and_reports_the_previous_role(self):
        code, out = self.set_role("u1", "pending")
        self.assertEqual(code, 0)
        self.assertEqual(out, {"ok": True, "userId": "u1", "previousRole": "user", "role": "pending", "changed": True})
        self.assertEqual(self.stored()["u1"]["role"], "pending")
        sent = json.loads((self.tmp / "update-bodies").read_text().splitlines()[0])
        self.assertEqual(sent, {"role": "pending", "name": "Ann", "email": "ann@x", "profile_image_url": "/p.png"})

    def test_restoring_and_repeating_are_harmless(self):
        code, out = self.set_role("u2", "user")
        self.assertEqual((code, out["changed"], out["previousRole"]), (0, True, "pending"))
        code, out = self.set_role("u2", "user")
        self.assertEqual((code, out["changed"]), (0, False))
        self.assertEqual(len((self.tmp / "update-bodies").read_text().splitlines()), 1)

    def test_an_administrator_is_never_changed(self):
        code, out = self.set_role("adm", "pending")
        self.assertEqual(code, 1)
        self.assertFalse(out["ok"])
        self.assertEqual(self.stored()["adm"]["role"], "admin")

    def test_bad_arguments_and_unknown_users_are_refused(self):
        self.assertEqual(self.set_role("u1", "admin")[0], 2)
        self.assertEqual(self.set_role("u1; rm -rf /", "pending")[0], 2)
        code, out = self.set_role("nobody", "pending")
        self.assertEqual(code, 1)
        self.assertIn("does not know", out["error"])

    def test_a_refused_update_is_a_failure_and_password_login_off_is_reported(self):
        code, out = self.set_role("u1", "pending", STUB_REFUSE="1")
        self.assertEqual(code, 1)
        self.assertEqual(self.stored()["u1"]["role"], "user")
        self.settings.write_text(self.settings.read_text().replace("LOCAL_LOGIN='true'", "LOCAL_LOGIN='false'"))
        code, out = self.set_role("u1", "pending")
        self.assertEqual(code, 3)

    def test_it_leaves_the_admins_idle_clock_as_it_found_it(self):
        self.set_role("u1", "pending")
        self.assertEqual(self.admin_last_active(), self.admin_baseline)


if __name__ == "__main__":
    unittest.main()
