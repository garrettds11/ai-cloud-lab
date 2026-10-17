#!/usr/bin/env python3
"""Scanner host runner. Invoked by Systems Manager; secrets never enter SSM parameters."""

import json
import hashlib
import os
import pathlib
import re
import subprocess
import sys
import time

from assess_aws import assess
from managed_images import prepare, harden, build_tool


ROOT = pathlib.Path("/opt/ai-scanner")
COMPOSE_URL = "https://greenbone.github.io/docs/latest/_static/compose.yaml"
TRIVY_IMAGE = "ghcr.io/aquasecurity/trivy@sha256:af6acf9a6b85dfe389a1941505c0ce9efef52a4719635e1a962f022a3d855daa"


def cmd(*args, check=True, input_text=None, timeout=None):
    return subprocess.run(
        args, input=input_text, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=check, timeout=timeout,
    )


def settings():
    values = {}
    for line in pathlib.Path("/etc/ai-scanner.env").read_text().splitlines():
        key, value = line.split("=", 1)
        values[key] = value.strip().strip("'")
    return values


def secret(arn, region):
    value = cmd("aws", "secretsmanager", "get-secret-value", "--secret-id", arn,
                "--query", "SecretString", "--output", "text", "--region", region).stdout
    if not value.strip() or value.strip() == "None":
        raise RuntimeError("A required scan secret is empty")
    return value


def gvm_cli(compose, config, xml):
    result = cmd("docker", "compose", "-f", str(compose), "run", "--rm", "-T",
                 "-v", f"{config}:/work/gvm.conf:ro", "gvm-tools",
                 "gvm-cli", "-c", "/work/gvm.conf", "socket",
                 input_text=xml, timeout=120)
    return result.stdout


def wait_for_manager(compose, configured, initial, deadline):
    while time.time() < deadline:
        for label, config in (("configured", configured), ("initial", initial)):
            try:
                # get_users requires authentication; get_version alone does not.
                out = gvm_cli(compose, config, "<get_users/>")
                if 'status="200"' in out:
                    return label
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                pass
        time.sleep(30)
    raise TimeoutError("Greenbone manager did not become ready")


