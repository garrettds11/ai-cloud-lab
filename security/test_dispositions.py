import copy
import datetime
import json
from pathlib import Path
import unittest

import dispositions
import scan


class SecretReviewTests(unittest.TestCase):
    def setUp(self):
        self.review = dispositions.load(Path(__file__).with_name("feed-secret-review.json"))
        entry = self.review["files"][0]
        match = entry["matches"][0]
        self.item = {"kind": "secret", "path": entry["path"], "id": match["rule"],
                     "line": match["line"], "severity": "HIGH"}
        self.today = datetime.date.fromisoformat(self.review["reviewed"])

    def classify(self, items=None, image=None, today=None, review=None):
        return dispositions.classify(items or [self.item], image or self.review["image"],
                                     review or self.review, today or self.today)

    def test_review_is_exact_and_keeps_findings_in_sarif(self):
        items = self.classify()
        self.assertEqual(scan.gate(items), 0)
        target = {"id": "image", "kind": "image", "name": self.review["image"],
                  "origins": [{"file": "scanner/run_scan.py", "line": 1}]}
        result = scan.sarif(target, items, "0.75.0")["runs"][0]["results"][0]
        self.assertEqual(result["suppressions"][0]["status"], "accepted")
        self.assertEqual(result["properties"]["disposition"]["classification"], "false_positive")
        self.assertEqual(len(items), 1)
        self.assertNotIn("disposition", self.item)

    def test_new_digest_does_not_inherit_review(self):
        image = dispositions.FEED_REPOSITORY + "@sha256:" + "0" * 64
        self.assertEqual(scan.gate(self.classify(image=image)), 1)

    def test_other_images_never_inherit_review(self):
        self.assertEqual(scan.gate(self.classify(image="registry/pg-gvm@sha256:" + "0" * 64)), 1)

    def test_other_platform_never_inherits_review(self):
        items = dispositions.classify([self.item], self.review["image"], self.review,
                                      self.today, platform="linux/arm64")
        self.assertEqual(scan.gate(items), 1)

    def test_expiration_and_future_review_fail_closed(self):
        for today in (datetime.date.fromisoformat(self.review["expires"]),
                      self.today - datetime.timedelta(days=1)):
            self.assertEqual(scan.gate(self.classify(today=today)), 1)

    def test_changed_path_line_or_rule_requires_review(self):
        for key, value in (("path", "/etc/ssl/private/key"), ("line", 9999), ("id", "new-rule")):
            item = dict(self.item, **{key: value})
            self.assertEqual(scan.gate(self.classify(items=[item])), 1)

    def test_cves_cannot_be_dispositioned(self):
        item = dict(self.item, kind="vulnerability")
        self.assertEqual(scan.gate(self.classify(items=[item])), 1)

    def test_replayed_disposition_does_not_bypass_expiration(self):
        items = self.classify()
        self.assertEqual(scan.gate(self.classify(
            items=items, today=datetime.date.fromisoformat(self.review["expires"]))), 1)

    def test_invalid_review_fails_instead_of_ignoring_findings(self):
        for change in (lambda r: r.update(image="registry/app:latest"),
                       lambda r: r.update(expires="2027-01-01"),
                       lambda r: r["files"][0].update(path="/etc/ssl/private/key"),
                       lambda r: r["files"][0]["matches"][0].update(rule="CVE-123"),
                       lambda r: r["files"].append(copy.deepcopy(r["files"][0]))):
            review = copy.deepcopy(self.review)
            change(review)
            with self.assertRaises(ValueError):
                self.classify(review=review)

    def test_review_covers_only_the_72_verified_feed_matches(self):
        self.assertEqual(len(self.review["files"]), 28)
        self.assertEqual(sum(len(f["matches"]) for f in self.review["files"]), 72)
        items = [dict(self.item, path=f["path"], id=m["rule"], line=m["line"])
                 for f in self.review["files"] for m in f["matches"]]
        self.assertEqual(scan.gate(self.classify(items=items)), 0)

    def test_secrets_on_different_image_lines_keep_distinct_fingerprints(self):
        items = [dict(self.item, line=38), dict(self.item, line=63)]
        target = {"id": "image", "kind": "image", "name": self.review["image"],
                  "origins": [{"file": "scanner/run_scan.py", "line": 1}]}
        results = scan.sarif(target, items, "0.75.0")["runs"][0]["results"]
        self.assertNotEqual(results[0]["partialFingerprints"], results[1]["partialFingerprints"])


if __name__ == "__main__":
    unittest.main()
