"""Build reviewed scanner images and replace their exact Compose references."""
import hashlib
import copy
import json
from pathlib import Path
import re
import subprocess
import uuid


def scanner_smoke(image_id, redis_image_id):
    """Run a local NASL fixture with disposable Redis; no production targets."""
    for identity in (image_id, redis_image_id):
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", identity):
            raise ValueError("Scanner acceptance requires immutable image IDs")
    name = "security-nasl-" + uuid.uuid4().hex
    volume, redis = name + "-socket", name + "-redis"
    subprocess.run(["docker", "volume", "create", "--label", "ai-cloud-lab.verification=nasl",
                    volume], check=True, timeout=60, stdout=subprocess.DEVNULL)
    script = '''set -eu
python3 - <<'PY'
import socket, time
for attempt in range(30):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(1)
            client.connect("/run/redis/redis.sock")
            client.sendall(b"*1\\r\\n$4\\r\\nPING\\r\\n")
            assert client.recv(64).startswith(b"+PONG")
        break
    except (OSError, AssertionError):
        time.sleep(0.5)
else:
    raise RuntimeError("Disposable Redis did not become ready")
PY
cp "$(command -v openvas-nasl)" /tmp/nasl-check
cmp "$(command -v openvas-nasl)" /tmp/nasl-check
printf 'display("security-nasl-fixture-ok");\\n' > /tmp/fixture.nasl
/tmp/nasl-check -p -X /tmp/fixture.nasl
/tmp/nasl-check -X /tmp/fixture.nasl > /tmp/fixture-result 2>&1 || { cat /tmp/fixture-result; exit 1; }
cat /tmp/fixture-result
grep -q 'security-nasl-fixture-ok' /tmp/fixture-result
echo 'Redis readiness, NASL parsing and fixture execution passed'
'''
    isolation = ["--network", "none", "--read-only", "--cap-drop", "ALL",
                 "--security-opt", "no-new-privileges:true", "--cpus", "1",
                 "--label", "ai-cloud-lab.verification=nasl"]
    try:
        subprocess.run(["docker", "run", "--detach", "--name", redis, *isolation,
                        "--user", "redis",
                        "--memory", "128m", "--mount", f"type=volume,source={volume},target=/run/redis",
                        "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m", "--entrypoint", "redis-server",
                        redis_image_id, "--port", "0", "--unixsocket", "/run/redis/redis.sock",
                        "--unixsocketperm", "777", "--save", "", "--appendonly", "no"],
                       check=True, timeout=60, stdout=subprocess.DEVNULL)
        subprocess.run(["docker", "run", "--rm", "--name", name, *isolation,
                        "--user", "0:0",
                        "--memory", "512m", "--mount", f"type=volume,source={volume},target=/run/redis",
                        "--tmpfs", "/tmp:rw,exec,nosuid,nodev,size=16m", "--entrypoint", "/bin/sh",
                        image_id, "-c", script], check=True, timeout=120)
    except Exception:
        # Only the ephemeral fixture's logs; no application data or credentials.
        subprocess.run(["docker", "logs", redis], check=False, timeout=30)
        subprocess.run(["docker", "inspect", "--format", "{{.State.Status}} {{.State.ExitCode}}", redis],
                       check=False, timeout=30)
        raise
    finally:
        # Generated unique names only. No host directories, user volumes or containers.
        subprocess.run(["docker", "rm", "--force", "--volumes", name, redis],
                       check=False, timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["docker", "volume", "rm", volume], check=True, timeout=60,
                       stdout=subprocess.DEVNULL)
    return {"nasl_parse": "passed", "nasl_fixture_execution": "passed", "redis_readiness": "passed",
            "redis_image_id": redis_image_id, "network": "none", "storage": "disposable",
            "production_targets_scanned": False}


