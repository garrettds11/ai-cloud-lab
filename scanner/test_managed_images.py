import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import managed_images


def setUpModule():
    (Path(__file__).parent.parent / ".security-scan").mkdir(exist_ok=True)


class ManagedImageTests(unittest.TestCase):
    def test_recipe_matches_digest_pinned_base(self):
        images = managed_images.definitions(Path(__file__).parent / "image_builds")
        self.assertEqual({image["context"] for image in images},
                         {"pg-gvm", "pg-gvm-migrator", "redis-server", "gvm-tools", "ospd-openvas"})
        for image in images:
            self.assertTrue(image["recipe_files"])

    def test_changed_compose_fails_before_build(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent / ".security-scan") as temporary:
            compose = Path(temporary) / "compose.yaml"
            compose.write_text("services:\n  pg:\n    image: other/postgres:latest\n")
            with patch("managed_images.build") as build:
                with self.assertRaises(ValueError):
                    managed_images.prepare(compose, Path(__file__).parent / "image_builds")
                build.assert_not_called()

    def test_compose_uses_built_id_and_preserves_other_images(self):
        images = managed_images.definitions(Path(__file__).parent / "image_builds")
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent / ".security-scan") as temporary:
            compose = Path(temporary) / "compose.yaml"
            compose.write_text("services:\n" + "".join(
                "  " + image["context"] + ":\n    image: '" + image["reference"] + "' # upstream\n"
                for image in images) + "  feed:\n    image: other/feed:latest\n")
            results = [dict(image, image_id="sha256:" + str(index + 1) * 64)
                       for index, image in enumerate(images)]
            with patch("managed_images.build", side_effect=results):
                records = managed_images.prepare(compose, Path(__file__).parent / "image_builds")
            self.assertEqual([record["image_id"] for record in records], [result["image_id"] for result in results])
            for result in results:
                self.assertIn(result["image_id"] + " # upstream", compose.read_text())
                self.assertNotIn(result["reference"], compose.read_text())
            self.assertIn("other/feed:latest", compose.read_text())

    def test_mutable_base_or_mismatched_dockerfile_fails(self):
        for base, recipe in (("registry/app:latest", "FROM registry/app:latest\n"),
                             ("registry/app@sha256:" + "1" * 64, "FROM registry/app:latest\n")):
            with tempfile.TemporaryDirectory(dir=Path(__file__).parent.parent / ".security-scan") as temporary:
                root = Path(temporary)
                (root / "app").mkdir()
                (root / "app/Dockerfile").write_text(recipe)
                (root / "manifest.json").write_text(json.dumps({"schema_version": 1, "images": [{
                    "reference": "registry/app:stable", "base": base, "context": "app",
                    "tag": "ai-cloud-lab/app:patched"}]}))
                with self.assertRaises(ValueError):
                    managed_images.definitions(root)

    def test_build_uses_fresh_packages_without_credentials_or_host_mounts(self):
        root = Path(__file__).parent / "image_builds"
        image = managed_images.definitions(root)[0]
        with patch("managed_images.subprocess.run") as run, patch("managed_images.subprocess.check_output", return_value="sha256:" + "1" * 64):
            managed_images.build(root, image)
        command = run.call_args.args[0]
        self.assertIn("--no-cache", command)
        self.assertIn("--pull", command)
        self.assertNotIn("--secret", command)
        self.assertNotIn("--ssh", command)
        self.assertNotIn("--privileged", command)


if __name__ == "__main__":
    unittest.main()
