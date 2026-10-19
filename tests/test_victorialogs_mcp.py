"""Offline tests for the Lab Logs MCP server (logging/modules/track/package-src/mcp) and the
backend gateway's read-only query path (logging/modules/track/gateway.py).

Run: python -m unittest discover -s tests

A fake backend stands in for VictoriaLogs, and a stubbed opener checks the HTTP request that
VictoriaLogsBackend makes. Nothing here talks to AWS or a real log store.
"""

import http.client
import io
import json
import pathlib
import sys
import threading
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lambda" / "vuln_mcp"))
sys.path.insert(0, str(ROOT / "logging" / "modules" / "track" / "package-src" / "mcp"))
sys.path.insert(0, str(ROOT / "logging" / "modules" / "track"))

import gateway  # noqa: E402
import mcp_protocol  # noqa: E402
import server  # noqa: E402
import victorialogs_tools as vl  # noqa: E402
from vuln_tools import ToolError  # noqa: E402

NOW = 1_800_000_000.0  # 2027-01-15T08:00:00Z


class FakeBackend:
    def __init__(self, rows=None, points=None):
        self.rows, self.points, self.calls = rows or [], points or [], []

    def streams(self, query, start_s, end_s, limit):
        self.calls.append(("streams", query, start_s, end_s, limit))
        return list(self.rows)[:limit]

    def counts(self, query, start_s, end_s, step_s):
        self.calls.append(("counts", query, start_s, end_s, step_s))
        return list(self.points)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class QueryBuildingTests(unittest.TestCase):
    def test_source_maps_to_the_stream_field(self):
        self.assertEqual(vl.build_query("containers"), '{source="open-webui"}')
        self.assertEqual(vl.build_query("services"), '{source="ubuntu-host"}')

    def test_contains_is_matched_literally_and_quoted(self):
        query = vl.build_query("services", contains='a "b" (c)|d\\e')
        self.assertEqual(query, '{source="ubuntu-host"} ~"a \\"b\\" \\\\(c\\\\)\\\\|d\\\\\\\\e"')

    def test_level_unit_and_container(self):
        query = vl.build_query("services", level="errors", unit="ollama")
        self.assertIn('"_SYSTEMD_UNIT":~"^ollama"', query)
        self.assertIn('~"(?i)error|fail', query)
        self.assertIn('"container_name":~"open-webui"', vl.build_query("containers", container="open-webui"))
        self.assertNotIn("re(", query)

    def test_bad_arguments_are_refused(self):
        for kwargs in ({"source": "bootstrap"}, {"source": "services", "unit": 'x" OR *'},
                       {"source": "services", "contains": "line\nbreak"}, {"source": "services", "contains": "x" * 101},
                       {"source": "services", "level": "debug"}, {"source": "containers", "container": ""}):
            with self.assertRaises(ToolError, msg=kwargs):
                vl.build_query(**kwargs)


class BackendTests(unittest.TestCase):
    def test_streams_request_and_parsing(self):
        seen = {}

        def opener(request, timeout):
            seen["url"], seen["timeout"] = request.full_url, timeout
            return FakeResponse(b'{"_time":"2027-01-15T07:59:00.123456789Z","_msg":"older"}\n'
                                b'{"_time":"2027-01-15T07:59:30Z","_msg":"newer"}\n')

        rows = vl.VictoriaLogsBackend("https://10.0.0.5", opener=opener).streams('{source="open-webui"}', NOW - 60, NOW, 10)
        self.assertEqual([line for _, line in rows], ["newer", "older"])
        url = urllib.parse.urlsplit(seen["url"])
        self.assertEqual((url.scheme, url.netloc, url.path), ("https", "10.0.0.5", "/select/logsql/query"))
        args = dict(urllib.parse.parse_qsl(url.query))
        self.assertEqual(args["query"], '{source="open-webui"} | fields _time, _msg')
        self.assertEqual((args["start"], args["end"], args["limit"]), ("2027-01-15T07:59:00Z", "2027-01-15T08:00:00Z", "10"))

    def test_counts_parsing(self):
        body = json.dumps({"hits": [{"fields": {}, "timestamps": ["2027-01-15T07:00:00Z", "2027-01-15T07:30:00Z"],
                                     "values": [3, 4], "total": 7}]}).encode()
        backend = vl.VictoriaLogsBackend("https://10.0.0.5", opener=lambda request, timeout: FakeResponse(body))
        points = backend.counts('{source="ubuntu-host"}', NOW - 3600, NOW, 1800)
        self.assertEqual([count for _, count in points], [3, 4])

    def test_bad_answers_and_addresses_are_tool_errors(self):
        backend = vl.VictoriaLogsBackend("https://10.0.0.5", opener=lambda request, timeout: FakeResponse(b"not json\n"))
        with self.assertRaises(ToolError):
            backend.streams("x", NOW - 60, NOW, 5)
        with self.assertRaises(ToolError):
            vl.VictoriaLogsBackend("http://10.0.0.5")
        too_big = b"x" * (vl.MAX_RESPONSE_BYTES + 1)
        backend = vl.VictoriaLogsBackend("https://10.0.0.5", opener=lambda request, timeout: FakeResponse(too_big))
        with self.assertRaises(ToolError):
            backend.streams("x", NOW - 60, NOW, 5)