def run(run_id):
    env = settings()
    work = ROOT / "runs" / run_id
    work.mkdir(parents=True, exist_ok=False)
    work.chmod(0o700)
    output = work / "output"
    output.mkdir(mode=0o700)
    compose = ROOT / "compose.yaml"
    region = env["AWS_REGION"]
    deadline = time.time() + int(float(env["SCAN_TIMEOUT_HOURS"]) * 3600) + 3 * 3600
    assessment_work = work / "lambda-artifacts"
    assessment_work.mkdir(mode=0o700)
    tool = build_tool(ROOT / "image_builds")
    trivy_image = tool["image_id"]
    (output / "scanner-tool-build.json").write_text(json.dumps(tool, indent=2))
    assessment = assess(json.loads((ROOT / "assessment.json").read_text()), region,
                        assessment_work, output, trivy_image)
    upload_reports(env, run_id, output)
    password = secret(env["GREENBONE_SECRET_ARN"], region).strip()
    private_key = secret(env["SSH_SECRET_ARN"], region)
    if "\n" in password or "\r" in password or not password:
        raise ValueError("Greenbone password must be one nonempty line")
    key_file = work / "ssh-key"
    key_file.write_text(private_key)
    key_file.chmod(0o600)
    os.chown(key_file, 1001, 1001)
    config = work / "gvm.conf"
    config.write_text("[gmp]\nusername=admin\npassword=" + password.replace("%", "%%") + "\n")
    config.chmod(0o600)
    os.chown(config, 1001, 1001)
    initial = work / "initial.conf"
    initial.write_text("[gmp]\nusername=admin\npassword=admin\n")
    initial.chmod(0o600)
    os.chown(initial, 1001, 1001)
    # Upstream gvm-tools drops to its gvm user (UID/GID 1001). The bind-mounted
    # credentials remain 0600, and its report directory remains 0700.
    os.chown(output, 1001, 1001)

    # Image coverage is independent of Greenbone's feed load. Publish it first
    # so the exact deployed digest still has a report if Greenbone later fails.
    cmd("docker", "run", "--rm", "-v", f"{output}:/reports", trivy_image,
        "image", "--scanners", "vuln", "--format", "json",
        "--output", "/reports/trivy.json", env["IMAGE_DIGEST"], timeout=3600)
    cmd("aws", "s3", "cp", str(output / "trivy.json"),
        f"s3://{env['REPORT_BUCKET']}/runs/{run_id}/trivy.json",
        "--region", region, timeout=300)

    # Greenbone explicitly recommends fetching the current Compose definition.
    cmd("curl", "--fail", "--location", "--silent", "--show-error", COMPOSE_URL,
        "--output", str(compose), timeout=60)
    cmd("docker", "compose", "-f", str(compose), "pull", timeout=7200)
    built = [tool] + prepare(compose, ROOT / "image_builds")
    (output / "scanner-hardening.json").write_text(json.dumps(harden(compose), indent=2))
    for image in built:
        archive = work / ("scanner-image-" + image["context"] + ".tar")
        try:
            cmd("docker", "image", "save", "--output", str(archive), image["image_id"], timeout=300)
            with archive.open("rb") as stream:
                image["image_archive_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
            # Assess the exact runtime rebuild independently of CI's earlier build.
            cmd("docker", "run", "--rm", "-v", f"{archive}:/image.tar:ro",
                "-v", f"{output}:/reports", trivy_image, "image", "--input", "/image.tar",
                "--scanners", "vuln", "--format", "json", "--output",
                "/reports/scanner-image-" + image["context"] + ".json", timeout=3600)
        finally:
            archive.unlink(missing_ok=True)
    (output / "scanner-image-builds.json").write_text(json.dumps(built, indent=2))
    upload_reports(env, run_id, output)
    cmd("docker", "compose", "-f", str(compose), "up", "-d", timeout=600)
    auth = wait_for_manager(compose, config, initial, min(deadline, time.time() + 5400))
    if auth == "initial":
        # A new Greenbone volume starts with admin/admin. Replace it at first boot.
        cmd("docker", "compose", "-f", str(compose), "exec", "-T", "-u", "gvmd",
            "gvmd", "gvmd", "--user=admin", "--new-password=" + password, timeout=120)
        if wait_for_manager(compose, config, initial, min(deadline, time.time() + 300)) != "configured":
            raise RuntimeError("Greenbone password rotation did not take effect")
    initial.unlink()

    scan_config = {
        "run_id": run_id,
        "lab_private_ip": env["LAB_PRIVATE_IP"],
        "public_hostname": env["PUBLIC_HOSTNAME"],
        "control_panel_hostname": env["CONTROL_PANEL_HOSTNAME"],
        "private_key_file": "/work/ssh-key",
        "output": "/work/output",
        "timeout_seconds": max(60, int(deadline - time.time())),
    }
    config_file = work / "scan.json"
    config_file.write_text(json.dumps(scan_config))
    cmd("docker", "compose", "-f", str(compose), "run", "--rm", "-T",
        "-v", f"{config}:/work/gvm.conf:ro", "-v", f"{key_file}:/work/ssh-key:ro",
        "-v", f"{config_file}:/work/scan.json:ro", "-v", f"{output}:/work/output",
        "-v", f"{ROOT / 'gmp_scan.py'}:/work/gmp_scan.py:ro",
        "gvm-tools", "gvm-script", "-c", "/work/gvm.conf", "socket",
        "/work/gmp_scan.py", "/work/scan.json", timeout=max(60, int(deadline - time.time())))

    (output / "manifest.json").write_text(json.dumps({
        "run_id": run_id, "status": "success" if assessment["coverage"] == "complete" else "incomplete", "completed_at": int(time.time()),
        "host": env["LAB_PRIVATE_IP"], "public_hostname": env["PUBLIC_HOSTNAME"],
        "control_panel_hostname": env["CONTROL_PANEL_HOSTNAME"],
        "aws_coverage": assessment["coverage"], "aws_checks": assessment["summary"],
        "note": "Successful execution is not a finding-free assessment. Review all reports.",
        "image_digest": env["IMAGE_DIGEST"],
        "files": sorted(p.name for p in output.iterdir()),
    }, indent=2))
    upload_reports(env, run_id, output)
    key_file.unlink(missing_ok=True)
    config.unlink(missing_ok=True)
    return 0 if assessment["coverage"] == "complete" else 1


def upload_reports(env, run_id, output):
    for path in sorted(output.iterdir()):
        cmd("aws", "s3", "cp", str(path),
            f"s3://{env['REPORT_BUCKET']}/runs/{run_id}/{path.name}",
            "--region", env["AWS_REGION"], timeout=300)


if __name__ == "__main__":
    if len(sys.argv) != 2 or not re.fullmatch(r"[0-9a-f-]{36}", sys.argv[1]):
        raise SystemExit("Expected a generated scan run ID")
    if not (ROOT / "ready").exists():
        raise SystemExit("Scanner bootstrap has not finished")
    try:
        raise SystemExit(run(sys.argv[1]))
    except Exception as exc:
        work = ROOT / "runs" / sys.argv[1]
        for name in ("ssh-key", "gvm.conf", "initial.conf"):
            (work / name).unlink(missing_ok=True)
        # Keep sanitized partial results even when feed loading or a later scan
        # fails. Lambda still reports failure and does not advance last_success.
        try:
            upload_reports(settings(), sys.argv[1], work / "output")
        except Exception:
            pass
        # Avoid printing command stderr: a helper could include a credential.
        print(f"Scan failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1)
