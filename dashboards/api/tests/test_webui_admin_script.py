"""Runs webui-admin.sh (the script inside the panel's SSM document) under bash, with Open WebUI
replaced by a stub curl. SSM substitutes {{ action }} and {{ expectedVersion }} after checking
them against the document's allowed values; the test does the same substitution
(including {{ sinceHour }} for the usage action).

Run from dashboards/api:   python -m pytest -q tests/test_webui_admin_script.py
"""

import base64
import json
import os
import pathlib
import shutil
import subprocess

import pytest

SCRIPT = (pathlib.Path(__file__).resolve().parent.parent / "webui-admin.sh").read_text()

CURL = """#!/usr/bin/env bash
url="${@: -1}"
case "$url" in
  */health) [[ "$HEALTH" == up ]] || exit 7 ;;
  */api/version) [[ "$HEALTH" == up ]] || exit 7; echo "{\\"version\\": \\"$VERSION\\"}" ;;
  */api/tags) [[ "$OLLAMA" == up ]] || exit 7; echo '{"models":[{"name":"qwen3:14b","size":9300000000}]}' ;;
  */api/ps) [[ "$OLLAMA" == up ]] || exit 7; echo '{"models":[{"name":"qwen3:14b","size":10500000000,"size_vram":10500000000}]}' ;;
esac
"""

pytestmark = pytest.mark.skipif(not (shutil.which("bash") and shutil.which("jq")), reason="needs bash and jq")


def run(tmp_path, action="status", expected="0.11.4", since="", health="up", ollama="up", version="0.11.4", settings="PORT='8080'\n",
        user_id="", role="", helper=None, model="", chat_helper=None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "curl").write_text(CURL)
    (bin_dir / "curl").chmod(0o755)
    env_file = tmp_path / "settings.env"
    if settings is not None:
        env_file.write_text(settings)
    script = SCRIPT.replace("{{ action }}", action).replace("{{ expectedVersion }}", expected).replace("{{ sinceHour }}", since)
    script = script.replace("{{ userId }}", user_id).replace("{{ role }}", role).replace("{{ model }}", model)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", AI_LAB_SETTINGS_FILE=str(env_file),
               HEALTH=health, OLLAMA=ollama, VERSION=version)
    if helper is not None:
        env["AI_LAB_SET_ROLE"] = str(helper)
    if chat_helper is not None:
        env["AI_LAB_CHAT_TEST"] = str(chat_helper)
    done = subprocess.run(["bash", "-s"], input=script, env=env, capture_output=True, text=True, timeout=30)
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, done.stdout + done.stderr  # exactly one JSON line
    return done.returncode, json.loads(lines[0])


def test_status_reports_health_and_a_matching_version(tmp_path):
    code, out = run(tmp_path)
    assert code == 0
    assert out == {"ok": True, "action": "status", "healthy": True, "version": "0.11.4",
                   "expectedVersion": "0.11.4", "versionMatches": True,
                   "ollama": {"installed": [{"name": "qwen3:14b", "sizeBytes": 9300000000}],
                              "loaded": [{"name": "qwen3:14b", "sizeBytes": 10500000000, "vramBytes": 10500000000}]}}


def test_ollama_not_answering_is_null_not_an_error(tmp_path):
    code, out = run(tmp_path, ollama="down")
    assert code == 0 and out["ok"] is True and out["healthy"] is True and out["ollama"] is None


def test_a_different_running_version_is_reported_as_a_mismatch(tmp_path):
    _, out = run(tmp_path, version="v0.12.0")
    assert out["version"] == "0.12.0" and out["versionMatches"] is False


def test_an_unpinned_image_leaves_the_match_unknown(tmp_path):
    _, out = run(tmp_path, expected="")
    assert out["expectedVersion"] is None and out["versionMatches"] is None


def test_open_webui_down_is_reported_not_crashed(tmp_path):
    code, out = run(tmp_path, health="down")
    assert code == 0 and out["healthy"] is False and out["version"] is None and out["versionMatches"] is None


def test_missing_settings_or_port_fails_cleanly(tmp_path):
    code, out = run(tmp_path, settings=None)
    assert code == 1 and out["ok"] is False and "settings file" in out["error"]
    code, out = run(tmp_path, settings="PORT='8080; rm -rf /'\n")
    assert code == 1 and out["ok"] is False and "no Open WebUI port" in out["error"]


def test_an_action_the_script_does_not_know_is_refused(tmp_path):
    code, out = run(tmp_path, action="delete-everything")
    assert code == 1 and out == {"ok": False, "action": "delete-everything", "error": "Unknown action."}


def test_the_script_never_sources_the_settings_file():
    # The settings file is root-owned, but the script reads one value from it rather than
    # executing it, so a bad line there cannot run as a command through the panel.
    code = [line.strip() for line in SCRIPT.splitlines() if line.strip() and not line.strip().startswith("#")]
    # The one thing it sources is the helper library, by its own variable.
    assert not any(line.startswith((". ", "source ")) and '"$LIB"' not in line for line in code)
    assert not any("SETTINGS_FILE" in line and line.startswith((". ", "source ")) for line in code)


def helper_script(tmp_path, body, code=0):
    path = tmp_path / "set-role"
    path.write_text(f"#!/usr/bin/env bash\n{body}\nexit {code}\n")
    path.chmod(0o755)
    return path


def test_set_role_passes_the_user_and_role_to_the_helper_and_adds_the_action(tmp_path):
    helper = helper_script(tmp_path, 'echo "{\\"ok\\": true, \\"userId\\": \\"$1\\", \\"role\\": \\"$2\\", \\"changed\\": true}"')
    code, out = run(tmp_path, action="set-role", user_id="u-1", role="pending", helper=helper)
    assert code == 0 and out == {"ok": True, "userId": "u-1", "role": "pending", "changed": True, "action": "set-role"}


