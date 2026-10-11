"""A small, stateless Model Context Protocol (MCP) server core, with no dependencies.

Implements the parts of the streamable HTTP transport that a tools-only server needs:
initialize, ping, tools/list and tools/call, with plain JSON responses and no sessions.
That is what keeps it deployable as one Lambda zip. The tool logic lives in vuln_tools.py,
so this layer can be swapped for a full MCP SDK later without touching the tools.
"""

import json
import time

from vuln_tools import TOOL_NAMES, TOOLS, ToolError

SERVER_INFO = {"name": "vuln-findings", "version": "0.1.0"}
SUPPORTED_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
INSTRUCTIONS = (
    "Read-only access to vulnerability scan findings. "
    "Call get_data_dictionary first if you are unsure which hosts or fields exist."
)

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _result(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _log(**fields):
    print(json.dumps({"event": "tool_call", **fields}, default=str))


class Toolset:
    """Several tool objects served as one server. Each part is (object, definitions); a tool is
    found by name on the object that defines it, so the protocol layer needs no change per source."""

    def __init__(self, *parts):
        self._owner = {}
        self.definitions = []
        for owner, definitions in parts:
            for definition in definitions:
                self._owner[definition["name"]] = owner
                self.definitions.append(definition)
        self.names = set(self._owner)

    def __getattr__(self, name):
        owner = self.__dict__.get("_owner", {}).get(name)
        if owner is None:
            raise AttributeError(name)
        return getattr(owner, name)


def handle_message(message, tools):
    """Handle one JSON-RPC message. Returns a response dict, or None for notifications."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or "method" not in message:
        return _error(message.get("id") if isinstance(message, dict) else None, INVALID_REQUEST, "Invalid JSON-RPC request")

    method = message["method"]
    request_id = message.get("id")
    params = message.get("params") or {}

    if "id" not in message:
        return None  # notifications/initialized, notifications/cancelled, and so on

    if method == "initialize":
        asked = params.get("protocolVersion")
        version = asked if asked in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
        return _result(request_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": INSTRUCTIONS,
        })
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": getattr(tools, "definitions", TOOLS)})
    if method == "tools/call":
        return _call_tool(request_id, params, tools)
    return _error(request_id, METHOD_NOT_FOUND, f"Method not found: {method}")


def _call_tool(request_id, params, tools):
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if name not in getattr(tools, "names", TOOL_NAMES):
        return _error(request_id, INVALID_PARAMS, f"Unknown tool: {name}")
    if not isinstance(arguments, dict):
        return _error(request_id, INVALID_PARAMS, "arguments must be an object")

    started = time.monotonic()
    failed = False
    try:
        text = json.dumps(getattr(tools, name)(**arguments), separators=(",", ":"), default=str)
    except ToolError as error:
        failed, text = True, str(error)
    except TypeError as error:  # wrong or unexpected argument names
        failed, text = True, f"Invalid arguments for {name}: {error}"
    except Exception as error:  # never leak internals to the model; the log has the type
        _log(tool=name, arguments=arguments, ok=False, error=type(error).__name__)
        text = f"{name} failed. Try again, or ask for a different query."
        return _result(request_id, {"content": [{"type": "text", "text": text}], "isError": True})

    _log(tool=name, arguments=arguments, ok=not failed, result_bytes=len(text), ms=round((time.monotonic() - started) * 1000))
    result = {"content": [{"type": "text", "text": text}]}
    if failed:
        result["isError"] = True
    return _result(request_id, result)


def handle_body(raw_body, tools):
    """Parse a request body (one message or a batch). Returns (status_code, body_text)."""
    try:
        payload = json.loads(raw_body)
    except (TypeError, ValueError):
        return 400, json.dumps(_error(None, PARSE_ERROR, "Parse error"))

    if isinstance(payload, list):
        if not payload:
            return 400, json.dumps(_error(None, INVALID_REQUEST, "Empty batch"))
        responses = [r for r in (handle_message(m, tools) for m in payload) if r is not None]
        return (202, "") if not responses else (200, json.dumps(responses))

    response = handle_message(payload, tools)
    return (202, "") if response is None else (200, json.dumps(response))
