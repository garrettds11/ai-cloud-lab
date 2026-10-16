"""Run a digest-pinned Trivy scanner and publish only sanitized findings."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import quote

from dispositions import classify, load
import source_fixes

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".security-scan"
TRIVY_IMAGE = "ghcr.io/aquasecurity/trivy@sha256:af6acf9a6b85dfe389a1941505c0ce9efef52a4719635e1a962f022a3d855daa"
sys.path.insert(0, str(ROOT / "scanner"))
import managed_images


def findings(report):
    """Allowlist fields; never publish secret matches, snippets or image environment."""
    result = []
    for target in report.get("Results", []):
        for group, kind, key in (("Vulnerabilities", "vulnerability", "VulnerabilityID"),
                                 ("Secrets", "secret", "RuleID"),
                                 ("Misconfigurations", "misconfiguration", "ID")):
            for item in target.get(group, []) or []:
                finding = {"kind": kind, "id": item[key],
                           "severity": item.get("Severity", "UNKNOWN"),
                           "path": target.get("Target", ""),
                           "line": max(1, int(item.get("StartLine", 1) or 1))}
                if kind == "vulnerability":
                    finding.update({field: item.get(field, "") for field in
                                    ("PkgName", "InstalledVersion", "FixedVersion", "PrimaryURL", "Title")})
                elif kind == "misconfiguration":
                    finding["Title"] = item.get("Title", item[key])
                    finding["PrimaryURL"] = item.get("PrimaryURL", "")
                    finding["line"] = max(1, int(item.get("CauseMetadata", {}).get("StartLine", 1) or 1))
                # Secret descriptions/messages can themselves contain matching text.
                else:
                    finding["Title"] = "Potential secret detected; matching value redacted"
                result.append(finding)
    return result


def gate(items):
    return sum((item["kind"] == "secret" and
                item.get("disposition", {}).get("classification") != "false_positive") or
               (item["kind"] == "vulnerability" and item["severity"] in {"HIGH", "CRITICAL"})
               for item in items)


def source_location(target, finding):
    if target["kind"] == "repository":
        path = finding["path"].removeprefix("/work/" + target["scan_ref"] + "/")
        if not path.startswith("/") and ".." not in Path(path).parts:
            return path, finding["line"]
    if target["kind"] == "package":
        path = finding["path"].removeprefix("/work/" + target["scan_ref"] + "/")
        for member in target.get("members", []):
            if member["member"] == path and "source" in member:
                return member["source"], finding["line"]
    return target["origins"][0]["file"], target["origins"][0]["line"]


def sarif(target, items, version):
    rules, results = {}, []
    for item in items:
        rule_id = item["kind"] + ":" + item["id"]
        rule = {"id": rule_id, "shortDescription": {"text": item.get("Title") or item["id"]}}
        if item.get("PrimaryURL", "").startswith("https://"):
            rule["helpUri"] = item["PrimaryURL"]
        rules.setdefault(rule_id, rule)
        path, line = source_location(target, item)
        message = f"{item['id']} ({item['severity']}) in {target['name']}: {item['path']}"
        if item["kind"] == "vulnerability":
            message += (f"; {item['PkgName']} {item['InstalledVersion']}; "
                        f"fixed: {item['FixedVersion'] or 'not available'}")
            if item.get("source_fix"):
                message += ("; verified upstream source fix " + item["source_fix"]["upstream_version"] +
                            " (fixed from " + item["source_fix"]["first_fixed_upstream"] +
                            "); informational only, still counted as blocking")
        elif item["kind"] == "secret":
            message += "; matching value redacted"
            if item.get("disposition"):
                message += ("; reviewed false positive: " + item["disposition"]["justification"] +
                            "; review expires " + item["disposition"]["expires"])
        fingerprint = hashlib.sha256(json.dumps(
            [target["id"], rule_id, item["path"], item.get("PkgName"),
             item.get("InstalledVersion"), item["line"]], separators=(",", ":")
        ).encode()).hexdigest()
        results.append({"ruleId": rule_id,
                        "level": "error" if item["severity"] in {"CRITICAL", "HIGH"} else "warning",
                        "message": {"text": message},
                        "locations": [{"physicalLocation": {
                            "artifactLocation": {"uri": quote(path.replace("\\", "/"), safe="/")},
                            "region": {"startLine": line}}}],
                        "partialFingerprints": {"primaryLocationLineHash": fingerprint},
                        "properties": {"target": target["id"], "kind": item["kind"],
                                       "severity": item["severity"]}})
        if item.get("source_fix"):
            # Deliberately no SARIF suppression: the alert stays open and blocking.
            results[-1]["properties"]["source_fix"] = item["source_fix"]
        if item.get("disposition"):
            disposition = item["disposition"]
            results[-1]["suppressions"] = [{
                "kind": "external", "status": "accepted",
                "justification": disposition["justification"],
            }]
            results[-1]["properties"]["disposition"] = disposition
    return {"version": "2.1.0", "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
            "runs": [{"tool": {"driver": {"name": "Trivy", "version": version,
                      "informationUri": "https://github.com/aquasecurity/trivy",
                      "rules": list(rules.values())}},
                      "automationDetails": {"id": "trivy/" + target["id"] + "/"},
                      "results": results}]}


def resolve_image(reference):
    output = subprocess.check_output(["docker", "buildx", "imagetools", "inspect", reference],
                                     text=True, timeout=180)
    match = re.search(r"^Digest:\s+(sha256:[a-f0-9]{64})\s*$", output, re.MULTILINE)
    if not match:
        raise ValueError("Image registry did not return an immutable digest")
    repository = reference.split("@")[0]
    if ":" in repository.rsplit("/", 1)[-1]:
        repository = repository.rsplit(":", 1)[0]
    return repository + "@" + match[1]


def scanner_ref(inventory):
    record = inventory["scanner_build"]
    if record.get("reference") != TRIVY_IMAGE or record.get("scope") != "tool":
        raise ValueError("Missing or unexpected reviewed scanner build")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", record.get("image_id", "")):
        raise ValueError("Scanner requires an immutable image ID")
    archive = OUT / "inputs" / "scanner-image.tar"
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != record["archive_sha256"]:
            raise ValueError("Scanner archive differs from inventoried artifact")
    subprocess.run(["docker", "image", "load", "--input", str(archive)], check=True, timeout=300,
                   stdout=subprocess.DEVNULL)
    loaded = subprocess.check_output(["docker", "image", "inspect", record["image_id"],
                                      "--format", "{{.Id}}"], text=True, timeout=60).strip()
    if loaded != record["image_id"]:
        raise ValueError("Loaded scanner image does not match inventoried build")
    return record["image_id"], record


def scan(target_id):
    inventory = json.loads((OUT / "inventory.json").read_text())
    target = next(item for item in inventory["targets"] if item["id"] == target_id)
    destination = OUT / "reports" / target_id
    destination.mkdir(parents=True, exist_ok=True)
    status = {"target": target, "commit": inventory["commit"], "complete": False,
              "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "scanner_base_image": TRIVY_IMAGE, "blocking_findings": None}
    raw = OUT / (target_id + ".raw.json")
    image_archive = OUT / (target_id + ".image.tar")
    cache = OUT / "cache"
    cache.mkdir(exist_ok=True)
    # No Docker socket, AWS credentials, or execution of scanned images/packages.
    try:
        reference, scanner = scanner_ref(inventory)
        status["scanner_image"], status["scanner_build"] = reference, scanner
        docker = ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}",
                  "-e", "HOME=/tmp", "-v", f"{ROOT}:/work:ro", "-v", f"{OUT}:/output",
                  "-v", f"{cache}:/cache", reference]
        version = json.loads(subprocess.check_output(docker + ["--version", "--format", "json"],
                                                     text=True, timeout=180))
        status["scanner_version_before"] = version
        if target["kind"] == "image":
            if target.get("build"):
                root = (ROOT / target["build_root"]).resolve()
                root.relative_to(OUT / "inputs")
                definition = next(image for image in managed_images.definitions(root)
                                  if image["reference"] == target["scan_ref"])
                if definition != target["build"]:
                    raise ValueError("Managed image recipe differs from inventoried input")
                if definition.get("scope") == "tool":
                    if any(scanner.get(key) != value for key, value in definition.items()):
                        raise ValueError("Scanner build differs from reviewed recipe")
                    record = scanner
                    # Assess the exact image already executing this scan, never rebuild it.
                    input_archive = OUT / "inputs" / "scanner-image.tar"
                else:
                    record = managed_images.build(root, definition)
                    subprocess.run(["docker", "image", "save", "--output", str(image_archive), record["image_id"]],
                                   check=True, timeout=300)
                    input_archive = image_archive
                status["image_build"] = record
                with input_archive.open("rb") as stream:
                    status["image_archive_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
                # Read a static archive: the scanner still gets no Docker socket.
                command = ["image", "--scanners", "vuln,secret", "--input",
                           "/work/" + input_archive.relative_to(ROOT).as_posix()]
            else:
                reference = resolve_image(target["scan_ref"])
                status["resolved_image"] = reference
                command = ["image", "--image-src", "remote", "--platform", target["platform"],
                           "--scanners", "vuln,secret", reference]
        else:
            command = ["fs", "--scanners", "vuln,secret,misconfig", "/work/" + target["scan_ref"]]
        # Findings don't determine the process exit code until reports are persisted.
        scan_command = docker + [command[0], "--cache-dir", "/cache", "--timeout", "45m",
                       "--format", "json", "--list-all-pkgs", "--exit-code", "0",
                       "--output", "/output/" + raw.name] + command[1:]
        # Registry streams can fail mid-layer. Retry the same immutable target,
        # with a shared time budget; CVE/secret findings never trigger retries.
        deadline = time.monotonic() + 3000
        for attempt in range(1, 4):
            status["scan_attempts"] = attempt
            try:
                subprocess.run(scan_command, check=True,
                               timeout=max(1, int(deadline - time.monotonic())))
                break
            except subprocess.CalledProcessError:
                if attempt == 3 or time.monotonic() >= deadline:
                    raise
                raw.unlink(missing_ok=True)
                time.sleep(5)
        report = json.loads(raw.read_text(encoding="utf-8"))
        if report.get("SchemaVersion") != 2:
            raise ValueError("Unexpected Trivy report schema")
        items = findings(report)
        status["raw_blocking_findings"] = gate(items)
        review_path = ROOT / "security" / "feed-secret-review.json"
        items = classify(items, status.get("resolved_image"), load(review_path),
                         platform=target.get("platform"))
        status["secret_review_sha256"] = hashlib.sha256(review_path.read_bytes()).hexdigest()
        status["reviewed_false_positives"] = sum(bool(item.get("disposition")) for item in items)
        fixes_path = ROOT / "security" / "source-fixes.json"
        before = gate(items)
        items = source_fixes.annotate(items, status.get("image_build"), source_fixes.load(fixes_path))
        if gate(items) != before:
            raise ValueError("Source-fix records must not change the policy count")
        status["source_fix_review_sha256"] = hashlib.sha256(fixes_path.read_bytes()).hexdigest()
        status["verified_source_fixes"] = sum(bool(item.get("source_fix")) for item in items)
        # Preserve DB timestamps/version after the scan, not just before first download.
        status["scanner_version_after"] = json.loads(subprocess.check_output(
            docker + ["--cache-dir", "/cache", "--version", "--format", "json"],
            text=True, timeout=180))
        status["complete"] = True
        status["blocking_findings"] = gate(items)
        status["finding_count"] = len(items)
        status["image_metadata"] = {key: report.get("Metadata", {}).get(key) for key in
                                    ("ImageID", "RepoDigests", "OS")}
        status["package_inventory"] = [{"target": entry.get("Target"),
                                        "type": entry.get("Type"),
                                        "packages": entry.get("Packages", [])}
                                       for entry in report.get("Results", [])]
        (destination / "findings.json").write_text(json.dumps(items, indent=2) + "\n")
        (destination / "results.sarif").write_text(json.dumps(
            sarif(target, items, version["Version"]), indent=2) + "\n")
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, StopIteration) as error:
        # Raw stderr/report content can contain sensitive input; retain only error type.
        status["error_type"] = type(error).__name__
        print(f"Scan incomplete for {target_id}: {type(error).__name__}", file=sys.stderr)
    finally:
        raw.unlink(missing_ok=True)
        image_archive.unlink(missing_ok=True)
        status["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        (destination / "status.json").write_text(json.dumps(status, indent=2) + "\n")
    return 0 if status["complete"] and not status["blocking_findings"] else 1


if __name__ == "__main__":
    sys.exit(scan(sys.argv[1]))
