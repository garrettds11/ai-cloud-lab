"""Reachability evidence for Chroma CVE-2026-45829 and CVE-2026-45833 in this candidate.

Both advisories end in Chroma building an embedding function from collection
configuration (known_embedding_functions[...].build_from_config), which can load
remote model code. This check runs Open WebUI's own installed ChromaClient code
in embedded mode, plants a malicious persisted configuration (top-level and
nested trust_remote_code), and records every build_from_config call.

Measured with Open WebUI v0.12.0 and chromadb 1.5.9: read paths (startup,
has_collection, search, query, get, delete, delete_collection) never reach the
builder. Write paths (insert, upsert) do: Collection.add/upsert re-read the
persisted schema to find sparse-embedding targets, which rebuilds any stored
embedding function. So in embedded mode the sink is reachable by any document
upload once an attacker can write chroma.sqlite3 in the data volume. Without that
write access (or an HTTP Chroma server, CHROMA_HTTP_HOST, or `chroma run`) there
is no path to plant the configuration. The check fails if this behavior changes
in either direction, forcing a new review.

This is evidence for this exact pairing and configuration. It does not fix or
waive the CVEs. No network or model download is needed; a reached builder is
recorded and refused, never run.
"""

# Operations that re-read persisted schema in chromadb 1.5.9 (see docstring).
EXPECTED_REACHING = {"insert", "upsert"}
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import types

OPEN_WEBUI = Path(sys.argv[1] if len(sys.argv) > 1 else "/app/backend/open_webui")
MALICIOUS_EF = {
    "type": "known", "name": "sentence_transformer",
    "config": {"model_name": "attacker/never-downloaded", "device": "cpu",
               "normalize_embeddings": False,
               "kwargs": {"trust_remote_code": True, "model_kwargs": {"trust_remote_code": True}}},
}


def load_client(data_path):
    """Import the installed chroma.py unmodified; stub only configuration constants."""
    for name in ("open_webui", "open_webui.utils", "open_webui.retrieval",
                 "open_webui.retrieval.vector", "open_webui.retrieval.vector.dbs"):
        module = types.ModuleType(name)
        module.__path__ = []
        sys.modules[name] = module
    config = types.ModuleType("open_webui.config")
    config.CHROMA_DATA_PATH, config.CHROMA_HTTP_HOST = str(data_path), ""
    config.CHROMA_HTTP_PORT, config.CHROMA_HTTP_HEADERS, config.CHROMA_HTTP_SSL = 8000, None, False
    config.CHROMA_CLIENT_AUTH_PROVIDER = config.CHROMA_CLIENT_AUTH_CREDENTIALS = None
    import chromadb
    config.CHROMA_TENANT, config.CHROMA_DATABASE = chromadb.DEFAULT_TENANT, chromadb.DEFAULT_DATABASE
    env = types.ModuleType("open_webui.env")
    env.USE_SLIM, env.RAG_METADATA_MAX_VALUE_CHARS = False, None
    misc = types.ModuleType("open_webui.utils.misc")
    misc.sanitize_text_for_db = lambda text: text.replace("\x00", "") if isinstance(text, str) else text
    sys.modules.update({"open_webui.config": config, "open_webui.env": env, "open_webui.utils.misc": misc})
    loaded = None
    for name, relative in (("open_webui.retrieval.vector.main", "retrieval/vector/main.py"),
                           ("open_webui.retrieval.vector.utils", "retrieval/vector/utils.py"),
                           ("open_webui.retrieval.vector.dbs.chroma", "retrieval/vector/dbs/chroma.py")):
        spec = importlib.util.spec_from_file_location(name, OPEN_WEBUI / relative)
        loaded = importlib.util.module_from_spec(spec)
        sys.modules[name] = loaded
        spec.loader.exec_module(loaded)
    return loaded.ChromaClient


