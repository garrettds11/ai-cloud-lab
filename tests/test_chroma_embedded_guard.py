"""Keeps the deployed Open WebUI on embedded Chroma with no Chroma HTTP server.

Chroma CVE-2026-45829/45833 are reached through Chroma's HTTP API (create/update
collection). The lab runs Open WebUI with its default embedded PersistentClient,
which never exposes those routes (reachability review in #82). That review only
holds while the deployment never points Open WebUI at a Chroma server or starts one.
This guard fails if user-data starts doing either, forcing a new review.
"""

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEPLOYMENT_FILES = ["cloud-init.sh.tpl", "variables.tf", "main.tf", "terraform.tfvars.example"]


class ChromaEmbeddedGuardTests(unittest.TestCase):
    def test_deployment_never_configures_or_starts_a_chroma_server(self):
        for name in DEPLOYMENT_FILES:
            path = ROOT / name
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            with self.subTest(file=name):
                self.assertIsNone(re.search(r"CHROMA_[A-Z_]+", text), "Chroma client/server setting")
                self.assertIsNone(re.search(r"\bVECTOR_DB\b", text), "vector database override")
                self.assertIsNone(re.search(r"\bchroma\s+run\b|chromadb/chroma", text), "Chroma server")

    def test_open_webui_keeps_its_data_volume(self):
        # Embedded Chroma persists under /app/backend/data; the precondition for the
        # write-path finding is write access to this volume.
        text = (ROOT / "cloud-init.sh.tpl").read_text(encoding="utf-8")
        self.assertIn(":/app/backend/data", text)


if __name__ == "__main__":
    unittest.main()
