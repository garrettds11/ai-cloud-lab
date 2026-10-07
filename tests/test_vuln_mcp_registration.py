"""Offline tests for scripts/ai-lab-register-vuln-mcp.sh (the Open WebUI registration step).

What is real and what is faked. Nothing here touches AWS or a running deployment.

  * REAL: the registration script itself, run with bash, using the real curl and jq.
  * FAKED: Open WebUI. FakeOpenWebUI is a small local HTTP server that implements only the admin
    endpoints the script calls (/health, signin, GET/POST tool_servers, tool_servers/verify) with
    the request and response shapes of Open WebUI v0.11.4. A pass proves the script's logic, not
    that a real Open WebUI accepts it.
  * FAKED: Secrets Manager. A stub `aws` on PATH answers get-secret-value from local files.
  * In McpSdkIntegrationTest the fake's verify endpoint uses the REAL MCP Python SDK (the one
    Open WebUI v0.11.4 bundles, mcp 1.27.2) against the REAL handler in lambda/vuln_mcp, on
    loopback, with fixture data. That shows the server speaks the transport Open WebUI uses,
    enforces the bearer token and lists its six tools. These skip if mcp 1.x is not installed
    (pip install mcp==1.27.2).

Tests against a running deployment are in docs/vuln-mcp-acceptance-tests.md and are manual.
The script tests need bash, curl and jq (all present on the CI runner and on the lab instance);
they skip on a machine without them, such as plain Windows.

Run from the repository root:  python -m unittest discover -s tests -v
"""

import contextlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "ai-lab-register-vuln-mcp.sh"

ADMIN_EMAIL = "admin@example.local"
ADMIN_PASSWORD = "admin-password-VALUE"
SESSION_TOKEN = "admin-session-token"
MCP_TOKEN = "mcp-token-VALUE-0123456789"
MCP_URL = "https://abc123.lambda-url.us-east-1.on.aws/mcp"
ADMIN_ARN = "arn:aws:secretsmanager:us-east-1:123456789012:secret:admin-AbCdEf"
TOKEN_ARN = "arn:aws:secretsmanager:us-east-1:123456789012:secret:vuln-mcp-token-AbCdEf"
SIX_TOOLS = ["find_hosts_by_vulnerability", "get_data_dictionary", "get_finding",
             "get_host_findings", "list_findings", "summarize_findings"]
CONNECTION_ID = "vuln-findings"
CANNOT_AUTOMATE = 3
FAILED = 2

HAVE_TOOLS = os.name == "posix" and all(shutil.which(t) for t in ("bash", "curl", "jq"))


class FakeOpenWebUI:
    """Just enough of Open WebUI's admin API, with switches for failure cases."""

    def __init__(self):
        self.connections = []
        self.requests = []          # (method, path, body)
        self.health_failures = 0    # answer /health with 503 this many times first
        self.verify_failures = 0    # answer verify with 400 this many times first
        self.verify_hook = None     # optional callable(body) -> list of tool names; raises to refuse
        self.password_auth = True
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, payload):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _admin(self):
                return self.headers.get("authorization") == f"Bearer {SESSION_TOKEN}"

            def do_GET(self):
                outer.requests.append(("GET", self.path, None))
                if self.path == "/health":
                    if outer.health_failures > 0:
                        outer.health_failures -= 1
                        return self._send(503, {"status": False})
                    return self._send(200, {"status": True})
                if self.path == "/api/v1/configs/tool_servers":
                    if not self._admin():
                        return self._send(401, {"detail": "Not authenticated"})
                    return self._send(200, {"TOOL_SERVER_CONNECTIONS": outer.connections})
                self._send(404, {})

            def do_POST(self):
                length = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                outer.requests.append(("POST", self.path, body))
                if self.path == "/api/v1/auths/signin":
                    if not outer.password_auth:
                        return self._send(403, {"detail": "Password authentication is disabled"})
                    if body.get("email") != ADMIN_EMAIL or body.get("password") != ADMIN_PASSWORD:
                        return self._send(400, {"detail": "Invalid credentials"})
                    return self._send(200, {"token": SESSION_TOKEN, "role": "admin"})
                if not self._admin():
                    return self._send(401, {"detail": "Not authenticated"})
                if self.path == "/api/v1/configs/tool_servers/verify":
                    if outer.verify_failures > 0:
                        outer.verify_failures -= 1
                        return self._send(400, {"detail": "Failed to create MCP client"})
                    try:
                        names = outer.verify_hook(body) if outer.verify_hook else list(SIX_TOOLS)
                    except Exception:
                        return self._send(400, {"detail": "Failed to create MCP client"})
                    return self._send(200, {"status": True, "specs": [{"name": n} for n in names]})
                if self.path == "/api/v1/configs/tool_servers":
                    outer.connections = body["TOOL_SERVER_CONNECTIONS"]
                    return self._send(200, {"TOOL_SERVER_CONNECTIONS": outer.connections})
                self._send(404, {})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def posts(self, path):
        return [r for r in self.requests if r[0] == "POST" and r[1] == path]

    def saves(self):
        return self.posts("/api/v1/configs/tool_servers")

    def managed(self):
        return [c for c in self.connections if (c.get("info") or {}).get("id") == CONNECTION_ID]


