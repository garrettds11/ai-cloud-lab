import copy
import datetime
import json
from pathlib import Path
import tempfile
import unittest

import cve_report
import scan
import source_fixes


class SourceFixTests(unittest.TestCase):
    def setUp(self):
        # The SQLite source build was retired (#82); its last record is kept as a fixture.
        self.review = source_fixes.load(Path(__file__).parent / "testdata" / "source-fixes-sample.json")
        fix = self.review["fixes"][0]
        self.fix = fix
        self.receipt = {"version": fix["upstream_version"], "package_version": fix["installed_version"],
                        "source_url": fix["source_url"], "archive_sha3_256": fix["archive_sha3_256"],
                        "source_id": fix["source_id"], "library_sha256": "b" * 64}
        self.build = {"image_id": "sha256:" + "c" * 64,
                      "compatibility_checks": {"sqlite_runtime": self.receipt}}
        self.item = {"kind": "vulnerability", "id": "CVE-2025-7458", "severity": "CRITICAL",
                     "path": "candidate (debian 12.13)", "line": 1, "PkgName": "libsqlite3-0",
                     "InstalledVersion": fix["installed_version"], "FixedVersion": "",
                     "PrimaryURL": "https://avd.aquasec.com/nvd/cve-2025-7458", "Title": "sqlite"}
        self.today = datetime.date.fromisoformat(self.review["reviewed"])

    def annotate(self, items=None, build=None, review=None, today=None):
        return source_fixes.annotate(items or [self.item], self.build if build is None else build,
                                     review or self.review, today or self.today)

    def test_exact_receipt_annotates_without_changing_gate_or_sarif_state(self):
        items = self.annotate()
        self.assertEqual(items[0]["source_fix"]["status"], "verified_source_fix")
        self.assertEqual(items[0]["source_fix"]["library_sha256"], "b" * 64)
        self.assertEqual(scan.gate(items), scan.gate([self.item]))
        self.assertEqual(scan.gate(items), 1)
        target = {"id": "image-x", "kind": "image", "name": "candidate",
                  "origins": [{"file": "scanner/image_builds/manifest.json", "line": 1}]}
        result = scan.sarif(target, items, "0.75.0")["runs"][0]["results"][0]
        plain = scan.sarif(target, [self.item], "0.75.0")["runs"][0]["results"][0]
        self.assertNotIn("suppressions", result)
        self.assertEqual(result["level"], "error")
        self.assertEqual(result["partialFingerprints"], plain["partialFingerprints"])
        self.assertIn("verified upstream source fix 3.54.0", result["message"]["text"])
        self.assertNotIn("source_fix", self.item)

    def test_all_three_reviewed_ids_match_and_nothing_else(self):
        items = [dict(self.item, id=cve) for cve in ("CVE-2025-7458", "CVE-2026-11822", "CVE-2026-11824",
                                                     "CVE-2099-0001")]
        marked = [bool(item.get("source_fix")) for item in self.annotate(items)]
        self.assertEqual(marked, [True, True, True, False])

    def test_mismatches_void_the_record(self):
        cases = [
            dict(self.item, InstalledVersion="3.40.1-2+deb12u2"),
            dict(self.item, PkgName="sqlite3"),
            dict(self.item, FixedVersion="3.40.1-2+deb12u3"),
            dict(self.item, kind="secret"),
        ]
        for item in cases:
            self.assertNotIn("source_fix", self.annotate([item])[0], item)
        for field, value in (("archive_sha3_256", "0" * 64), ("source_id", "other"),
                             ("version", "3.53.1"), ("library_sha256", "not-a-hash")):
            build = copy.deepcopy(self.build)
            build["compatibility_checks"]["sqlite_runtime"][field] = value
            self.assertNotIn("source_fix", self.annotate(build=build)[0], field)
        self.assertNotIn("source_fix", self.annotate(build={})[0])
        self.assertNotIn("source_fix", self.annotate(build={"compatibility_checks": None})[0])

    def test_expired_or_future_review_applies_nothing(self):
        expires = datetime.date.fromisoformat(self.review["expires"])
        self.assertNotIn("source_fix", self.annotate(today=expires)[0])
        self.assertNotIn("source_fix", self.annotate(today=self.today - datetime.timedelta(days=1))[0])

    def test_replayed_annotation_is_removed(self):
        forged = dict(self.item, source_fix={"status": "verified_source_fix"}, InstalledVersion="1")
        self.assertNotIn("source_fix", self.annotate([forged])[0])

    def test_invalid_reviews_fail_closed(self):
        mutations = [
            lambda review: review.update(schema_version=2),
            lambda review: review.update(expires="2027-01-01"),
            lambda review: review["fixes"][0]["findings"][0].update(first_fixed_upstream="3.99.0"),
            lambda review: review["fixes"][0]["findings"][0].update(evidence=[]),
            lambda review: review["fixes"][0]["findings"][0].update(id="GHSA-xxxx"),
            lambda review: review["fixes"][0].update(archive_sha3_256="abc"),
            lambda review: review["fixes"][0].update(justification=""),
            lambda review: review["fixes"][0]["findings"].append(
                copy.deepcopy(review["fixes"][0]["findings"][0])),
        ]
        for mutate in mutations:
            review = copy.deepcopy(self.review)
            mutate(review)
            with self.assertRaises(ValueError):
                source_fixes.annotate([self.item], self.build, review, self.today)

    def test_report_keeps_rows_and_labels_them(self):
        items = self.annotate()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "reports" / "image-x"
            directory.mkdir(parents=True)
            (directory / "findings.json").write_text(json.dumps(items))
            (directory / "status.json").write_text(json.dumps({"complete": True}))
            inventory = {"commit": "a" * 40, "targets": [
                {"id": "image-x", "name": "candidate", "scan_ref": "candidate",
                 "deployment_status": "candidate"}]}
            markdown, csv_path = cve_report.write_report(root, inventory,
                                                         {"complete": True, "policy_passed": False})
            self.assertIn("**1 HIGH/CRITICAL instances**", markdown.read_text())
            self.assertIn("Verified upstream source fixes (informational): 1 instances.", markdown.read_text())
            self.assertIn("Verified upstream source fix 3.54.0", csv_path.read_text())


if __name__ == "__main__":
    unittest.main()
