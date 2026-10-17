"""Export detected package inventories as CycloneDX 1.6, without rescanning."""
import hashlib
import json
from pathlib import Path
import re
import sys
import uuid


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def document(target, status):
    inventories = status.get("package_inventory")
    if not isinstance(inventories, list):
        raise ValueError("Missing package inventory; an empty SBOM cannot replace missing evidence")
    identity = (status.get("resolved_image") or status.get("image_build", {}).get("image_id")
                or target.get("zip_sha256") or status["commit"])
    if target["kind"] == "image" and not re.search(r"(?:^|@)sha256:[a-f0-9]{64}$", identity):
        raise ValueError("Image SBOM requires an assessed immutable identity")
    subject = "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, target["id"] + ":" + identity))
    components = {}
    for inventory in inventories:
        for package in inventory.get("packages") or []:
            name, version = package.get("Name"), package.get("Version")
            if not isinstance(name, str) or not name or (version is not None and not isinstance(version, str)):
                raise ValueError("Package inventory lacks a name or has an invalid version")
            # Preserve separate embedded/vendored copies of the same package.
            key = [inventory.get("target"), inventory.get("type"), name, version,
                   package.get("FilePath"), package.get("Identifier", {}).get("UID")]
            ref = "urn:sha256:" + hashlib.sha256(json.dumps(key).encode()).hexdigest()
            component = {"type": "library", "bom-ref": ref, "name": name}
            if version:
                component["version"] = version
            purl = package.get("Identifier", {}).get("PURL")
            if isinstance(purl, str) and purl.startswith("pkg:"):
                component["purl"] = purl
            licenses = package.get("Licenses", [])
            if licenses:
                component["licenses"] = [{"license": {"name": license_name}}
                                         for license_name in licenses if isinstance(license_name, str)]
            component["properties"] = [
                {"name": "ai-cloud-lab:detected-in", "value": str(inventory.get("target") or "")},
                {"name": "ai-cloud-lab:package-type", "value": str(inventory.get("type") or "")},
            ]
            if not version:
                component["properties"].append(
                    {"name": "ai-cloud-lab:version-detected", "value": "false"})
            if package.get("FilePath"):
                component["properties"].append(
                    {"name": "ai-cloud-lab:package-file", "value": package["FilePath"]})
            components[ref] = component
    component = {"type": "container" if target["kind"] == "image" else "application",
                 "bom-ref": subject, "name": target["name"], "version": identity}
    archive_hash = status.get("image_archive_sha256") or target.get("zip_sha256")
    if archive_hash:
        if not re.fullmatch(r"[a-f0-9]{64}", archive_hash):
            raise ValueError("Invalid assessed archive hash")
        component["hashes"] = [{"alg": "SHA-256", "content": archive_hash}]
    properties = {"target-id": target["id"], "assessment-commit": status["commit"],
                  "assessed-identity": identity,
                  "deployment-status": target.get("deployment_status", "configured"),
                  "scope": "Detected packages only; not a live host or deployment inventory",
                  "dependency-graph": "Not reconstructed; no direct/transitive relationships inferred"}
    if status.get("scanner_image"):
        properties["scanner-image"] = status["scanner_image"]
    return {"$schema": "http://cyclonedx.org/schema/bom-1.6.schema.json",
            "bomFormat": "CycloneDX", "specVersion": "1.6", "version": 1,
            "serialNumber": "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, subject + status["commit"])),
            "metadata": {"component": component,
                         "properties": [{"name": "ai-cloud-lab:" + name, "value": value}
                                        for name, value in properties.items()]},
            "components": [components[ref] for ref in sorted(components)],
            "compositions": [{"aggregate": "incomplete", "assemblies": [subject]}]}


def write_sboms(source, output, inventory):
    """Missing/failed assessment produces an unavailable entry, never a clean empty BOM."""
    source, output = Path(source), Path(output)
    entries = []
    for target in inventory["targets"]:
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,79}", target["id"]):
            raise ValueError("Unexpected target ID")
        path = source / "reports" / target["id"] / "status.json"
        status = json.loads(path.read_text()) if path.exists() else {}
        entry = {"target_id": target["id"], "asset": target["name"], "kind": target["kind"],
                 "deployment_status": target.get("deployment_status", "configured"), "available": False}
        if (status.get("complete") is True and status.get("target") == target
                and status.get("commit") == inventory["commit"]):
            try:
                bom = document(target, status)
                relative = "reports/" + target["id"] + "/sbom.cdx.json"
                destination = output / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(json.dumps(bom, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                entry.update(available=True, file=relative, sha256=sha256(destination),
                             component_count=len(bom["components"]),
                             assessed_identity=bom["metadata"]["component"]["version"])
            except (ValueError, KeyError, TypeError) as error:
                entry["error_type"] = type(error).__name__
        entries.append(entry)
    output.mkdir(parents=True, exist_ok=True)
    index = {"format": "CycloneDX", "spec_version": "1.6", "assessment_commit": inventory["commit"],
             "complete": all(entry["available"] for entry in entries), "targets": entries}
    (output / "sbom-index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    summary = ("# Software bills of materials\n\n"
               f"Assessment commit: `{inventory['commit']}`. SBOM availability complete: **{index['complete']}**.\n\n"
               "CycloneDX 1.6 package inventories derived from the assessed image/ZIP/source artifacts. "
               "These describe detected packages, not the current Docker runtime, AWS-managed runtimes, "
               "host kernel/AMI or private deployment overrides. No dependency graph is inferred. "
               "Undetected package versions are omitted, not invented. "
               "Zero detected packages is not proof that an artifact has no dependencies. "
               "Candidate images remain undeployed. SBOMs do not approve a release or disposition CVEs.\n\n"
               "See `sbom-index.json` for immutable identities and each SBOM's SHA256.\n\n"
               "| Asset | State | Detected components | SBOM |\n|---|---|---:|---|\n")
    for entry in entries:
        link = f"[CycloneDX JSON]({entry['file']})" if entry["available"] else "Unavailable: assessment/inventory incomplete"
        summary += (f"| {entry['asset'].replace('|', '&#124;').replace(chr(10), ' ')} | "
                    f"{entry['deployment_status']} | {entry.get('component_count', 'Unknown')} | {link} |\n")
    (output / "sboms.md").write_text(summary, encoding="utf-8")
    return index


def verify_evidence(root):
    root = Path(root).resolve()
    checked = set()
    for line in (root / "SHA256SUMS").read_text().splitlines():
        expected, relative = line.split("  ", 1)
        path = (root / relative).resolve()
        path.relative_to(root)
        if sha256(path) != expected:
            raise ValueError("Assessment evidence checksum mismatch")
        checked.add(relative)
    if "inventory.json" not in checked:
        raise ValueError("Source inventory was not checksummed")
    inventory = json.loads((root / "inventory.json").read_text())
    for target in inventory["targets"]:
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,79}", target["id"]):
            raise ValueError("Unexpected target ID")
        relative = "reports/" + target["id"] + "/status.json"
        if (root / relative).exists() and relative not in checked:
            raise ValueError("Source package inventory was not checksummed")


if __name__ == "__main__":
    source, output = map(Path, sys.argv[1:])
    verify_evidence(source)
    inventory = json.loads((source / "inventory.json").read_text())
    index = write_sboms(source, output, inventory)
    print(f"Exported {sum(entry['available'] for entry in index['targets'])} SBOMs; complete={index['complete']}")
    sys.exit(0 if index["complete"] else 1)
