"""ARC Hub v0.2: multi-user MCP broker + authenticated device polling.

Production deployment requires a trusted HTTPS reverse proxy. Gateway binds to
loopback by default. Bearer tokens are SHA-256 hashed in storage. Every MCP call
is authorized for the authenticated user AND the target device.
"""
from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import queue
import secrets
import threading
import time
import uuid
from urllib.parse import parse_qs, urlsplit
from arc_web import WebState, handle_get as web_get, handle_post as web_post

ROLE_SCOPES = {
    "viewer": {"status"},
    "operator": {"status", "inspect", "files.read", "screen.read", "apps.launch", "input.control"},
    "admin": {"status", "inspect", "files.read", "screen.read", "apps.launch",
              "input.control", "files.write", "shell.exec"},
}
TOOL_SCOPE = {
    "computer_status": "status", "list_windows": "inspect",
    "list_processes": "inspect", "list_directory": "files.read",
    "file_info": "files.read", "search_files": "files.read",
    "read_text_file": "files.read", "capture_screen": "screen.read",
    "open_notepad": "apps.launch", "open_application": "apps.launch",
    "click_mouse": "input.control", "type_text": "input.control",
    "hotkey": "input.control", "scroll_mouse": "input.control",
    "move_mouse": "input.control", "focus_window": "input.control",
    "write_file": "files.write", "edit_file": "files.write",
    "create_directory": "files.write", "move_path": "files.write",
    "delete_file": "files.write", "run_command": "shell.exec",
    "start_process": "shell.exec", "read_process_output": "shell.exec",
    "send_process_input": "shell.exec", "stop_process": "shell.exec",
}
READ_ONLY = {"status", "inspect", "files.read", "screen.read"}
CONTROL_SCOPES = {"apps.launch", "input.control", "files.write", "shell.exec"}
LEASE_SECONDS = 90
MAX_BODY = 12_000_000
MAX_MCP_RESPONSE = 10_000_000


def digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def schema(name, description, props, required=()):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": props,
                            "required": list(required), "additionalProperties": False},
            "annotations": {"readOnlyHint": name in ("arc_devices", "arc_permissions")}}


TOOLS = [
    schema("arc_devices", "List computers this signed-in user is authorized to access.", {}),
    schema("arc_permissions", "Show granted tools for one explicitly identified computer.",
           {"device_id": {"type": "string"}}, ["device_id"]),
    schema("arc_take_control", "Take temporary exclusive control of a permitted device for changes and input.",
           {"device_id": {"type": "string"}}, ["device_id"]),
    schema("arc_release_control", "Release your exclusive control of a device.",
           {"device_id": {"type": "string"}}, ["device_id"]),
    schema("arc_call", "Call a device tool on one explicitly identified computer with user-scoped authorization.",
           {"device_id": {"type": "string"}, "tool": {"type": "string"},
            "arguments": {"type": "object"}},
           ["device_id", "tool", "arguments"]),
]


