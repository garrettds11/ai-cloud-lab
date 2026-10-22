import copy
import datetime
import json
from pathlib import Path
import unittest

import risk_acceptance
import scan

DIGEST = "registry/app@sha256:" + "a" * 64
OTHER = "registry/app@sha256:" + "b" * 64


def finding(cve="CVE-2026-0001", severity="HIGH", fixed="2", kind="vulnerability"):
    return {"kind": kind, "id": cve, "severity": severity, "path": "/app", "PkgName": "pkg",
            "InstalledVersion": "1", "FixedVersion": fixed, "line": 1}


class RiskAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.today = datetime.date(2026, 10, 20)
        self.policy = {"schema_version": 1, "policy": "test", "records": [{
            "id": "app-1", "image": DIGEST, "accepted_by": "owner", "accepted_on": "2026-10-17",
            "expires": "2026-11-16", "reason": "Vendor has not shipped the fix.",
            "findings": ["CVE-2026-0001"]}]}

    def annotate(self, items, image=DIGEST, policy=None, today=None):
        return risk_acceptance.annotate(items, image, policy or self.policy, today or self.today)

    def test_no_vendor_fix_is_accepted_and_reported(self):
        items = self.annotate([finding(fixed="")], image=None)
        self.assertEqual(items[0]["acceptance"]["basis"], "no_vendor_fix")
        self.assertEqual(scan.gate(items), 0)
        self.assertEqual(risk_acceptance.counts(items), {"accepted_no_vendor_fix": 1, "accepted_by_record": 0})

    def test_fixable_finding_blocks_without_a_record(self):
        items = self.annotate([finding(cve="CVE-2026-0002")])
        self.assertNotIn("acceptance", items[0])
        self.assertEqual(scan.gate(items), 1)

    def test_record_applies_only_to_its_exact_digest_and_ids(self):
        self.assertEqual(scan.gate(self.annotate([finding()])), 0)
        self.assertEqual(scan.gate(self.annotate([finding()], image=OTHER)), 1)
        self.assertEqual(scan.gate(self.annotate([finding()], image=None)), 1)
        self.assertEqual(scan.gate(self.annotate([finding(cve="CVE-2026-0003")])), 1)

    def test_record_can_bind_a_local_build_image_id(self):
        policy = copy.deepcopy(self.policy)
        local = "sha256:" + "c" * 64
        policy["records"][0]["image"] = local
        self.assertEqual(scan.gate(self.annotate([finding()], image=local, policy=policy)), 0)
        self.assertEqual(scan.gate(self.annotate([finding()], image="sha256:" + "d" * 64, policy=policy)), 1)

    def test_managed_rebuild_matches_its_vendor_base_digest(self):
        local = "sha256:" + "c" * 64
        self.assertEqual(scan.gate(self.annotate([finding()], image=[local, DIGEST])), 0)
        self.assertEqual(scan.gate(self.annotate([finding()], image=[local, OTHER])), 1)
        self.assertEqual(scan.gate(self.annotate([finding()], image=[local, None])), 1)
        self.assertEqual(scan.gate(self.annotate([finding()], image=[None, None])), 1)

    def test_only_compose_rebuilds_offer_their_vendor_base(self):
        build = {"image_id": "sha256:" + "c" * 64, "base": DIGEST}
        self.assertEqual(scan.risk_identities({"image_build": build}), (build["image_id"], DIGEST))
        for scope in ("candidate", "tool"):
            self.assertEqual(scan.risk_identities({"image_build": dict(build, scope=scope)}),
                             (build["image_id"], None))
        self.assertEqual(scan.risk_identities({"resolved_image": OTHER}), (OTHER, None))

    def test_all_covers_every_fixable_finding_of_that_digest(self):
        policy = copy.deepcopy(self.policy)
        policy["records"][0]["findings"] = "all"
        items = self.annotate([finding(cve="CVE-2026-0003"), finding(cve="CVE-2026-0004", severity="CRITICAL")],
                              policy=policy)
        self.assertEqual(scan.gate(items), 0)
        self.assertEqual(risk_acceptance.counts(items)["accepted_by_record"], 2)

    def test_expired_or_future_record_does_not_apply(self):
        self.assertEqual(scan.gate(self.annotate([finding()], today=datetime.date(2026, 11, 16))), 1)
        self.assertEqual(scan.gate(self.annotate([finding()], today=datetime.date(2026, 10, 16))), 1)

    def test_secrets_and_lower_severities_are_untouched(self):
        items = self.annotate([finding(kind="secret", fixed=""), finding(severity="MEDIUM", fixed="")])
        self.assertTrue(all("acceptance" not in item for item in items))
        self.assertEqual(scan.gate(items), 1)

    def test_accepted_rows_stay_in_sarif_without_suppression(self):
        target = {"id": "image-x", "kind": "image", "name": "app", "origins": [{"file": "compose.yaml", "line": 1}]}
        items = self.annotate([finding(fixed="")])
        result = scan.sarif(target, items, "0.75.0")["runs"][0]["results"][0]
        self.assertNotIn("suppressions", result)
        self.assertEqual(result["level"], "error")
        self.assertEqual(result["properties"]["acceptance"]["basis"], "no_vendor_fix")

    def test_invalid_records_are_rejected(self):
        for mutate in (lambda r: r.update(image="registry/app:1"),
                       lambda r: r.update(image="sha256:short"),
                       lambda r: r.update(expires="2026-12-31"),
                       lambda r: r.update(accepted_by="someone"),
                       lambda r: r.update(reason=""),
                       lambda r: r.update(findings=[]),
                       lambda r: r.update(findings=["not-a-cve"])):
            policy = copy.deepcopy(self.policy)
            mutate(policy["records"][0])
            with self.assertRaises(ValueError):
                risk_acceptance.validate(policy)

    def test_repository_records_are_valid(self):
        path = Path(__file__).resolve().parent / "accepted-risk.json"
        risk_acceptance.validate(json.loads(path.read_text()))


if __name__ == "__main__":
    unittest.main()
