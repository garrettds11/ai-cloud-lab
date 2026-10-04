"""Run the MCP server on your own computer for development and testing.

    $env:AWS_PROFILE = "your-profile"; $env:AWS_DEFAULT_REGION = "us-east-1"
    python .\\lambda\\vuln_mcp\\local_server.py

Then point an MCP client (such as the MCP Inspector) at http://127.0.0.1:8765/mcp using
the "Streamable HTTP" transport. No token is needed unless you set AUTH_TOKEN.
This is for development only; in AWS the Lambda Function URL calls mcp_handler directly.
"""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import mcp_handler


class Handler(BaseHTTPRequestHandler):
    def _serve(self):
        length = int(self.headers.get("content-length") or 0)
        event = {
            "requestContext": {"http": {"method": self.command, "path": self.path.split("?")[0]}},
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": self.rfile.read(length).decode("utf-8") if length else "",
        }
        result = mcp_handler.lambda_handler(event)
        body = (result.get("body") or "").encode("utf-8")
        self.send_response(result["statusCode"])
        for key, value in result["headers"].items():
            self.send_header(key, value)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = do_GET = do_DELETE = _serve

    def log_message(self, fmt, *args):  # tool calls are logged by the server; keep this quiet
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    print(f"MCP server: http://127.0.0.1:{args.port}/mcp  (Ctrl+C to stop)")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