def test_set_role_reports_a_refusal_and_still_fails_the_command(tmp_path):
    helper = helper_script(tmp_path, 'echo \'{"ok": false, "error": "That user is an administrator and is never changed."}\'', code=1)
    code, out = run(tmp_path, action="set-role", user_id="u-1", role="pending", helper=helper)
    assert code == 1 and out["ok"] is False and out["action"] == "set-role" and "administrator" in out["error"]


def test_set_role_needs_a_user_and_a_role_and_an_installed_helper(tmp_path):
    helper = helper_script(tmp_path, "echo '{}'")
    code, out = run(tmp_path, action="set-role", helper=helper)
    assert code == 1 and "required" in out["error"]
    code, out = run(tmp_path, action="set-role", user_id="u-1", role="user", helper=tmp_path / "missing")
    assert code == 1 and "not installed" in out["error"]


def test_chat_test_passes_the_model_to_the_helper_and_adds_the_action(tmp_path):
    helper = helper_script(tmp_path, 'echo "{\\"ok\\": true, \\"model\\": \\"$1\\", \\"reply\\": \\"ready\\"}"')
    code, out = run(tmp_path, action="chat-test", model="qwen3:14b", chat_helper=helper)
    assert code == 0 and out == {"ok": True, "model": "qwen3:14b", "reply": "ready", "action": "chat-test"}


def test_chat_test_reports_a_failure_and_needs_a_model_and_an_installed_helper(tmp_path):
    helper = helper_script(tmp_path, 'echo \'{"ok": false, "error": "Open WebUI did not return a reply."}\'', code=1)
    code, out = run(tmp_path, action="chat-test", model="m", chat_helper=helper)
    assert code == 1 and out["ok"] is False and out["action"] == "chat-test"
    code, out = run(tmp_path, action="chat-test", chat_helper=helper)
    assert code == 1 and "required" in out["error"]
    code, out = run(tmp_path, action="chat-test", model="m", chat_helper=tmp_path / "missing")
    assert code == 1 and "not installed" in out["error"]


# ---- the actions that sign in to Open WebUI: tool-servers, settings, export-config ----

SIGNED_IN_CURL = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
url = args[-1]
cfg = json.load(open(os.environ["STUB_CONFIG"]))
body = ""
for i, a in enumerate(args):
    if a == "--data-binary" and args[i + 1].startswith("@") and args[i + 1] != "@-":
        body = open(args[i + 1][1:]).read()
method = args[args.index("-X") + 1] if "-X" in args else "GET"
open(os.environ["STUB_DIR"] + "/calls", "a").write(method + " " + url.split("127.0.0.1:8080")[-1] + "\n")
out = args[args.index("-o") + 1] if "-o" in args else None
def reply(status, data=None):
    text = data if isinstance(data, str) else json.dumps(data if data is not None else {})
    if out: open(out, "w").write(text)
    elif status == 200: sys.stdout.write(text)
    if "-w" in args: sys.stdout.write(str(status))
    sys.exit(0 if status == 200 or "-w" in args else 22)
path = url.split("127.0.0.1:8080")[-1]
if path == "/health": reply(200, "{}")
if path == "/api/version": reply(200, {"version": "0.11.4"})
if path == "/api/v1/auths/signin": reply(cfg.get("signin", 200), {"token": "tok-secret-1"})
if path == "/api/v1/auths/": reply(200, {"id": "u-admin"})
if path == "/api/v1/configs/tool_servers/verify":
    sent = json.loads(body)
    reply(200, cfg["verify"].get(sent.get("url"), {"status": False}))
WRITES = {"/api/v1/configs/models": "/api/v1/configs/models", "/api/v1/auths/admin/config": "/api/v1/auths/admin/config",
          "/api/v1/images/config/update": "/api/v1/images/config", "/api/v1/users/default/permissions": "/api/v1/users/default/permissions",
          "/api/v1/configs/tool_servers": "/api/v1/configs/tool_servers"}
if method == "POST":
    open(os.environ["STUB_DIR"] + "/posts", "a").write(path + " " + body + "\n")
    if path in cfg.get("refuse", []): reply(500, {})
    data = json.loads(body) if body else {}
    if path in WRITES:
        if path not in cfg.get("drop", []):
            cfg["get"][WRITES[path]] = data
            json.dump(cfg, open(os.environ["STUB_CONFIG"], "w"))
        reply(200, data)
    if path == "/api/v1/skills/create" or (path.startswith("/api/v1/skills/id/") and path.endswith("/update")):
        if path not in cfg.get("drop", []):
            rec = dict(data, user_id="u-admin", access_grants=[dict(g, id="grant-1") for g in (data.get("access_grants") or [])])
            if cfg.get("hide_content"): rec.pop("content", None)
            cfg["get"]["/api/v1/skills/id/" + data["id"]] = rec
            json.dump(cfg, open(os.environ["STUB_CONFIG"], "w"))
        reply(200, data)
    if path in ("/api/v1/models/model/update", "/api/v1/models/create"):
        if path not in cfg.get("drop", []):
            cfg["get"]["/api/v1/models/model?id=" + data["id"]] = data
            listing = cfg["get"]["/api/v1/models"]["data"]
            if not any(m["id"] == data["id"] for m in listing): listing.append({"id": data["id"]})
            json.dump(cfg, open(os.environ["STUB_CONFIG"], "w"))
        reply(200, data)