FAKE_AWS = """#!/usr/bin/env bash
# Stub for `aws secretsmanager get-secret-value`: answers from files, logs the ARN asked for.
while [[ $# -gt 0 ]]; do [[ "$1" == "--secret-id" ]] && arn="$2"; shift; done
echo "$arn" >>"$FAKE_AWS_DIR/requested"
if [[ -f "$FAKE_AWS_DIR/fail_first" ]] && [[ "$(cat "$FAKE_AWS_DIR/fail_first")" -gt 0 ]]; then
    echo $(( $(cat "$FAKE_AWS_DIR/fail_first") - 1 )) >"$FAKE_AWS_DIR/fail_first"
    echo "ThrottlingException" >&2
    exit 254
fi
cat "$FAKE_AWS_DIR/$(printf '%s' "$arn" | tr -c 'A-Za-z0-9' _)"
"""

CURL_LOGGER = """#!/usr/bin/env bash
# Records every curl command line (what `ps` would show), then runs the real curl.
echo "$*" >>"$FAKE_AWS_DIR/curl_argv"
exec "$REAL_CURL" "$@"
"""


def other_connection(name="Other tools", url="https://tools.example.com/mcp"):
    return {
        "url": url, "path": "", "type": "mcp", "auth_type": "none", "key": "", "headers": None,
        "forward_cookies": False, "config": {"enable": True, "access_grants": []},
        "info": {"id": name.lower().replace(" ", "-"), "name": name, "description": ""},
    }


@unittest.skipUnless(HAVE_TOOLS, "needs bash, curl and jq")
class ScriptTestCase(unittest.TestCase):
    def setUp(self):
        self.webui = FakeOpenWebUI()
        self.addCleanup(self.webui.close)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.aws_dir = self.tmp / "aws"
        self.aws_dir.mkdir()
        for name, text in (("aws", FAKE_AWS), ("curl", CURL_LOGGER)):
            (self.bin / name).write_text(text)
            (self.bin / name).chmod(0o755)
        self.set_secret(ADMIN_ARN, ADMIN_PASSWORD)
        self.set_secret(TOKEN_ARN, MCP_TOKEN)
        self.settings = {
            "ENABLED": "true", "URL": MCP_URL, "TOKEN_ARN": TOKEN_ARN, "ADMIN_ARN": ADMIN_ARN,
            "ADMIN_EMAIL": ADMIN_EMAIL, "LOCAL_LOGIN": "true", "PORT": str(self.webui.port), "REGION": "us-east-1",
        }

    def set_secret(self, arn, text):
        (self.aws_dir / re.sub(r"[^A-Za-z0-9]", "_", arn)).write_text(text)

    def requested_secrets(self):
        path = self.aws_dir / "requested"
        return path.read_text().split() if path.exists() else []

    def curl_command_lines(self):
        path = self.aws_dir / "curl_argv"
        return path.read_text() if path.exists() else ""

    def run_script(self, settings=None, *args, retries=3, ready_attempts=3, env_extra=None, write_env=True):
        """Runs the script the way cloud-init does, with no waiting. Returns (exit code, output)."""
        settings = {**self.settings, **(settings or {})}
        env_file = self.tmp / "vuln-mcp.env"
        if write_env:
            env_file.write_text("".join(f"{k}='{v}'\n" for k, v in settings.items()))
        env = {
            **os.environ,
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "REAL_CURL": shutil.which("curl"),
            "FAKE_AWS_DIR": str(self.aws_dir),
            "AI_LAB_VULN_MCP_ENV": str(env_file),
            "AI_LAB_RETRIES": str(retries), "AI_LAB_RETRY_DELAY": "0",
            "AI_LAB_READY_ATTEMPTS": str(ready_attempts), "AI_LAB_READY_DELAY": "0",
            **(env_extra or {}),
        }
        done = subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=120)
        return done.returncode, done.stdout + done.stderr


