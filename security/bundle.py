"""Reconcile expected coverage and bundle evidence, even when a scan failed."""
import hashlib
import json
import os
import shutil
from pathlib import Path
import tarfile

from cve_report import write_report
from sbom import write_sboms

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".security-scan"


def reconcile(target, status, commit, has_sarif, has_findings, has_sbom):
    return (status.get("complete") is True and status.get("commit") == commit
            and status.get("target") == target and has_sarif and has_findings and has_sbom)


def main():
    inventory = json.loads((OUT / "inventory.json").read_text())
    sbom_root = OUT / "sboms"
    sboms = write_sboms(OUT, sbom_root, inventory)
    available_sboms = {entry["target_id"] for entry in sboms["targets"] if entry["available"]}
    coverage = []
    for target in inventory["targets"]:
        directory = OUT / "reports" / target["id"]
        path = directory / "status.json"
        status = json.loads(path.read_text()) if path.exists() else {}
        complete = reconcile(target, status, inventory["commit"],
                             (directory / "results.sarif").is_file(),
                             (directory / "findings.json").is_file(), target["id"] in available_sboms)
        coverage.append({"id": target["id"], "complete": complete,
                         "blocking_findings": status.get("blocking_findings"),
                         "reviewed_false_positives": status.get("reviewed_false_positives", 0)})
    complete = all(item["complete"] for item in coverage)
    clean = complete and all(item["blocking_findings"] == 0 for item in coverage)
    manifest = {"commit": inventory["commit"], "run_id": os.environ.get("GITHUB_RUN_ID"),
                "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
                "complete": complete, "policy_passed": clean, "coverage": coverage,
                "policy": "Block all secrets and HIGH/CRITICAL CVEs, including unfixed; "
                          "except exact unexpired immutable-feed test-data false positives; "
                          "Trivy misconfiguration advisory; Checkov separate blocking gate"}
    (OUT / "coverage.json").write_text(json.dumps(manifest, indent=2) + "\n")
    evidence = [OUT / "inventory.json", OUT / "coverage.json"]
    evidence += sorted(sbom_root.rglob("*.json")) + [sbom_root / "sboms.md"]
    evidence += write_report(OUT, inventory, manifest)
    review = OUT / "feed-secret-review.json"
    shutil.copyfile(ROOT / "security" / "feed-secret-review.json", review)
    evidence.append(review)
    evidence += sorted((OUT / "reports").rglob("*.json"))
    evidence += sorted((OUT / "reports").rglob("*.sarif"))
    checksum = OUT / "SHA256SUMS"
    checksum.write_text("".join(hashlib.sha256(path.read_bytes()).hexdigest() + "  " +
                               path.relative_to(OUT).as_posix() + "\n" for path in evidence))
    # Contains no source ZIPs, raw Trivy output, matched secrets, or credentials.
    with tarfile.open(OUT / "security-evidence.tar.gz", "w:gz") as archive:
        for path in evidence + [checksum]:
            archive.add(path, arcname=path.relative_to(OUT).as_posix())
    summary = f"## Trivy assessment\n\nCoverage complete: **{complete}**\n\nPolicy passed: **{clean}**\n\n"
    summary += "| Target | Complete | Blocking findings | Reviewed false positives |\n|---|---|---|---|\n"
    summary += "".join(f"| {item['id']} | {item['complete']} | {item['blocking_findings']} | {item['reviewed_false_positives']} |\n"
                       for item in coverage)
    summary += "\nAttestation proves report provenance; consult coverage and findings for assessment status.\n"
    summary += "\nDownload `security-evidence` for `remaining-cves.md` and the sortable `remaining-cves.csv` (asset, CVE, package and fixed version).\n"
    summary += "\nDownload `security-sboms` for the readable index and per-target CycloneDX files; the same files are checksummed under `sboms/` in `security-evidence`.\n"
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as output:
            output.write(summary)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write(f"policy_passed={str(clean).lower()}\n")


if __name__ == "__main__":
    main()
