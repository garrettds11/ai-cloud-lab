"""Derive scan inputs from the checkout, Terraform archive definitions and Compose."""
import ast
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import urllib.request
import zipfile
import sys

import hcl2

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".security-scan"
sys.path.insert(0, str(ROOT / "scanner"))
import managed_images


def digest(data):
    return hashlib.sha256(data).hexdigest()


# Distributor package members come from the package's own folder or, for the Lab Logs MCP
# server, the shared MCP modules. Each member must be a plain script name and byte-identical
# to its source, so findings map to the repository file that ships.
PACKAGE_SHARED_SOURCES = ("lambda/vuln_mcp",)
_PACKAGE_MEMBER = re.compile(r"[a-z0-9_]+\.(sh|py)")


def package_member_source(snapshot, builder, package, name, data):
    if not _PACKAGE_MEMBER.fullmatch(name):
        raise ValueError(f"Unsupported Distributor package member: {name}")
    candidates = [Path(builder).parent / package / name] + [Path(shared) / name for shared in PACKAGE_SHARED_SOURCES]
    for candidate in candidates:
        source = snapshot / candidate
        if source.is_file() and source.read_bytes() == data:
            return candidate.as_posix()
    raise ValueError(f"Distributor package member {name} does not match a repository source")


def identifier(kind, name):
    return kind + "-" + digest(name.encode())[:20]


def scalar(value):
    # Also accommodates Checkov's older vendored HCL parser in local verification.
    return value[0] if isinstance(value, list) and len(value) == 1 else value


def local_path(expression, module):
    expression = scalar(expression)
    if not isinstance(expression, str):
        raise ValueError("Archive path must be a literal path.module expression")
    path = (module / expression.replace("${path.module}", str(module))).resolve()
    path.relative_to(ROOT)
    if "${" in str(path) or not path.exists() or path.is_symlink():
        raise ValueError(f"Unsupported or missing archive input: {expression}")
    return path


def archive_members(config, module, known_files):
    """Fail closed when an archive definition needs a new packaging strategy."""
    if scalar(config.get("type")) != "zip":
        raise ValueError("Only ZIP archives are supported")
    members = {}
    if "source_file" in config:
        path = local_path(config["source_file"], module)
        members[path.name] = path
    elif "source_dir" in config:
        base = local_path(config["source_dir"], module)
        excludes = config.get("excludes", [])
        if len(excludes) == 1 and isinstance(excludes[0], list):
            excludes = excludes[0]
        for path in sorted(base.rglob("*")):
            name = path.relative_to(base).as_posix()
            if any(fnmatch.fnmatchcase(name, pattern) for pattern in excludes):
                continue
            if path.is_file():
                if path.is_symlink() or path.relative_to(ROOT).as_posix() not in known_files:
                    raise ValueError(f"Archive contains an ignored or symlinked file: {name}")
                members[name] = path
    elif "source" in config:
        for source in config["source"]:
            expression = scalar(source["content"])
            match = re.fullmatch(r'\$\{file\("([^"\n]+)"\)\}', expression)
            if not match:
                raise ValueError("Archive source content must be file(path.module/...)")
            name = scalar(source["filename"])
            if name in members:
                raise ValueError(f"Duplicate archive member: {name}")
            members[name] = local_path(match[1], module)
    else:
        raise ValueError("Archive lacks a supported source")
    if not members:
        raise ValueError("Archive has no files")
    for name, path in members.items():
        if name.startswith("/") or ".." in Path(name).parts or "\\" in name:
            raise ValueError(f"Unsafe ZIP member: {name}")
        if path.relative_to(ROOT).as_posix() not in known_files:
            raise ValueError(f"Archive input is ignored by Git: {path}")
    return members


