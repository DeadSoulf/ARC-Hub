#!/usr/bin/env python3
"""ARC Lite local CLI fallback for Codex and ChatGPT Desktop.

Routes named, validated actions to the SAME local ARC Lite Windows backend.
No VDS, tunnel, listening ports, OpenAI credentials or background process.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import tempfile

from arc_lite import FullAccessAgent

# A one-shot CLI cannot read output from long-lived sessions owned by another
# process. Those tools remain available over persistent MCP stdio.
SESSION_TOOLS = {"start_process", "read_process_output", "send_process_input", "stop_process"}
STATE_CHANGING = {
    "open_application", "open_notepad", "write_file", "edit_file",
    "create_directory", "move_path", "delete_file", "run_command",
    "click_mouse", "move_mouse", "type_text", "hotkey", "scroll_mouse",
    "focus_window", "start_process", "send_process_input", "stop_process",
}
ALIASES = {
    "status": ("pc_status", {}),
    "windows": ("list_windows", {}),
    "processes": ("list_processes", {}),
    "screenshot": ("capture_screen", {}),
}
APPROVED_APPS = {"calculator", "notepad", "paint", "explorer"}
GUI_TOOLS = {
    "list_windows", "capture_screen", "open_application", "open_notepad",
    "click_mouse", "move_mouse", "type_text", "scroll_mouse", "hotkey",
    "focus_window",
}


def current_desktop_name():
    """The Codex shell sandbox may share the Windows session but not its desktop."""
    if os.name != "nt":
        return None
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.GetThreadDesktop.argtypes = [wintypes.DWORD]
    user32.GetThreadDesktop.restype = wintypes.HANDLE
    user32.GetUserObjectInformationW.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    handle = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
    buf = ctypes.create_unicode_buffer(256)
    needed = wintypes.DWORD()
    if user32.GetUserObjectInformationW(handle, 2, buf, ctypes.sizeof(buf),
                                         ctypes.byref(needed)):
        return buf.value
    return None


def encode(obj):
    return json.dumps(obj, ensure_ascii=True, separators=(",", ":"))


def parse(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", help="status/windows/processes/screenshot/app/ps/ls/cat/write/tool")
    parser.add_argument("value", nargs="?", default="", help="Application name, command or file path")
    parser.add_argument("--tool", default="", help="Exact registered MCP tool name for action 'tool'")
    parser.add_argument("--params-file", type=Path, help="Read JSON arguments from a local file")
    parser.add_argument("--path", default="", help="File path, or output path for screenshot")
    parser.add_argument("--content", default="", help="UTF-8 content to write to a file")
    parser.add_argument("--confirm", action="store_true", help="Acknowledgment required for state-changing actions")
    parser.add_argument("--timeout", type=int, default=15, help="PowerShell timeout (1..30 seconds)")
    return parser.parse_args(argv)


def decode_request(args):
    if args.action in ALIASES:
        return ALIASES[args.action]
    if args.action == "app":
        if args.value not in APPROVED_APPS:
            raise ValueError("App must be calculator, notepad, paint or explorer")
        return "open_application", {"name": args.value}
    if args.action == "ps":
        if not args.value.strip():
            raise ValueError("PowerShell command required")
        return "run_command", {"command": args.value, "timeout_seconds": args.timeout}
    if args.action == "ls":
        if not args.value:
            raise ValueError("Directory path required")
        return "list_directory", {"path": args.value}
    if args.action == "cat":
        if not args.value:
            raise ValueError("File path required")
        return "read_text_file", {"path": args.value}
    if args.action == "write":
        if not args.value:
            raise ValueError("File path required")
        return "write_file", {"path": args.value, "content": args.content}
    if args.action == "tool":
        if not args.tool:
            raise ValueError("--tool is required for action tool")
        if args.params_file:
            if args.params_file.stat().st_size > 65536:
                raise ValueError("Parameters file exceeds 64 KiB")
            params = json.loads(args.params_file.read_text(encoding="utf-8-sig"))
            if not isinstance(params, dict):
                raise ValueError("Tool parameters must be a JSON object")
            return args.tool, params
        return args.tool, {}
    raise ValueError("Unknown ARC Lite action: " + args.action)


def dispatch(args, agent):
    name, params = decode_request(args)
    catalog = {x["name"] for x in agent.tools()}
    if name not in catalog:
        raise ValueError("Tool not enabled by ARC Lite: " + name)
    if name in SESSION_TOOLS:
        raise ValueError("This tool requires a persistent MCP session; use the desktop plugin")
    if name in GUI_TOOLS:
        desktop = current_desktop_name()
        if not desktop or desktop.casefold() != "default":
            raise PermissionError(
                "GUI command cannot run on desktop " + str(desktop) +
                "; use the installed Computer Use skill with @oai/sky in node_repl")
    if name in STATE_CHANGING and not args.confirm:
        raise PermissionError("Action requires --confirm after user approval: " + name)
    if name == "delete_file" and args.confirm:
        params = {**params, "confirm": True}
    result = agent.call(name, params)
    if name == "capture_screen":
        # Never flood terminal output with base64 or overwrite an existing file.
        png = base64.b64decode(result["image"], validate=True)
        if not png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Screenshot did not return a valid PNG")
        if args.path:
            output = Path(args.path).expanduser()
            if output.suffix.lower() != ".png":
                raise ValueError("Screenshot output must be .png")
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("xb") as image_file:
                image_file.write(png)
        else:
            fd, temp_path = tempfile.mkstemp(prefix="arc-lite-capture-", suffix=".png")
            output = Path(temp_path)
            with os.fdopen(fd, "wb") as image_file:
                image_file.write(png)
        return {"saved_screenshot": str(output), "bytes": output.stat().st_size}
    return result


def main(argv=None):
    args = parse(argv)
    if os.name != "nt":
        raise SystemExit("ARC Lite local control requires Windows")
    try:
        shared = Path(os.environ["LOCALAPPDATA"]) / "ARC-Lite" / "shared"
        agent = FullAccessAgent(shared)
        result = dispatch(args, agent)
        print(encode({"ok": True, "result": result}), flush=True)
    except Exception as error:
        print(encode({"ok": False, "error": type(error).__name__, "message": str(error)}), flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
