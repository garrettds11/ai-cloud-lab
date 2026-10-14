import csv
import json
from pathlib import Path
import tempfile
import unittest

from cve_report import write_report


class CveReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary_root = Path(__file__).resolve().parents[1] / ".security-scan"
        self.temporary_root.mkdir(exist_ok=True)

    def test_duplicate_instances_fixes_and_secret_redaction(self):
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as temporary:
            root = Path(temporary)
            directory = root / "reports/image"
            directory.mkdir(parents=True)
            target = {"id": "image", "name": "=unsafe", "scan_ref": "registry/app:1"}
            (directory / "status.json").write_text(json.dumps({"complete": True, "resolved_image": "digest"}))
            items = [{"kind": "vulnerability", "id": "CVE-example", "severity": "HIGH", "PkgName": package,
                      "InstalledVersion": "1", "FixedVersion": fixed}
                     for package, fixed in (("first", "2"), ("second", ""))]
            items.append({"kind": "secret", "id": "private-key", "severity": "HIGH", "Match": "DO-NOT-PUBLISH"})
            (directory / "findings.json").write_text(json.dumps(items))
            files = write_report(root, {"commit": "commit", "targets": [target]},
                                 {"complete": True, "policy_passed": False})
            with files[1].open(encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["fixed_version"], "2")
            self.assertEqual(rows[0]["asset"], "'=unsafe")
            self.assertIn("2 HIGH/CRITICAL instances", files[0].read_text())
            self.assertIn("1 distinct vulnerability IDs", files[0].read_text())
            self.assertNotIn("DO-NOT-PUBLISH", "".join(path.read_text() for path in files))

    def test_missing_report_is_visible_as_incomplete_not_clean(self):
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as temporary:
            root = Path(temporary)
            write_report(root, {"commit": "commit", "targets": [{"id": "missing", "name": "missing", "scan_ref": "missing"}]},
                         {"complete": False, "policy_passed": False})
            report = (root / "remaining-cves.md").read_text()
            self.assertIn("Coverage complete: **False**", report)
            self.assertIn("| missing | 0 | 0 | False |", report)
