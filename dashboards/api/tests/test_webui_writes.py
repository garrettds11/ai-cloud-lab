"""Phase 2 of #55 through the Control API: the write actions, their checks, the desired-state table,
the tool token list and apply-desired. The instance side is tested in test_webui_admin_script.py; the
timed reapply after a start is in test_spend_job.py. Run from dashboards/api: python -m pytest -q tests/test_webui_writes.py"""

import base64
import json
import socket

import pytest

from test_handler import ACTION, ACTIONS, ADM, CMD, NONE, OP, FakeTable, aws, call, webui  # noqa: F401

import handler  # noqa: E402

PREFIX = "aiwebdemo/tool-tokens/"
HOSTS = {"docs.example.com": "93.184.216.34", "internal.example.com": "10.1.2.3", "meta.example.com": "169.254.169.254", "loop.example.com": "127.0.0.1",
         "mixed.example.com": ["93.184.216.34", "192.168.0.9"], "v6.example.com": "fd00:ec2::254", "ok6.example.com": "2606:2800:220:1:248:1893:25c8:1946"}


@pytest.fixture(autouse=True)
def lab(aws, webui, monkeypatch):
    monkeypatch.setattr(handler, "desired", FakeTable(("setting",)))
    monkeypatch.setattr(handler, "TOOL_TOKEN_PREFIX", PREFIX)
    monkeypatch.setattr(handler, "PRIVATE_TOOL_HOSTS", set())

    def resolve(host, port, type=0):
        if host not in HOSTS:
            raise socket.gaierror("no such host")
        values = HOSTS[host] if isinstance(HOSTS[host], list) else [HOSTS[host]]
        return [(0, 0, 0, "", (v, port)) for v in values]

    monkeypatch.setattr(handler.socket, "getaddrinfo", resolve)
    return webui


def start(action, user=ADM, **body):
    return call(ACTIONS, user, body={"action": action, "instanceId": "i-aaa", **body})


def sent_payload(webui, index=-1):
    return json.loads(base64.b64decode(webui.sent[index]["Parameters"]["payload"][0]))


SERVER = {"id": "docs", "name": "Docs search", "type": "openapi", "url": "https://docs.example.com", "path": "openapi.json", "tokenSecret": PREFIX + "docs", "confirmed": True}


def finish(webui, result, status="Success"):
    webui.invocation = {"Status": status, "DocumentName": "panel-webui-admin", "StandardOutputContent": "noise\n" + json.dumps(result)}
    return call(ACTION, ADM, path={"commandId": CMD})


def test_only_administrators_can_use_the_write_actions(lab):
    for user in (OP, NONE):
        for action, body in (("set-default-model", {"model": "qwen3:14b"}), ("set-feature", {"feature": "api_keys", "value": False, "confirmed": True}),
                             ("upsert-tool-server", SERVER), ("apply-desired", {})):
            assert start(action, user=user, **body)[0] == 403
    assert lab.sent == []


def test_a_default_model_and_model_params_are_sent_as_checked_base64_json(lab):
    status, body = start("set-default-model", model="qwen3:14b")
    assert status == 200 and body["action"] == "set-default-model"
    assert sent_payload(lab) == {"model": "qwen3:14b"} and lab.sent[-1]["Parameters"]["toolTokenPrefix"] == [""]
    start("set-model-params", model="qwen3:14b", temperature=0.4, numCtx=16384, extra="ignored")
    assert sent_payload(lab) == {"model": "qwen3:14b", "temperature": 0.4, "numCtx": 16384}


@pytest.mark.parametrize("body", [
    {"model": ""}, {"model": 5}, {"model": "a b"}, {"model": "x;id"}, {},
])
def test_a_bad_default_model_is_refused(lab, body):
    assert start("set-default-model", **body)[0] == 400 and lab.sent == []


@pytest.mark.parametrize("body", [
    {"model": "m"}, {"model": "m", "temperature": 2.1}, {"model": "m", "temperature": -0.1}, {"model": "m", "temperature": "1"}, {"model": "m", "temperature": True},
    {"model": "m", "numCtx": 511}, {"model": "m", "numCtx": 131073}, {"model": "m", "numCtx": 4096.5}, {"model": "m", "numCtx": "4096"},
    {"model": "security-analyst", "temperature": 0.5},
])
def test_bad_model_params_are_refused(lab, body):
    assert start("set-model-params", **body)[0] == 400 and lab.sent == []


def test_the_edges_of_the_ranges_are_allowed(lab):
    for body in ({"temperature": 0}, {"temperature": 2}, {"numCtx": 512}, {"numCtx": 131072}, {"temperature": 1, "numCtx": 8192.0}):
        assert start("set-model-params", model="m", **body)[0] == 200


