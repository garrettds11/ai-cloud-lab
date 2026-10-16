"""Verify the installed SQLite and a disposable database across upgrades/reopens."""
import hashlib
import json
import mmap
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile

SOURCE_ID = re.compile(rb"(20[0-9]{2}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} [0-9a-f]{64})")


def embedded_copies(roots=None):
    """Report SQLite engines compiled into other native modules.

    Statically linked copies (for example Chroma's Rust bindings) do not load the
    replaced system library and are invisible to package-based scanners. This is
    an inventory, not a fix or an exception.
    """
    system = Path("/usr/lib/x86_64-linux-gnu/libsqlite3.so.0").resolve()
    roots = roots or sorted({Path(entry) for entry in sys.path if entry and Path(entry).is_dir()})
    copies, seen = [], set()
    for root in roots:
        for directory, _, files in os.walk(root):
            for name in files:
                path = Path(directory) / name
                if ".so" not in name or path.is_symlink() or path.resolve() in seen or path.resolve() == system:
                    continue
                seen.add(path.resolve())
                with path.open("rb") as stream:
                    if os.fstat(stream.fileno()).st_size == 0:
                        continue
                    with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
                        if data.find(b"SQLite format 3\x00") < 0:
                            continue
                        found = sorted({match.decode() for match in SOURCE_ID.findall(data)})
                if found:
                    copies.append({"path": str(path), "source_ids": found})
    return copies


def main():
    if sys.argv[1:] == ["--embedded-copies"]:
        print(json.dumps(embedded_copies(), indent=2))
        return
    record = json.loads(Path("/opt/ai-cloud-lab/sqlite/security-build.json").read_text())
    assert sqlite3.sqlite_version == record["version"], "Python loaded an unexpected SQLite library"
    library = Path("/usr/lib/x86_64-linux-gnu/libsqlite3.so.0")
    assert hashlib.sha256(library.read_bytes()).hexdigest() == record["library_sha256"]
    if len(sys.argv) == 2:
        with sqlite3.connect(sys.argv[1]) as previous:
            assert previous.execute("select version, value from previous_runtime").fetchall() == [
                (record["previous_version"], "preserved")]
            assert previous.execute("pragma integrity_check").fetchone() == ("ok",)
            previous.execute("alter table previous_runtime add column migrated integer default 1")
            assert previous.execute("select migrated from previous_runtime").fetchone() == (1,)
    with tempfile.TemporaryDirectory(prefix="security-sqlite-check-") as directory:
        database = Path(directory) / "test.db"
        with sqlite3.connect(database) as connection:
            assert connection.execute("select sqlite_source_id()").fetchone()[0] == record["source_id"]
            options = {row[0] for row in connection.execute("pragma compile_options")}
            required = {option for option in record["compile_options_before"]
                        if option.startswith(("ENABLE_", "THREADSAFE=", "MAX_VARIABLE_NUMBER="))}
            assert required <= options, "Upstream replacement lost a distro SQLite feature"
            connection.execute("pragma journal_mode=wal")
            connection.execute("pragma foreign_keys=on")
            connection.execute("create table accounts(id integer primary key, data text)")
            connection.execute("insert into accounts values(1, ?)", ('{"name":"fixture"}',))
            assert connection.execute("select json_extract(data, '$.name') from accounts").fetchone() == ("fixture",)
            connection.execute("create virtual table search using fts5(body)")
            connection.execute("insert into search values('retrieval fixture')")
            assert connection.execute("select count(*) from search where search match 'fixture'").fetchone() == (1,)
            connection.execute("create virtual table coordinates using rtree(id, lo, hi)")
            connection.execute("insert into coordinates values(1, 0, 1)")
            # Check actual grammar, not just PRAGMA compile_options metadata.
            connection.execute("create table bounded_dml(id integer primary key, value integer)")
            connection.executemany("insert into bounded_dml values(?, 0)", [(1,), (2,), (3,)])
            connection.execute("update bounded_dml set value=1 order by id limit 1")
            assert connection.execute("select sum(value) from bounded_dml").fetchone() == (1,)
            connection.execute("delete from bounded_dml order by id desc limit 1")
            assert connection.execute("select id from bounded_dml order by id").fetchall() == [(1,), (2,)]
            connection.commit()
            connection.execute("begin")
            connection.execute("insert into accounts values(2, '{}')")
            connection.rollback()
        with sqlite3.connect(database) as reopened:
            assert reopened.execute("select count(*) from accounts").fetchone() == (1,)
            assert reopened.execute("pragma integrity_check").fetchone() == ("ok",)
    print("Pinned SQLite identity, features, WAL, JSON, FTS5, RTree, bounded DML, rollback and reopen passed")


if __name__ == "__main__":
    main()