class RegisterTest(ScriptTestCase):
    def test_adds_one_bearer_authenticated_mcp_connection(self):
        code, output = self.run_script()
        self.assertEqual(code, 0, output)
        [entry] = self.webui.connections
        self.assertEqual((entry["type"], entry["url"], entry["auth_type"]), ("mcp", MCP_URL, "bearer"))
        self.assertEqual(entry["key"], MCP_TOKEN)
        self.assertEqual(entry["info"]["id"], CONNECTION_ID)
        self.assertEqual(entry["info"]["name"], "Vulnerability Findings")
        self.assertTrue(entry["config"]["enable"])
        self.assertEqual(entry["config"]["access_grants"],
                         [{"principal_type": "user", "principal_id": "*", "permission": "read"}])

    def test_verifies_the_connection_with_the_token_before_saving_it(self):
        self.run_script()
        paths = [p for m, p, _ in self.webui.requests if m == "POST"]
        self.assertLess(paths.index("/api/v1/configs/tool_servers/verify"), paths.index("/api/v1/configs/tool_servers"))
        [verified] = self.webui.posts("/api/v1/configs/tool_servers/verify")
        self.assertEqual((verified[2]["url"], verified[2]["auth_type"], verified[2]["key"]), (MCP_URL, "bearer", MCP_TOKEN))

    def test_reports_the_discovered_tools_and_prints_no_secret(self):
        _, output = self.run_script()
        self.assertIn("listed 6 tools", output)
        self.assertIn(", ".join(SIX_TOOLS), output)
        for secret in (MCP_TOKEN, ADMIN_PASSWORD, SESSION_TOKEN):
            self.assertNotIn(secret, output)

    def test_no_secret_is_ever_on_a_command_line(self):
        self.run_script()
        lines = self.curl_command_lines()
        self.assertIn("/api/v1/configs/tool_servers", lines)  # the recorder saw the real calls
        for secret in (MCP_TOKEN, ADMIN_PASSWORD, SESSION_TOKEN):
            self.assertNotIn(secret, lines)
        self.assertNotIn("Authorization", lines)

    def test_secrets_are_read_by_arn(self):
        self.run_script()
        self.assertEqual(sorted(set(self.requested_secrets())), sorted([ADMIN_ARN, TOKEN_ARN]))

    def test_key_value_and_plain_secrets_are_both_understood(self):
        self.set_secret(TOKEN_ARN, json.dumps({"token": MCP_TOKEN}))
        self.set_secret(ADMIN_ARN, json.dumps(ADMIN_PASSWORD))
        code, output = self.run_script()
        self.assertEqual(code, 0, output)
        self.assertEqual(self.webui.connections[0]["key"], MCP_TOKEN)

    def test_rejected_verify_means_nothing_is_saved(self):
        self.webui.verify_hook = mock.Mock(side_effect=RuntimeError("401"))
        code, output = self.run_script(retries=2)
        self.assertEqual(code, FAILED, output)
        self.assertEqual(self.webui.saves(), [])
        self.assertEqual(self.webui.connections, [])