def candidate_smoke(image_id):
    """Exercise a candidate backend in disposable storage, with no network access."""
    container = subprocess.check_output([
        "docker", "run", "--detach", "--network", "none", "--memory", "4g", "--cpus", "2",
        "--security-opt", "no-new-privileges:true", "--label", "ai-cloud-lab.verification=candidate",
        "-e", "HF_HUB_OFFLINE=1", "-e", "TRANSFORMERS_OFFLINE=1",
        "-e", "RAG_EMBEDDING_MODEL_AUTO_UPDATE=false", "-e", "ENABLE_SIGNUP=true",
        "-e", "WEBUI_SECRET_KEY=compatibility-check-only-never-a-production-key", image_id],
        text=True, timeout=60).strip()
    if not re.fullmatch(r"[a-f0-9]{64}", container):
        raise ValueError("Candidate test lacks a disposable container identity")
    script = '''
import json, time, urllib.request
base = "http://127.0.0.1:8080"
for attempt in range(90):
    try:
        urllib.request.urlopen(base + "/health", timeout=2).read()
        break
    except Exception:
        time.sleep(2)
else:
    raise RuntimeError("Candidate backend health check timed out")
def post(path, data):
    req = urllib.request.Request(base + path, json.dumps(data).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=15))
account = {"email": "compatibility-check@example.test", "name": "Compatibility check",
           "password": "compatibility-check-only-never-a-production-password"}
created = post("/api/v1/auths/signup", account)
assert created.get("token")
signed_in = post("/api/v1/auths/signin", {key: account[key] for key in ("email", "password")})
req = urllib.request.Request(base + "/api/v1/auths/", headers={"Authorization": "Bearer " + signed_in["token"]})
assert json.load(urllib.request.urlopen(req, timeout=15))["email"] == account["email"]
try:
    urllib.request.urlopen(urllib.request.Request(base + "/api/v1/auths/", headers={"Authorization": "Bearer invalid-test-token"}), timeout=15)
except urllib.error.HTTPError as error:
    assert error.code in (401, 403)
else:
    raise AssertionError("Candidate accepted an invalid token")
print("Backend health, signup, signin and token round-trip passed")
'''
    try:
        subprocess.run(["docker", "exec", container, "python3", "-c", script], check=True, timeout=240)
    finally:
        subprocess.run(["docker", "rm", "--force", "--volumes", container], check=True, timeout=60,
                       stdout=subprocess.DEVNULL)
    return {"dependency_checks": "passed", "backend_health": "passed", "signup_signin_token": "passed",
            "network": "none", "storage": "disposable", "production_deployed": False}


def isolated_services(config):
    """Restrict socket-only services; retain networking for actual scanners/UI."""
    config = copy.deepcopy(config)
    services = config["services"]
    selected = ("pg-gvm", "pg-gvm-migrator", "redis-server", "gvm-tools")

    def volume(service, target):
        mounts = [mount for mount in services[service].get("volumes", [])
                  if mount.get("target") == target and mount.get("type") == "volume" and mount.get("source")]
        if len(mounts) != 1:
            raise ValueError("Expected shared socket/data volume missing; review upstream Compose")
        return mounts[0]["source"]

    # Fail closed if an upstream change replaces the supported socket paths.
    for left, right, path in (("pg-gvm", "gvmd", "/var/run/postgresql"),
                              ("pg-gvm-migrator", "pg-gvm", "/var/lib/postgresql"),
                              ("pg-gvm-migrator", "pg-gvm", "/var/run/postgresql"),
                              ("redis-server", "ospd-openvas", "/run/redis"),
                              ("gvm-tools", "gvmd", "/run/gvmd")):
        if volume(left, path) != volume(right, path):
            raise ValueError("Services no longer share the expected socket/data volume")
    for name in selected:
        service = services[name]
        conflicting_privilege = any(option.startswith("no-new-privileges") and
                                    option.replace("=", ":").replace(" ", "") not in
                                    {"no-new-privileges", "no-new-privileges:true"}
                                    for option in service.get("security_opt", []))
        if service.get("ports") or service.get("privileged") or conflicting_privilege:
            raise ValueError("Unexpected privileged or published socket-only service")
        service.pop("networks", None)
        service["network_mode"] = "none"
        service["security_opt"] = sorted(set(service.get("security_opt", []) + ["no-new-privileges:true"]))
        service["cap_drop"] = sorted(set(service.get("cap_drop", []) + ["NET_RAW"]))
    services["redis-server"]["read_only"] = True
    services["redis-server"]["tmpfs"] = sorted(set(services["redis-server"].get("tmpfs", []) +
                                                    ["/tmp:rw,noexec,nosuid,size=16m"]))
    return config


def harden(compose):
    normalized = json.loads(subprocess.check_output(
        ["docker", "compose", "-f", str(compose), "config", "--format", "json"], text=True, timeout=60))
    normalized = isolated_services(normalized)
    # Compose accepts JSON. Preserve commands, dependencies, volumes and project identity.
    Path(compose).write_text(json.dumps(normalized, indent=2))
    return {name: {key: normalized["services"][name][key] for key in
                  ("network_mode", "security_opt", "cap_drop", "read_only", "tmpfs")
                  if key in normalized["services"][name]}
            for name in ("pg-gvm", "pg-gvm-migrator", "redis-server", "gvm-tools")}