def test_feature_changes_need_a_confirmation_and_a_known_feature_with_the_right_kind_of_value(lab):
    assert start("set-feature", feature="api_keys", value=False)[0] == 400  # no confirmation
    assert start("set-feature", feature="api_keys", value=False, confirmed="yes")[0] == 400
    for body in ({"feature": "root_shell", "value": True}, {"feature": "api_keys", "value": "off"}, {"feature": "api_keys", "value": 0},
                 {"feature": "api_key_routes", "value": "all"}, {"feature": "api_key_routes", "value": ["no-slash"]}, {"feature": "api_key_routes", "value": ["/a b"]},
                 {"feature": "api_key_routes", "value": ["/x"] * 21}, {"feature": "memory"}):
        assert start("set-feature", confirmed=True, **body)[0] == 400, body
    assert lab.sent == []
    assert start("set-feature", feature="api_key_routes", value=["/api/chat/completions"], confirmed=True)[0] == 200
    assert sent_payload(lab) == {"feature": "api_key_routes", "value": ["/api/chat/completions"]}


@pytest.mark.parametrize("override,text", [
    ({"id": "vuln-findings"}, "set up by the lab"), ({"id": "bad id"}, "ID"), ({"id": ""}, "ID"), ({"name": ""}, "name"), ({"name": "x" * 61}, "name"),
    ({"type": "grpc"}, "MCP or OpenAPI"), ({"path": "../x"}, "path"), ({"path": "/abs"}, "path"), ({"path": "a b"}, "path"),
    ({"url": "http://docs.example.com"}, "https://"), ({"url": "https://u:p@docs.example.com"}, "https://"), ({"url": "https://docs.example.com/x?t=1"}, "https://"),
    ({"url": "https://docs.example.com/#f"}, "https://"), ({"url": "ftp://docs.example.com"}, "https://"), ({"url": "https://docs.example.com:99999"}, "port"),
    ({"url": "https://[::1]/x"}, "host name"), ({"url": 5}, "address"), ({"url": "https://" + "a" * 300}, "address"),
    ({"tokenSecret": "other/secret"}, "tool tokens"), ({"tokenSecret": PREFIX + "../admin"}, "tool tokens"), ({"tokenSecret": PREFIX + "a b"}, "tool tokens"),
])
def test_a_bad_tool_server_request_is_refused_before_anything_is_sent(lab, override, text):
    status, body = start("upsert-tool-server", **{**SERVER, **override})
    assert status == 400 and text in body["message"] and lab.sent == []


def test_tool_server_addresses_may_not_point_inside_the_network_unless_listed(lab, monkeypatch):
    for host in ("internal.example.com", "meta.example.com", "loop.example.com", "mixed.example.com", "v6.example.com", "10.0.0.5", "169.254.169.254", "127.0.0.1", "192.168.1.1", "172.16.0.9", "0.0.0.0"):
        status, body = start("upsert-tool-server", **{**SERVER, "url": f"https://{host}"})
        assert status == 400 and "private or internal" in body["message"], host
    assert start("upsert-tool-server", **{**SERVER, "url": "https://nowhere.example.com"})[1]["message"].startswith("That address could not be found")
    assert start("upsert-tool-server", **{**SERVER, "url": "https://ok6.example.com"})[0] == 200
    assert start("upsert-tool-server", **{**SERVER, "url": "https://8.8.8.8/mcp", "type": "mcp"})[0] == 200
    monkeypatch.setattr(handler, "PRIVATE_TOOL_HOSTS", {"internal.example.com"})
    assert start("upsert-tool-server", **{**SERVER, "url": "https://internal.example.com"})[0] == 200
    assert start("upsert-tool-server", **{**SERVER, "url": "https://meta.example.com"})[0] == 400  # only the listed host


def test_a_tool_server_is_sent_with_the_token_prefix_and_without_any_token_value(lab):
    assert start("upsert-tool-server", **{k: v for k, v in SERVER.items() if k != "confirmed"})[0] == 400  # needs confirmation
    status, _ = start("upsert-tool-server", **SERVER)
    assert status == 200
    assert lab.sent[-1]["Parameters"]["toolTokenPrefix"] == [PREFIX]
    assert sent_payload(lab) == {"id": "docs", "name": "Docs search", "type": "openapi", "url": "https://docs.example.com", "path": "openapi.json", "tokenSecret": PREFIX + "docs", "enabled": True}
    assert start("upsert-tool-server", **{**SERVER, "tokenSecret": "", "enabled": False})[0] == 200
    assert sent_payload(lab)["tokenSecret"] == "" and sent_payload(lab)["enabled"] is False


def test_removing_a_tool_server_needs_confirmation_and_never_touches_the_lab_own(lab):
    assert start("remove-tool-server", id="docs")[0] == 400
    assert start("remove-tool-server", id="vuln-findings", confirmed=True)[0] == 400
    assert start("remove-tool-server", id="docs", confirmed=True)[0] == 200 and sent_payload(lab) == {"id": "docs"}


