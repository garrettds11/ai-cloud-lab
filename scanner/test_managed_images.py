import json
import copy
import subprocess
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import managed_images


def setUpModule():
    (Path(__file__).parent.parent / ".security-scan").mkdir(exist_ok=True)


class ManagedImageTests(unittest.TestCase):
    def test_nasl_fixture_is_isolated_and_cleans_its_resources(self):
        with patch("managed_images.subprocess.run") as run:
            result = managed_images.scanner_smoke("sha256:" + "a" * 64, "sha256:" + "b" * 64)
        commands = [call.args[0] for call in run.call_args_list]
        for command in commands:
            if command[:2] == ["docker", "run"]:
                self.assertEqual(command[command.index("--network") + 1], "none")
                self.assertEqual(command[command.index("--cap-drop") + 1], "ALL")
                self.assertIn("--read-only", command)
                self.assertNotIn("--privileged", command)
                self.assertNotIn("--publish", command)
                self.assertIn("type=volume,source=security-nasl-", " ".join(command))
        volume = commands[0][-1]
        redis = commands[1]
        self.assertEqual(redis[redis.index("--user") + 1], "redis")
        fixture = commands[2]
        self.assertIn("exec", fixture[fixture.index("--tmpfs") + 1].split(","))
        self.assertIn("noexec", redis[redis.index("--tmpfs") + 1].split(","))
        self.assertEqual(commands[-1], ["docker", "volume", "rm", volume])
        self.assertEqual(result["network"], "none")
        self.assertFalse(result["production_targets_scanned"])
        with patch("managed_images.subprocess.run") as run:
            managed_images.scanner_smoke("sha256:" + "a" * 64, "sha256:" + "b" * 64, ospd=True)
        script = run.call_args_list[2].args[0][-1]
        self.assertLess(script.index('client.connect'), script.index('ospd-openvas --version'))
        fixture = run.call_args_list[2].args[0]
        caps = [fixture[i + 1] for i, flag in enumerate(fixture) if flag == "--cap-add"]
        self.assertEqual(caps, ["NET_ADMIN", "NET_RAW"])
        self.assertNotIn("--cap-add", run.call_args_list[1].args[0])

    def test_nasl_failure_cleans_only_generated_resources(self):
        commands = []
        def run(command, **kwargs):
            commands.append(command)
            if command[:2] == ["docker", "run"] and "--rm" in command:
                raise subprocess.CalledProcessError(1, command)
        with patch("managed_images.subprocess.run", side_effect=run):
            with self.assertRaises(subprocess.CalledProcessError):
                managed_images.scanner_smoke("sha256:" + "a" * 64, "sha256:" + "b" * 64)
        name = commands[2][commands[2].index("--name") + 1]
        self.assertEqual(commands[-2], ["docker", "rm", "--force", "--volumes", name, name + "-redis"])
        self.assertEqual(commands[-1], ["docker", "volume", "rm", name + "-socket"])
        self.assertIn(["docker", "logs", name + "-redis"], commands)
        with patch("managed_images.subprocess.run") as command:
            with self.assertRaises(ValueError):
                managed_images.scanner_smoke("image:mutable", "sha256:" + "b" * 64)
            command.assert_not_called()

    def test_scanner_header_pruning_preserves_runtime_libraries(self):
        for context in ("openvas-scanner", "ospd-openvas"):
            path = Path(__file__).parent / "image_builds" / context / "remove-headers.py"
            spec = importlib.util.spec_from_file_location("prune_headers", path)
            prune = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(prune)
            self.assertEqual(prune.planned_removals("Purg linux-libc-dev:amd64 [6.12]\n"),
                             {"linux-libc-dev"})
            inventory = "linux-libc-dev installed\nlibc6 installed\n"
            with patch.object(prune.subprocess, "check_output", side_effect=[
                    inventory, "Remv linux-libc-dev [6.12]\nRemv libc6 [2.41]\n"]), \
                    patch.object(prune.subprocess, "run") as run:
                with self.assertRaisesRegex(RuntimeError, "libc6"):
                    prune.main()
                run.assert_not_called()

    def test_candidate_pruning_refuses_removal_of_runtime_libraries(self):
        path = Path(__file__).parent / "image_builds/open-webui/remove-build-deps.py"
        spec = importlib.util.spec_from_file_location("prune_candidate", path)
        prune = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(prune)
        with self.assertRaisesRegex(RuntimeError, "libmariadb3"):
            prune.planned_removals("Remv libmariadb-dev [10.11]\nRemv libmariadb3 [10.11]\n")
        self.assertEqual(prune.planned_removals("Purg linux-libc-dev:amd64 [6.1]\n"),
                         {"linux-libc-dev"})

    def test_failed_candidate_check_removes_only_its_disposable_container(self):
        container = "a" * 64
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            if command[:2] == ["docker", "exec"]:
                raise subprocess.CalledProcessError(1, command)
        with patch("managed_images.subprocess.check_output", return_value=container), \
                patch("managed_images.subprocess.run", side_effect=run):
            with self.assertRaises(subprocess.CalledProcessError):
                managed_images.candidate_smoke("sha256:" + "1" * 64)
        self.assertEqual(calls[-1], ["docker", "rm", "--force", "--volumes", container])

    def socket_config(self):
        mounts = {
            "pg-gvm": [("pg-data", "/var/lib/postgresql"), ("pg-socket", "/var/run/postgresql")],
            "pg-gvm-migrator": [("pg-data", "/var/lib/postgresql"), ("pg-socket", "/var/run/postgresql")],
            "gvmd": [("pg-socket", "/var/run/postgresql"), ("gmp-socket", "/run/gvmd")],
            "redis-server": [("redis-socket", "/run/redis")],
            "ospd-openvas": [("redis-socket", "/run/redis")],
            "gvm-tools": [("gmp-socket", "/run/gvmd")],
        }
        return {"services": {name: {"networks": {"default": None}, "volumes": [
            {"type": "volume", "source": source, "target": target} for source, target in volumes]}
            for name, volumes in mounts.items()}}

    def test_isolation_preserves_scanner_network_and_shared_sockets(self):
        config = self.socket_config()
        original = copy.deepcopy(config)
        result = managed_images.isolated_services(config)
        self.assertEqual(config, original)
        self.assertEqual(result["services"]["ospd-openvas"], original["services"]["ospd-openvas"])
        for name in ("pg-gvm", "pg-gvm-migrator", "redis-server", "gvm-tools"):
            self.assertEqual(result["services"][name]["network_mode"], "none")
            self.assertNotIn("networks", result["services"][name])
            self.assertIn("no-new-privileges:true", result["services"][name]["security_opt"])
            self.assertEqual(result["services"][name]["volumes"], original["services"][name]["volumes"])
        self.assertTrue(result["services"]["redis-server"]["read_only"])

    def test_unexpected_exposure_or_changed_socket_fails_closed(self):
        for change in (lambda c: c["services"]["pg-gvm"].update(ports=["5432:5432"]),
                       lambda c: c["services"]["gvm-tools"].update(privileged=True),
                       lambda c: c["services"]["gvm-tools"].update(security_opt=["no-new-privileges=false"]),
                       lambda c: c["services"]["redis-server"]["volumes"][0].update(source="unshared")):
            config = self.socket_config()
            change(config)
            with self.assertRaises(ValueError):
                managed_images.isolated_services(config)

    def test_recipe_matches_digest_pinned_base(self):
        images = managed_images.definitions(Path(__file__).parent / "image_builds")
        self.assertEqual({image["context"] for image in images},
                         {"pg-gvm", "pg-gvm-migrator", "redis-server", "gvm-tools", "ospd-openvas", "openvas-scanner", "trivy", "open-webui", "gsad", "gvmd", "nginx"})
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
        images = [image for image in managed_images.definitions(Path(__file__).parent / "image_builds")
                  if image.get("scope", "compose") == "compose"]
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
