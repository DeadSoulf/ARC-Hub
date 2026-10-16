"""Extra Windows desktop actions; only call from a user-enabled interactive session."""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import subprocess


ALLOWED_APPLICATIONS = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "paint": "mspaint.exe",
    "explorer": "explorer.exe",
}


def open_application(name):
    if not isinstance(name, str) or name not in ALLOWED_APPLICATIONS:
        raise ValueError("Application not in local allowlist")
    p = subprocess.Popen([ALLOWED_APPLICATIONS[name]], close_fds=True)
    return {"launched": name, "pid": p.pid}


def focus_window(title):
    if not isinstance(title, str) or not title.strip() or len(title) > 256:
        raise ValueError("Invalid window title")
    user32 = ctypes.windll.user32
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HWND, wintypes.LPARAM)
    found = []

    @callback_type
    def enumerate_window(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return 1
        length = min(user32.GetWindowTextLengthW(hwnd), 256)
        if not length:
            return 1
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        if buffer.value == title:
            found.append(hwnd)
            return 0
        return 1

    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows(enumerate_window, 0)
    if not found:
        raise ValueError("No visible window has exactly this title")
    user32.ShowWindow(found[0], 9)
    result = user32.SetForegroundWindow(found[0])
    if not result:
        raise RuntimeError("Windows did not allow focus (foreground restrictions)")
    return {"focused": title}


def move_mouse(x, y):
    user32 = ctypes.windll.user32
    width = user32.GetSystemMetrics(0)
    height = user32.GetSystemMetrics(1)
    if type(x) is not int or type(y) is not int or not 0 <= x < width or not 0 <= y < height:
        raise ValueError("Coordinates outside screen")
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    if not user32.SetCursorPos(x, y):
        raise RuntimeError("Cursor movement was denied")
    return {"x": x, "y": y}
