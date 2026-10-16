"""Windows desktop operations for the local, stdio-only ARC Lite.
No network listener, secrets, external services or elevated permissions.
"""
from __future__ import annotations
import base64
import ctypes
from ctypes import wintypes
import os
import re
import socket
import struct
import subprocess
import threading
import zlib

REQUEST_RE = re.compile(r"^[A-Za-z0-9_-]{12,64}$")

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


class LocalMCP:
    """GUI adapter; this module does not expose an HTTP server."""

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

