import json
import hashlib
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

import bundle
import inventory
import scan


def setUpModule():
    (scan.ROOT / ".security-scan").mkdir(exist_ok=True)


class ReportTests(unittest.TestCase):
    def test_secret_values_and_image_environment_never_enter_evidence(self):
        report = {"Metadata": {"ImageConfig": {"config": {"Env": ["PASSWORD=do-not-publish"]}}},
                  "Results": [{"Target": "a.py", "Secrets": [{"RuleID": "private-key",
                      "Severity": "HIGH", "Match": "do-not-publish", "Title": "do-not-publish",
                      "Code": {"Lines": [{"Content": "do-not-publish"}]}, "StartLine": 4}]}]}
        target = {"id": "repository", "kind": "repository", "name": "source", "scan_ref": "input",
                  "origins": [{"file": "README.md", "line": 1}]}
        items = scan.findings(report)
        serialized = json.dumps([items, scan.sarif(target, items, "0.75.0")])
        self.assertNotIn("do-not-publish", serialized)
        self.assertEqual(items[0]["line"], 4)
        self.assertEqual(scan.gate(items), 1)

    def test_policy_includes_unfixed_cves_and_all_secrets(self):
        self.assertEqual(scan.gate([
            {"kind": "vulnerability", "severity": "HIGH", "FixedVersion": ""},
            {"kind": "vulnerability", "severity": "CRITICAL"},
            {"kind": "secret", "severity": "LOW"},
            {"kind": "misconfiguration", "severity": "CRITICAL"},
            {"kind": "vulnerability", "severity": "MEDIUM"}]), 3)

    def test_image_findings_link_to_repository_declaration(self):
        target = {"id": "image-one", "kind": "image", "name": "registry/app:1",
                  "origins": [{"file": "a folder/compose.yaml", "line": 3}]}
        item = {"kind": "vulnerability", "id": "CVE-123", "severity": "HIGH", "path": "/lib/lib.so",
                "PkgName": "pkg", "InstalledVersion": "1", "FixedVersion": "2", "line": 1}
        report = scan.sarif(target, [item], "0.75.0")
        finding = report["runs"][0]["results"][0]
        location = finding["locations"][0]["physicalLocation"]
        self.assertEqual(location["artifactLocation"]["uri"], "a%20folder/compose.yaml")
        self.assertEqual(location["region"]["startLine"], 3)
        self.assertEqual(report["runs"][0]["automationDetails"]["id"], "trivy/image-one/")

    def test_package_locations_map_to_source(self):
        target = {"kind": "package", "scan_ref": "inputs/zip", "members": [
            {"member": "handler.py", "source": "api/handler.py"}]}
        self.assertEqual(scan.source_location(target, {"path": "/work/inputs/zip/handler.py", "line": 8}),
                         ("api/handler.py", 8))

    def test_digest_resolution_preserves_registry_port(self):
        with patch("scan.subprocess.check_output", return_value="Digest: sha256:" + "a" * 64 + "\n"):
            self.assertEqual(scan.resolve_image("localhost:5000/app:tag"),
                             "localhost:5000/app@sha256:" + "a" * 64)

    def test_missing_digest_is_error(self):
        with patch("scan.subprocess.check_output", return_value="Name: app:latest\n"):
            with self.assertRaises(ValueError):
                scan.resolve_image("app:latest")