class Hub:
    def __init__(self, config: dict, audit_path: Path | None = None):
        if config.get("version") != 2:
            raise ValueError("ARC Hub registry version 2 required")
        self.users = config["users"]
        self.devices = config["devices"]
        self.sanitize_registry()
        self.queues = {key: queue.Queue(maxsize=32) for key in self.devices}
        self.seen = {}
        self.pending = {}
        self.leases = {}
        self.lock = threading.Lock()
        self.audit_path = audit_path
        self.log = logging.getLogger("arc.hub.audit")
        if audit_path:
            audit_path.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(audit_path, maxBytes=4_000_000,
                                          backupCount=6, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(message)s"))
            self.log.setLevel(logging.INFO)
            self.log.addHandler(handler)

    def sanitize_registry(self):
        for ident, d in self.devices.items():
            if not isinstance(d.get("token_hash"), str) or len(d["token_hash"]) != 64:
                raise ValueError("device missing hashed credential")
            if not isinstance(d.get("allowed_scopes"), list):
                raise ValueError("device must provide allowed_scopes")
            if set(d["allowed_scopes"]) - set(TOOL_SCOPE.values()):
                raise ValueError("unknown device scope")
        for name, u in self.users.items():
            if u.get("role") not in ROLE_SCOPES or len(u.get("token_hash", "")) != 64:
                raise ValueError("invalid user role or hashed credential")
            if not isinstance(u.get("grants"), dict):
                raise ValueError("user grants missing")
            for device, grant in u["grants"].items():
                if device not in self.devices or set(grant) - set(TOOL_SCOPE.values()):
                    raise ValueError("invalid user device grant")

    def authenticate_user(self, raw: str):
        candidate = digest(raw)
        result = None
        for ident, value in self.users.items():
            if hmac.compare_digest(candidate, value["token_hash"]) and value.get("enabled", True):
                result = ident
        return result

    def authenticate_device(self, ident: str, raw: str):
        dev = self.devices.get(ident)
        return bool(dev and dev.get("enabled", True) and hmac.compare_digest(
            digest(raw), dev["token_hash"]))

    def allowed(self, user: str, ident: str):
        u = self.users.get(user, {})
        d = self.devices.get(ident)
        if not d or not d.get("enabled", True) or not u.get("enabled", True):
            return set()
        return (set(u.get("grants", {}).get(ident, []))
                & set(d.get("allowed_scopes", []))
                & ROLE_SCOPES[u["role"]])

    def device_list(self, user):
        return [{"device_id": ident, "name": dev["name"],
                 "online": time.time() - self.seen.get(ident, 0) < 35,
                 "permissions": sorted(self.allowed(user, ident)),
                 "control_busy": bool(self.leases.get(ident, {}).get("expires", 0) > time.monotonic())}
                for ident, dev in sorted(self.devices.items()) if self.allowed(user, ident)]

    def device_tools(self, user, ident):
        if not self.allowed(user, ident):
            raise PermissionError("device not authorized")
        return sorted(tool for tool, scope in TOOL_SCOPE.items()
                      if scope in self.allowed(user, ident))

    def take_control(self, user, ident):
        if not (CONTROL_SCOPES & self.allowed(user, ident)):
            raise PermissionError("no control permissions on device")
        with self.lock:
            lease = self.leases.get(ident)
            if lease and lease["expires"] > time.monotonic() and lease["user"] != user:
                raise PermissionError("device controlled by another user")
            self.leases[ident] = {"user": user, "expires": time.monotonic() + LEASE_SECONDS}
        self.audit(user, ident, "arc_take_control", "ok")
        return {"device_id": ident, "exclusive": True, "expires_in_seconds": LEASE_SECONDS}

    def release_control(self, user, ident):
        with self.lock:
            lease = self.leases.get(ident)
            if lease and lease["expires"] > time.monotonic() and lease["user"] != user:
                raise PermissionError("device controlled by another user")
            self.leases.pop(ident, None)
        self.audit(user, ident, "arc_release_control", "ok")
        return {"device_id": ident, "released": True}

    def poll(self, ident):
        with self.lock:
            self.seen[ident] = time.time()
        try:
            return self.queues[ident].get(timeout=8)
        except queue.Empty:
            return None

    def result(self, ident, data):
        task_id = data.get("task_id")
        with self.lock:
            pending = self.pending.get(task_id)
            if not pending or pending["device"] != ident or pending["event"].is_set():
                return False
            pending["result"] = data.get("result")
            pending["error"] = data.get("error")
            pending["event"].set()
        return True

    def audit(self, user, ident, tool, outcome):
        # Deliberately excludes arguments and tool results (secrets/PII).
        self.log.info(json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(),
                                  "user": user, "device": ident, "tool": tool,
                                  "outcome": outcome}, ensure_ascii=False))

    def dispatch(self, user, ident, tool, arguments):
        if not isinstance(ident, str) or not isinstance(tool, str):
            raise ValueError("device_id and tool must be strings")
        scope = TOOL_SCOPE.get(tool)
        if not scope or scope not in self.allowed(user, ident):
            self.audit(user, ident, tool, "denied")
            raise PermissionError("tool not authorized for this user/device")
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        if scope in CONTROL_SCOPES:
            with self.lock:
                lease = self.leases.get(ident)
                if not lease or lease["user"] != user or lease["expires"] <= time.monotonic():
                    self.audit(user, ident, tool, "exclusive_control_required")
                    raise PermissionError("take exclusive device control before modifying or sending input")
                lease["expires"] = time.monotonic() + LEASE_SECONDS
        if time.time() - self.seen.get(ident, 0) > 35:
            raise RuntimeError("device offline")
        task_id = uuid.uuid4().hex
        event = threading.Event()
        with self.lock:
            self.pending[task_id] = {"device": ident, "event": event,
                                      "result": None, "error": None}
        try:
            self.queues[ident].put_nowait({
                "task_id": task_id, "tool": tool, "arguments": arguments})
            if not event.wait(25):
                raise TimeoutError("device timeout")
            with self.lock:
                record = self.pending[task_id]
                result, error = record["result"], record["error"]
            if error:
                raise RuntimeError(str(error)[:180])
            if len(json.dumps(result, ensure_ascii=False)) > MAX_MCP_RESPONSE:
                raise ValueError("tool result too large")
            self.audit(user, ident, tool, "ok")
            return result
        except Exception as exc:
            self.audit(user, ident, tool, type(exc).__name__)
            raise
        finally:
            with self.lock:
                self.pending.pop(task_id, None)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64

    def __init__(self, address, hub, registry_path=None):
        super().__init__(address, Handler)
        self.hub = hub
        self.web = WebState(hub, registry_path)


