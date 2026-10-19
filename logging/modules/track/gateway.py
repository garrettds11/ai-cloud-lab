#!/usr/bin/env python3
"""Private VPC-only gateway in front of VictoriaLogs on the logging backend.

POST /events takes the bake-off event contract from Vector and writes it to the native ingest API.
GET /select/logsql/query and /select/logsql/hits are a read-only query path for the lab's "Lab Logs"
MCP server: only these two endpoints and a few arguments pass, so nothing can delete or change logs.
Both are reachable only from the lab host's security group (logging/modules/track/main.tf).
"""

import json
import os
import ssl
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path("/opt/logging-bakeoff")
CREDENTIALS = Path("/root/logging-bakeoff-credentials")
VICTORIALOGS = "http://127.0.0.1:9428"
QUERY_PATHS = {"/select/logsql/query", "/select/logsql/hits"}
QUERY_ARGS = {"query", "start", "end", "limit", "step"}
MAX_QUERY_CHARS = 4000
MAX_LIMIT = 1000
MAX_ANSWER_BYTES = 4_000_000


def backend():
    return next(line.split("=", 1)[1].strip() for line in CREDENTIALS.read_text().splitlines() if line.startswith("backend="))


def query_arguments(raw_query):
    """The allowed, single-valued arguments of a read request. Raises ValueError otherwise."""
    pairs = urllib.parse.parse_qsl(raw_query, keep_blank_values=True, strict_parsing=bool(raw_query))
    names = [name for name, _ in pairs]
    if len(names) != len(set(names)):
        raise ValueError("each argument may appear once")
    arguments = dict(pairs)
    unknown = set(arguments) - QUERY_ARGS
    if unknown:
        raise ValueError("unsupported argument: " + sorted(unknown)[0])
    if not 0 < len(arguments.get("query", "")) <= MAX_QUERY_CHARS:
        raise ValueError(f"query must be 1-{MAX_QUERY_CHARS} characters")
    if "limit" in arguments and not (arguments["limit"].isdigit() and 1 <= int(arguments["limit"]) <= MAX_LIMIT):
        raise ValueError(f"limit must be 1-{MAX_LIMIT}")
    for name in ("start", "end", "step"):
        if name in arguments and (not arguments[name] or len(arguments[name]) > 40):
            raise ValueError(f"{name} is not valid")
    return arguments


def request(url, payload, content_type="application/json", headers=None, method="POST", insecure=False):
    head = {"Content-Type": content_type}
    head.update(headers or {})
    data = payload if isinstance(payload, bytes) else payload.encode()
    req = urllib.request.Request(url, data=data, headers=head, method=method)
    context = ssl._create_unverified_context() if insecure else None
    with urllib.request.urlopen(req, timeout=30, context=context) as response:
        return response.read()


def deliver(events):
    if backend() != "victorialogs":
        raise RuntimeError("unsupported backend")
    body = "\n".join(json.dumps(e) for e in events) + "\n"
    # The event text is "message"; VictoriaLogs searches _msg by default, so map it there.
    request(VICTORIALOGS + "/insert/jsonline?_time_field=timestamp&_msg_field=message&_stream_fields=source,host",
            body, "application/x-ndjson")


def select(path, arguments):
    """Forwards a read request to VictoriaLogs as a form POST. Returns (content type, body)."""
    req = urllib.request.Request(VICTORIALOGS + path, data=urllib.parse.urlencode(arguments).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as response:
        body = response.read(MAX_ANSWER_BYTES + 1)
        if len(body) > MAX_ANSWER_BYTES:
            raise OverflowError("answer too large")
        return response.headers.get("Content-Type", "application/json"), body


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path, _, raw_query = self.path.partition("?")
        if path not in QUERY_PATHS:
            self.send_error(404)
            return
        try:
            arguments = query_arguments(raw_query)
        except ValueError as exc:
            self.send_error(400, str(exc))
            return
        try:
            content_type, body = select(path, arguments)
        except urllib.error.HTTPError as exc:
            self.send_error(502 if exc.code >= 500 else 400, "log store rejected the query")
            return
        except OverflowError:
            self.send_error(413, "answer too large; narrow the query")
            return
        except Exception as exc:
            print(f"query failed: {type(exc).__name__}", flush=True)
            self.send_error(503, "backend unavailable")
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/events":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 2_000_000:
                raise ValueError("batch must be 1-2000000 bytes")
            raw = self.rfile.read(length)
            try:
                events = json.loads(raw)
            except json.JSONDecodeError:
                events = [json.loads(line) for line in raw.splitlines() if line.strip()]
            if isinstance(events, dict):
                events = [events]
            if not isinstance(events, list) or not 1 <= len(events) <= 500:
                raise ValueError("batch must contain 1-500 events")
            for e in events:
                if not all(k in e for k in ("event_id", "timestamp", "epoch", "source", "host", "message")):
                    raise ValueError("event contract missing field")
            deliver(events)
            self.send_response(204)
            self.end_headers()
        except (ValueError, KeyError) as exc:
            self.send_error(400, str(exc))
        except Exception as exc:
            print(f"delivery failed: {exc}", flush=True)
            self.send_error(503, "backend unavailable")

    def log_message(self, format_string, *args):
        print("gateway: " + format_string % args, flush=True)


def main():
    cert = ROOT / "gateway.crt"
    key = ROOT / "gateway.key"
    if not cert.exists():
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650", "-subj", "/CN=logging-bakeoff", "-keyout", str(key), "-out", str(cert)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.chmod(key, 0o600)
    server = ThreadingHTTPServer(("0.0.0.0", 443), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
