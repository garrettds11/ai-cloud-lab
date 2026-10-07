"""AWS Lambda entry point (Lambda Function URL) for the vulnerability MCP server.

Request: POST /mcp with a JSON-RPC body and `Authorization: Bearer <token>`.
The token comes from AUTH_TOKEN or, in AWS, from the Secrets Manager secret named by
AUTH_TOKEN_SECRET_ARN. Inside Lambda the server refuses every request if no token is
configured (fail closed). Run locally with local_server.py, which needs no token.
"""

import base64
import hmac
import json
import os

import mcp_protocol
from vuln_tools import VulnTools

MAX_BODY_BYTES = 100_000
_JSON = {"content-type": "application/json"}

_tools = None
_token_cache = None


def _get_tools():
    global _tools
    if _tools is None:
        _tools = VulnTools()
    return _tools


def _secret_text(secret_string):
    """Same rules as the lab's bootstrap: plain text, or a one-value key/value JSON secret."""
    try:
        parsed = json.loads(secret_string)
    except ValueError:
        return secret_string
    if isinstance(parsed, dict) and len(parsed) == 1 and isinstance(next(iter(parsed.values())), str):
        return next(iter(parsed.values()))
    if isinstance(parsed, str):
        return parsed
    return secret_string


def _expected_token():
    global _token_cache
    token = os.environ.get("AUTH_TOKEN")
    if token:
        return token
    if _token_cache is None:
        arn = os.environ.get("AUTH_TOKEN_SECRET_ARN")
        if not arn:
            return None
        import boto3

        value = boto3.client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"]
        _token_cache = _secret_text(value).strip()
    return _token_cache


def _auth_required():
    return bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME")) or os.environ.get("REQUIRE_AUTH") == "1"


def _response(status, body="", headers=None):
    return {"statusCode": status, "headers": headers or _JSON, "body": body}


def _authorize(headers):
    """Returns an error response, or None when the caller may continue."""
    try:
        expected = _expected_token()
    except Exception as error:
        print(json.dumps({"event": "auth_config_error", "error": type(error).__name__}))
        return _response(500, json.dumps({"error": "Server is not configured"}))
    if not expected:
        if _auth_required():
            print(json.dumps({"event": "auth_config_error", "error": "no token configured"}))
            return _response(500, json.dumps({"error": "Server is not configured"}))
        return None
    supplied = headers.get("authorization", "")
    scheme, _, value = supplied.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(value.strip().encode(), expected.encode()):
        return _response(401, json.dumps({"error": "Unauthorized"}), {**_JSON, "www-authenticate": "Bearer"})
    return None


def lambda_handler(event, context=None):
    http = (event.get("requestContext") or {}).get("http") or {}
    method = (http.get("method") or "POST").upper()
    path = (http.get("path") or event.get("rawPath") or "/").rstrip("/") or "/"
    headers = {str(k).lower(): v for k, v in (event.get("headers") or {}).items()}

    denied = _authorize(headers)
    if denied:
        return denied
    if path not in ("/", "/mcp"):
        return _response(404, json.dumps({"error": "Not found"}))
    if method != "POST":  # no server-initiated stream and no sessions to terminate
        return _response(405, json.dumps({"error": "Use POST"}), {**_JSON, "allow": "POST"})

    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8", errors="replace")
    if len(body) > MAX_BODY_BYTES:
        return _response(413, json.dumps({"error": "Request too large"}))

    status, text = mcp_protocol.handle_body(body, _get_tools())
    return _response(status, text)
