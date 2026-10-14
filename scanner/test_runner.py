"""Runner integration tests: target propagation and incomplete coverage signaling."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as XML

sys.path.insert(0, str(Path(__file__).parent))
import run_scan


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.previous_tempdir = tempfile.tempdir
        tempfile.tempdir = str(Path(__file__).parent / ".test-tmp")
        Path(tempfile.tempdir).mkdir(exist_ok=True)

    def tearDown(self):
        Path(tempfile.tempdir).rmdir()
        tempfile.tempdir = self.previous_tempdir

    def execute(self, coverage):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "assessment.json").write_text("{}")
            run_id = "01234567-89ab-cdef-0123-456789abcdef"
            env = {"AWS_REGION": "us-east-1", "REPORT_BUCKET": "private-reports",
                   "GREENBONE_SECRET_ARN": "password", "SSH_SECRET_ARN": "key",
                   "IMAGE_DIGEST": "image@sha256:digest", "PUBLIC_HOSTNAME": "lab.example.com",
                   "CONTROL_PANEL_HOSTNAME": "cp.aiwebdemo.click", "LAB_PRIVATE_IP": "172.31.1.10",
                   "SCAN_TIMEOUT_HOURS": "12"}
            uploaded = []
            captured = {}
            output = root / "runs" / run_id / "output"

            def assess(config, region, work, reports, image):
                result = {"coverage": coverage, "summary": {"PASS": 2, "FAIL": 1, "REVIEW": 1}}
                (reports / "aws-assessment.json").write_text(json.dumps(result))
                work.rmdir()
                return result

            def command(*args, **kwargs):
                if args[:2] == ("docker", "run"):
                    report = args[args.index("--output") + 1].rsplit("/", 1)[-1]
                    (output / report).write_text("{}")
                if args[:3] == ("docker", "image", "save"):
                    Path(args[args.index("--output") + 1]).write_bytes(b"image archive")
                if "gvm-script" in args:
                    captured.update(json.loads((output.parent / "scan.json").read_text()))
                    for name in ("host.xml", "public.xml", "control-panel.xml", "greenbone.json"):
                        (output / name).write_text("report")
                if args[:3] == ("aws", "s3", "cp"):
                    uploaded.append(Path(args[3]).name)
                return SimpleNamespace(stdout="")

            with patch.object(run_scan, "ROOT", root), patch.object(run_scan, "settings", return_value=env), \
                 patch.object(run_scan, "assess", assess), patch.object(run_scan, "cmd", command), \
                 patch.object(run_scan, "secret", side_effect=lambda arn, region: "private-key" if arn == "key" else "password"), \
                 patch.object(run_scan, "wait_for_manager", return_value="configured"), \
                 patch.object(run_scan, "prepare", return_value=[{"image_id": "sha256:test", "context": "pg-gvm"}]), \
                 patch.object(run_scan, "build_tool", return_value={"image_id": "sha256:tool", "context": "trivy"}), \
                 patch.object(run_scan, "harden", return_value={"pg-gvm": {"network_mode": "none"}}), \
                 patch.object(run_scan.os, "chown", create=True):
                status = run_scan.run(run_id)
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertFalse((output.parent / "ssh-key").exists())
            self.assertFalse((output.parent / "gvm.conf").exists())
            self.assertNotIn("ssh-key", uploaded)
            self.assertNotIn("gvm.conf", uploaded)
            self.assertEqual(captured["control_panel_hostname"], "cp.aiwebdemo.click")
            self.assertEqual(uploaded[0], "aws-assessment.json")
            self.assertIn("scanner-image-pg-gvm.json", uploaded)
            self.assertIn("scanner-hardening.json", uploaded)
            self.assertFalse((output.parent / "scanner-image-pg-gvm.tar").exists())
            builds = json.loads((output / "scanner-image-builds.json").read_text())
            pg_build = next(build for build in builds if build["context"] == "pg-gvm")
            self.assertEqual(pg_build["image_id"], "sha256:test")
            self.assertEqual(len(pg_build["image_archive_sha256"]), 64)
            self.assertIn("scanner-image-trivy.json", uploaded)
            return status, manifest

    def test_incomplete_aws_assessment_fails_run_after_other_scans(self):
        status, manifest = self.execute("incomplete")
        self.assertEqual(status, 1)
        self.assertEqual(manifest["status"], "incomplete")
        self.assertIn("control-panel.xml", manifest["files"])

    def test_configuration_findings_do_not_look_like_execution_failure(self):
        status, manifest = self.execute("complete")
        self.assertEqual(status, 0)
        self.assertEqual(manifest["aws_checks"]["FAIL"], 1)

    def test_greenbone_uses_separate_panel_target_without_ssh_credentials(self):
        # XML serialization is not under test; stand in for lxml so this test
        # stays dependency-free while exercising GMP target/task separation.
        with patch.dict(sys.modules, {"lxml": SimpleNamespace(etree=XML)}):
            spec = importlib.util.spec_from_file_location("tested_gmp", Path(__file__).with_name("gmp_scan.py"))
            gmp_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(gmp_module)

        class GMP:
            def __init__(self):
                self.targets = []
            def create_credential(self, **kwargs):
                return XML.fromstring('<credential status="201" id="credential"/>')
            def create_target(self, **kwargs):
                self.targets.append(kwargs)
                return XML.fromstring('<target status="201" id="target"/>')
            def create_task(self, **kwargs):
                return XML.fromstring('<task status="201" id="task"/>')
            def start_task(self, task_id):
                return XML.fromstring('<start status="202"><report_id>report</report_id></start>')
            def get_task(self, task_id):
                return XML.fromstring('<tasks status="200"><task><status>Done</status></task></tasks>')
            def get_report(self, *args, **kwargs):
                return XML.fromstring('<reports status="200"><report>report</report></reports>')

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "key").write_text("private-key")
            config = {"run_id": "test", "output": str(root / "reports"), "timeout_seconds": 60,
                      "private_key_file": str(root / "key"), "lab_private_ip": "172.31.1.10",
                      "public_hostname": "lab.example.com", "control_panel_hostname": "cp.aiwebdemo.click"}
            (root / "config.json").write_text(json.dumps(config))
            gmp = GMP()
            with patch.object(gmp_module, "_feed_ready"):
                gmp_module.main(gmp, SimpleNamespace(argv=[str(root / "config.json")]))
            self.assertEqual([t["hosts"] for t in gmp.targets], [["172.31.1.10"], ["lab.example.com"], ["cp.aiwebdemo.click"]])
            self.assertEqual(gmp.targets[0]["ssh_credential_id"], "credential")
            self.assertNotIn("ssh_credential_id", gmp.targets[2])
            self.assertTrue((root / "reports" / "control-panel.xml").exists())


if __name__ == "__main__":
    unittest.main()
