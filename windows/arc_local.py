#!/usr/bin/env python3
"""AI Remote Commander Local — experimental Windows MCP endpoint.

No VDS, administrator account, remote shell or system service required.
Run in the logged-in user's interactive Windows session.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
from ctypes import wintypes
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import socket
import struct
import subprocess
import sys
import threading
import urllib.request
import zlib

TOOL_VERSION = "0.1.0-local-experiment"
TOKEN_FILE = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AI Remote Commander Local" / "mcp-token"
REQUEST_RE = re.compile(r"^[A-Za-z0-9_-]{12,64}$")


def token_from_disk(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        # x creates the token file exclusively; never replace an existing token.
        try:
            with path.open("x", encoding="ascii") as handle:
                handle.write(secrets.token_urlsafe(48))
            os.chmod(path, 0o600)
        except FileExistsError:
            pass
    value = path.read_text(encoding="ascii").strip()
    if len(value) < 48:
        raise RuntimeError("local token is too short; refusing to start")
    return value


def windows_api():
    if os.name != "nt":
        raise RuntimeError("this pilot supports interactive Windows only")
    return ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("gdi32", use_last_error=True)


def screen_size() -> tuple[int, int]:
    user32, _ = windows_api()
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def visible_windows() -> list[dict]:
    user32, _ = windows_api()
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HWND, wintypes.LPARAM)
    output = []

    @callback_type
    def inspect(hwnd, _):
        if len(output) >= 30:
            return 0
        if user32.IsWindowVisible(hwnd):
            length = min(user32.GetWindowTextLengthW(hwnd), 256)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if buf.value.strip():
                    output.append({"title": buf.value})
        return 1

    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows(inspect, 0)
    return output


def png_chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack("!I", len(data)) + tag + data + struct.pack("!I", zlib.crc32(tag + data) & 0xffffffff)


def screen_png(max_width: int = 960) -> bytes:
    """Capture and downscale the current interactive desktop in memory."""
    user32, gdi32 = windows_api()
    sw, sh = screen_size()
    if sw < 1 or sh < 1:
        raise RuntimeError("no interactive display")
    width = min(sw, max_width)
    height = max(1, round(sh * width / sw))

    class BitmapInfoHeader(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    class BitmapInfo(ctypes.Structure):
        _fields_ = [("bmiHeader", BitmapInfoHeader), ("bmiColors", wintypes.DWORD * 1)]

    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(BitmapInfo), wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.StretchBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    info = BitmapInfo()
    info.bmiHeader.biSize = ctypes.sizeof(BitmapInfoHeader)
    info.bmiHeader.biWidth = width
    info.bmiHeader.biHeight = -height  # top-down BGRA
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = 0
    bits = ctypes.c_void_p()
    desktop = user32.GetDC(None)
    if not desktop:
        raise RuntimeError("no screen device context")
    memory = None
    bitmap = None
    old = None
    try:
        memory = gdi32.CreateCompatibleDC(desktop)
        if not memory:
            raise RuntimeError("cannot create capture context")
        bitmap = gdi32.CreateDIBSection(desktop, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        if not bitmap or not bits.value:
            raise RuntimeError("cannot allocate capture bitmap")
        old = gdi32.SelectObject(memory, bitmap)
        if not old:
            raise RuntimeError("cannot select capture bitmap")
        # Stretch desktop into a bounded-size bitmap; CAPTUREBLT includes layered windows.
        if not gdi32.StretchBlt(memory, 0, 0, width, height, desktop, 0, 0, sw, sh, 0x40CC0020):
            raise RuntimeError("screen capture failed")
        raw = ctypes.string_at(bits, width * height * 4)
    finally:
        if old:
            gdi32.SelectObject(memory, old)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory:
            gdi32.DeleteDC(memory)
        user32.ReleaseDC(None, desktop)
    data = bytearray()
    for y in range(height):
        row = raw[y * width * 4 : (y + 1) * width * 4]
        rgb = bytearray(width * 3)
        rgb[0::3] = row[2::4]
        rgb[1::3] = row[1::4]
        rgb[2::3] = row[0::4]
        data.extend(b"\x00")
        data.extend(rgb)
    return (b"\x89PNG\r\n\x1a\n" +
            png_chunk(b"IHDR", struct.pack("!IIBBBBB", width, height, 8, 2, 0, 0, 0)) +
            png_chunk(b"IDAT", zlib.compress(data, 6)) +
            png_chunk(b"IEND", b""))


def click_mouse(x: int, y: int, button: str):
    user32, _ = windows_api()
    w, h = screen_size()
    if not (0 <= x < w and 0 <= y < h):
        raise ValueError("coordinates outside desktop")
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    if not user32.SetCursorPos(x, y):
        raise RuntimeError("could not move cursor")
    flags = (0x0002, 0x0004) if button == "left" else (0x0008, 0x0010)
    for flag in flags:
        user32.mouse_event(flag, 0, 0, 0, 0)


def type_text(text: str):
    if not text or len(text) > 300:
        raise ValueError("text must contain 1–300 characters")
    user32, _ = windows_api()
    ulong_ptr = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

    class KeybdInput(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ulong_ptr)]

    class MouseInput(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ulong_ptr)]

    class HardwareInput(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                    ("wParamH", wintypes.WORD)]

    class InputUnion(ctypes.Union):
        _fields_ = [("mi", MouseInput), ("ki", KeybdInput), ("hi", HardwareInput)]

    class Input(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("payload", InputUnion)]

    units = struct.unpack("<" + "H" * (len(text.encode("utf-16-le")) // 2), text.encode("utf-16-le"))
    events = []
    for unit in units:
        for flag in (0x0004, 0x0006):  # KEYEVENTF_UNICODE, then KEYUP
            item = Input()
            item.type = 1
            item.payload.ki = KeybdInput(0, unit, flag, 0, 0)
            events.append(item)
    array = (Input * len(events))(*events)
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    if user32.SendInput(len(events), array, ctypes.sizeof(Input)) != len(events):
        raise RuntimeError("Windows declined keyboard input")


def tool_definition(name, description, properties=None, required=None):
    return {"name": name, "description": description, "inputSchema": {
        "type": "object", "properties": properties or {}, "required": required or [],
        "additionalProperties": False}}


class LocalMCP(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, token: str, screen: bool, apps: bool, user_input: bool):
        super().__init__(address, MCPHandler)
        self.token = token
        self.screen_enabled = screen
        self.apps_enabled = apps
        self.input_enabled = user_input
        self.request_ids: dict[str, dict] = {}
        self.action_lock = threading.Lock()

    def tools(self):
        tools = [
            tool_definition("computer_status", "Status of THIS Windows desktop; read-only."),
            tool_definition("list_windows", "Visible top-level windows of THIS computer; read-only.")]
        if self.screen_enabled:
            tools.append(tool_definition("capture_screen", "Return an image of THIS desktop (may contain private information)."))
        if self.apps_enabled:
            tools.append(tool_definition("open_notepad", "Open Notepad in the current user session. No shell or arguments.",
                                         {"request_id": {"type": "string", "minLength": 12, "maxLength": 64}}, ["request_id"]))
        if self.input_enabled:
            tools.extend([
                tool_definition("click_mouse", "Click this desktop with the user's enabled input permission.",
                                {"x": {"type": "integer"}, "y": {"type": "integer"},
                                 "button": {"type": "string", "enum": ["left", "right"]}}, ["x", "y", "button"]),
                tool_definition("type_text", "Type up to 300 Unicode characters into the focused application.",
                                {"text": {"type": "string", "minLength": 1, "maxLength": 300}}, ["text"])])
        return tools

    def call_tool(self, name: str, args: dict):
        tools = {t["name"]: t for t in self.tools()}
        if name not in tools or not isinstance(args, dict):
            raise ValueError("tool unavailable")
        schema = tools[name]["inputSchema"]
        if set(args) - set(schema["properties"]) or set(schema["required"]) - set(args):
            raise ValueError("unsupported/missing parameters")
        if name == "computer_status":
            if args: raise ValueError("no arguments expected")
            w, h = screen_size()
            return {"device": socket.gethostname(), "user": os.environ.get("USERNAME", ""),
                    "os": "windows", "screen": {"width": w, "height": h},
                    "execution": "local-user", "central_vds": False}
        if name == "list_windows":
            if args: raise ValueError("no arguments expected")
            return {"windows": visible_windows()}
        if name == "capture_screen":
            if args: raise ValueError("no arguments expected")
            return {"image": base64.b64encode(screen_png()).decode("ascii")}
        if name == "open_notepad":
            key = args.get("request_id")
            if not isinstance(key, str) or not REQUEST_RE.fullmatch(key):
                raise ValueError("invalid request_id")
            with self.action_lock:
                if key not in self.request_ids:
                    process = subprocess.Popen(["notepad.exe"], close_fds=True)
                    self.request_ids[key] = {"launched": True, "pid": process.pid}
                return self.request_ids[key]
        if name == "click_mouse":
            x, y, button = args.get("x"), args.get("y"), args.get("button")
            if type(x) is not int or type(y) is not int or button not in ("left", "right"):
                raise ValueError("invalid click")
            with self.action_lock:
                click_mouse(x, y, button)
            return {"clicked": True, "x": x, "y": y, "button": button}
        if name == "type_text":
            text = args.get("text")
            if not isinstance(text, str):
                raise ValueError("invalid text")
            with self.action_lock:
                type_text(text)
            return {"typed": len(text)}
        raise ValueError("unknown tool")


class MCPHandler(BaseHTTPRequestHandler):
    server: LocalMCP

    def log_message(self, *_):
        pass  # Never log credentials, screenshots, typed text or request bodies.

    def respond(self, status: int, obj: dict | None = None):
        body = json.dumps(obj or {}, ensure_ascii=False).encode("utf-8") if obj is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        if self.path == "/healthz":
            return self.respond(200, {"status": "ok", "local_only": True})
        return self.respond(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/mcp":
            return self.respond(404, {"error": "not found"})
        # Browser-originated requests are never accepted, even on loopback.
        if self.headers.get("Origin") or self.headers.get("Sec-Fetch-Site") in ("cross-site", "same-site"):
            return self.respond(403, {"error": "browser origins are not authorized"})
        header = self.headers.get("Authorization", "")
        presented = header[7:] if header.startswith("Bearer ") else self.headers.get("X-ARC-Tunnel-Token", "")
        if not hmac.compare_digest(presented, self.server.token):
            return self.respond(401, {"error": "unauthorized"})
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return self.respond(415, {"error": "JSON required"})
        try:
            count = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            count = -1
        if not 0 <= count <= 32768:
            return self.respond(413, {"error": "invalid request size"})
        try:
            payload = json.loads(self.rfile.read(count))
            if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
                raise ValueError("invalid JSON-RPC request")
            method = payload.get("method")
            ident = payload.get("id")
            if method == "notifications/initialized":
                return self.respond(202)
            if method == "initialize":
                result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": "arc-local-windows", "version": TOOL_VERSION}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": self.server.tools()}
            elif method == "tools/call":
                params = payload.get("params")
                if not isinstance(params, dict) or not isinstance(params.get("name"), str):
                    raise ValueError("invalid tools/call")
                value = self.server.call_tool(params["name"], params.get("arguments", {}))
                if params["name"] == "capture_screen":
                    result = {"content": [{"type": "image", "mimeType": "image/png", "data": value["image"]}]}
                else:
                    result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}]}
            else:
                return self.respond(200, {"jsonrpc": "2.0", "id": ident,
                                          "error": {"code": -32601, "message": "unknown method"}})
            return self.respond(200, {"jsonrpc": "2.0", "id": ident, "result": result})
        except (ValueError, json.JSONDecodeError) as exc:
            return self.respond(200, {"jsonrpc": "2.0", "id": None,
                                      "error": {"code": -32602, "message": str(exc)}})
        except Exception:
            return self.respond(200, {"jsonrpc": "2.0", "id": None,
                                      "error": {"code": -32603, "message": "local operation failed"}})


def self_test(screen: bool, apps: bool, user_input: bool):
    server = LocalMCP(("127.0.0.1", 0), secrets.token_urlsafe(48), screen, apps, user_input)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def rpc(method, params=None, authorized=True):
        request = urllib.request.Request(
            "http://127.0.0.1:%d/mcp" % server.server_port,
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + (server.token if authorized else "wrong")},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)

    try:
        status = rpc("tools/call", {"name": "computer_status", "arguments": {}})
        windows = rpc("tools/call", {"name": "list_windows", "arguments": {}})
        tools = rpc("tools/list")["result"]["tools"]
        assert "result" in status and "result" in windows
        assert rpc("initialize")["result"]["serverInfo"]["name"] == "arc-local-windows"
        assert 2 <= len(tools) <= 6
        print("LOCAL_MCP_OK status/windows/tools")
        print("TOOLS=" + ",".join(t["name"] for t in tools))
        if screen:
            result = rpc("tools/call", {"name": "capture_screen", "arguments": {}})
            raw = base64.b64decode(result["result"]["content"][0]["data"])
            assert raw.startswith(b"\x89PNG") and len(raw) > 200
            print("SCREEN_CAPTURE_OK bytes=" + str(len(raw)))
        # No app launches, mouse clicks, or typing during automatic smoke tests.
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--enable-screen", action="store_true")
    parser.add_argument("--enable-apps", action="store_true")
    parser.add_argument("--enable-input", action="store_true",
                        help="Allow mouse and keyboard actions in the interactive session")
    parser.add_argument("--self-test", action="store_true", help="Safe, temporary local API checks")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Windows only")
    if args.self_test:
        return self_test(args.enable_screen, args.enable_apps, args.enable_input)
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    token = token_from_disk(TOKEN_FILE)
    server = LocalMCP(("127.0.0.1", args.port), token, args.enable_screen, args.enable_apps, args.enable_input)
    print("AI Remote Commander Local " + TOOL_VERSION, flush=True)
    print("MCP on http://127.0.0.1:%d/mcp (loopback only)" % args.port, flush=True)
    print("Enabled: " + ", ".join(t["name"] for t in server.tools()), flush=True)
    print("Token stored locally at " + str(TOKEN_FILE), flush=True)
    print("No central VDS. Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