class RepeatedRunTest(ScriptTestCase):
    def test_second_run_changes_nothing_and_does_not_write(self):
        self.run_script()
        first = json.loads(json.dumps(self.webui.connections))
        self.run_script()
        code, output = self.run_script()
        self.assertEqual(code, 0, output)
        self.assertIn("already correct", output)
        self.assertEqual(self.webui.connections, first)
        self.assertEqual(len(self.webui.saves()), 1)
        self.assertEqual(len(self.webui.managed()), 1)

    def test_changed_url_updates_in_place_without_a_duplicate(self):
        self.run_script()
        self.run_script({"URL": "https://new.lambda-url.us-east-1.on.aws/mcp"})
        [entry] = self.webui.connections
        self.assertEqual(entry["url"], "https://new.lambda-url.us-east-1.on.aws/mcp")

    def test_a_rotated_token_is_picked_up_with_one_write(self):
        self.run_script()
        self.set_secret(TOKEN_ARN, "rotated-token")
        self.run_script()
        self.run_script()
        self.assertEqual(self.webui.connections[0]["key"], "rotated-token")
        self.assertEqual(len(self.webui.saves()), 2)

    def test_hand_made_connection_to_the_same_url_is_not_duplicated(self):
        self.webui.connections = [other_connection("My copy", MCP_URL)]
        self.run_script()
        self.assertEqual(len(self.webui.connections), 1)
        self.assertEqual(self.webui.connections[0]["info"]["id"], CONNECTION_ID)

    def test_unrelated_connections_survive_untouched_and_keep_their_order(self):
        first, last = other_connection("First"), other_connection("Last", "https://last.example.com/mcp")
        self.webui.connections = [first, last]
        self.run_script()
        self.assertEqual(self.webui.connections[:2], [first, last])
        self.assertEqual(self.webui.connections[2]["info"]["id"], CONNECTION_ID)
        self.run_script()
        self.assertEqual(len(self.webui.connections), 3)

    def test_managed_connection_keeps_its_position_when_updated(self):
        self.webui.connections = [other_connection("First")]
        self.run_script()
        self.webui.connections.append(other_connection("Last", "https://last.example.com/mcp"))
        self.run_script({"URL": "https://new.example.com/mcp"})
        self.assertEqual([c["info"]["id"] for c in self.webui.connections], ["first", CONNECTION_ID, "last"])

    def test_duplicates_from_an_earlier_mistake_collapse_to_one(self):
        self.webui.connections = [other_connection("Dup A", MCP_URL), other_connection("Dup B", MCP_URL)]
        self.run_script()
        self.assertEqual([c["info"]["id"] for c in self.webui.connections], [CONNECTION_ID])

    def test_check_mode_reports_and_never_writes(self):
        code, output = self.run_script(None, "--check")
        self.assertEqual(code, 0, output)
        self.assertIn("would change", output)
        self.assertEqual(self.webui.saves(), [])
        self.run_script()
        _, output = self.run_script(None, "--check")
        self.assertIn("already correct", output)
        self.assertEqual(len(self.webui.saves()), 1)


class DisabledTest(ScriptTestCase):
    def setUp(self):
        super().setUp()
        self.off = {"ENABLED": "false", "URL": "", "TOKEN_ARN": ""}

    def test_removes_an_existing_connection_and_keeps_the_others(self):
        self.run_script()
        keep = other_connection()
        self.webui.connections.insert(0, keep)
        code, output = self.run_script(self.off)
        self.assertEqual(code, 0, output)
        self.assertEqual(self.webui.connections, [keep])
        self.assertIn("removed", output)

    def test_absent_stays_absent_without_writing(self):
        self.webui.connections = [other_connection()]
        self.run_script(self.off)
        self.run_script(self.off)
        self.assertEqual(self.webui.saves(), [])
        self.assertEqual(len(self.webui.connections), 1)

    def test_disabled_never_reads_the_token_or_contacts_the_mcp_server(self):
        self.run_script()
        self.aws_dir.joinpath("requested").unlink()
        verifies = len(self.webui.posts("/api/v1/configs/tool_servers/verify"))
        self.run_script(self.off)
        self.assertNotIn(TOKEN_ARN, self.requested_secrets())
        self.assertEqual(len(self.webui.posts("/api/v1/configs/tool_servers/verify")), verifies)

    def test_off_on_off(self):
        self.run_script()
        self.assertEqual(len(self.webui.managed()), 1)
        self.run_script(self.off)
        self.assertEqual(self.webui.connections, [])
        self.run_script()
        self.assertEqual(len(self.webui.managed()), 1)
        self.run_script(self.off)
        self.assertEqual(self.webui.connections, [])


