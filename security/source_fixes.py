"""Annotate verified local source fixes without removing or suppressing findings.

A record documents that the exact assessed library was rebuilt from upstream
source at or beyond the upstream fixed version, while the distro advisory has no
fixed-version boundary that a scanner can compare. Findings stay in reports
and SARIF. These records never change the policy count themselves; under the
owner's vendor-patch policy (risk_acceptance.py) such rows are accepted because
no vendor fixed version exists, not because of the source fix.
"""
import datetime
import json
import re

VERSION = r"[0-9]+(?:\.[0-9]+){1,3}"
RECEIPT_FIELDS = ("version", "package_version", "source_url", "archive_sha3_256", "source_id")


def version_tuple(value):
    if not re.fullmatch(VERSION, value):
        raise ValueError("Unsupported upstream version: " + value)
    return tuple(int(part) for part in value.split("."))


def validate(review):
    if review.get("schema_version") != 1:
        raise ValueError("Unsupported source-fix review schema")
    expires = datetime.date.fromisoformat(review["expires"])
    reviewed = datetime.date.fromisoformat(review["reviewed"])
    if expires <= reviewed or (expires - reviewed).days > 31:
        raise ValueError("Source-fix reviews must expire within 31 days")
    seen = set()
    for fix in review["fixes"]:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", fix["receipt"]):
            raise ValueError("Source-fix receipt must name a compatibility-check record")
        if not re.fullmatch(r"[a-z0-9][a-z0-9+.-]+", fix["package"]) or not fix["installed_version"]:
            raise ValueError("Source-fix record must identify an exact package version")
        if not re.fullmatch(r"[a-f0-9]{64}", fix["archive_sha3_256"]) or not fix["source_url"].startswith("https://"):
            raise ValueError("Source-fix record lacks pinned upstream provenance")
        if not fix.get("justification"):
            raise ValueError("Source-fix record lacks a justification")
        upstream = version_tuple(fix["upstream_version"])
        for finding in fix["findings"]:
            if not re.fullmatch(r"CVE-[0-9]{4}-[0-9]{4,}", finding["id"]):
                raise ValueError("Source-fix findings must be CVE identifiers")
            if version_tuple(finding["first_fixed_upstream"]) > upstream:
                raise ValueError("Installed upstream version precedes the fixed version")
            if not finding["evidence"] or not all(url.startswith("https://") for url in finding["evidence"]):
                raise ValueError("Source-fix findings require primary-source evidence links")
            key = (fix["package"], fix["installed_version"], finding["id"])
            if key in seen:
                raise ValueError("Duplicate source-fix record")
            seen.add(key)
    return review


def receipt_matches(fix, image_build):
    checks = (image_build or {}).get("compatibility_checks") or {}
    receipt = checks.get(fix["receipt"])
    if not isinstance(receipt, dict):
        return None
    expected = {"version": fix["upstream_version"], "package_version": fix["installed_version"],
                "source_url": fix["source_url"], "archive_sha3_256": fix["archive_sha3_256"],
                "source_id": fix["source_id"]}
    if any(receipt.get(field) != expected[field] for field in RECEIPT_FIELDS):
        return None
    if not re.fullmatch(r"[a-f0-9]{64}", receipt.get("library_sha256", "")):
        return None
    return receipt


def annotate(items, image_build, review, today=None):
    """Return copies of items; matching rows gain an informational source_fix record."""
    validate(review)
    today = today or datetime.datetime.now(datetime.timezone.utc).date()
    current = (datetime.date.fromisoformat(review["reviewed"]) <= today
               < datetime.date.fromisoformat(review["expires"]))
    result = []
    for original in items:
        item = dict(original)
        item.pop("source_fix", None)
        for fix in review["fixes"] if current else []:
            # A distro fixed version means ordinary version comparison applies.
            if (item["kind"] != "vulnerability" or item.get("FixedVersion")
                    or item.get("PkgName") != fix["package"]
                    or item.get("InstalledVersion") != fix["installed_version"]):
                continue
            finding = next((entry for entry in fix["findings"] if entry["id"] == item["id"]), None)
            receipt = finding and receipt_matches(fix, image_build)
            if receipt:
                item["source_fix"] = {
                    "status": "verified_source_fix", "policy_effect": "none",
                    "upstream_version": fix["upstream_version"],
                    "first_fixed_upstream": finding["first_fixed_upstream"],
                    "source_id": fix["source_id"], "archive_sha3_256": fix["archive_sha3_256"],
                    "library_sha256": receipt["library_sha256"],
                    "image_id": (image_build or {}).get("image_id"),
                    "evidence": finding["evidence"], "justification": fix["justification"],
                    "reviewed": review["reviewed"], "expires": review["expires"],
                    "review": "security/source-fixes.json",
                }
                break
        result.append(item)
    return result


def load(path):
    return json.loads(path.read_text())
