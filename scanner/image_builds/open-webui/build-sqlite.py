"""Package pinned upstream SQLite for this Bookworm candidate; never relabel a binary."""
import ctypes
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import subprocess
import tempfile
import time
import urllib.request
import zipfile

VERSION = "3.54.0"
PACKAGE_VERSION = VERSION + "-0ai.cloud.lab1"
URL = "https://sqlite.org/2026/sqlite-src-3540000.zip"
ARCHIVE_SHA3 = "a5c29342ca185abcda332448fbb27c536661245be27e20199b46c49099b0e15e"
SOURCE_ID = "2026-10-09 15:46:58 be8d059e9a49089ab2dce5ed26dd87aaf598fdc5fbe0b107c0dd758464bcd5e3"
INSTALLED = Path("/usr/lib/x86_64-linux-gnu/libsqlite3.so.0")


def exported_symbols(path):
    """Defined public API symbols; a replacement must not drop any the distro library exported.

    Debian's non-amalgamation build also exports internal sqlite3Xxx helpers. The
    amalgamation keeps those static by design; only the documented sqlite3_* API counts.
    """
    output = subprocess.check_output(["nm", "-D", "--defined-only", str(path)], text=True)
    names = {fields[-1].split("@", 1)[0] for fields in (line.split() for line in output.splitlines())
             if len(fields) >= 3}
    return {name for name in names if name.startswith("sqlite3_")}


def build_flags():
    """Debian's own hardening flags, minus debug info; recorded in the receipt."""
    environment = {"DEB_BUILD_MAINT_OPTIONS": "hardening=+all", "PATH": "/usr/bin:/bin"}
    flags = {name: subprocess.check_output(["dpkg-buildflags", "--get", name], text=True,
                                           env=environment).split()
             for name in ("CPPFLAGS", "CFLAGS", "LDFLAGS")}
    flags["CFLAGS"] = [flag for flag in flags["CFLAGS"]
                       if not flag.startswith(("-g", "-ffile-prefix-map=", "-fdebug-prefix-map="))]
    for required in ("-fstack-protector-strong", "-Werror=format-security"):
        if required not in flags["CFLAGS"]:
            raise RuntimeError("Distro hardening flags are missing " + required)
    if "-Wl,-z,relro" not in flags["LDFLAGS"] or "-Wl,-z,now" not in flags["LDFLAGS"]:
        raise RuntimeError("Distro hardening flags lack full RELRO")
    return flags


def main():
    architecture = subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip()
    if architecture != "amd64":
        raise RuntimeError("This candidate has only been reviewed for amd64")
    previous = sqlite3.sqlite_version
    with sqlite3.connect(":memory:") as connection:
        options = sorted(row[0] for row in connection.execute("pragma compile_options"))
    # Keep the distro's feature/limit configuration. COMPILER describes the old
    # toolchain and MUTEX_PTHREADS is derived by SQLite, not a build input.
    options_used = [option for option in options
                    if not option.startswith(("COMPILER=", "MUTEX_"))]
    if not all(re.fullmatch(r"[A-Z][A-Z0-9_]*(?:=-?[A-Za-z0-9_]+)?", option) for option in options_used):
        raise RuntimeError("Unexpected SQLite compile option")
    dependencies = subprocess.check_output(
        ["dpkg-query", "-W", "-f=${Depends}", "libsqlite3-0"], text=True).strip()
    previous_symbols = exported_symbols(INSTALLED.resolve())
    flags = build_flags()
    for attempt in range(3):
        try:
            with urllib.request.urlopen(URL, timeout=60) as response:
                archive = response.read(32 * 1024 * 1024)
            break
        except OSError:
            if attempt == 2:
                raise
            time.sleep(5)
    if hashlib.sha3_256(archive).hexdigest() != ARCHIVE_SHA3:
        raise RuntimeError("SQLite archive checksum mismatch")
    with tempfile.TemporaryDirectory(prefix="security-sqlite-") as temporary:
        root = Path(temporary)
        source_tree = root / "source"
        source_tree.mkdir()
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            for member in bundle.infolist():
                relative = PurePosixPath(member.filename).relative_to("sqlite-src-3540000")
                if ".." in relative.parts or "\\" in member.filename or stat.S_ISLNK(member.external_attr >> 16):
                    raise RuntimeError("Unsafe source archive path")
                destination = source_tree.joinpath(*relative.parts)
                if member.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(bundle.read(member))
                    destination.chmod((member.external_attr >> 16) & 0o777 or 0o644)
        # Canonical source includes JimTCL and Lemon: no new build packages.
        # The optional DML grammar must be generated with the feature enabled;
        # merely defining it while compiling the published amalgamation loses it.
        subprocess.run(["sh", "configure", "--disable-tcl", "--enable-update-limit"],
                       cwd=source_tree, check=True, timeout=180)
        subprocess.run(["make", "sqlite3.c", "OPTS=" + " ".join("-DSQLITE_" + option for option in options_used)],
                       cwd=source_tree, check=True, timeout=180)
        source = source_tree / "sqlite3.c"
        package = root / "package"
        library = package / "usr/lib/x86_64-linux-gnu/libsqlite3.so.0.8.6"
        library.parent.mkdir(parents=True)
        subprocess.run([
            "cc", *flags["CPPFLAGS"], *flags["CFLAGS"], "-fPIC", "-shared", "-pthread",
            *flags["LDFLAGS"], "-Wl,-soname,libsqlite3.so.0",
            *["-DSQLITE_" + option for option in options_used], str(source), "-ldl", "-lm",
            "-o", str(library)], check=True, timeout=300)
        missing = previous_symbols - exported_symbols(library)
        if missing:
            raise RuntimeError("Replacement drops exported SQLite symbols: " + ", ".join(sorted(missing)))
        native = ctypes.CDLL(str(library))
        native.sqlite3_libversion.restype = ctypes.c_char_p
        native.sqlite3_sourceid.restype = ctypes.c_char_p
        if native.sqlite3_libversion().decode() != VERSION or native.sqlite3_sourceid().decode() != SOURCE_ID:
            raise RuntimeError("Compiled SQLite identity differs from reviewed source")
        (library.parent / "libsqlite3.so.0").symlink_to(library.name)
        # Debian slim images exclude /usr/share/doc during dpkg unpack. Keep
        # this required, package-owned provenance outside those pruning rules.
        document = package / "opt/ai-cloud-lab/sqlite"
        document.mkdir(parents=True)
        record = {"version": VERSION, "package_version": PACKAGE_VERSION, "previous_version": previous,
                  "source_url": URL, "archive_sha3_256": ARCHIVE_SHA3,
                  "source_kind": "canonical; parser generated with distro options",
                  "source_sha3_256": hashlib.sha3_256(source.read_bytes()).hexdigest(),
                  "source_id": SOURCE_ID, "compile_options_before": options,
                  "build_flags": flags, "exported_symbols_retained": len(previous_symbols),
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
            "Multi-Arch: same\n"
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
