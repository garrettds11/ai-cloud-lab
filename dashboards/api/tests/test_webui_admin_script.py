"""Runs webui-admin.sh (the script inside the panel's SSM document) under bash, with Open WebUI
replaced by a stub curl. SSM substitutes {{ action }} and {{ expectedVersion }} after checking
them against the document's allowed values; the test does the same substitution.

Run from dashboards/api:   python -m pytest -q tests/test_webui_admin_script.py
"""

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
esac
"""

pytestmark = pytest.mark.skipif(not (shutil.which("bash") and shutil.which("jq")), reason="needs bash and jq")


def run(tmp_path, action="status", expected="0.11.4", health="up", version="0.11.4", settings="PORT='8080'\n"):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "curl").write_text(CURL)
    (bin_dir / "curl").chmod(0o755)
    env_file = tmp_path / "settings.env"
    if settings is not None:
        env_file.write_text(settings)
    script = SCRIPT.replace("{{ action }}", action).replace("{{ expectedVersion }}", expected)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", AI_LAB_SETTINGS_FILE=str(env_file),
               HEALTH=health, VERSION=version)
    done = subprocess.run(["bash", "-s"], input=script, env=env, capture_output=True, text=True, timeout=30)
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, done.stdout + done.stderr  # exactly one JSON line
    return done.returncode, json.loads(lines[0])


def test_status_reports_health_and_a_matching_version(tmp_path):
    code, out = run(tmp_path)
    assert code == 0
    assert out == {"ok": True, "action": "status", "healthy": True, "version": "0.11.4",
                   "expectedVersion": "0.11.4", "versionMatches": True}


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
    assert not any(line.startswith((". ", "source ")) for line in code)
