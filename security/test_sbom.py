import copy
import json
from pathlib import Path
import tempfile
import unittest

import sbom


class SbomTests(unittest.TestCase):
    def setUp(self):
        self.target = {"id": "image", "kind": "image", "name": "scanner database",
                       "scan_ref": "registry/db:stable", "deployment_status": "candidate"}
        self.status = {"complete": True, "target": self.target, "commit": "commit",
                       "resolved_image": "registry/db@sha256:" + "a" * 64,
                       "package_inventory": [{"target": "image", "type": "python-pkg", "packages": [
                           {"Name": "wheel", "Version": "1", "FilePath": "vendor/wheel/METADATA",
                            "Identifier": {"PURL": "pkg:pypi/wheel@1", "UID": "first"}},
                           {"Name": "wheel", "Version": "1", "FilePath": "wheel/METADATA",
                            "Identifier": {"PURL": "pkg:pypi/wheel@1", "UID": "second"}}]}]}

    def test_sbom_keeps_vendored_instances_and_immutable_candidate_identity(self):
        self.status["Secrets"] = [{"Match": "DO-NOT-PUBLISH"}]
        self.status["ImageConfig"] = {"Env": ["PASSWORD=DO-NOT-PUBLISH"]}
        self.status["package_inventory"][0]["packages"][0]["Description"] = "DO-NOT-PUBLISH"
        bom = sbom.document(self.target, self.status)
        self.assertEqual(len(bom["components"]), 2)
        self.assertEqual(len({c["bom-ref"] for c in bom["components"]}), 2)
        self.assertEqual(bom["metadata"]["component"]["version"], self.status["resolved_image"])
        self.assertNotIn("DO-NOT-PUBLISH", json.dumps(bom))
        self.assertIn({"name": "ai-cloud-lab:deployment-status", "value": "candidate"},
                      bom["metadata"]["properties"])

    def test_missing_versions_and_dependency_graph_are_not_invented(self):
        self.status["package_inventory"][0]["packages"][0]["Version"] = None
        bom = sbom.document(self.target, self.status)
        component = next(c for c in bom["components"] if
                         {"name": "ai-cloud-lab:package-file", "value": "vendor/wheel/METADATA"}
                         in c["properties"])
        self.assertNotIn("version", component)
        self.assertNotIn("dependencies", bom)
        self.assertEqual(bom["compositions"][0]["aggregate"], "incomplete")

    def test_failed_missing_or_wrong_commit_inventory_is_unavailable(self):
        parent = Path(__file__).resolve().parents[1] / ".security-scan"
        parent.mkdir(exist_ok=True)
        for change in (lambda s: s.update(complete=False), lambda s: s.update(commit="wrong"),
                       lambda s: s.pop("package_inventory"), lambda s: s.pop("resolved_image")):
            with tempfile.TemporaryDirectory(dir=parent) as temporary:
                root = Path(temporary)
                directory = root / "reports/image"
                directory.mkdir(parents=True)
                status = copy.deepcopy(self.status)
                change(status)
                (directory / "status.json").write_text(json.dumps(status))
                index = sbom.write_sboms(root, root, {"commit": "commit", "targets": [self.target]})
                self.assertFalse(index["complete"])
                self.assertFalse(index["targets"][0]["available"])
                self.assertFalse((directory / "sbom.cdx.json").exists())

    def test_offline_export_rejects_unchecksummed_package_inventory(self):
        parent = Path(__file__).resolve().parents[1] / ".security-scan"
        with tempfile.TemporaryDirectory(dir=parent) as temporary:
            root = Path(temporary)
            directory = root / "reports/image"
            directory.mkdir(parents=True)
            inventory = root / "inventory.json"
            inventory.write_text(json.dumps({"commit": "commit", "targets": [self.target]}))
            (directory / "status.json").write_text(json.dumps(self.status))
            (root / "SHA256SUMS").write_text(sbom.sha256(inventory) + "  inventory.json\n")
            with self.assertRaisesRegex(ValueError, "package inventory was not checksummed"):
                sbom.verify_evidence(root)


if __name__ == "__main__":
    unittest.main()
