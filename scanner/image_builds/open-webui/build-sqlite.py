"""Package pinned upstream SQLite for this Bookworm candidate; never relabel a binary."""
import ctypes
import hashlib
import io
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
import time
import urllib.request
import zipfile

VERSION = "3.54.0"
PACKAGE_VERSION = VERSION + "-0ai.cloud.lab1"
URL = "https://sqlite.org/2026/sqlite-amalgamation-3540000.zip"
ARCHIVE_SHA3 = "7b670a62fdfbd672b75fef004cb703c8a3e87d3a5cc7d675b4a08337004a2d93"
SOURCE_SHA3 = "8efd453d08a7cfc4c79de5923153036cdc4dcaa254d4f1e617be6a2a98828f9e"
SOURCE_ID = "2026-10-09 15:46:58 be8d059e9a49089ab2dce5ed26dd87aaf598fdc5fbe0b107c0dd758464bcd5e3"


def main():
    architecture = subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip()
    if architecture != "amd64":
        raise RuntimeError("This candidate has only been reviewed for amd64")
    previous = sqlite3.sqlite_version
    with sqlite3.connect(":memory:") as connection:
        options = sorted(row[0] for row in connection.execute("pragma compile_options"))
    # Keep the distro's feature/limit configuration. COMPILER describes the old
    # toolchain and MUTEX_PTHREADS is derived by SQLite, not a build input.
    flags = [option for option in options
             if not option.startswith(("COMPILER=", "MUTEX_"))]
    if not all(re.fullmatch(r"[A-Z][A-Z0-9_]*(?:=-?[A-Za-z0-9_]+)?", flag) for flag in flags):
        raise RuntimeError("Unexpected SQLite compile option")
    dependencies = subprocess.check_output(
        ["dpkg-query", "-W", "-f=${Depends}", "libsqlite3-0"], text=True).strip()
    for attempt in range(3):
        try:
            with urllib.request.urlopen(URL, timeout=60) as response:
                archive = response.read(16 * 1024 * 1024)
            break
        except OSError:
            if attempt == 2:
                raise
            time.sleep(5)
    if hashlib.sha3_256(archive).hexdigest() != ARCHIVE_SHA3:
        raise RuntimeError("SQLite archive checksum mismatch")
    with tempfile.TemporaryDirectory(prefix="security-sqlite-") as temporary:
        root = Path(temporary)
        source = root / "sqlite3.c"
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            # Read a known member, never extract paths supplied by an archive.
            source.write_bytes(bundle.read("sqlite-amalgamation-3540000/sqlite3.c"))
        if hashlib.sha3_256(source.read_bytes()).hexdigest() != SOURCE_SHA3:
            raise RuntimeError("SQLite C source checksum mismatch")
        package = root / "package"
        library = package / "usr/lib/x86_64-linux-gnu/libsqlite3.so.0.8.6"
        library.parent.mkdir(parents=True)
        subprocess.run([
            "cc", "-O2", "-fPIC", "-shared", "-fstack-protector-strong", "-D_FORTIFY_SOURCE=2",
            "-Wl,-soname,libsqlite3.so.0", "-Wl,-z,relro,-z,now", "-pthread",
            *["-DSQLITE_" + flag for flag in flags], str(source), "-ldl", "-lm",
            "-o", str(library)], check=True, timeout=300)
        native = ctypes.CDLL(str(library))
        native.sqlite3_libversion.restype = ctypes.c_char_p
        native.sqlite3_sourceid.restype = ctypes.c_char_p
        if native.sqlite3_libversion().decode() != VERSION or native.sqlite3_sourceid().decode() != SOURCE_ID:
            raise RuntimeError("Compiled SQLite identity differs from reviewed source")
        (library.parent / "libsqlite3.so.0").symlink_to(library.name)
        document = package / "usr/share/doc/libsqlite3-0"
        document.mkdir(parents=True)
        record = {"version": VERSION, "package_version": PACKAGE_VERSION, "previous_version": previous,
                  "source_url": URL, "archive_sha3_256": ARCHIVE_SHA3, "source_sha3_256": SOURCE_SHA3,
                  "source_id": SOURCE_ID, "compile_options_before": options,
                  "library_sha256": hashlib.sha256(library.read_bytes()).hexdigest(),
                  "maintainer": "ai-cloud-lab (local source rebuild; not a Debian security update)"}
        (document / "security-build.json").write_text(json.dumps(record, indent=2) + "\n")
        (document / "copyright").write_text(
            "SQLite is in the public domain. https://sqlite.org/copyright.html\n"
            "Locally compiled from the upstream source identified in security-build.json.\n")
        control = package / "DEBIAN"
        control.mkdir()
        (control / "control").write_text(
            f"Package: libsqlite3-0\nVersion: {PACKAGE_VERSION}\nArchitecture: {architecture}\n"
            f"Source: sqlite3\nDepends: {dependencies}\nSection: libs\nPriority: optional\n"
            "Maintainer: ai-cloud-lab <noreply@aiwebdemo.click>\n"
            "Description: SQLite shared library, locally built from checksum-pinned upstream source\n")
        (control / "md5sums").write_text("".join(
            hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest() + "  "
            + path.relative_to(package).as_posix() + "\n"
            for path in sorted(package.rglob("*"))
            if path.is_file() and not path.is_symlink() and control not in path.parents))
        deb = root / "sqlite.deb"
        legacy = root / "previous-version.db"
        with sqlite3.connect(legacy) as database:
            database.execute("create table previous_runtime(version text, value text)")
            database.execute("insert into previous_runtime values(?, ?)", (previous, "preserved"))
            database.commit()
        subprocess.run(["dpkg-deb", "--build", "--root-owner-group", str(package), str(deb)], check=True)
        subprocess.run(["dpkg", "--install", str(deb)], check=True)
        subprocess.run(["apt-get", "check"], check=True)
        subprocess.run(["ldconfig"], check=True)
        # New process: the builder has already loaded the old SQLite shared object.
        subprocess.run(["python3", "/opt/security-sqlite-smoke.py", str(legacy)], check=True)
        print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
