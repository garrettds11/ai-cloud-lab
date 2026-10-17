import hashlib
import io
import subprocess
import tarfile
import unittest
from unittest.mock import patch

import dispositions
import scan

IMAGE = dispositions.FEED_REPOSITORY + "@sha256:" + "a" * 64
PATH = "/var/lib/openvas/25.0/vt-data/nasl/bad_ssh_keys.inc"


def archive(name, data=b"fixture", symlink=False):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as bundle:
        info = tarfile.TarInfo(name)
        if symlink:
            info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
            bundle.addfile(info)
        else:
            info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))
    return stream.getvalue()


class FeedHashTests(unittest.TestCase):
    def run_with(self, payload):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            if command[:2] == ["docker", "cp"]:
                return subprocess.CompletedProcess(command, 0, stdout=payload)
            return subprocess.CompletedProcess(command, 0)
        with patch("scan.subprocess.run", side_effect=run), \
                patch("scan.subprocess.check_output", return_value="c" * 64 + "\n"):
            try:
                return scan.feed_file_hashes(IMAGE, [PATH]), calls
            except ValueError as error:
                return error, calls

    def test_hashes_regular_file_without_starting_container(self):
        hashes, calls = self.run_with(archive("bad_ssh_keys.inc", b"fixture"))
        self.assertEqual(hashes, {PATH: hashlib.sha256(b"fixture").hexdigest()})
        self.assertFalse(any(command[:2] == ["docker", "start"] or command[:2] == ["docker", "run"]
                             for command in calls))
        self.assertIn(["docker", "rm", "--force", "c" * 64], calls)

    def test_symlink_is_refused_and_container_removed(self):
        result, calls = self.run_with(archive("bad_ssh_keys.inc", symlink=True))
        self.assertIsInstance(result, ValueError)
        self.assertIn(["docker", "rm", "--force", "c" * 64], calls)

    def test_only_immutable_feed_images_are_hashed(self):
        for reference in ("registry/pg-gvm@sha256:" + "a" * 64, dispositions.FEED_REPOSITORY + ":latest"):
            with self.assertRaises(ValueError):
                scan.feed_file_hashes(reference, [PATH])


if __name__ == "__main__":
    unittest.main()