class StartupFailureTest(ScriptTestCase):
    def test_waits_for_open_webui_to_become_healthy(self):
        self.webui.health_failures = 2
        code, output = self.run_script()
        self.assertEqual(code, 0, output)
        self.assertEqual(output.count("Waiting for Open WebUI"), 2)
        self.assertEqual(len(self.webui.managed()), 1)

    def test_retries_when_open_webui_never_becomes_ready_then_gives_up(self):
        self.webui.health_failures = 1000
        code, output = self.run_script(retries=2)
        self.assertEqual(code, FAILED)
        self.assertIn("Attempt 2/2 failed", output)
        self.assertIn("Gave up", output)
        self.assertEqual(self.webui.connections, [])

    def test_retries_when_the_mcp_server_is_not_answering_yet(self):
        self.webui.verify_failures = 2
        code, output = self.run_script(retries=5)
        self.assertEqual(code, 0, output)
        self.assertEqual(output.count("could not connect to the MCP server"), 2)
        self.assertEqual(len(self.webui.managed()), 1)
        self.assertEqual(len(self.webui.saves()), 1)

    def test_retries_when_secrets_manager_fails_transiently(self):
        (self.aws_dir / "fail_first").write_text("1")
        code, output = self.run_script(retries=3)
        self.assertEqual(code, 0, output)
        self.assertEqual(len(self.webui.managed()), 1)

    def test_wrong_admin_password_fails_fast_without_retrying(self):
        self.set_secret(ADMIN_ARN, "wrong-password")
        code, output = self.run_script(retries=5)
        self.assertEqual(code, FAILED, output)
        self.assertEqual(len(self.webui.posts("/api/v1/auths/signin")), 1)
        self.assertNotIn("wrong-password", output)

    def test_open_webui_not_listening_at_all_gives_up_cleanly(self):
        code, output = self.run_script({"PORT": "9"}, retries=1)
        self.assertEqual(code, FAILED)
        self.assertIn("Gave up", output)


class LocalLoginTest(ScriptTestCase):
    def test_login_disabled_in_settings_skips_with_a_clear_message_and_touches_nothing(self):
        code, output = self.run_script({"LOCAL_LOGIN": "false"})
        self.assertEqual(code, CANNOT_AUTOMATE, output)
        self.assertIn("Admin Settings", output)
        self.assertEqual(self.webui.posts("/api/v1/auths/signin"), [])
        self.assertEqual(self.webui.saves(), [])
        self.assertEqual(self.requested_secrets(), [])

    def test_server_side_403_on_signin_is_reported_as_cannot_automate(self):
        self.webui.password_auth = False
        code, output = self.run_script(retries=5)
        self.assertEqual(code, CANNOT_AUTOMATE, output)
        self.assertEqual(len(self.webui.posts("/api/v1/auths/signin")), 1)
        self.assertIn("local login is disabled", output)


class ConfigTest(ScriptTestCase):
    def test_enabled_without_url_or_token_arn_is_an_error(self):
        for missing in ("URL", "TOKEN_ARN"):
            with self.subTest(missing=missing):
                code, output = self.run_script({missing: ""})
                self.assertEqual(code, FAILED)
                self.assertIn("missing", output)
        self.assertEqual(self.webui.requests, [])

    def test_missing_settings_file_and_bad_arguments_are_errors(self):
        self.assertEqual(self.run_script(write_env=False)[0], FAILED)
        self.assertEqual(self.run_script(None, "--nonsense")[0], FAILED)