class CoverageTests(unittest.TestCase):
    def setUp(self):
        scanner_patch = patch("scan.scanner_ref", return_value=(scan.TRIVY_IMAGE, {}))
        scanner_patch.start()
        self.addCleanup(scanner_patch.stop)

    def test_missing_or_partial_report_never_passes(self):
        target = {"id": "one"}
        valid = {"target": target, "commit": "abc", "complete": True}
        self.assertTrue(bundle.reconcile(target, valid, "abc", True, True, True))
        self.assertFalse(bundle.reconcile(target, {}, "abc", True, True, True))
        self.assertFalse(bundle.reconcile(target, valid, "wrong", True, True, True))
        self.assertFalse(bundle.reconcile(target, valid, "abc", False, True, True))
        self.assertFalse(bundle.reconcile({"id": "different"}, valid, "abc", True, True, True))
        self.assertFalse(bundle.reconcile(target, valid, "abc", True, True, False))

    def test_scan_error_keeps_incomplete_status_and_deletes_raw_output(self):
        target = {"id": "repository", "kind": "repository", "name": "source",
                  "scan_ref": "inputs/repository", "origins": [{"file": "README.md", "line": 1}]}
        with tempfile.TemporaryDirectory(dir=scan.ROOT / ".security-scan") as temporary:
            output = Path(temporary)
            (output / "inventory.json").write_text(json.dumps({"commit": "abc", "targets": [target]}))
            (output / "repository.raw.json").write_text("sensitive raw data")
            with patch("scan.OUT", output), patch("scan.os.getuid", return_value=1000, create=True), \
                    patch("scan.os.getgid", return_value=1000, create=True), \
                    patch("scan.subprocess.check_output", side_effect=OSError("sensitive stderr")):
                self.assertEqual(scan.scan("repository"), 1)
            status = json.loads((output / "reports/repository/status.json").read_text())
            self.assertFalse(status["complete"])
            self.assertEqual(status["error_type"], "OSError")
            self.assertNotIn("sensitive", json.dumps(status))
            self.assertFalse((output / "repository.raw.json").exists())

    def test_policy_failure_still_persists_complete_sarif(self):
        target = {"id": "repository", "kind": "repository", "name": "source",
                  "scan_ref": "inputs/repository", "origins": [{"file": "README.md", "line": 1}]}
        with tempfile.TemporaryDirectory(dir=scan.ROOT / ".security-scan") as temporary:
            output = Path(temporary)
            (output / "inventory.json").write_text(json.dumps({"commit": "abc", "targets": [target]}))
            raw = {"SchemaVersion": 2, "Results": [{"Target": "handler.py", "Secrets": [
                {"RuleID": "secret", "Severity": "LOW", "Match": "sensitive value"}]}]}
            def produce_report(*args, **kwargs):
                (output / "repository.raw.json").write_text(json.dumps(raw))
            with patch("scan.OUT", output), patch("scan.os.getuid", return_value=1000, create=True), \
                    patch("scan.os.getgid", return_value=1000, create=True), \
                    patch("scan.subprocess.check_output", return_value='{"Version":"0.75.0"}'), \
                    patch("scan.subprocess.run", side_effect=produce_report):
                self.assertEqual(scan.scan("repository"), 1)
            status = json.loads((output / "reports/repository/status.json").read_text())
            self.assertTrue(status["complete"])
            self.assertEqual(status["blocking_findings"], 1)
            self.assertTrue((output / "reports/repository/results.sarif").is_file())
            self.assertNotIn("sensitive value", (output / "reports/repository/results.sarif").read_text())

    def test_registry_failure_retries_same_digest_and_preserves_success(self):
        target = {"id": "image", "kind": "image", "name": "registry/app:1",
                  "scan_ref": "registry/app:1", "platform": "linux/amd64",
                  "origins": [{"file": "compose.yaml", "line": 1}]}
        with tempfile.TemporaryDirectory(dir=scan.ROOT / ".security-scan") as temporary:
            output = Path(temporary)
            (output / "inventory.json").write_text(json.dumps({"commit": "abc", "targets": [target]}))
            calls = []
            def run(command, **kwargs):
                calls.append(command)
                if len(calls) < 3:
                    raise subprocess.CalledProcessError(1, command)
                (output / "image.raw.json").write_text('{"SchemaVersion":2,"Results":[]}')
            with patch("scan.OUT", output), patch("scan.os.getuid", return_value=1000, create=True), \
                    patch("scan.os.getgid", return_value=1000, create=True), \
                    patch("scan.resolve_image", return_value="registry/app@sha256:" + "a" * 64), \
                    patch("scan.subprocess.check_output", return_value='{"Version":"0.75.0"}'), \
                    patch("scan.subprocess.run", side_effect=run), patch("scan.time.sleep"):
                self.assertEqual(scan.scan("image"), 0)
            self.assertEqual(len(calls), 3)
            self.assertEqual(calls[0], calls[1])
            self.assertEqual(calls[1], calls[2])
            self.assertEqual(json.loads((output / "reports/image/status.json").read_text())["scan_attempts"], 3)

    def test_managed_image_scans_archive_without_docker_socket(self):
        image = scan.managed_images.definitions(scan.ROOT / "scanner/image_builds")[0]
        target = {"id": "managed", "kind": "image", "name": "managed PG", "scan_ref": image["reference"],
                  "build": image, "platform": "linux/amd64", "build_root": ".security-scan/inputs/test",
                  "origins": [{"file": "scanner/image_builds/pg-gvm/Dockerfile", "line": 1}]}
        with tempfile.TemporaryDirectory(dir=scan.ROOT / ".security-scan") as temporary:
            output = Path(temporary)
            target["build_root"] = (output / "inputs/test").relative_to(scan.ROOT).as_posix()
            (output / "inventory.json").write_text(json.dumps({"commit": "abc", "targets": [target]}))
            calls = []
            def run(command, **kwargs):
                calls.append(command)
                if command[:3] == ["docker", "image", "save"]:
                    (output / "managed.image.tar").write_bytes(b"image bytes")
                else:
                    (output / "managed.raw.json").write_text('{"SchemaVersion":2,"Results":[]}')
            with patch("scan.OUT", output), patch("scan.os.getuid", return_value=1000, create=True), \
                    patch("scan.os.getgid", return_value=1000, create=True), \
                    patch("scan.managed_images.definitions", return_value=[image]), \
                    patch("scan.managed_images.build", return_value=dict(image, image_id="sha256:" + "1" * 64)), \
                    patch("scan.subprocess.check_output", return_value='{"Version":"0.75.0"}'), \
                    patch("scan.subprocess.run", side_effect=run):
                self.assertEqual(scan.scan("managed"), 0)
            scanner_command = calls[-1]
            self.assertIn("--input", scanner_command)
            self.assertNotIn("remote", scanner_command)
            self.assertNotIn("/var/run/docker.sock", " ".join(scanner_command))
            self.assertFalse((output / "managed.image.tar").exists())
            status = json.loads((output / "reports/managed/status.json").read_text())
            self.assertEqual(status["image_build"]["image_id"], "sha256:" + "1" * 64)