class ToolTests(unittest.TestCase):
    def tools(self, **kwargs):
        backend = FakeBackend(**kwargs)
        return vl.VictoriaLogTools(backend, clock=lambda: NOW), backend

    def test_search_scrubs_and_marks_untrusted(self):
        tools, backend = self.tools(rows=[(NOW - 5, "login password=hunter2 for alice@example.com"), (NOW - 9, "ok")])
        result = tools.search_logs("containers", contains="login", minutes=15, limit=5)
        self.assertEqual(result["returned"], 2)
        self.assertNotIn("hunter2", json.dumps(result))
        self.assertIn("a***@example.com", result["lines"][0]["line"])
        self.assertEqual(result["note"], vl.UNTRUSTED)
        _, query, start, end, limit = backend.calls[0]
        self.assertEqual((end - start, limit), (900, 5))
        self.assertIn('~"login"', query)

    def test_bounds(self):
        tools, _ = self.tools()
        for kwargs in ({"minutes": 0}, {"minutes": vl.MAX_MINUTES + 1}, {"limit": vl.MAX_LINES + 1}, {"limit": True}):
            with self.assertRaises(ToolError):
                tools.search_logs("services", **kwargs)
        with self.assertRaises(ToolError):
            tools.count_log_events("services", minutes=1440, bucket_minutes=1)

    def test_counts_and_summary(self):
        tools, _ = self.tools(points=[(NOW - 3600, 2), (NOW - 1800, 5)],
                              rows=[(NOW - 1, "ERROR db timeout after 30s"), (NOW - 2, "ERROR db timeout after 31s")])
        counted = tools.count_log_events("services", level="errors", minutes=60, bucket_minutes=30)
        self.assertEqual(counted["total"], 7)
        summary = tools.summarize_log_errors(minutes=30)
        self.assertEqual([s["source"] for s in summary["sources"]], ["containers", "services"])
        self.assertEqual(summary["sources"][0]["top_messages"][0]["count"], 2)

    def test_signin_events(self):
        tools, backend = self.tools(rows=[
            (NOW - 1, '1.2.3.4 - "POST /api/v1/auths/signin HTTP/1.1" 400'),
            (NOW - 2, '1.2.3.4 - "POST /api/v1/auths/signin HTTP/1.1" 200'),
            (NOW - 3, '1.2.3.4 - "GET /oauth/oidc/callback?code=abc HTTP/1.1" 302')])
        result = tools.get_signin_events(minutes=60)
        self.assertEqual(result["password_signins"], {"succeeded": 1, "failed": 1, "other": 0})
        self.assertEqual(result["single_sign_on_callbacks"]["succeeded"], 1)
        self.assertIn('{source="open-webui"}', backend.calls[0][1])


