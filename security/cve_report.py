"""Create a readable index from sanitized findings, without accepting any risk."""
import csv
import json
from pathlib import Path


def cell(value):
    value = str(value or "")
    # Make the downloadable CSV safe to open in a spreadsheet.
    return "'" + value if value[:1] in {"=", "+", "-", "@", "\t", "\r"} else value


def write_report(root, inventory, coverage):
    root = Path(root)
    rows, summary = [], []
    for target in inventory["targets"]:
        directory = root / "reports" / target["id"]
        status_path, findings_path = directory / "status.json", directory / "findings.json"
        status = json.loads(status_path.read_text()) if status_path.exists() else {}
        findings = json.loads(findings_path.read_text()) if findings_path.exists() else []
        blocked = [item for item in findings if item["kind"] == "vulnerability"
                   and item["severity"] in {"HIGH", "CRITICAL"}]
        fixed = sum(bool(item.get("FixedVersion")) for item in blocked)
        asset = target["name"].removeprefix("Managed rebuild of ")
        identity = status.get("resolved_image") or status.get("image_build", {}).get("image_id") or target["scan_ref"]
        if blocked or not status.get("complete"):
            summary.append((asset, target.get("deployment_status", "configured"), len(blocked), fixed,
                            status.get("complete") is True))
        for item in blocked:
            rows.append({"asset": asset, "deployment_status": target.get("deployment_status", "configured"),
                         "target_id": target["id"], "assessed_identity": identity,
                         "cve": item["id"], "severity": item["severity"], "package": item.get("PkgName", ""),
                         "installed_version": item.get("InstalledVersion", ""),
                         "fixed_version": item.get("FixedVersion", ""),
                         "mitigation_review": ("Verified upstream source fix " + item["source_fix"]["upstream_version"]
                                               + "; informational, still blocking pending policy")
                         if item.get("source_fix") else "Not individually assessed",
                         "advisory": item.get("PrimaryURL", ""),
                         "findings_file": "reports/" + target["id"] + "/findings.json"})
    rows.sort(key=lambda row: (row["severity"] != "CRITICAL", not bool(row["fixed_version"]),
                              row["asset"], row["cve"], row["package"]))
    fields = ["asset", "deployment_status", "target_id", "assessed_identity", "cve", "severity", "package", "installed_version",
              "fixed_version", "mitigation_review", "advisory", "findings_file"]
    csv_path = root / "remaining-cves.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: cell(value) for key, value in row.items()} for row in rows)
    unique = len({row["cve"] for row in rows})
    fixable = sum(bool(row["fixed_version"]) for row in rows)
    candidate_count = sum(row["deployment_status"] == "candidate" for row in rows)
    source_fixed = sum(row["mitigation_review"].startswith("Verified upstream source fix") for row in rows)
    report = ("# Remaining merge-blocking CVEs\n\n"
              f"Commit: `{inventory['commit']}`\n\n"
              f"Coverage complete: **{coverage['complete']}**. Policy passed: **{coverage['policy_passed']}**.\n\n"
              f"**{len(rows)} HIGH/CRITICAL instances**, **{unique} distinct vulnerability IDs**, "
              f"**{fixable} instances reporting fixed versions**. "
              f"**{source_fixed} instances have a verified upstream source fix**; they remain counted and "
              "blocking because the scanner has no distro fixed version to compare and no policy accepts them.\n\n"
              f"Configured assets: **{len(rows)-candidate_count} instances**. "
              f"Candidates not deployed: **{candidate_count} instances**. A candidate does not replace "
              "the assessment of the configured upstream image or imply deployment/approval.\n\n"
              "[Download the full sortable CVE list](remaining-cves.csv). Each row identifies the assessed artifact, "
              "package, installed/fixed versions and advisory. Repeated IDs across packages/images are separate instances.\n\n"
              "These are scanner findings, not proof of exploitability. Individual compensating controls and "
              "mitigations have not been evaluated by this report. An empty fixed-version field means the scanner "
              "reports no fix; it does not grant an exception. Incomplete coverage must never be read as clean. "
              "Lower severities, secrets and other evidence remain in each target's findings.json/SARIF.\n\n"
              "| Asset | State | Blocking CVE instances | Reporting fixes | Scan complete |\n|---|---|---:|---:|---|\n")
    for asset, state, count, fixed, complete in summary:
        report += f"| {asset.replace('|', '&#124;').replace(chr(10), ' ')} | {state} | {count} | {fixed} | {complete} |\n"
    markdown = root / "remaining-cves.md"
    markdown.write_text(report, encoding="utf-8")
    return [markdown, csv_path]