class ScannerArtifactTests(unittest.TestCase):
    def test_changed_archive_fails_before_any_docker_load(self):
        with tempfile.TemporaryDirectory(dir=scan.ROOT / ".security-scan") as temporary:
            output = Path(temporary)
            (output / "inputs").mkdir()
            (output / "inputs/scanner-image.tar").write_bytes(b"changed")
            record = {"reference": scan.TRIVY_IMAGE, "scope": "tool", "image_id": "sha256:" + "1" * 64,
                      "archive_sha256": hashlib.sha256(b"original").hexdigest()}
            with patch("scan.OUT", output), patch("scan.subprocess.run") as run:
                with self.assertRaises(ValueError):
                    scan.scanner_ref({"scanner_build": record})
                run.assert_not_called()

    def test_verified_archive_load_must_match_immutable_id(self):
        with tempfile.TemporaryDirectory(dir=scan.ROOT / ".security-scan") as temporary:
            output = Path(temporary)
            (output / "inputs").mkdir()
            (output / "inputs/scanner-image.tar").write_bytes(b"image")
            record = {"reference": scan.TRIVY_IMAGE, "scope": "tool", "image_id": "sha256:" + "1" * 64,
                      "archive_sha256": hashlib.sha256(b"image").hexdigest()}
            with patch("scan.OUT", output), patch("scan.subprocess.run"), \
                    patch("scan.subprocess.check_output", return_value="sha256:" + "2" * 64):
                with self.assertRaises(ValueError):
                    scan.scanner_ref({"scanner_build": record})


class InventoryTests(unittest.TestCase):
    def setUp(self):
        (inventory.ROOT / ".security-scan").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=inventory.ROOT / ".security-scan")
        self.root = Path(self.temp.name)
        self.root_patch = patch("inventory.ROOT", self.root)
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop()
        self.temp.cleanup()

    def test_source_dir_obeys_terraform_exclusions(self):
        base = self.root / "lambda"
        base.mkdir()
        for name in ("handler.py", "local_server.py", "README.md"):
            (base / name).write_text("fixture")
        config = {"type": "zip", "source_dir": "${path.module}/lambda",
                  "excludes": ["local_server.py", "README.md"]}
        members = inventory.archive_members(config, self.root, {"lambda/handler.py"})
        self.assertEqual(list(members), ["handler.py"])

    def test_inline_file_sources_and_unknown_content(self):
        (self.root / "handler.py").write_text("fixture")
        config = {"type": "zip", "source": [{"content": '${file("${path.module}/handler.py")}',
                                                "filename": "handler.py"}]}
        self.assertEqual(list(inventory.archive_members(config, self.root, {"handler.py"})), ["handler.py"])
        config["source"][0]["content"] = "new generated archive content"
        with self.assertRaises(ValueError):
            inventory.archive_members(config, self.root, {"handler.py"})

    def test_missing_input_and_ignored_member_fail(self):
        (self.root / "handler.py").write_text("fixture")
        with self.assertRaises(ValueError):
            inventory.archive_members({"type": "zip", "source_file": "${path.module}/missing.py"},
                                      self.root, set())
        with self.assertRaises(ValueError):
            inventory.archive_members({"type": "zip", "source_dir": "${path.module}"}, self.root, set())

    def test_compose_deduplicates_and_rejects_unresolved_images(self):
        images = {}
        inventory.compose_images(" image: registry/app:1\n image: registry/app:1", "compose.yml", images)
        self.assertEqual(len(images), 1)
        self.assertEqual(len(images["registry/app:1"]), 2)
        with self.assertRaises(ValueError):
            inventory.compose_images(" image: ${new_image}", "compose.yml", images)


if __name__ == "__main__":
    unittest.main()