def test_a_confirmed_change_is_saved_for_the_next_start_only_after_the_instance_says_it_worked(lab):
    start("set-default-model", model="qwen3:14b")
    status, body = finish(lab, {"ok": False, "kind": "default-model", "error": "Open WebUI did not keep the change."})
    assert status == 200 and handler.desired.items == {}
    start("set-default-model", model="qwen3:14b")
    handler.events.items.pop(("webui-actions", CMD + "#result"), None)
    status, body = finish(lab, {"ok": True, "kind": "default-model", "model": "qwen3:14b", "scope": "Global"})
    assert status == 200 and body["status"] == "Success"
    row = handler.desired.items[("default-model",)]
    assert row["kind"] == "default-model" and json.loads(row["payload"]) == {"model": "qwen3:14b"} and row["updatedBy"] == ADM
    assert handler._desired_items() == [{"kind": "default-model", "model": "qwen3:14b"}]


def test_model_params_are_merged_per_model_and_removal_forgets_a_server(lab):
    for body in ({"temperature": 0.4}, {"numCtx": 8192}):
        start("set-model-params", model="qwen3:14b", **body)
        handler.events.items.pop(("webui-actions", CMD + "#result"), None)
        finish(lab, {"ok": True, "kind": "model-params"})
    assert handler._desired_items() == [{"kind": "model-params", "model": "qwen3:14b", "temperature": 0.4, "numCtx": 8192}]
    start("upsert-tool-server", **SERVER)
    handler.events.items.pop(("webui-actions", CMD + "#result"), None)
    finish(lab, {"ok": True, "kind": "tool-server", "id": "docs", "toolCount": 2})
    assert ("tool-server#docs",) in handler.desired.items
    assert "SECRET" not in json.dumps(list(handler.desired.items.values()))  # only the secret's name is kept
    start("remove-tool-server", id="docs", confirmed=True)
    handler.events.items.pop(("webui-actions", CMD + "#result"), None)
    finish(lab, {"ok": True, "kind": "tool-server-remove", "removed": True})
    assert ("tool-server#docs",) not in handler.desired.items


def test_a_result_read_twice_is_saved_once_and_a_failed_command_is_not_saved(lab):
    start("set-feature", feature="image_generation", value=True, confirmed=True)
    finish(lab, {"ok": True, "kind": "feature"})
    first = dict(handler.desired.items[("feature#image_generation",)])
    handler.desired.items[("feature#image_generation",)]["updatedBy"] = "someone-else"
    finish(lab, {"ok": True, "kind": "feature"})
    assert handler.desired.items[("feature#image_generation",)]["updatedBy"] == "someone-else" and first["updatedBy"] == ADM
    handler.events.items.clear()
    start("set-feature", feature="memory", value=False, confirmed=True)
    finish(lab, None, status="Failed")
    assert ("feature#memory",) not in handler.desired.items


def test_a_failure_to_save_does_not_hide_that_the_change_was_made(lab, monkeypatch):
    start("set-default-model", model="qwen3:14b")
    handler.desired.down = True
    status, body = finish(lab, {"ok": True, "kind": "default-model"})
    assert status == 200 and body["status"] == "Success" and body["result"]["ok"] is True
    logs = [e["event"] for e in handler.events.items.values() if e.get("event")]
    assert any("could not be saved for the next start" in text for text in logs)


def test_saved_settings_that_would_not_fit_in_one_command_refuse_a_new_one_up_front(lab):
    for n in range(12):
        handler.desired.put_item(Item={"setting": f"tool-server#s{n}", "kind": "tool-server", "payload": json.dumps(
            {"id": f"s{n}", "name": "n" * 60, "type": "mcp", "url": "https://docs.example.com/" + "p" * 80, "path": "", "tokenSecret": PREFIX + "t" * 40, "enabled": True})})
    before = len(lab.sent)
    status, body = start("upsert-tool-server", **{**SERVER, "id": "one-more"})
    assert status == 409 and "Too many settings" in body["message"] and len(lab.sent) == before
    assert start("remove-tool-server", id="s1", confirmed=True)[0] == 200  # removing always works
    assert start("upsert-tool-server", **{**SERVER, "id": "s3"})[0] in (200, 409)


def test_apply_desired_sends_every_saved_setting_and_refuses_when_none_are_saved(lab):
    assert start("apply-desired")[0] == 409
    handler.desired.put_item(Item={"setting": "default-model", "kind": "default-model", "payload": json.dumps({"model": "qwen3:14b"})})
    handler.desired.put_item(Item={"setting": "_reapply", "launchMs": 5})  # the marker is never replayed
    assert start("apply-desired")[0] == 200
    assert sent_payload(lab) == {"items": [{"kind": "default-model", "model": "qwen3:14b"}]}
    assert lab.sent[-1]["Parameters"]["toolTokenPrefix"] == [PREFIX]