if path in cfg["get"]: reply(200, cfg["get"][path])
reply(404, {})
'''
AWS_STUB = "#!/usr/bin/env bash\necho 'admin-pass-123'\n"
SETTINGS = ("PORT='8080'\nREGION='us-east-1'\nADMIN_ARN='arn:aws:secretsmanager:us-east-1:1:secret:x'\n"
            "ADMIN_EMAIL='admin@lab.test'\nLOCAL_LOGIN='true'\n")
LIB_PATH = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "ai-lab-webui-lib.sh"

TOOL_SERVERS = {"TOOL_SERVER_CONNECTIONS": [
    {"url": "https://user:pw@mcp.example.com/mcp?token=abc#frag", "path": "", "type": "mcp", "auth_type": "bearer", "key": "SECRET-KEY-1",
     "headers": {"X-Api": "SECRET-HEADER"}, "forward_cookies": False, "config": {"enable": True}, "info": {"id": "vuln-findings", "name": "Vulnerability Findings"}},
    {"url": "https://down.example.com", "path": "openapi.json", "type": "openapi", "auth_type": "none", "config": {"enable": True}, "info": {"id": "down", "name": "Down"}},
    {"url": "https://off.example.com", "type": "openapi", "config": {"enable": False}, "info": {"id": "off", "name": "Off"}},
]}
GETS = {
    "/api/v1/configs/tool_servers": TOOL_SERVERS,
    "/api/v1/auths/admin/config": {"ENABLE_SIGNUP": False, "DEFAULT_USER_ROLE": "pending", "JWT_EXPIRES_IN": "4w", "WEBHOOK_URL": "https://hooks.example.com/SECRET-HOOK",
                                   "ENABLE_API_KEY": True, "SHOW_ADMIN_DETAILS": True, "LDAP_SERVER_PASSWORD": "SECRET-LDAP"},
    "/api/v1/configs/models": {"DEFAULT_MODELS": "qwen3:14b", "MODEL_ORDER_LIST": ["a"]},
    "/api/v1/images/config": {"ENABLED": False, "IMAGES_OPENAI_API_KEY": "SECRET-IMG"},
    "/api/v1/users/default/permissions": {"workspace": {"models": False, "tools": False}, "chat": {"delete": True, "temporary": "x"}, "note": "not a group"},
    "/api/v1/models": {"data": [{"id": "security-analyst", "name": "Security Analyst", "info": {"base_model_id": "qwen3:14b",
                                 "params": {"temperature": 0.6, "system": "SECRET-PROMPT", "stop": ["x"], "use_mmap": True}}}, {"id": "qwen3:14b", "name": "qwen3:14b"}]},
}
VERIFY = {"https://user:pw@mcp.example.com/mcp?token=abc#frag": {"status": True, "specs": [{"name": "get_finding"}, {"name": "list_hosts"}]}}


def run_signed_in(tmp_path, action, gets=None, verify=None, signin=200, settings=SETTINGS, login_off=False, payload="", prefix="", refuse=(), drop=(), hide_content=False):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "curl").write_text(SIGNED_IN_CURL)
    (bin_dir / "curl").chmod(0o755)
    (bin_dir / "aws").write_text(AWS_STUB)
    (bin_dir / "aws").chmod(0o755)
    (tmp_path / "cfg.json").write_text(json.dumps({"get": GETS if gets is None else gets, "verify": VERIFY if verify is None else verify, "signin": signin,
                                                 "refuse": list(refuse), "drop": list(drop), "hide_content": hide_content}))
    env_file = tmp_path / "settings.env"
    env_file.write_text(settings.replace("'true'", "'false'") if login_off else settings)
    lib = tmp_path / "webui-lib.sh"
    lib.write_text(LIB_PATH.read_text().replace("\r\n", "\n"))
    ready = tmp_path / "ready"
    ready.write_text("")
    script = SCRIPT.replace("{{ action }}", action).replace("{{ expectedVersion }}", "0.11.4")
    for name in ("sinceHour", "userId", "role", "model"):
        script = script.replace("{{ %s }}" % name, "")
    script = script.replace("{{ payload }}", base64.b64encode(json.dumps(payload).encode()).decode() if payload != "" else "").replace("{{ toolTokenPrefix }}", prefix)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", AI_LAB_SETTINGS_FILE=str(env_file), AI_LAB_WEBUI_LIB=str(lib),
               AI_LAB_READY_FILE=str(ready), AI_LAB_STATE_DIR=str(tmp_path / "state"), STUB_CONFIG=str(tmp_path / "cfg.json"), STUB_DIR=str(tmp_path))
    done = subprocess.run(["bash", "-s"], input=script, env=env, capture_output=True, text=True, timeout=60)
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, done.stdout + done.stderr
    return done.returncode, json.loads(lines[0]), done.stdout


def test_tool_servers_are_listed_and_each_enabled_one_is_tested(tmp_path):
    code, out, raw = run_signed_in(tmp_path, "tool-servers")
    assert code == 0 and out["ok"] is True and out["signedIn"] is True and out["total"] == 3
    mcp, down, off = out["servers"]
    assert mcp == {"id": "vuln-findings", "name": "Vulnerability Findings", "type": "mcp", "enabled": True, "url": "https://mcp.example.com/mcp", "path": "",
                   "authType": "bearer", "forwardCookies": False, "reachable": True, "toolCount": 2, "toolNames": ["get_finding", "list_hosts"], "error": None}
    assert down["reachable"] is False and down["toolCount"] is None and "could not connect" in down["error"]
    assert off["enabled"] is False and off["reachable"] is None and off["error"] is None
    # Only the enabled servers are asked to connect.
    verified = [line for line in (tmp_path / "calls").read_text().splitlines() if line == "POST /api/v1/configs/tool_servers/verify"]
    assert len(verified) == 2
    for secret in ("SECRET-KEY-1", "SECRET-HEADER", "user:pw", "token=abc", "admin-pass-123", "tok-secret-1"):
        assert secret not in raw


def test_only_the_first_five_enabled_tool_servers_are_tested(tmp_path):
    many = {"TOOL_SERVER_CONNECTIONS": [{"url": f"https://s{i}.example.com", "type": "mcp", "config": {"enable": True}, "info": {"id": f"s{i}"}} for i in range(7)]}
    gets = dict(GETS, **{"/api/v1/configs/tool_servers": many})
    _, out, _ = run_signed_in(tmp_path, "tool-servers", gets=gets)
    assert out["total"] == 7 and sum(1 for s in out["servers"] if s["reachable"] is False) == 5
    assert [s["error"] for s in out["servers"][5:]] == ["Not checked this time (only the first five enabled servers are)."] * 2


def test_settings_are_marked_global_or_user_default_and_only_the_fixed_list_is_read(tmp_path):
    code, out, raw = run_signed_in(tmp_path, "settings")
    assert code == 0 and out["ok"] is True and out["unavailable"] == []
    by_key = {s["key"]: s for s in out["settings"]}
    assert by_key["ENABLE_SIGNUP"] == {"key": "ENABLE_SIGNUP", "value": False, "scope": "Global"}
    assert by_key["DEFAULT_MODELS"]["value"] == "qwen3:14b" and by_key["IMAGE_GENERATION_ENABLED"]["value"] is False
    assert by_key["workspace.models"] == {"key": "workspace.models", "value": False, "scope": "User default"}
    assert by_key["chat.delete"]["value"] is True and "chat.temporary" not in by_key
    for hidden in ("WEBHOOK_URL", "LDAP_SERVER_PASSWORD", "MODEL_ORDER_LIST", "IMAGES_OPENAI_API_KEY", "SECRET-HOOK", "SECRET-LDAP", "SECRET-IMG", "note"):
        assert hidden not in raw


def test_settings_that_cannot_be_read_are_named_not_hidden(tmp_path):
    gets = {k: v for k, v in GETS.items() if k not in ("/api/v1/images/config", "/api/v1/users/default/permissions")}
    _, out, _ = run_signed_in(tmp_path, "settings", gets=gets)
    assert out["ok"] is True and out["unavailable"] == ["Image generation", "Default permissions for new users"]


def test_the_export_leaves_secrets_out_and_says_what_it_omits(tmp_path):
    code, out, raw = run_signed_in(tmp_path, "export-config")
    assert code == 0 and out["ok"] is True and out["action"] == "export-config" and out["openWebuiVersion"] == "0.11.4" and out["truncated"] is False
    assert "passwords" in out["omitted"] and "system prompts" in out["omitted"]
    assert out["toolServers"][0]["url"] == "https://mcp.example.com/mcp" and "key" not in out["toolServers"][0]
    analyst = next(m for m in out["models"] if m["id"] == "security-analyst")
    assert analyst == {"id": "security-analyst", "name": "Security Analyst", "baseModel": "qwen3:14b", "params": {"temperature": 0.6, "use_mmap": True}}
    assert any(s["key"] == "ENABLE_SIGNUP" for s in out["settings"])
    for secret in ("SECRET-KEY-1", "SECRET-HEADER", "SECRET-PROMPT", "SECRET-HOOK", "SECRET-LDAP", "SECRET-IMG", "user:pw", "admin-pass-123", "tok-secret-1"):
        assert secret not in raw


def test_an_export_that_is_too_big_drops_model_detail_then_refuses(tmp_path):
    many = {"data": [{"id": f"model-{i}", "name": "n" * 90, "info": {"base_model_id": "b" * 90, "params": {f"p{j}": j for j in range(30)}}} for i in range(200)]}
    _, out, _ = run_signed_in(tmp_path, "export-config", gets=dict(GETS, **{"/api/v1/models": many}))
    assert out["ok"] is True and out["truncated"] is True and set(out["models"][0]) == {"id"}
    huge = {"data": [{"id": "m" * 100 + str(i)} for i in range(400)]}
    code, out, _ = run_signed_in(tmp_path, "export-config", gets=dict(GETS, **{"/api/v1/models": huge}))
    assert code == 1 and out["ok"] is False and "too large" in out["error"]


def test_sign_in_problems_are_reported_in_plain_words(tmp_path):
    code, out, _ = run_signed_in(tmp_path, "settings", login_off=True)
    assert code == 1 and "Password login is off" in out["error"]
    code, out, _ = run_signed_in(tmp_path, "tool-servers", signin=401)
    assert code == 1 and "could not sign in" in out["error"]
    code, out, _ = run_signed_in(tmp_path, "tool-servers", gets={k: v for k, v in GETS.items() if k != "/api/v1/configs/tool_servers"})
    assert code == 1 and "Could not read the tool servers" in out["error"]


def test_these_actions_only_read(tmp_path):
    for action in ("tool-servers", "settings", "export-config"):
        run_signed_in(tmp_path, action)
    posts = {line for line in (tmp_path / "calls").read_text().splitlines() if line.startswith("POST")}
    # The sign-in and the connection test are the only POSTs; nothing is written to Open WebUI.
    assert posts == {"POST /api/v1/auths/signin", "POST /api/v1/configs/tool_servers/verify"}


def test_the_script_has_no_double_brace_that_ssm_would_read_as_a_parameter():
    stray = [line for line in SCRIPT.splitlines() if "{{" in line and not line.startswith(("ACTION=", "EXPECTED_VERSION=", "SINCE_HOUR=", "USER_ID=", "ROLE=", "MODEL=", "PAYLOAD=", "TOOL_TOKEN_PREFIX="))]
    assert stray == []


# ---- write actions ----

def state(tmp_path, key):
    return json.loads((tmp_path / "cfg.json").read_text())["get"][key]


def posts(tmp_path):
    f = tmp_path / "posts"
    return f.read_text().splitlines() if f.exists() else []


def test_default_model_is_set_and_read_back(tmp_path):
    code, out, _ = run_signed_in(tmp_path, "set-default-model", payload={"model": "qwen3:14b"})
    assert code == 0 and out == {"ok": True, "kind": "default-model", "model": "qwen3:14b", "scope": "Global", "action": "set-default-model"}
    assert state(tmp_path, "/api/v1/configs/models") == {"DEFAULT_MODELS": "qwen3:14b", "MODEL_ORDER_LIST": ["a"]}  # other settings kept


def test_default_model_must_be_installed_and_must_stick(tmp_path):
    code, out, _ = run_signed_in(tmp_path, "set-default-model", payload={"model": "not-installed"})
    assert code == 1 and "not installed" in out["error"] and posts(tmp_path) == [p for p in posts(tmp_path) if "configs/models" not in p]
    code, out, _ = run_signed_in(tmp_path, "set-default-model", payload={"model": "security-analyst"}, drop=["/api/v1/configs/models"])
    assert code == 1 and "did not keep" in out["error"]
    code, out, _ = run_signed_in(tmp_path, "set-default-model", payload={"model": "qwen3:14b"}, refuse=["/api/v1/configs/models"])
    assert code == 1 and "refused" in out["error"]


def test_model_params_update_an_existing_model_and_keep_its_other_fields(tmp_path):
    gets = dict(GETS)
    gets["/api/v1/models"] = {"data": [{"id": "analyst2"}, {"id": "qwen3:14b"}]}
    gets["/api/v1/models/model?id=analyst2"] = {"id": "analyst2", "name": "Analyst 2", "base_model_id": "qwen3:14b", "meta": {"description": "x"},
                                                  "params": {"system": "KEEP-ME", "top_k": 20, "temperature": 0.9}, "created_at": 5, "updated_at": 6}
    code, out, _ = run_signed_in(tmp_path, "set-model-params", gets=gets, payload={"model": "analyst2", "temperature": 0.4, "numCtx": 16384})
    assert code == 0 and out["change"] == "update" and out["params"] == {"temperature": 0.4, "num_ctx": 16384}
    saved = state(tmp_path, "/api/v1/models/model?id=analyst2")
    assert saved["params"] == {"system": "KEEP-ME", "top_k": 20, "temperature": 0.4, "num_ctx": 16384} and saved["meta"] == {"description": "x"}
    assert "created_at" not in saved and "updated_at" not in saved


def test_model_params_for_an_installed_model_with_no_entry_create_one(tmp_path):
    code, out, _ = run_signed_in(tmp_path, "set-model-params", payload={"model": "qwen3:14b", "temperature": 0.2})
    assert code == 0 and out["change"] == "create"
    assert state(tmp_path, "/api/v1/models/model?id=qwen3:14b")["params"] == {"temperature": 0.2}
    assert any(p.startswith("/api/v1/models/create ") for p in posts(tmp_path))


def test_model_params_refuse_bad_ranges_unknown_and_boot_managed_models(tmp_path):
    for bad in ({"temperature": 2.5}, {"temperature": -1}, {"numCtx": 100}, {"numCtx": 1e7}, {"numCtx": 4096.5}, {"temperature": "hot"}, {}):
        code, out, _ = run_signed_in(tmp_path, "set-model-params", payload={"model": "qwen3:14b", **bad})
        assert code == 1 and out["ok"] is False, bad
    code, out, _ = run_signed_in(tmp_path, "set-model-params", payload={"model": "security-analyst", "temperature": 0.5})
    assert code == 1 and "overwritten" in out["error"]
    code, out, _ = run_signed_in(tmp_path, "set-model-params", payload={"model": "ghost", "temperature": 0.5})
    assert code == 1 and "not installed" in out["error"]
    assert posts(tmp_path) == [p for p in posts(tmp_path) if "models" not in p.split(" ")[0]]


def test_features_change_one_setting_and_read_it_back(tmp_path):
    cases = [
        ({"feature": "api_keys", "value": False}, "/api/v1/auths/admin/config", "ENABLE_API_KEY", False, "Global"),
        ({"feature": "message_rating", "value": True}, "/api/v1/auths/admin/config", None, None, "Global"),
        ({"feature": "image_generation", "value": True}, "/api/v1/images/config", "ENABLED", True, "Global"),
    ]
    gets = dict(GETS)
    gets["/api/v1/auths/admin/config"] = dict(GETS["/api/v1/auths/admin/config"], ENABLE_MESSAGE_RATING=False,
                                               ENABLE_API_KEY_ENDPOINT_RESTRICTIONS=False, API_KEY_ALLOWED_ENDPOINTS="")
    for payload, endpoint, key, value, scope in cases:
        code, out, _ = run_signed_in(tmp_path, "set-feature", gets=gets, payload=payload)
        assert code == 0 and out["feature"] == payload["feature"] and out["scope"] == scope, out
        if key:
            assert state(tmp_path, endpoint)[key] is value
    # Other fields of the same object are left alone.
    assert state(tmp_path, "/api/v1/auths/admin/config")["DEFAULT_USER_ROLE"] == "pending"


def test_api_key_routes_and_memory_and_unsupported_settings(tmp_path):
    gets = dict(GETS)
    gets["/api/v1/auths/admin/config"] = dict(GETS["/api/v1/auths/admin/config"], ENABLE_API_KEY_ENDPOINT_RESTRICTIONS=False, API_KEY_ALLOWED_ENDPOINTS="")
    code, out, _ = run_signed_in(tmp_path, "set-feature", gets=gets, payload={"feature": "api_key_routes", "value": ["/api/chat/completions", "/api/models"]})
    assert code == 0
    saved = state(tmp_path, "/api/v1/auths/admin/config")
    assert saved["ENABLE_API_KEY_ENDPOINT_RESTRICTIONS"] is True and saved["API_KEY_ALLOWED_ENDPOINTS"] == "/api/chat/completions,/api/models"
    code, out, _ = run_signed_in(tmp_path, "set-feature", gets=gets, payload={"feature": "api_key_routes", "value": []})
    assert code == 0 and state(tmp_path, "/api/v1/auths/admin/config")["ENABLE_API_KEY_ENDPOINT_RESTRICTIONS"] is False
    for bad in (["no-slash"], ["/a b"], ["/x"] * 21, "all", [1]):
        code, out, _ = run_signed_in(tmp_path, "set-feature", gets=gets, payload={"feature": "api_key_routes", "value": bad})
        assert code == 1 and "Routes must be" in out["error"], bad
    # Memory is a user default and only if this Open WebUI has that permission.
    perms = dict(GETS["/api/v1/users/default/permissions"], features={"memories": True, "notes": True})
    code, out, _ = run_signed_in(tmp_path, "set-feature", gets=dict(gets, **{"/api/v1/users/default/permissions": perms}), payload={"feature": "memory", "value": False})
    assert code == 0 and out["scope"] == "User default" and state(tmp_path, "/api/v1/users/default/permissions")["features"] == {"memories": False, "notes": True}
    code, out, _ = run_signed_in(tmp_path, "set-feature", gets=gets, payload={"feature": "memory", "value": False})
    assert code == 1 and "does not have that setting" in out["error"]
    code, out, _ = run_signed_in(tmp_path, "set-feature", gets=gets, payload={"feature": "message_rating", "value": True})
    assert code == 1 and "does not have that setting" in out["error"]  # this fixture has no such key
    code, out, _ = run_signed_in(tmp_path, "set-feature", gets=gets, payload={"feature": "api_keys", "value": "yes"})
    assert code == 1 and "on or off" in out["error"]
    code, out, _ = run_signed_in(tmp_path, "set-feature", gets=gets, payload={"feature": "root_shell", "value": True})
    assert code == 1 and out["error"] == "Unknown feature."


def test_a_feature_change_that_open_webui_does_not_keep_is_a_failure(tmp_path):
    code, out, _ = run_signed_in(tmp_path, "set-feature", payload={"feature": "image_generation", "value": True}, drop=["/api/v1/images/config/update"])
    assert code == 1 and "did not keep" in out["error"]


NEW_SERVER = {"id": "docs", "name": "Docs search", "type": "openapi", "url": "https://docs.example.com", "path": "openapi.json", "tokenSecret": "proj/tool-tokens/docs", "enabled": True}


def test_a_tool_server_is_tested_then_saved_with_the_chosen_token_and_read_back(tmp_path):
    verify = dict(VERIFY, **{"https://docs.example.com": {"status": True, "specs": [{"name": "search"}]}})
    code, out, raw = run_signed_in(tmp_path, "upsert-tool-server", verify=verify, payload=NEW_SERVER, prefix="proj/tool-tokens/")
    assert code == 0 and out["id"] == "docs" and out["toolCount"] == 1
    kept = state(tmp_path, "/api/v1/configs/tool_servers")["TOOL_SERVER_CONNECTIONS"]
    assert [c["info"]["id"] for c in kept] == ["vuln-findings", "down", "off", "docs"]  # the others pass through untouched
    docs = kept[-1]
    assert docs["auth_type"] == "bearer" and docs["key"] == "admin-pass-123" and docs["forward_cookies"] is False and docs["config"]["enable"] is True
    assert kept[0]["key"] == "SECRET-KEY-1"  # unchanged
    assert "admin-pass-123" not in raw  # the token went to Open WebUI only


def test_a_tool_server_that_cannot_connect_is_not_saved(tmp_path):
    code, out, _ = run_signed_in(tmp_path, "upsert-tool-server", payload=dict(NEW_SERVER, url="https://nowhere.example.com"), prefix="proj/tool-tokens/")
    assert code == 1 and "nothing was saved" in out["error"]
    assert state(tmp_path, "/api/v1/configs/tool_servers") == TOOL_SERVERS


def test_tool_server_requests_are_checked_again_on_the_instance(tmp_path):
    bad = [
        (dict(NEW_SERVER, id="vuln-findings"), "set up by the lab"),
        (dict(NEW_SERVER, id="bad id"), "server ID"),
        (dict(NEW_SERVER, type="grpc"), "MCP or OpenAPI"),
        (dict(NEW_SERVER, url="http://docs.example.com"), "https://"),
        (dict(NEW_SERVER, url="https://u:p@docs.example.com"), "https://"),
        (dict(NEW_SERVER, url="https://docs.example.com/x?token=1"), "https://"),
        (dict(NEW_SERVER, path="../etc"), "path"),
        (dict(NEW_SERVER, path="/abs"), "path"),
        (dict(NEW_SERVER, name=""), "name"),
        (dict(NEW_SERVER, tokenSecret="other/secret"), "list of lab tool tokens"),
        (dict(NEW_SERVER, tokenSecret="proj/tool-tokens/../admin"), "list of lab tool tokens"),
    ]
    for payload, text in bad:
        code, out, _ = run_signed_in(tmp_path, "upsert-tool-server", payload=payload, prefix="proj/tool-tokens/")
        assert code == 1 and text in out["error"], (payload, out)
    # No token offered at all (empty prefix) means no token can be chosen.
    code, out, _ = run_signed_in(tmp_path, "upsert-tool-server", payload=NEW_SERVER, prefix="")
    assert code == 1 and "list of lab tool tokens" in out["error"]
    assert state(tmp_path, "/api/v1/configs/tool_servers") == TOOL_SERVERS


def test_the_lab_findings_server_address_cannot_be_registered_under_another_id(tmp_path):
    verify = {"https://mcp.example.com/mcp": {"status": True, "specs": []}}
    gets = dict(GETS, **{"/api/v1/configs/tool_servers": {"TOOL_SERVER_CONNECTIONS": [dict(TOOL_SERVERS["TOOL_SERVER_CONNECTIONS"][0], url="https://mcp.example.com/mcp")]}})
    code, out, _ = run_signed_in(tmp_path, "upsert-tool-server", gets=gets, verify=verify, payload=dict(NEW_SERVER, url="https://mcp.example.com/mcp", tokenSecret=""))
    assert code == 1 and "managed at every start" in out["error"]


def test_editing_a_tool_server_keeps_its_description_and_access_and_can_drop_the_token(tmp_path):
    existing = {"url": "https://docs.example.com", "path": "", "type": "openapi", "auth_type": "bearer", "key": "OLD-KEY", "config": {"enable": True, "access_grants": [{"principal_type": "group", "principal_id": "g1", "permission": "read"}]},
                "info": {"id": "docs", "name": "Old", "description": "KEEP"}}
    gets = dict(GETS, **{"/api/v1/configs/tool_servers": {"TOOL_SERVER_CONNECTIONS": [existing]}})
    verify = {"https://docs.example.com": {"status": True, "specs": []}}
    code, out, _ = run_signed_in(tmp_path, "upsert-tool-server", gets=gets, verify=verify, payload=dict(NEW_SERVER, tokenSecret="", enabled=False))
    assert code == 0
    saved = state(tmp_path, "/api/v1/configs/tool_servers")["TOOL_SERVER_CONNECTIONS"]
    assert len(saved) == 1 and saved[0]["info"] == {"id": "docs", "name": "Docs search", "description": "KEEP"}
    assert saved[0]["auth_type"] == "none" and saved[0]["key"] == "" and saved[0]["config"]["enable"] is False
    assert saved[0]["config"]["access_grants"][0]["principal_id"] == "g1"


def test_removing_a_tool_server(tmp_path):
    code, out, _ = run_signed_in(tmp_path, "remove-tool-server", payload={"id": "down"})
    assert code == 0 and out["removed"] is True
    assert [c["info"]["id"] for c in state(tmp_path, "/api/v1/configs/tool_servers")["TOOL_SERVER_CONNECTIONS"]] == ["vuln-findings", "off"]
    code, out, _ = run_signed_in(tmp_path, "remove-tool-server", payload={"id": "never-there"})
    assert code == 0 and out["alreadyGone"] is True
    code, out, _ = run_signed_in(tmp_path, "remove-tool-server", payload={"id": "vuln-findings"})
    assert code == 1 and "set up by the lab" in out["error"]
    code, out, _ = run_signed_in(tmp_path, "remove-tool-server", payload={"id": "off"}, drop=["/api/v1/configs/tool_servers"])
    assert code == 1 and "did not keep" in out["error"]


def test_apply_desired_applies_each_saved_setting_and_reports_each(tmp_path):
    items = {"items": [{"kind": "default-model", "model": "qwen3:14b"},
                       {"kind": "feature", "feature": "image_generation", "value": True},
                       {"kind": "model-params", "model": "ghost", "temperature": 0.3},
                       {"kind": "something-else"},
                       {"kind": "tool-server-remove", "id": "down"}]}
    code, out, _ = run_signed_in(tmp_path, "apply-desired", payload=items)
    assert code == 0 and out["action"] == "apply-desired" and out["ok"] is False and out["applied"] == 3 and out["failed"] == 2
    assert [r["ok"] for r in out["results"]] == [True, True, False, False, True]
    assert out["results"][3]["error"] == "Unknown setting."
    assert state(tmp_path, "/api/v1/configs/models")["DEFAULT_MODELS"] == "qwen3:14b"


def test_a_write_without_a_readable_request_is_refused(tmp_path):
    code, out, _ = run_signed_in(tmp_path, "set-default-model", payload="")
    assert code == 1 and "not understood" in out["error"]


# ---- import-skill ---------------------------------------------------------------------------------

SKILL = {"id": "triage-notes", "name": "Triage notes", "description": "How to write up a finding", "content": "# Triage notes\n\nSummarise the finding first.\n", "enabled": True, "models": ["qwen3:14b"]}


def run_skill(tmp_path, payload, gets=None, hide_content=False, **kwargs):
    base = dict(GETS if gets is None else gets)
    code, out, raw = run_signed_in(tmp_path, "import-skill", gets=base, payload=payload, hide_content=hide_content, **kwargs)
    return code, out, raw


def skill_posts(tmp_path):
    return [line.split(" ", 1) for line in posts(tmp_path)]


def test_a_new_skill_is_created_and_attached_to_the_chosen_model(tmp_path):
    code, out, raw = run_skill(tmp_path, SKILL)
    assert code == 0 and out["ok"] is True and out["kind"] == "skill" and out["action"] == "import-skill"
    assert out["change"] == "created" and out["attachedTo"] == ["qwen3:14b"] and out["enabled"] is True
    sent = {path: json.loads(body) for path, body in skill_posts(tmp_path)}
    skill = sent["/api/v1/skills/create"]
    assert skill["id"] == "triage-notes" and skill["is_active"] is True and skill["content"].startswith("# Triage notes")
    assert skill["access_grants"] == [{"principal_type": "user", "principal_id": "*", "permission": "read"}]
    model = sent["/api/v1/models/create"]  # the installed model had no entry of its own yet
    assert model["id"] == "qwen3:14b" and model["meta"]["skillIds"] == ["triage-notes"]
    assert "admin-pass-123" not in raw and "tok-secret-1" not in raw


def test_an_existing_skill_is_updated_and_keeps_who_may_read_it(tmp_path):
    for grants in ([], [{"id": "g1", "principal_type": "group", "principal_id": "staff", "permission": "read"}]):
        tmp = tmp_path / str(len(grants))
        tmp.mkdir()
        gets = dict(GETS, **{"/api/v1/skills/id/triage-notes": {"id": "triage-notes", "name": "Old", "is_active": False, "access_grants": grants}})
        code, out, _ = run_skill(tmp, dict(SKILL, models=[]), gets=gets)
        assert code == 0 and out["change"] == "updated" and out["attachedTo"] == []
        sent = {path: json.loads(body) for path, body in skill_posts(tmp)}
        expected = [{k: g[k] for k in ("principal_type", "principal_id", "permission")} for g in grants]
        assert sent["/api/v1/skills/id/triage-notes/update"]["access_grants"] == expected
        assert "/api/v1/skills/create" not in sent


def test_a_grant_in_a_shape_this_panel_cannot_send_back_falls_back_to_read_for_everyone(tmp_path):
    gets = dict(GETS, **{"/api/v1/skills/id/triage-notes": {"id": "triage-notes", "name": "Old", "is_active": True, "access_grants": [{"id": "g1"}]}})
    run_skill(tmp_path, dict(SKILL, models=[]), gets=gets)
    sent = {path: json.loads(body) for path, body in skill_posts(tmp_path)}
    assert sent["/api/v1/skills/id/triage-notes/update"]["access_grants"] == [{"principal_type": "user", "principal_id": "*", "permission": "read"}]


def test_attaching_keeps_the_skills_the_model_already_has(tmp_path):
    gets = dict(GETS, **{"/api/v1/models/model?id=qwen3:14b": {"id": "qwen3:14b", "name": "qwen3:14b", "meta": {"skillIds": ["other"], "description": "d"}, "params": {}}})
    code, out, _ = run_skill(tmp_path, SKILL, gets=gets)
    assert code == 0
    sent = {path: json.loads(body) for path, body in skill_posts(tmp_path)}
    meta = sent["/api/v1/models/model/update"]["meta"]
    assert meta["skillIds"] == ["other", "triage-notes"] and meta["description"] == "d"


def test_a_version_that_does_not_show_the_content_back_still_passes_on_the_rest(tmp_path):
    code, out, _ = run_skill(tmp_path, SKILL, hide_content=True)
    assert code == 0 and out["ok"] is True and out["change"] == "created"


@pytest.mark.parametrize("change", [
    {"id": "Triage"}, {"id": "a b"}, {"id": "x" * 41}, {"id": ""}, {"name": ""}, {"name": "n" * 81}, {"content": ""}, {"content": "x" * 1801},
    {"content": "bad\x00text"}, {"content": "bell\x07"}, {"description": "d" * 301}, {"enabled": "yes"}, {"models": "qwen3:14b"},
    {"models": ["qwen3:14b"] * 11}, {"models": ["security-analyst"]}, {"models": ["../etc"]}, {"models": ["nope:1b"]},
])
def test_a_request_this_panel_does_not_accept_changes_nothing(tmp_path, change):
    code, out, _ = run_skill(tmp_path, dict(SKILL, **change))
    assert code == 1 and out["ok"] is False and out["kind"] == "skill"
    assert skill_posts(tmp_path) == []


def test_crlf_and_tabs_in_the_text_are_fine(tmp_path):
    code, out, _ = run_skill(tmp_path, dict(SKILL, content="# T\r\n\tstep one\r\n", models=[]))
    assert code == 0 and out["ok"] is True


def test_when_open_webui_refuses_the_skill_nothing_is_attached(tmp_path):
    code, out, _ = run_skill(tmp_path, SKILL, refuse=["/api/v1/skills/create"])
    assert code == 1 and "refused the skill" in out["error"]
    assert [p for p, _ in skill_posts(tmp_path) if "models" in p] == []


def test_a_skill_open_webui_does_not_keep_is_not_reported_as_saved(tmp_path):
    code, out, _ = run_skill(tmp_path, SKILL, drop=["/api/v1/skills/create"])
    assert code == 1 and out["ok"] is False


def test_a_model_that_cannot_take_the_skill_is_named_and_the_skill_is_still_reported_saved(tmp_path):
    code, out, _ = run_skill(tmp_path, SKILL, drop=["/api/v1/models/create"])
    assert code == 1 and out["ok"] is False
    assert "qwen3:14b" in out["error"] and "saved" in out["error"]
