#!/usr/bin/env python3
"""ARC Lite: one-file Windows MCP stdio agent. No VDS, ports or dependencies.

The safe default is read-only access to a dedicated shared directory.
Use --enable-apps to permit the single allowlisted Notepad action.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import re
import socket
import subprocess
import sys

VERSION = "0.2.0-full"
MAX_REQUEST = 65536
MAX_TEXT = 65536
REQUEST_ID = re.compile(r"^[a-zA-Z0-9_-]{8,64}$")


def definition(name, description, properties=None, required=None):
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties or {},
            "required": required or [],
            "additionalProperties": False,
        },
    }


class Agent:
    def __init__(self, shared: Path, enable_apps: bool = False):
        shared.mkdir(parents=True, exist_ok=True)
        self.shared = shared.resolve(strict=True)
        if not self.shared.is_dir():
            raise ValueError("Shared path must be a directory")
        self.enable_apps = enable_apps
        self.launched = {}

    def tools(self):
        tools = [
            definition("pc_status", "Read this Windows computer's status."),
            definition("list_shared_files", "List files in the dedicated ARC Lite shared folder."),
            definition(
                "read_shared_text",
                "Read a UTF-8 text file from the dedicated shared folder (max 64 KiB).",
                {"name": {"type": "string", "maxLength": 200}},
                ["name"],
            ),
        ]
        if self.enable_apps:
            tools.append(definition(
                "open_notepad",
                "Open Windows Notepad in the logged-in user's session. Repeated request_id has no effect.",
                {"request_id": {"type": "string", "minLength": 8, "maxLength": 64}},
                ["request_id"],
            ))
        return tools

    def safe_shared_file(self, name):
        if not isinstance(name, str) or not name or len(name) > 200:
            raise ValueError("Invalid file name")
        if Path(name).is_absolute() or ":" in name or "\x00" in name:
            raise ValueError("Absolute or unsafe path")
        target = (self.shared / name).resolve(strict=True)
        if not target.is_relative_to(self.shared):
            raise PermissionError("Outside ARC Lite shared folder")
        if not target.is_file():
            raise ValueError("Not a regular file")
        return target

    def call(self, name, args):
        specs = {tool["name"]: tool["inputSchema"] for tool in self.tools()}
        if name not in specs:
            raise ValueError("Tool is not enabled")
        if not isinstance(args, dict):
            raise ValueError("Tool arguments must be an object")
        spec = specs[name]
        if set(args) - set(spec["properties"]) or set(spec["required"]) - set(args):
            raise ValueError("Unsupported or missing arguments")

        if name == "pc_status":
            return {"online": True, "hostname": socket.gethostname(),
                    "os": platform.system(), "release": platform.release(),
                    "mode": "local_stdio", "vds_required": False}
        if name == "list_shared_files":
            result = []
            for entry in sorted(self.shared.iterdir(), key=lambda p: p.name.lower()):
                if len(result) >= 100:
                    break
                result.append({"name": entry.name, "is_file": entry.is_file()})
            return {"files": result, "limit": 100}
        if name == "read_shared_text":
            path = self.safe_shared_file(args["name"])
            if path.stat().st_size > MAX_TEXT:
                raise ValueError("Text file exceeds 64 KiB")
            return {"name": path.name, "text": path.read_text(encoding="utf-8")}
        if name == "open_notepad":
            key = args["request_id"]
            if not isinstance(key, str) or not REQUEST_ID.fullmatch(key):
                raise ValueError("Invalid request_id")
            if key in self.launched:
                return {"launched": True, "pid": self.launched[key], "replayed": True}
            if os.name != "nt":
                raise RuntimeError("Notepad is available only on Windows")
            executable = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "notepad.exe"
            if not executable.is_file():
                raise RuntimeError("Windows Notepad executable was not found")
            process = subprocess.Popen([str(executable)], close_fds=True)
            self.launched[key] = process.pid
            return {"launched": True, "pid": process.pid, "replayed": False}
        raise ValueError("Unknown tool")

    def handle(self, request):
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
            raise ValueError("JSON-RPC 2.0 required")
        method = request.get("method")
        if method == "initialize":
            return {"protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "arc-lite", "version": VERSION}}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": self.tools()}
        if method == "tools/call":
            params = request.get("params")
            if not isinstance(params, dict):
                raise ValueError("Invalid tools/call")
            name = params.get("name")
            output = self.call(name, params.get("arguments") or {})
            if name == "capture_screen":
                return {"content": [{"type": "image", "mimeType": "image/png",
                                     "data": output["image"]}]}
            return {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}]}
        raise LookupError("Unknown MCP method")




def fixed_drive_roots():
    """Return only connected, fixed Windows drives (not network or USB removable)."""
    if os.name != "nt":
        return [Path.home()]
    import ctypes
    import string
    drive_type = ctypes.windll.kernel32.GetDriveTypeW
    drive_type.argtypes = [ctypes.c_wchar_p]
    drive_type.restype = ctypes.c_uint
    roots = []
    for letter in string.ascii_uppercase:
        path = Path(letter + ":\\")
        if path.exists() and drive_type(str(path)) == 3:  # DRIVE_FIXED
            roots.append(path)
    return roots or [Path.home()]


class FullAccessAgent(Agent):
    """Single-process stdio wrapper around the existing Windows tools.

    Uses the signed-in user's own permissions and only explicit local MCP calls.
    No TCP, VDS, OAuth, tunnel, install service, or privilege escalation.
    """

    def __init__(self, shared: Path, roots=None):
        if os.name != "nt":
            raise RuntimeError("Full access is supported on Windows only")
        super().__init__(shared, enable_apps=False)
        modules = Path(__file__).resolve().parent / "windows_full"
        if not (modules / "arc_full_ops.py").is_file():
            raise FileNotFoundError("Full backend is missing: windows_full/arc_full_ops.py")
        if str(modules) not in sys.path:
            sys.path.insert(0, str(modules))
        # GitHub checkout uses the existing Windows GUI library. The local Asus
        # workspace has its slim stdio-only copy inside windows_full instead.
        fallback = Path(__file__).resolve().parent.parent / "windows"
        if not (modules / "arc_local.py").is_file() and fallback.is_dir():
            sys.path.append(str(fallback))
        from arc_full_ops import FullAgent
        chosen = roots if roots else fixed_drive_roots()
        self.roots = [Path(root).expanduser().resolve(strict=True) for root in chosen]
        self.backend = FullAgent(self.roots, screen=True, apps=True, user_input=True,
                                 write=True, shell=True)

    def tools(self):
        basic = super().tools()
        return basic + self.backend.tools()

    def call(self, name, args):
        if name in ("pc_status", "list_shared_files", "read_shared_text"):
            return super().call(name, args)
        return self.backend.call(name, args)

def serve(agent):
    for wire in sys.stdin.buffer:
        ident = None
        try:
            if len(wire) > MAX_REQUEST:
                raise ValueError("MCP request too large")
            request = json.loads(wire)
            if isinstance(request, dict) and "id" not in request:
                continue  # MCP notifications have no response.
            ident = request.get("id") if isinstance(request, dict) else None
            response = {"jsonrpc": "2.0", "id": ident, "result": agent.handle(request)}
        except LookupError:
            response = {"jsonrpc": "2.0", "id": ident,
                        "error": {"code": -32601, "message": "Method not found"}}
        except (ValueError, TypeError, PermissionError, OSError, UnicodeError) as exc:
            response = {"jsonrpc": "2.0", "id": ident,
                        "error": {"code": -32602, "message": str(exc)}}
        except Exception:
            response = {"jsonrpc": "2.0", "id": ident,
                        "error": {"code": -32603, "message": "Operation failed"}}
        sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    default_shared = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ARC-Lite" / "shared"
    parser.add_argument("--shared", type=Path, default=default_shared)
    parser.add_argument("--enable-apps", action="store_true",
                        help="Permit the limited Notepad action in safe mode")
    parser.add_argument("--full-access", action="store_true",
                        help="Enable Windows desktop, files, processes and PowerShell (current user)")
    parser.add_argument("--allow-root", action="append", type=Path,
                        help="Limit full-access file operations to this folder; repeat as needed")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    agent = (FullAccessAgent(args.shared, roots=args.allow_root)
             if args.full_access else Agent(args.shared, enable_apps=args.enable_apps))
    if args.self_test:
        assert agent.call("pc_status", {})["online"] is True
        names = {tool["name"] for tool in agent.tools()}
        assert ("open_notepad" in names) == (args.enable_apps or args.full_access)
        assert ("run_command" in names) == args.full_access
        assert ("capture_screen" in names) == args.full_access
        print("ARC_LITE_SELF_TEST_OK tools=" + ",".join(sorted(names)))
        if args.full_access:
            print("ARC_LITE_FULL_ACCESS_READY; roots=" + ",".join(map(str, agent.roots)))
        return
    serve(agent)


if __name__ == "__main__":
    main()
