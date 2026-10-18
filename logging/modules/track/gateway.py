#!/usr/bin/env python3
"""Private VPC-only adapter from the bake-off event contract to native ingest APIs."""

import json
import os
import ssl
import subprocess
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path("/opt/logging-bakeoff")
BACKEND = next(line.split("=", 1)[1].strip() for line in Path("/root/logging-bakeoff-credentials").read_text().splitlines() if line.startswith("backend="))


def request(url, payload, content_type="application/json", headers=None, method="POST", insecure=False):
    head = {"Content-Type": content_type}
    head.update(headers or {})
    data = payload if isinstance(payload, bytes) else payload.encode()
    req = urllib.request.Request(url, data=data, headers=head, method=method)
    context = ssl._create_unverified_context() if insecure else None
    with urllib.request.urlopen(req, timeout=30, context=context) as response:
        return response.read()


def deliver(events):
    if BACKEND != "victorialogs":
        raise RuntimeError("unsupported backend")
    body = "\n".join(json.dumps(e) for e in events) + "\n"
    request("http://127.0.0.1:9428/insert/jsonline?_time_field=timestamp&_stream_fields=source,host", body, "application/x-ndjson")


class Handler(BaseHTTPRequestHandler):
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