def assemble(name, members, origin, targets):
    target_id = identifier("package", name)
    directory = OUT / "inputs" / target_id
    directory.mkdir(parents=True)
    archive_path = OUT / "packages" / (target_id + ".zip")
    archive_path.parent.mkdir(exist_ok=True)
    manifest = []
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for member, path in sorted(members.items()):
            data = path.read_bytes()
            item = zipfile.ZipInfo(member, date_time=(1980, 1, 1, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = 0o100644 << 16
            archive.writestr(item, data)
            destination = directory / member
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            manifest.append({"member": member, "source": path.relative_to(ROOT).as_posix(),
                             "sha256": digest(data)})
    targets.append({"id": target_id, "kind": "package", "name": name,
                    "scan_ref": directory.relative_to(ROOT).as_posix(), "origins": [origin],
                    "zip_sha256": digest(archive_path.read_bytes()), "members": manifest})


def compose_images(text, origin, images):
    for line_number, line in enumerate(text.splitlines(), 1):
        match = re.match(r"\s*image:\s*([^\s#]+)", line)
        if match:
            image = match[1].strip("\"'")
            if "$" in image or "{{" in image:
                raise ValueError(f"Unresolved image in {origin}: {image}")
            images.setdefault(image, []).append({"file": origin, "line": line_number})


def external_compose(url, origin, images, external):
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024:
        raise ValueError("Upstream Compose exceeds size limit")
    external.append({"url": url, "sha256": digest(data), "origin": origin})
    # Locations point to the repository reference; upstream line numbers aren't local lines.
    temporary = {}
    compose_images(data.decode(), origin, temporary)
    if not temporary:
        raise ValueError(f"No images found in {url}")
    for image in temporary:
        images.setdefault(image, []).append({"file": origin, "line": 1, "upstream": url})


def main():
    OUT.mkdir(exist_ok=True)
    for name in ("inputs", "packages"):
        generated = OUT / name
        generated.resolve().relative_to(ROOT)
        if generated.is_symlink():
            raise ValueError("Generated input directory must not be a symlink")
        if generated.exists():
            shutil.rmtree(generated)
    files = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT
    ).decode().strip("\0").split("\0")
    files = sorted(set(files))
    known_files = set(files)
    targets, images, external = [], {}, []
    recipes = managed_images.definitions(ROOT / "scanner" / "image_builds")
    managed = {image["reference"]: image for image in recipes if image.get("scope") != "candidate"}
    candidates = [image for image in recipes if image.get("scope") == "candidate"]
    pins = {pin["reference"]: pin["image"] for pin in managed_images.pins(ROOT / "scanner" / "image_builds")}
    dockerfiles = {"scanner/image_builds/" + image["context"] + "/Dockerfile" for image in recipes}
    snapshot = OUT / "inputs" / "repository"
    snapshot.mkdir(parents=True)
    sources = []
    for filename in files:
        path = ROOT / filename
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing or unsupported checkout entry: {filename}")
        if path.name.startswith("Dockerfile") and filename not in dockerfiles:
            raise ValueError(f"Add a build-and-scan strategy for {filename}")
        destination = snapshot / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        sources.append({"file": filename, "sha256": digest(path.read_bytes())})
        if "compose" in path.name and ("yaml" in path.name or "yml" in path.name):
            compose_images(path.read_text(), filename, images)
        if path.suffix == ".tf":
            with path.open() as stream:
                config = hcl2.load(stream)
            for block in config.get("data", []):
                for name, definition in block.get("archive_file", {}).items():
                    assemble(filename + ":" + name,
                             archive_members(definition, path.parent, known_files),
                             {"file": filename, "line": 1}, targets)
            for block in config.get("variable", []):
                for name, definition in block.items():
                    if name.endswith("container_image") or name.endswith("image_ref"):
                        image = scalar(definition.get("default"))
                        if not isinstance(image, str) or "$" in image:
                            raise ValueError(f"Explicit CI image required for {filename}:{name}")
                        images.setdefault(image, []).append({"file": filename, "line": 1})
        if filename.endswith("backend-bootstrap.sh.tftpl"):
            text = path.read_text()
            for revision in re.findall(r"--branch (\S+) https://github.com/wazuh/wazuh-docker.git", text):
                for compose in ("docker-compose.yml", "generate-indexer-certs.yml"):
                    external_compose("https://raw.githubusercontent.com/wazuh/wazuh-docker/"
                                     + revision + "/single-node/" + compose,
                                     filename, images, external)
        if filename == "scanner/run_scan.py":
            constants = {}
            for node in ast.parse(path.read_text()).body:
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                    for variable in node.targets:
                        if isinstance(variable, ast.Name):
                            constants[variable.id] = node.value.value
            images.setdefault(constants["TRIVY_IMAGE"], []).append({"file": filename, "line": 1})
            external_compose(constants["COMPOSE_URL"], filename, images, external)
    # Distributor archives contain these exact scripts and a per-track text marker.
    # Use the repository's package builder, preserving its executable ZIP modes.
    for filename in files:
        if filename.endswith("package-src/build_packages.py"):
            # Execute the copied builder, so packaging never overwrites local user archives.
            subprocess.run([os.sys.executable, str(snapshot / filename)], cwd=snapshot, check=True)
            package_root = (snapshot / filename).parent
            for archive in sorted(package_root.parent.joinpath("packages").glob("*.zip")):
                archive_name = archive.relative_to(snapshot).as_posix()
                target_id = identifier("package", archive_name)
                directory = OUT / "inputs" / target_id
                directory.mkdir(parents=True)
                members = []
                package = archive.stem.rsplit("-", 1)[1]
                with zipfile.ZipFile(archive) as contents:
                    for item in contents.infolist():
                        data = contents.read(item)
                        member = {"member": item.filename, "sha256": digest(data)}
                        if item.filename != "track.txt":
                            member["source"] = package_member_source(snapshot, filename, package, item.filename, data)
                        (directory / item.filename).write_bytes(data)
                        members.append(member)
                targets.append({"id": target_id, "kind": "package", "name": archive.name,
                                "scan_ref": directory.relative_to(ROOT).as_posix(),
                                "zip_sha256": digest(archive.read_bytes()), "members": members,
                                "origins": [{"file": filename, "line": 1}]})
    targets.insert(0, {"id": "repository", "kind": "repository", "name": "Git checkout",
                       "scan_ref": snapshot.relative_to(ROOT).as_posix(),
                       "origins": [{"file": "README.md", "line": 1}]})
    for image, origins in sorted(images.items()):
        target = {"id": identifier("image", image), "kind": "image", "name": image,
                  "scan_ref": image, "origins": origins, "platform": "linux/amd64"}
        if image in pins:
            target["scan_ref"] = pins[image]
            target["coordinated_pin"] = True
        if image in managed:
            definition = managed[image]
            target["name"] = "Managed rebuild of " + image
            target["build"] = definition
            target["build_root"] = (snapshot / "scanner" / "image_builds").relative_to(ROOT).as_posix()
            target["origins"] = [{"file": "scanner/image_builds/" + definition["context"] + "/Dockerfile", "line": 1}] + origins
            for entry in definition["recipe_files"]:
                if "scanner/image_builds/" + entry["file"] not in known_files:
                    raise ValueError("Managed image contains ignored build input")
        targets.append(target)
    for candidate in candidates:
        if candidate["reference"] not in images:
            raise ValueError("Candidate no longer matches a configured upstream asset")
        for entry in candidate["recipe_files"]:
            if "scanner/image_builds/" + entry["file"] not in known_files:
                raise ValueError("Candidate contains ignored build input")
        targets.append({"id": identifier("image", candidate["reference"] + ":patched-candidate"),
                        "kind": "image", "name": "Candidate (not deployed): " + candidate["reference"],
                        "scan_ref": candidate["reference"], "build": candidate, "platform": "linux/amd64",
                        "deployment_status": "candidate",
                        "build_root": (snapshot / "scanner/image_builds").relative_to(ROOT).as_posix(),
                        "origins": [{"file": "scanner/image_builds/" + candidate["context"] + "/Dockerfile", "line": 1}]})
    if (set(managed) | set(pins)) - set(images):
        raise ValueError("Managed-image manifest no longer matches the deployable image inventory")
    if not images or not any(t["kind"] == "package" for t in targets):
        raise ValueError("Image/package inventory is unexpectedly empty")
    inventory = {"schema_version": 1, "commit": os.environ.get("GITHUB_SHA") or
                 subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip(),
                 "targets": targets, "sources": sources, "upstream_definitions": external,
                 "limitations": [
                     "CI scans committed defaults, not private tfvars overrides or live deployments",
                     "Upstream Compose and image tags are resolved at scan time; deployment may differ",
                     "Managed scanner images use reviewed recipes and pinned bases; CI and runtime independently resolve signed Debian updates and record their built IDs",
                     "ZIP hashes identify CI repackaging; deployed ZIP byte identity is assessed by the runtime scanner",
                     "Lambda AWS-managed runtime/SDK are not in source ZIPs and cannot be assessed here",
                     "Host packages and plugins downloaded at boot require runtime assessment",
                     "Source CVE analysis requires supported dependency manifests or bundled packages",
                     "No network, authenticated web, or business authorization assessment in this workflow"]}
    # Validate patch candidates/tool early while retaining every configured target.
    targets.sort(key=lambda target: (target.get("deployment_status") != "candidate",
                                     target.get("build", {}).get("scope") != "tool"))
    # Build the scanner once, then distribute the exact assessed artifact to jobs.
    # The host performs builds; the scanning container never gets a Docker socket.
    scanner = managed_images.build_tool(snapshot / "scanner" / "image_builds")
    scanner_archive = OUT / "inputs" / "scanner-image.tar"
    subprocess.run(["docker", "image", "save", "--output", str(scanner_archive), scanner["image_id"]],
                   check=True, timeout=300)
    with scanner_archive.open("rb") as stream:
        scanner["archive_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
    inventory["scanner_build"] = scanner
    (OUT / "inventory.json").write_text(json.dumps(inventory, indent=2) + "\n")
    matrix = json.dumps({"include": [{"id": t["id"]} for t in targets]}, separators=(",", ":"))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write("matrix=" + matrix + "\n")
    print(f"Inventoried {len(sources)} files, {len(images)} images, "
          f"{sum(t['kind'] == 'package' for t in targets)} packages")


if __name__ == "__main__":
    main()
