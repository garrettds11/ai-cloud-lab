"""Builds the SSM Distributor archives in ../packages from the scripts in this folder.

Each archive holds track.txt (the track name) plus its package folder's files. The mcp package also
carries the lab's shared MCP modules from lambda/vuln_mcp, so the Lab Logs server reuses the same
protocol layer and scrubbing rules. security/inventory.py maps every archive member back to one of
these two places.
"""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

root = Path(__file__).resolve().parent
repository = root.parents[3]
destination = root.parent / "packages"
destination.mkdir(parents=True, exist_ok=True)

SHARED_DIR = repository / "lambda" / "vuln_mcp"
PACKAGES = {
    "vector": {"executable": ["install.sh", "uninstall.sh"], "files": [], "shared": []},
    "mcp": {"executable": ["install.sh", "uninstall.sh", "register.sh"],
            "files": ["server.py", "victorialogs_tools.py"],
            "shared": ["mcp_protocol.py", "vuln_tools.py", "log_tools.py"]},
}


def add(archive, name, data, mode):
    item = ZipInfo(name)
    item.compress_type = ZIP_DEFLATED
    item.create_system = 0  # the same bytes whether built on Windows or Linux
    item.external_attr = (mode << 16)
    archive.writestr(item, data)


for existing in destination.glob("*.zip"):
    existing.unlink()

for track in ("victorialogs",):
    for package, contents in PACKAGES.items():
        with ZipFile(destination / f"{track}-{package}.zip", "w", ZIP_DEFLATED) as archive:
            add(archive, "track.txt", track, 0o100644)
            for filename in contents["executable"]:
                add(archive, filename, (root / package / filename).read_bytes(), 0o100755)
            for filename in contents["files"]:
                add(archive, filename, (root / package / filename).read_bytes(), 0o100644)
            for filename in contents["shared"]:
                add(archive, filename, (SHARED_DIR / filename).read_bytes(), 0o100644)