class ServerTests(unittest.TestCase):
    def setUp(self):
        backend = FakeBackend(rows=[(NOW - 1, "hello")])
        tools = mcp_protocol.Toolset((vl.VictoriaLogTools(backend, clock=lambda: NOW), vl.TOOL_DEFINITIONS),
                                     server_info=server.SERVER_INFO, instructions=server.INSTRUCTIONS)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(tools))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def post(self, message, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        connection.request("POST", "/mcp", json.dumps(message), {"Content-Type": "application/json", **(headers or {})})
        response = connection.getresponse()
        body = response.read().decode()
        connection.close()
        return response.status, (json.loads(body) if body else None)

    def test_initialize_names_this_server(self):
        status, body = self.post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
        self.assertEqual(status, 200)
        self.assertEqual(body["result"]["serverInfo"]["name"], "lab-logs")
        self.assertIn("VictoriaLogs", body["result"]["instructions"])

    def test_tools_list_and_call(self):
        _, listed = self.post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual({t["name"] for t in listed["result"]["tools"]}, vl.TOOL_NAMES)
        _, called = self.post({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                               "params": {"name": "search_logs", "arguments": {"source": "services"}}})
        self.assertIn("hello", called["result"]["content"][0]["text"])

    def test_foreign_origin_is_refused(self):
        status, _ = self.post({"jsonrpc": "2.0", "id": 4, "method": "ping"}, {"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        status, _ = self.post({"jsonrpc": "2.0", "id": 5, "method": "ping"}, {"Origin": "http://localhost:8080"})
        self.assertEqual(status, 200)

    def test_get_is_not_allowed(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        connection.request("GET", "/mcp")
        self.assertEqual(connection.getresponse().status, 405)
        connection.close()

    def test_vuln_server_defaults_are_unchanged(self):
        message = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        result = mcp_protocol.handle_message(message, object())["result"]
        self.assertEqual(result["serverInfo"]["name"], "vuln-findings")


class GatewayQueryTests(unittest.TestCase):
    def test_allowed_arguments_pass(self):
        raw = urllib.parse.urlencode({"query": '{source="open-webui"} | fields _time, _msg', "start": "2027-01-15T07:00:00Z",
                                      "end": "2027-01-15T08:00:00Z", "limit": "30"})
        self.assertEqual(gateway.query_arguments(raw)["limit"], "30")
        self.assertIn("step", gateway.query_arguments("query=x&step=1800s"))

    def test_other_arguments_and_shapes_are_refused(self):
        for raw in ("", "start=1h", "query=x&delete=1", "query=x&query=y", "query=x&limit=0", "query=x&limit=5000",
                    "query=" + "x" * (gateway.MAX_QUERY_CHARS + 1), "query=x&start=", "query=x&end=" + "9" * 41):
            with self.assertRaises(ValueError, msg=raw):
                gateway.query_arguments(raw)

    def test_only_two_read_endpoints(self):
        self.assertEqual(gateway.QUERY_PATHS, {"/select/logsql/query", "/select/logsql/hits"})

    def test_ingest_maps_message_to_msg(self):
        sent = {}
        original_request, original_backend = gateway.request, gateway.backend
        gateway.request = lambda url, body, content_type: sent.update(url=url)
        gateway.backend = lambda: "victorialogs"
        try:
            gateway.deliver([{"event_id": "1", "timestamp": "t", "epoch": 1, "source": "s", "host": "h", "message": "m"}])
        finally:
            gateway.request, gateway.backend = original_request, original_backend
        self.assertIn("_msg_field=message", sent["url"])


class PackageTests(unittest.TestCase):
    def test_archive_matches_sources(self):
        import zipfile
        archive = ROOT / "logging" / "modules" / "track" / "packages" / "victorialogs-mcp.zip"
        package = ROOT / "logging" / "modules" / "track" / "package-src" / "mcp"
        with zipfile.ZipFile(archive) as contents:
            for item in contents.infolist():
                if item.filename == "track.txt":
                    self.assertEqual(contents.read(item), b"victorialogs")
                    continue
                source = package / item.filename
                if not source.exists():
                    source = ROOT / "lambda" / "vuln_mcp" / item.filename
                self.assertEqual(contents.read(item), source.read_bytes().replace(b"\r\n", b"\n"),
                                 f"{item.filename} is stale; run package-src/build_packages.py")


if __name__ == "__main__":
    unittest.main()
