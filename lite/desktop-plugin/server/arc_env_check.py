"""Read-only ARC Lite diagnostics for interactive vs sandboxed Windows desktops."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys

from arc_lite import FullAccessAgent


def object_name(handle):
    user32 = ctypes.windll.user32
    user32.GetUserObjectInformationW.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    buffer = ctypes.create_unicode_buffer(256)
    size = wintypes.DWORD()
    if user32.GetUserObjectInformationW(handle, 2, buffer, ctypes.sizeof(buffer), ctypes.byref(size)):
        return buffer.value
    return None


def main():
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
    sid = wintypes.DWORD()
    kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(sid))
    thread_id = kernel32.GetCurrentThreadId()
    user32.GetThreadDesktop.argtypes = [wintypes.DWORD]
    user32.GetThreadDesktop.restype = wintypes.HANDLE
    user32.GetProcessWindowStation.restype = wintypes.HANDLE
    agent = FullAccessAgent(Path(os.environ["LOCALAPPDATA"]) / "ARC-Lite" / "shared")
    windows = agent.call("list_windows", {})["windows"]
    info = {
        "hostname": agent.call("pc_status", {})["hostname"],
        "username": os.environ.get("USERNAME"),
        "session_id": sid.value,
        "pid": os.getpid(),
        "desktop_name": object_name(user32.GetThreadDesktop(thread_id)),
        "window_station": object_name(user32.GetProcessWindowStation()),
        "visible_window_count": len(windows),
        "has_calculator_window": any("calculator" in w.get("title", "").lower()
                                   or "калькулятор" in w.get("title", "").lower()
                                   for w in windows),
    }
    print(json.dumps(info, ensure_ascii=True))


if __name__ == "__main__":
    main()