class Handler(BaseHTTPRequestHandler):
    server: Server
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def send_obj(self, status, obj=None):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8") if obj is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if data:
            try:
                self.wfile.write(data)
            except (OSError, BrokenPipeError):
                pass

    def json_body(self):
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            raise ValueError("Content-Type must be application/json")
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError as exc:
            raise ValueError("Content-Length required") from exc
        if not 0 <= length <= MAX_BODY:
            raise ValueError("request size invalid")
        return json.loads(self.rfile.read(length))

    def bearer_user(self):
        hdr = self.headers.get("Authorization", "")
        if not hdr.startswith("Bearer "):
            return None
        return self.server.hub.authenticate_user(hdr[7:])

    def device_id(self):
        return parse_qs(urlsplit(self.path).query).get("device_id", [""])[0]

    def check_agent(self):
        return self.server.hub.authenticate_device(
            self.device_id(), self.headers.get("X-ARC-Device-Token", ""))

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/" or path.startswith("/web"):
            return web_get(self, path)
        if path == "/healthz":
            return self.send_obj(200, {"status": "ok", "service": "arc-hub"})
        if path != "/agent/poll":
            return self.send_obj(404, {"error": "not found"})
        if not self.check_agent():
            return self.send_obj(401, {"error": "unauthorized"})
        return self.send_obj(200, {"task": self.server.hub.poll(self.device_id())})

    def do_POST(self):
        path = urlsplit(self.path).path
        if path.startswith("/web/api/"):
            return web_post(self, path)
        if self.headers.get("Origin") or self.headers.get("Sec-Fetch-Site") in ("same-site", "cross-site"):
            return self.send_obj(403, {"error": "browser origin rejected"})
        path = urlsplit(self.path).path
        if path == "/agent/enroll":
            try:
                length = int(self.headers.get("Content-Length", "-1"))
                if not 0 < length <= 4096:
                    return self.send_obj(400, {"error": "invalid request size"})
                payload = self.json_body()
                result = self.server.web.pairing.accept(payload)
                return self.send_obj(201, result)
            except PermissionError as exc:
                return self.send_obj(403, {"error": str(exc)[:150]})
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                return self.send_obj(400, {"error": str(exc)[:150]})
            except (OSError, RuntimeError):
                return self.send_obj(503, {"error": "pairing temporarily unavailable"})
        if path == "/agent/result":
            if not self.check_agent():
                return self.send_obj(401, {"error": "unauthorized"})
            try:
                payload = self.json_body()
                if not isinstance(payload, dict):
                    raise ValueError("object expected")
                accepted = self.server.hub.result(self.device_id(), payload)
                return self.send_obj(200 if accepted else 404, {"accepted": accepted})
            except (ValueError, json.JSONDecodeError):
                return self.send_obj(400, {"error": "invalid request"})
        if path != "/mcp":
            return self.send_obj(404, {"error": "not found"})
        principal = self.bearer_user()
        if principal is None:
            return self.send_obj(401, {"error": "unauthorized"})
        try:
            payload = self.json_body()
            if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
                raise ValueError("JSON-RPC 2.0 required")
            method = payload.get("method")
            ident = payload.get("id")
            if method == "notifications/initialized":
                return self.send_obj(202)
            if method == "initialize":
                out = {"protocolVersion": "2025-03-26",
                       "serverInfo": {"name": "arc", "version": "0.2.0"},
                       "capabilities": {"tools": {"listChanged": False}}}
            elif method == "ping":
                out = {}
            elif method == "tools/list":
                out = {"tools": TOOLS}
            elif method == "tools/call":
                params = payload.get("params") or {}
                args = params.get("arguments") or {}
                if not isinstance(params, dict) or not isinstance(args, dict):
                    raise ValueError("invalid parameters")
                name = params.get("name")
                if name == "arc_devices":
                    result = self.server.hub.device_list(principal)
                elif name == "arc_permissions":
                    result = {"device_id": args.get("device_id"),
                              "tools": self.server.hub.device_tools(
                                  principal, args.get("device_id"))}
                elif name == "arc_take_control":
                    result = self.server.hub.take_control(principal, args.get("device_id"))
                elif name == "arc_release_control":
                    result = self.server.hub.release_control(principal, args.get("device_id"))
                elif name == "arc_call":
                    result = self.server.hub.dispatch(
                        principal, args.get("device_id"), args.get("tool"),
                        args.get("arguments", {}))
                else:
                    raise ValueError("unknown tool")
                out = {"content": [{"type": "text",
                                    "text": json.dumps(result, ensure_ascii=False)}]}
            else:
                return self.send_obj(200, {"jsonrpc": "2.0", "id": ident,
                                           "error": {"code": -32601,
                                                     "message": "method not found"}})
            return self.send_obj(200, {"jsonrpc": "2.0", "id": ident, "result": out})
        except (ValueError, PermissionError, RuntimeError, TimeoutError, TypeError) as exc:
            return self.send_obj(200, {"jsonrpc": "2.0", "id": payload.get("id") if
                isinstance(locals().get("payload"), dict) else None,
                "result": {"content": [{"type": "text", "text": str(exc)[:200]}],
                           "isError": True}})
        except (json.JSONDecodeError, OverflowError):
            return self.send_obj(400, {"error": "invalid JSON"})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8877)
    parser.add_argument("--audit", type=Path)
    args = parser.parse_args()
    # No accidental public listener without an explicit proxy deployment review.
    if args.bind not in ("127.0.0.1", "::1"):
        parser.error("ARC Hub must bind to loopback; place a secured HTTPS proxy in front")
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    hub = Hub(cfg, args.audit)
    server = Server((args.bind, args.port), hub, args.config)
    print("ARC_HUB_READY bind=%s port=%d" % (args.bind, args.port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