def test_the_tool_token_list_has_names_only_and_only_for_administrators(lab, monkeypatch):
    asked = []

    class Secrets:
        def list_secrets(self, **kw):
            asked.append(kw)
            return {"SecretList": [{"Name": PREFIX + "docs", "ARN": "arn:x", "SecretString": "never"}, {"Name": "other/secret"}, {"Name": PREFIX + "alpha"}]}

    monkeypatch.setattr(handler, "secretsmanager", Secrets())
    assert call("GET /admin/webui/tool-tokens", OP)[0] == 403
    status, body = call("GET /admin/webui/tool-tokens", ADM)
    assert status == 200 and body == {"prefix": PREFIX, "names": [PREFIX + "alpha", PREFIX + "docs"]}
    assert asked[0]["Filters"] == [{"Key": "name", "Values": [PREFIX]}]
    monkeypatch.setattr(handler, "TOOL_TOKEN_PREFIX", "")
    assert call("GET /admin/webui/tool-tokens", ADM)[1] == {"prefix": "", "names": []}


def test_the_desired_state_view_lists_requests_and_the_last_reapply(lab):
    assert call("GET /admin/webui/desired-state", OP)[0] == 403
    handler.desired.put_item(Item={"setting": "feature#memory", "kind": "feature", "payload": json.dumps({"feature": "memory", "value": False})})
    handler.desired.put_item(Item={"setting": "_reapply", "launchMs": 1790000000000, "appliedAt": 1790000100000, "failed": 1})
    status, body = call("GET /admin/webui/desired-state", ADM)
    assert status == 200 and body["configured"] is True and body["items"] == [{"kind": "feature", "feature": "memory", "value": False}]
    assert body["lastApplied"] == {"launchMs": 1790000000000, "at": 1790000100000, "failed": 1}


# ---- import-skill -----------------------------------------------------------------------------------

SKILL = {"id": "triage-notes", "name": "Triage notes", "description": "How to write up a finding", "content": "# Triage notes\n\nSummarise first.\n",
         "enabled": True, "models": ["qwen3:14b"], "confirmed": True}


def test_a_skill_is_sent_checked_and_without_the_confirmation_flag(lab):
    status, body = start("import-skill", **SKILL, extra="ignored")
    assert status == 200 and body["action"] == "import-skill"
    assert sent_payload(lab) == {"id": "triage-notes", "name": "Triage notes", "description": "How to write up a finding",
                                 "content": "# Triage notes\n\nSummarise first.\n", "enabled": True, "models": ["qwen3:14b"]}


def test_a_skill_needs_a_confirmation_and_only_administrators_may_import_one(lab):
    assert start("import-skill", **dict(SKILL, confirmed=False))[0] == 400
    assert start("import-skill", **{k: v for k, v in SKILL.items() if k != "confirmed"})[0] == 400
    for user in (OP, NONE):
        assert start("import-skill", user=user, **SKILL)[0] == 403
    assert lab.sent == []


@pytest.mark.parametrize("change", [
    {"id": "Triage"}, {"id": "a b"}, {"id": "x" * 41}, {"id": ""}, {"id": 5}, {"name": ""}, {"name": "n" * 81}, {"name": 3},
    {"description": "d" * 301}, {"content": ""}, {"content": "x" * 1801}, {"content": 7}, {"content": "bad\x00text"}, {"content": "bell\x07"},
    {"enabled": "yes"}, {"models": "qwen3:14b"}, {"models": ["a"] * 11}, {"models": ["a b"]}, {"models": ["security-analyst"]},
])
def test_a_bad_skill_is_refused_before_anything_is_sent(lab, change):
    status, body = start("import-skill", **dict(SKILL, **change))
    assert status == 400 and body["message"]
    assert lab.sent == []


def test_a_skill_may_be_at_the_limits_and_keep_tabs_and_line_breaks(lab):
    status, _ = start("import-skill", **dict(SKILL, content="x" * 1800, models=[], description=""))
    assert status == 200
    status, _ = start("import-skill", **dict(SKILL, content="a\r\n\tb\n", models=["qwen3:14b", "qwen3:14b"]))
    assert status == 200 and sent_payload(lab)["models"] == ["qwen3:14b"]


def test_a_skill_is_not_saved_for_the_next_start(lab):
    start("import-skill", **SKILL)
    finish(lab, {"ok": True, "kind": "skill", "id": "triage-notes", "change": "created", "enabled": True, "attachedTo": ["qwen3:14b"]})
    assert handler.desired.items == {}
    # And it never takes room away from the saved settings that are kept.
    assert start("import-skill", **dict(SKILL, content="y" * 1800))[0] == 200