class TerraformWiringTest(unittest.TestCase):
    """Static checks that the Terraform and cloud-init side matches the script and stays secret-free."""

    @classmethod
    def setUpClass(cls):
        cls.main_tf = (ROOT / "main.tf").read_text()
        cls.vuln_tf = (ROOT / "vuln_mcp.tf").read_text()
        cls.outputs_tf = (ROOT / "outputs.tf").read_text()
        cls.cloud_init = (ROOT / "cloud-init.sh.tpl").read_text()
        cls.script = SCRIPT.read_text()

    def test_sign_in_password_goes_to_curl_on_stdin_never_into_a_file(self):
        self.assertIn("umask 077", self.script)
        for line in self.script.splitlines():
            if "PW=" in line or "env.PW" in line:
                self.assertNotRegex(line, r">\s*\"?\$tmp", "the password must not be written to a file")
        self.assertIn("api POST /api/v1/auths/signin -", self.script)

    def env_file_block(self):
        return re.search(r"cat > /etc/ai-lab/vuln-mcp.env <<'EOF'\n(.*?)\nEOF", self.cloud_init, re.S).group(1)

    def test_settings_file_has_every_name_the_script_needs_and_only_arns_and_urls(self):
        written = dict(re.findall(r"^(\w+)='(.*)'$", self.env_file_block(), re.M))
        needed = {"ENABLED", "URL", "TOKEN_ARN", "ADMIN_ARN", "ADMIN_EMAIL", "LOCAL_LOGIN", "PORT", "REGION"}
        self.assertEqual(set(written), needed)
        self.assertTrue(all(re.fullmatch(r"\$\{[a-z_]+\}", v) for v in written.values()))
        self.assertIn("${open_webui_admin_password_secret_arn}", written["ADMIN_ARN"])
        for name in needed:
            self.assertRegex(self.script, r"\$\{?%s\b" % name)

    def test_every_template_variable_used_is_passed_by_main_tf(self):
        used = set(re.findall(r"\$\{(vuln_mcp_\w+)\}", self.cloud_init))
        passed = set(re.findall(r"^\s+(vuln_mcp_\w+)\s+=", self.main_tf, re.M))
        self.assertTrue(used)
        self.assertLessEqual(used, passed)

    def test_user_data_never_resolves_the_token_itself(self):
        self.assertNotIn("data.aws_secretsmanager_secret_version", self.main_tf + self.vuln_tf)
        self.assertNotIn("ephemeral", self.vuln_tf)
        self.assertNotRegex(self.cloud_init, r"(?i)(token|mcp).{0,40}--secret-id")
        self.assertNotRegex(self.main_tf, r"vuln_mcp_token\s+=")

    def test_token_secret_is_only_readable_by_the_instance_when_the_feature_is_on(self):
        policy = re.search(r'resource "aws_iam_role_policy" "open_webui_admin_password".*?\n}\n', self.main_tf, re.S).group(0)
        self.assertIn("var.vuln_mcp_token_secret_arn", policy)
        self.assertIn("var.vuln_mcp_table_name != null", policy)
        self.assertNotIn("dynamodb", policy)

    def test_no_output_exposes_the_token(self):
        for name in ("vuln_mcp_url", "vuln_mcp_function_name", "vuln_mcp_log_group"):
            block = re.search(r'output "%s" \{.*?\n\}' % name, self.outputs_tf, re.S).group(0)
            self.assertNotIn("secret", block.split("value", 1)[1])
        self.assertNotRegex(self.outputs_tf, r'output "vuln_mcp_token"')

    def test_cloud_init_runs_registration_after_open_webui_is_up_and_without_failing_bootstrap(self):
        call = "/usr/local/sbin/ai-lab-register-vuln-mcp || register_status=$?"
        self.assertIn(call, self.cloud_init)
        self.assertGreater(self.cloud_init.index(call), self.cloud_init.rindex("\nwait_for_open_webui\n"))
        self.assertLess(self.cloud_init.index(call), self.cloud_init.index('touch "$READY_FILE"'))

    def test_script_is_substituted_as_a_value_inside_a_quoted_heredoc(self):
        self.assertIn("<<'VULN_MCP_REGISTER_EOF'\n${vuln_mcp_register_script}\nVULN_MCP_REGISTER_EOF", self.cloud_init)
        self.assertNotIn("VULN_MCP_REGISTER_EOF", self.script)


class UserDataBudgetTest(unittest.TestCase):
    """EC2 rejects user data over 16384 bytes (after gzip). Render the template the way Terraform does,
    with deliberately generous values, and keep a safety margin."""

    BUDGET = 14500

    def test_rendered_user_data_stays_inside_the_ec2_limit_with_margin(self):
        import base64
        import gzip

        template = (ROOT / "cloud-init.sh.tpl").read_text()
        b64 = lambda text: base64.b64encode(text.encode()).decode()  # noqa: E731
        b64gz = lambda text: base64.b64encode(gzip.compress(text.encode(), 9)).decode()  # noqa: E731
        read = lambda name: (ROOT / "scripts" / name).read_text()  # noqa: E731
        demo_users = json.dumps([{"email": f"demo{i}@example.local", "name": f"Demo User {i}"} for i in range(1, 26)])
        banner = json.dumps([{"id": "security-notice", "type": "warning", "title": "Security notice",
                              "content": "x" * 800, "dismissible": False, "timestamp": 0}])
        values = {
            "open_webui_demo_users_b64": b64(demo_users), "open_webui_banners_b64": b64(banner),
            "auto_stop_script_b64": b64gz(read("ai-lab-idle-check.sh")),
            "alloy_config_b64": b64gz(read("alloy-config.alloy")),
            "vuln_mcp_register_script": read("ai-lab-register-vuln-mcp.sh"),
            "vuln_mcp_url": "https://abcdefghijklmnopqrstuvwxyz012345.lambda-url.us-east-1.on.aws/mcp",
        }
        text = template.replace("$${", "\0DS{").replace("%%{", "%{")
        text = re.sub(r"\$\{([a-z_0-9]+)\}", lambda m: values.get(m.group(1), "v" * 40), text).replace("\0DS{", "${")
        size = len(gzip.compress(text.encode(), 9))
        self.assertLess(size, self.BUDGET, f"user data is {size} bytes gzipped; the EC2 limit is 16384")


