"""Apply the owner's vendor-patch policy to HIGH/CRITICAL vulnerability findings.

Owner policy (2026-10-18): this lab does not patch downstream software. A finding blocks
only while a vendor fix exists that has not been taken:

* No vendor fixed version (Trivy reports an empty FixedVersion): accepted risk, by policy.
* A fixed version exists, but the vendor of the pinned image has not shipped a release
  containing it: accepted only through an owner record in security/accepted-risk.json,
  bound to the exact image digest and expiring within 31 days. Re-pinning the image to a
  new digest ends the record, so every new vendor release is assessed again.
* Otherwise the finding blocks until the image or package is upgraded.

Accepted findings are annotated, never removed: they stay in findings.json, in SARIF (with
no suppression) and in the evidence reports, and are counted separately from blocking
findings. Secrets and their reviews are unaffected (see dispositions.py).
"""
import datetime
import json
import re

POLICY = "security/accepted-risk.json"
NO_VENDOR_FIX = "no_vendor_fix"
OWNER_RECORD = "owner_record"
_IMAGE = re.compile(r"[a-z0-9][a-z0-9._/:-]*@sha256:[a-f0-9]{64}")
_CVE = re.compile(r"(CVE-[0-9]{4}-[0-9]{4,}|GHSA(-[23456789cfghjmpqrvwx]{4}){3})")


def validate(policy):
    if policy.get("schema_version") != 1:
        raise ValueError("Unsupported accepted-risk schema")
    seen = set()
    for record in policy["records"]:
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", record["id"]) or record["id"] in seen:
            raise ValueError("Accepted-risk records need a unique lowercase id")
        seen.add(record["id"])
        if not _IMAGE.fullmatch(record["image"]):
            raise ValueError("Accepted-risk records must name an exact image digest: " + record["id"])
        accepted = datetime.date.fromisoformat(record["accepted_on"])
        expires = datetime.date.fromisoformat(record["expires"])
        if expires <= accepted or (expires - accepted).days > 31:
            raise ValueError("Accepted-risk records must expire within 31 days: " + record["id"])
        if record.get("accepted_by") != "owner" or not record.get("reason"):
            raise ValueError("Accepted-risk records need the owner's acceptance and a reason: " + record["id"])
        findings = record["findings"]
        if findings != "all" and (not findings or not all(_CVE.fullmatch(entry) for entry in findings)):
            raise ValueError("Accepted-risk findings must be \"all\" or advisory IDs: " + record["id"])
    return policy


def _record_for(item, image, records, today):
    for record in records:
        if record["image"] != image:
            continue
        if not (datetime.date.fromisoformat(record["accepted_on"]) <= today
                < datetime.date.fromisoformat(record["expires"])):
            continue
        if record["findings"] == "all" or item["id"] in record["findings"]:
            return record
    return None


def annotate(items, image, policy, today=None):
    """Return copies of items; accepted HIGH/CRITICAL vulnerabilities gain an acceptance."""
    validate(policy)
    today = today or datetime.datetime.now(datetime.timezone.utc).date()
    result = []
    for original in items:
        item = dict(original)
        item.pop("acceptance", None)
        if item["kind"] == "vulnerability" and item["severity"] in {"HIGH", "CRITICAL"}:
            if not item.get("FixedVersion"):
                item["acceptance"] = {"basis": NO_VENDOR_FIX, "policy": POLICY,
                                      "reason": "No vendor fixed version is published; accepted by owner policy."}
            else:
                record = image and _record_for(item, image, policy["records"], today)
                if record:
                    item["acceptance"] = {"basis": OWNER_RECORD, "policy": POLICY, "record": record["id"],
                                          "reason": record["reason"], "expires": record["expires"]}
        result.append(item)
    return result


def counts(items):
    accepted = [item.get("acceptance", {}).get("basis") for item in items]
    return {"accepted_no_vendor_fix": accepted.count(NO_VENDOR_FIX),
            "accepted_by_record": accepted.count(OWNER_RECORD)}


def load(path):
    return json.loads(path.read_text())