def definitions(root):
    root = Path(root).resolve()
    manifest = json.loads((root / "manifest.json").read_text())
    if manifest.get("schema_version") != 1 or not manifest.get("images"):
        raise ValueError("Unsupported or empty managed-image manifest")
    seen = set()
    for image in manifest["images"]:
        if image["reference"] in seen:
            raise ValueError("Duplicate managed image")
        seen.add(image["reference"])
        if not re.fullmatch(r"[a-z0-9-]+", image["context"]):
            raise ValueError("Invalid managed-image context")
        repository = image["reference"].split("@", 1)[0]
        if ":" in repository.rsplit("/", 1)[-1]:
            repository = repository.rsplit(":", 1)[0]
        if image.get("scope", "compose") not in {"compose", "tool", "candidate"}:
            raise ValueError("Unsupported managed-image scope")
        if not re.fullmatch(re.escape(repository) + r"@sha256:[a-f0-9]{64}", image["base"]):
            raise ValueError("Managed image must use a digest-pinned base from its upstream repository")
        if not re.fullmatch(r"ai-cloud-lab/[a-z0-9-]+:patched", image["tag"]):
            raise ValueError("Managed image must use a local patched tag")
        context = root / image["context"]
        context.resolve().relative_to(root)
        files = sorted(path for path in context.rglob("*") if path.is_file())
        if context.is_symlink() or not files or any(path.is_symlink() for path in context.rglob("*")):
            raise ValueError("Missing or symlinked managed-image context")
        recipe = (context / "Dockerfile").read_text()
        bases = re.findall(r"^FROM\s+(\S+)\s*$", recipe, re.MULTILINE)
        if bases != [image["base"]]:
            raise ValueError("Managed-image Dockerfile must declare the reviewed base exactly once")
        image["recipe_files"] = [{"file": path.relative_to(root).as_posix(),
                                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in files]
    return manifest["images"]


def build(root, image, timeout=1800, scanner_redis=None):
    if image.get("scope") == "candidate" and image["context"] != "open-webui":
        raise ValueError("Register candidate acceptance checks before building another application")
    # No build secrets, SSH forwarding, host mounts or privileged build flags.
    subprocess.run(["docker", "build", "--platform", "linux/amd64", "--no-cache", "--pull",
                    "--tag", image["tag"], str(Path(root) / image["context"])],
                   check=True, timeout=timeout)
    image_id = subprocess.check_output(["docker", "image", "inspect", image["tag"],
                                        "--format", "{{.Id}}"], text=True, timeout=60).strip()
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
        raise ValueError("Built image lacks an immutable ID")
    record = dict(image, image_id=image_id)
    if image.get("scope") == "candidate":
        record["compatibility_checks"] = candidate_smoke(image_id)
    if image["context"] in {"openvas-scanner", "ospd-openvas"}:
        if scanner_redis is None:
            redis = [entry for entry in definitions(root) if entry["context"] == "redis-server"]
            if len(redis) != 1:
                raise ValueError("Scanner fixture requires the existing managed Redis recipe")
            scanner_redis = build(root, redis[0])["image_id"]
        record["compatibility_checks"] = scanner_smoke(image_id, scanner_redis)
    return record


def build_tool(root):
    tools = [image for image in definitions(root) if image.get("scope") == "tool"]
    if len(tools) != 1 or tools[0]["context"] != "trivy":
        raise ValueError("Exactly one reviewed Trivy tool recipe is required")
    return build(root, tools[0])


def pins(root):
    manifest = json.loads((Path(root) / "manifest.json").read_text())
    result = manifest.get("pins", [])
    references = set()
    for pin in result:
        repository = pin["reference"].rsplit(":", 1)[0]
        if pin["reference"] in references or not re.fullmatch(
                re.escape(repository) + r"@sha256:[a-f0-9]{64}", pin["image"]):
            raise ValueError("Coordinated images require unique references and immutable upstream digests")
        references.add(pin["reference"])
    return result


def prepare(compose, root):
    text = Path(compose).read_text()
    images = [image for image in definitions(root) if image.get("scope", "compose") == "compose"]
    coordinated = pins(root)
    # Validate the complete expected coverage before building or rewriting.
    patterns = {}
    for image in images + coordinated:
        pattern = re.compile(r"^(\s*image:\s*)[\"']?" + re.escape(image["reference"]) + r"[\"']?(\s*(?:#.*)?)$", re.MULTILINE)
        if not pattern.search(text):
            raise ValueError("Upstream Compose no longer contains a reviewed image; update the manifest")
        patterns[image["reference"]] = pattern
    built = []
    for image in images:
        record = build(root, image)
        record["coordinated_pins"] = coordinated
        # Pin the local Compose image to its ID, preventing local-tag drift.
        text = patterns[image["reference"]].sub(lambda match: match[1] + record["image_id"] + match[2], text)
        built.append(record)
    for pin in coordinated:
        text = patterns[pin["reference"]].sub(lambda match: match[1] + pin["image"] + match[2], text)
    Path(compose).write_text(text)
    return built