def _mcp_available():
    try:
        # Open WebUI v0.11.4 uses this client; newer SDK majors renamed it.
        from mcp.client.streamable_http import streamablehttp_client  # noqa: F401
        return True
    except ImportError:
        return False


@contextlib.contextmanager
def warnings_ignored():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        yield


@unittest.skipUnless(HAVE_TOOLS and _mcp_available(), "needs bash, curl, jq and mcp==1.27.2 (the version Open WebUI v0.11.4 bundles)")
class McpSdkIntegrationTest(ScriptTestCase):
    """The fake Open WebUI's verify endpoint uses the real MCP client against the real handler."""

    def setUp(self):
        super().setUp()
        paths = [str(ROOT / "lambda" / "vuln_mcp"), str(ROOT / "tests")]
        sys.path[:0] = paths
        self.addCleanup(lambda: [sys.path.remove(p) for p in paths])
        import local_server
        import mcp_handler
        from test_vuln_mcp import FakeRepo
        from vuln_tools import VulnTools

        saved = (mcp_handler._tools, mcp_handler._token_cache)
        mcp_handler._tools, mcp_handler._token_cache = VulnTools(FakeRepo()), None
        self.addCleanup(lambda: setattr(mcp_handler, "_tools", saved[0]))
        self.addCleanup(lambda: setattr(mcp_handler, "_token_cache", saved[1]))
        env = mock.patch.dict("os.environ", {"AUTH_TOKEN": MCP_TOKEN})
        env.start()
        self.addCleanup(env.stop)

        self.mcp_server = ThreadingHTTPServer(("127.0.0.1", 0), local_server.Handler)
        threading.Thread(target=self.mcp_server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        self.addCleanup(self.mcp_server.server_close)
        self.addCleanup(self.mcp_server.shutdown)
        self.settings["URL"] = f"http://127.0.0.1:{self.mcp_server.server_port}/mcp"
        self.webui.verify_hook = self.verify_like_open_webui
        self.answers = []

    def verify_like_open_webui(self, body):
        """What Open WebUI's verify endpoint does: connect with the bearer token and list the tools."""
        import asyncio

        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async def go():
            async with streamablehttp_client(body["url"], headers={"Authorization": f"Bearer {body['key']}"}) as (r, w, _):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    names = [tool.name for tool in (await session.list_tools()).tools]
                    call = await session.call_tool("summarize_findings", {"group_by": "severity", "status": "open"})
                    self.answers.append(json.loads(call.content[0].text))
                    return names

        with warnings_ignored():
            return asyncio.run(go())

    def test_real_client_authenticates_discovers_six_tools_and_executes_a_call(self):
        code, output = self.run_script()
        self.assertEqual(code, 0, output)
        self.assertIn(", ".join(SIX_TOOLS), output)
        [summary] = self.answers
        counts = {g["value"]: g["count"] for g in summary["groups"]}
        self.assertEqual(counts["Critical"], 5)  # the fixture has 5 open Critical findings
        self.assertEqual(len(self.webui.managed()), 1)

    def test_wrong_bearer_token_is_refused_by_the_server_and_nothing_is_registered(self):
        self.set_secret(TOKEN_ARN, "not-the-token")
        code, _ = self.run_script(retries=2)
        self.assertEqual(code, FAILED)
        self.assertEqual(self.webui.connections, [])
        self.assertEqual(self.answers, [])


if __name__ == "__main__":
    unittest.main()
