#!/usr/bin/env python3
"""Read-only local Windows MCP over stdio: no TCP listener, no VDS."""
import argparse
import base64
import json
import os
from pathlib import Path
import socket
import sys

from arc_local import screen_png, screen_size, visible_windows

VERSION = "0.2.0-stdio-readonly"


def definition(name, description, props=None, required=None):
    return {"name": name, "description": description, "inputSchema": {
        "type": "object", "properties": props or {},
        "required": required or [], "additionalProperties": False}}


class LocalAgent:
    def __init__(self, roots):
        self.roots = [Path(p).expanduser().resolve(strict=True) for p in roots]
        if not all(p.is_dir() for p in self.roots):
            raise ValueError("All allowed roots must be directories")

    def secure_path(self, path):
        if not isinstance(path, str) or not path or len(path) > 2048:
            raise ValueError("Invalid path")
        raw = Path(path)
        if not raw.is_absolute():
            raise ValueError("Absolute path required")
        p = raw.resolve(strict=True)
        if not any(p == root or root in p.parents for root in self.roots):
            raise PermissionError("Path is outside allowed roots")
        return p

    def tools(self):
        path = {"type": "string", "maxLength": 2048}
        return [
            definition("computer_status", "Local Windows computer status, read-only."),
            definition("list_windows", "List visible desktop windows, read-only."),
            definition("capture_screen", "Desktop screenshot as PNG, read-only."),
            definition("list_directory", "List up to 200 entries in an allowed directory.",
                       {"path": path}, ["path"]),
            definition("read_text_file", "Read up to 96 KiB of a UTF-8 file in an allowed directory.",
                       {"path": path}, ["path"]),
        ]

    def call(self, name, args):
        catalog = {item["name"]: item for item in self.tools()}
        if name not in catalog or not isinstance(args, dict):
            raise ValueError("Unknown tool or invalid arguments")
        schema = catalog[name]["inputSchema"]
        if set(args) - set(schema["properties"]) or set(schema["required"]) - set(args):
            raise ValueError("Invalid tool arguments")
        if name == "computer_status":
            w, h = screen_size()
            return {"device": socket.gethostname(), "windows": True, "screen": {
                "width": w, "height": h}, "central_server": False}
        if name == "list_windows":
            return {"windows": visible_windows()}
        if name == "capture_screen":
            return {"image": base64.b64encode(screen_png()).decode("ascii")}
        path = self.secure_path(args["path"])
        if name == "list_directory":
            if not path.is_dir():
                raise ValueError("Not a directory")
            result = []
            for entry in path.iterdir():
                if len(result) == 200:
                    break
                result.append({"name": entry.name, "is_dir": entry.is_dir()})
            return {"entries": result, "limit": 200}
        if name == "read_text_file":
            if not path.is_file() or path.stat().st_size > 98304:
                raise ValueError("Invalid file or file too large")
            return {"content": path.read_text(encoding="utf-8")}
        raise ValueError("Unknown tool")

    def handle(self, request):
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
            raise ValueError("JSON-RPC 2.0 required")
        method = request.get("method")
        if method == "initialize":
            return {"protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "arc-local-stdio", "version": VERSION}}
        if method == "tools/list":
            return {"tools": self.tools()}
        if method == "ping":
            return {}
        if method == "tools/call":
            params = request.get("params")
            if not isinstance(params, dict):
                raise ValueError("Invalid tool call")
            name = params.get("name")
            output = self.call(name, params.get("arguments", {}))
            if name == "capture_screen":
                return {"content": [{"type": "image", "mimeType": "image/png",
                                     "data": output["image"]}]}
            return {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}]}
        raise LookupError("Method not found")


def serve(agent):
    # Standard MCP stdio framing: one JSON-RPC object per line.
    for wire in sys.stdin.buffer:
        ident = None
        try:
            if len(wire) > 65536:
                continue
            msg = json.loads(wire)
            if isinstance(msg, dict) and "id" not in msg:
                continue
            ident = msg.get("id") if isinstance(msg, dict) else None
            response = {"jsonrpc": "2.0", "id": ident, "result": agent.handle(msg)}
        except LookupError as exc:
            response = {"jsonrpc": "2.0", "id": ident,
                        "error": {"code": -32601, "message": str(exc)}}
        except (ValueError, OSError, PermissionError, TypeError) as exc:
            response = {"jsonrpc": "2.0", "id": ident,
                        "error": {"code": -32602, "message": str(exc)}}
        except Exception:
            response = {"jsonrpc": "2.0", "id": ident,
                        "error": {"code": -32603, "message": "Operation failed"}}
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-root", action="append", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Windows only")
    agent = LocalAgent(args.allow_root or [Path.home()])
    if args.self_test:
        assert agent.handle({"jsonrpc": "2.0", "method": "ping"}) == {}
        assert len(agent.tools()) == 5
        assert agent.call("computer_status", {})["central_server"] is False
        assert isinstance(agent.call("list_windows", {})["windows"], list)
        assert agent.call("capture_screen", {})["image"]
        print("STDIO_READ_ONLY_OK tools=" + ",".join(t["name"] for t in agent.tools()))
    else:
        serve(agent)


if __name__ == "__main__":
    main()
