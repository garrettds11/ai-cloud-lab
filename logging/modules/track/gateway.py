#!/usr/bin/env python3
"""Private VPC-only adapter from the bake-off event contract to native ingest APIs."""

import base64
import json
import os
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path("/opt/logging-bakeoff")
BACKEND = next(line.split("=", 1)[1].strip() for line in Path("/root/logging-bakeoff-credentials").read_text().splitlines() if line.startswith("backend="))
ENV = dict(line.strip().split("=", 1) for line in (ROOT / ".env").read_text().splitlines())


def request(url, payload, content_type="application/json", headers=None, method="POST", insecure=False):
    head = {"Content-Type": content_type}
    head.update(headers or {})
    data = payload if isinstance(payload, bytes) else payload.encode()
    req = urllib.request.Request(url, data=data, headers=head, method=method)
    context = ssl._create_unverified_context() if insecure else None
    with urllib.request.urlopen(req, timeout=30, context=context) as response:
        return response.read()


def basic(username, password):
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()


def ensure_quickwit_index():
    config = """version: 0.7
index_id: webui-logs
doc_mapping:
  mode: dynamic
  timestamp_field: timestamp
  field_mappings:
    - name: timestamp
      type: datetime
      input_formats: [rfc3339]
      fast: true
    - name: message
      type: text
      tokenizer: default
      record: position
      stored: true
"""
    try:
        request("http://127.0.0.1:7280/api/v1/indexes", config, "application/yaml")
    except urllib.error.HTTPError as exc:
        if exc.code not in (400, 409):
            raise


def ensure_graylog_input():
    auth = basic("admin", ENV["LOGGING_PASSWORD"])
    inputs = json.loads(request("http://127.0.0.1:8080/api/system/inputs", b"", method="GET", headers={"Authorization": auth, "X-Requested-By": "logging-bakeoff"}))
    if any(item.get("title") == "bakeoff-gelf" for item in inputs.get("inputs", [])):
        return
    body = json.dumps({
        "title": "bakeoff-gelf",
        "type": "org.graylog2.inputs.gelf.http.GELFHttpInput",
        "global": True,
        "configuration": {"bind_address": "0.0.0.0", "port": 12201, "tls_enable": False, "recv_buffer_size": 1048576, "max_message_size": 2097152},
    })
    request("http://127.0.0.1:8080/api/system/inputs", body, headers={"Authorization": auth, "X-Requested-By": "logging-bakeoff"})


def deliver(events):
    if BACKEND == "splunk":
        body = "\n".join(json.dumps({"event": e, "time": e["epoch"], "sourcetype": "_json", "index": "main"}) for e in events) + "\n"
        response = json.loads(request("https://127.0.0.1:8088/services/collector/event", body, headers={"Authorization": "Splunk " + ENV["LOGGING_HEC_TOKEN"]}, insecure=True))
        if response.get("code") != 0:
            raise RuntimeError("Splunk HEC rejected batch: " + json.dumps(response))
    elif BACKEND == "elastic":
        # The HTTP input's own host/header metadata is removed by the pipeline.
        # Wrapping each event keeps the common host field intact until JSON expansion.
        wrapped = [{"payload": json.dumps(e, separators=(",", ":"))} for e in events]
        request("http://127.0.0.1:8088/events", json.dumps(wrapped))
    elif BACKEND in ("opensearch", "wazuh"):
        body = "".join(json.dumps({"index": {"_index": "webui-logs", "_id": e["event_id"]}}) + "\n" + json.dumps(e) + "\n" for e in events)
        if BACKEND == "wazuh":
            url = "https://127.0.0.1:9200/_bulk"
            headers = {"Authorization": basic("admin", "SecretPassword")}
        else:
            url = "http://127.0.0.1:9200/_bulk"
            headers = {}
        response = json.loads(request(url, body, "application/x-ndjson", headers, insecure=BACKEND == "wazuh"))
        if response.get("errors"):
            raise RuntimeError("bulk ingest reported item errors: " + json.dumps(response.get("items", [])[:2]))
    elif BACKEND == "loki":
        streams = {}
        for e in events:
            labels = (e["source"], e["host"])
            streams.setdefault(labels, []).append([str(int(e["epoch"] * 1_000_000_000)), json.dumps(e)])
        body = {"streams": [{"stream": {"source": source, "host": host}, "values": values} for (source, host), values in streams.items()]}
        request("http://127.0.0.1:3100/loki/api/v1/push", json.dumps(body))
    elif BACKEND == "openobserve":
        request("http://127.0.0.1:8080/api/default/webui_logs/_json", json.dumps(events), headers={"Authorization": basic("admin@localhost", ENV["LOGGING_PASSWORD"])})
    elif BACKEND == "victorialogs":
        body = "\n".join(json.dumps(e) for e in events) + "\n"
        request("http://127.0.0.1:9428/insert/jsonline?_time_field=timestamp&_stream_fields=source,host", body, "application/x-ndjson")
    elif BACKEND == "quickwit":
        ensure_quickwit_index()
        body = "\n".join(json.dumps(e) for e in events) + "\n"
        request("http://127.0.0.1:7280/api/v1/webui-logs/ingest", body, "application/x-ndjson")
    elif BACKEND == "graylog":
        ensure_graylog_input()
        for e in events:
            gelf = {"version": "1.1", "host": e["host"], "short_message": e["message"][:250], "full_message": e["message"], "timestamp": e["epoch"], "_source": e["source"], "_event_id": e["event_id"]}
            request("http://127.0.0.1:12201/gelf", json.dumps(gelf))
    else:
        raise RuntimeError("unsupported backend")


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