def plant(data_path):
    """Write the malicious dense embedding function where Chroma 1.5.x persists it.

    Chroma 1.5 derives collection configuration from schema_str (config_json_str is
    empty), storing a configured embedding function in the dense vector index config.
    """
    database = sqlite3.connect(Path(data_path) / "chroma.sqlite3")
    rows = database.execute("select id, config_json_str, schema_str from collections").fetchall()
    if not rows:
        raise RuntimeError("No persisted collection found to plant")
    for row_id, config_text, schema_text in rows:
        schema = json.loads(schema_text)
        indexes = [schema["keys"]["#embedding"]["float_list"]["vector_index"],
                   schema["defaults"]["float_list"]["vector_index"]]
        for index in indexes:
            index["config"]["embedding_function"] = MALICIOUS_EF
        config = json.loads(config_text or "{}")
        config["embedding_function"] = MALICIOUS_EF
        database.execute("update collections set config_json_str=?, schema_str=? where id=?",
                         (json.dumps(config), json.dumps(schema), row_id))
    database.commit()
    database.close()


CURRENT = {"operation": "setup"}


def install_trap():
    from chromadb.utils import embedding_functions
    calls = []
    for registry_name in ("known_embedding_functions", "sparse_known_embedding_functions"):
        for name, cls in list(getattr(embedding_functions, registry_name, {}).items()):
            def trapped(config, _name=name):
                calls.append({"operation": CURRENT["operation"], "embedding_function": _name})
                raise RuntimeError("Persisted embedding-function configuration was deserialized")
            cls.build_from_config = staticmethod(trapped)
    return calls


def run(label, action):
    CURRENT["operation"] = label
    return action()


def main():
    import warnings
    import chromadb
    from chromadb.api.client import SharedSystemClient
    warnings.simplefilter("ignore", UserWarning)
    with tempfile.TemporaryDirectory(prefix="security-chroma-") as directory:
        ChromaClient = load_client(directory)
        item = {"id": "doc-1", "text": "reachability fixture", "vector": [1.0, 0.0, 0.0],
                "metadata": {"file_id": "fixture"}}
        ChromaClient().insert("file-fixture", [item])
        SharedSystemClient.clear_system_cache()
        plant(directory)
        calls = install_trap()
        client = run("client startup", ChromaClient)
        operations = [
            ("has_collection", lambda: client.has_collection("file-fixture")),
            ("search", lambda: client.search("file-fixture", vectors=[[1.0, 0.0, 0.0]], limit=1)),
            ("query", lambda: client.query("file-fixture", filter={"file_id": "fixture"})),
            ("get", lambda: client.get("file-fixture")),
            ("upsert", lambda: client.upsert("file-fixture", [dict(item, text="updated fixture")])),
            ("insert", lambda: client.insert("file-fixture",
                                             [dict(item, id="doc-2", vector=[0.0, 1.0, 0.0])])),
            ("delete(ids)", lambda: client.delete("file-fixture", ids=["doc-2"])),
            ("delete(filter)", lambda: client.delete("file-fixture", filter={"file_id": "absent"})),
        ]
        results = {label: run(label, action) for label, action in operations}
        assert results["has_collection"] is True
        assert results["search"] is not None and results["search"].ids == [["doc-1"]], "search failed"
        assert results["query"] is not None and results["query"].ids == [["doc-1"]], "query failed"
        assert results["get"].ids == [["doc-1"]], "get failed"
        reached = list(calls)
        # Positive control: Chroma's own configuration accessor must hit the planted sink.
        collection = client.client.get_collection(name="file-fixture", embedding_function=None)
        try:
            run("positive control", lambda: collection.configuration)
        except Exception:
            pass
        armed = any(call["operation"] == "positive control" for call in calls)
        run("delete_collection", lambda: client.delete_collection("file-fixture"))
        reached += [call for call in calls if call["operation"] == "delete_collection"]
        SharedSystemClient.clear_system_cache()
    reaching = sorted({call["operation"] for call in reached})
    all_operations = ["client startup"] + [label for label, _ in operations] + ["delete_collection"]
    result = {"chromadb": chromadb.__version__, "mode": "embedded PersistentClient",
              "operations_reaching_builder": reaching,
              "operations_not_reaching_builder": [op for op in all_operations if op not in reaching],
              "precondition": "attacker-controlled persisted schema (write access to chroma.sqlite3)",
              "positive_control_reached_builder": armed}
    print(json.dumps(result, indent=2))
    if set(reaching) != EXPECTED_REACHING:
        raise SystemExit("Chroma deserialization reachability changed; re-review CVE-2026-45829/45833")
    if not armed:
        raise SystemExit("Positive control failed: planted configuration was not deserialized")


if __name__ == "__main__":
    main()
