"""Opt-in filesystem, shell and desktop tools for AI Remote Commander Local."""
from __future__ import annotations

import ctypes
from collections import deque
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
from ctypes import wintypes

from arc_local import LocalMCP, tool_definition
from arc_stdio_readonly import LocalAgent
from arc_gui_extra import open_application, focus_window, move_mouse

MAX_TEXT = 512 * 1024
MAX_OUTPUT = 64 * 1024
PATH = {"type": "string", "maxLength": 2048}


class ProcessSession:
    """Bounded, in-memory output for a process started by this agent only."""
    def __init__(self, command, cwd):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-Command", command],
            cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, bufsize=0, creationflags=flags)
        self.lock = threading.Lock()
        self.chunks = deque()
        self.total = 0
        self.cursor = 0
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        while True:
            try:
                block = os.read(self.process.stdout.fileno(), 4096)
            except (OSError, ValueError):
                return
            if not block:
                return
            with self.lock:
                self.chunks.append((self.total, block))
                self.total += len(block)
                while self.chunks and self.total - self.chunks[0][0] > MAX_OUTPUT * 2:
                    self.chunks.popleft()

    def output(self, offset=0):
        with self.lock:
            if type(offset) is not int or offset < 0:
                raise ValueError("Invalid output offset")
            first = self.chunks[0][0] if self.chunks else self.total
            from_byte = max(first, offset)
            data = b"".join(block[max(0, from_byte - i):]
                            for i, block in self.chunks if i + len(block) > from_byte)
            return {"pid": self.process.pid, "running": self.process.poll() is None,
                    "exit_code": self.process.poll(), "offset": self.total,
                    "discarded": offset < first, "output": data.decode("utf-8", errors="replace")[:MAX_OUTPUT]}


class FullAgent(LocalAgent):
    def __init__(self, roots, *, screen=False, apps=False, user_input=False,
                 write=False, shell=False):
        super().__init__(roots)
        self.can_write = write
        self.can_shell = shell
        self.sessions = {}
        self.session_lock = threading.Lock()
        # No socket is created; LocalMCP is used only for Windows GUI functions.
        self.gui = object.__new__(LocalMCP)
        self.gui.screen_enabled = screen
        self.gui.apps_enabled = apps
        self.gui.input_enabled = user_input
        self.gui.request_ids = {}
        self.gui.action_lock = threading.Lock()

    def _new_path(self, value):
        if not isinstance(value, str) or not value or len(value) > 2048:
            raise ValueError("Invalid path")
        p = Path(value)
        if not p.is_absolute():
            raise ValueError("Absolute path required")
        path = p.resolve(strict=False)
        if not any(path == root or root in path.parents for root in self.roots):
            raise PermissionError("Outside allowed roots")
        return path

    def _cwd(self, args):
        path = self.secure_path(args["cwd"]) if "cwd" in args else self.roots[0]
        if not path.is_dir():
            raise ValueError("Working directory must exist")
        return path

    def tools(self):
        basics = super().tools()
        if not self.gui.screen_enabled:
            basics = [t for t in basics if t["name"] != "capture_screen"]
        names = {t["name"] for t in basics}
        basics += [t for t in self.gui.tools() if t["name"] not in names]
        basics += [
            tool_definition("search_files", "Search names of files inside an allowed directory; maximum 100 results.",
                            {"path": PATH, "query": {"type": "string", "minLength": 1, "maxLength": 128}},
                            ["path", "query"]),
            tool_definition("list_processes", "Read-only list of Windows processes, maximum 64 KiB of output."),
            tool_definition("file_info", "Read file or directory metadata within allowed roots.",
                            {"path": PATH}, ["path"]),
        ]
        if self.gui.apps_enabled:
            basics.append(tool_definition("open_application", "Launch an allowlisted local application.",
                            {"name": {"type": "string", "enum": ["notepad", "calculator", "paint", "explorer"]}}, ["name"]))
        if self.can_write:
            basics += [
                tool_definition("write_file", "Create or replace a UTF-8 text file in an allowed root.",
                                {"path": PATH, "content": {"type": "string", "maxLength": MAX_TEXT}},
                                ["path", "content"]),
                tool_definition("edit_file", "Replace a unique UTF-8 text fragment.",
                                {"path": PATH, "old_text": {"type": "string"},
                                 "new_text": {"type": "string"}}, ["path", "old_text", "new_text"]),
                tool_definition("create_directory", "Create a local directory within an allowed root.",
                                {"path": PATH}, ["path"]),
                tool_definition("move_path", "Move or rename a local file/directory within allowed roots; no overwrite.",
                                {"source": PATH, "destination": PATH}, ["source", "destination"]),
                tool_definition("delete_file", "Delete one regular file; confirmation flag required.",
                                {"path": PATH, "confirm": {"type": "boolean", "const": True}},
                                ["path", "confirm"]),
            ]
        if self.can_shell:
            basics += [
                tool_definition("run_command", "Run bounded PowerShell command as current user (max 30 s).",
                                {"command": {"type": "string", "maxLength": 4096},
                                 "cwd": PATH, "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 30}},
                                ["command"]),
                tool_definition("start_process", "Start a PowerShell process with streamed output.",
                                {"command": {"type": "string", "maxLength": 4096}, "cwd": PATH},
                                ["command"]),
                tool_definition("read_process_output", "Read output from a process created by this agent.",
                                {"pid": {"type": "integer"}, "offset": {"type": "integer", "minimum": 0}},
                                ["pid"]),
                tool_definition("send_process_input", "Send text to a process created by this agent.",
                                {"pid": {"type": "integer"}, "text": {"type": "string", "maxLength": 2048}},
                                ["pid", "text"]),
                tool_definition("stop_process", "Terminate a process created by this agent only.",
                                {"pid": {"type": "integer"}}, ["pid"]),
            ]
        if self.gui.input_enabled:
            basics += [
                tool_definition("hotkey", "Send keyboard combination to current desktop.",
                                {"keys": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 4}},
                                ["keys"]),
                tool_definition("scroll_mouse", "Scroll vertically at current mouse position.",
                                {"steps": {"type": "integer", "minimum": -10, "maximum": 10}}, ["steps"]),
                tool_definition("move_mouse", "Move the pointer within visible desktop.",
                                {"x": {"type": "integer"}, "y": {"type": "integer"}}, ["x", "y"]),
                tool_definition("focus_window", "Activate a visible window by its exact title.",
                                {"title": {"type": "string", "maxLength": 256}}, ["title"]),
            ]
        return basics

    def _validate(self, name, args):
        catalog = {t["name"]: t for t in self.tools()}
        if name not in catalog or not isinstance(args, dict):
            raise ValueError("Tool unavailable")
        spec = catalog[name]["inputSchema"]
        if set(args) - set(spec["properties"]) or set(spec["required"]) - set(args):
            raise ValueError("Invalid tool parameters")

    def call(self, name, args):
        self._validate(name, args)
        if name in ("computer_status", "list_windows", "capture_screen",
                    "list_directory", "read_text_file"):
            return super().call(name, args)
        if name in {t["name"] for t in self.gui.tools()}:
            return self.gui.call_tool(name, args)
        if name == "search_files":
            root = self.secure_path(args["path"])
            if not root.is_dir():
                raise ValueError("Search target must be directory")
            query = args["query"]
            if not isinstance(query, str) or not query or len(query) > 128:
                raise ValueError("Invalid query")
            matches = []
            visited = 0
            for here, subdirs, files in os.walk(root, followlinks=False):
                subdirs[:] = [d for d in subdirs if not (Path(here) / d).is_symlink()]
                for item in subdirs + files:
                    visited += 1
                    if query.casefold() in item.casefold():
                        matches.append(str(Path(here) / item))
                        if len(matches) >= 100:
                            return {"matches": matches, "truncated": True}
                    if visited >= 10000:
                        return {"matches": matches, "truncated": True}
            return {"matches": matches, "truncated": False}
        if name == "file_info":
            p = self.secure_path(args["path"])
            info = p.stat()
            return {"path": str(p), "is_directory": p.is_dir(), "is_file": p.is_file(),
                    "bytes": info.st_size, "modified_unix": info.st_mtime}
        if name == "open_application":
            return open_application(args["name"])
        if name == "move_mouse":
            return move_mouse(args["x"], args["y"])
        if name == "focus_window":
            return focus_window(args["title"])
        if name == "list_processes":
            p = subprocess.run(["tasklist.exe", "/FO", "CSV", "/NH"],
                               capture_output=True, timeout=10)
            return {"processes_csv": p.stdout.decode("utf-8", errors="replace")[:MAX_OUTPUT]}
        if name == "write_file":
            path = self._new_path(args["path"])
            content = args["content"]
            if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_TEXT:
                raise ValueError("Invalid text or too large")
            if path.exists() and not path.is_file():
                raise ValueError("Target must be a file")
            # Reject symbolic links and reparse points before writing.
            if path.is_symlink():
                raise PermissionError("Symbolic links are not writable")
            with path.open("w", encoding="utf-8", newline="") as f:
                f.write(content)
            return {"written": str(path), "bytes": len(content.encode("utf-8"))}
        if name == "edit_file":
            path = self.secure_path(args["path"])
            if not path.is_file() or path.stat().st_size > MAX_TEXT:
                raise ValueError("File unavailable or too large")
            old, new = args["old_text"], args["new_text"]
            if not isinstance(old, str) or not old or not isinstance(new, str):
                raise ValueError("Invalid replacement")
            data = path.read_text(encoding="utf-8")
            if data.count(old) != 1:
                raise ValueError("Expected exactly one text match")
            data = data.replace(old, new, 1)
            if len(data.encode("utf-8")) > MAX_TEXT:
                raise ValueError("Result too large")
            path.write_text(data, encoding="utf-8")
            return {"edited": str(path)}
        if name == "create_directory":
            p = self._new_path(args["path"])
            p.mkdir(parents=True, exist_ok=True)
            return {"directory": str(p)}
        if name == "move_path":
            src, dest = self.secure_path(args["source"]), self._new_path(args["destination"])
            if src in self.roots or dest in self.roots:
                raise PermissionError("Allowed filesystem roots cannot be moved")
            if dest.exists() or not dest.parent.is_dir():
                raise ValueError("Destination exists or parent missing")
            shutil.move(str(src), str(dest))
            return {"source": str(src), "destination": str(dest)}
        if name == "delete_file":
            if args["confirm"] is not True:
                raise PermissionError("Confirm deletion explicitly")
            p = self.secure_path(args["path"])
            if not p.is_file() or p.is_symlink():
                raise ValueError("Only regular files")
            p.unlink()
            return {"deleted": str(p)}
        if name == "run_command":
            command = args["command"]
            timeout = args.get("timeout_seconds", 15)
            if not isinstance(command, str) or not command.strip() or len(command) > 4096:
                raise ValueError("Invalid PowerShell command")
            if type(timeout) is not int or not 1 <= timeout <= 30:
                raise ValueError("Invalid timeout")
            try:
                p = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                                   cwd=self._cwd(args), capture_output=True, timeout=timeout,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                return {"exit_code": p.returncode,
                        "stdout": p.stdout.decode("utf-8", errors="replace")[:MAX_OUTPUT],
                        "stderr": p.stderr.decode("utf-8", errors="replace")[:MAX_OUTPUT]}
            except subprocess.TimeoutExpired:
                return {"timeout": True, "seconds": timeout}
        if name == "start_process":
            command = args["command"]
            if not isinstance(command, str) or not command.strip() or len(command) > 4096:
                raise ValueError("Invalid command")
            with self.session_lock:
                if len(self.sessions) >= 4:
                    raise RuntimeError("At most four process sessions")
                session = ProcessSession(command, self._cwd(args))
                self.sessions[session.process.pid] = session
            return {"pid": session.process.pid, "started": True}
        if name in ("read_process_output", "send_process_input", "stop_process"):
            pid = args["pid"]
            if type(pid) is not int or pid not in self.sessions:
                raise ValueError("Unknown session PID")
            session = self.sessions[pid]
            if name == "read_process_output":
                return session.output(args.get("offset", 0))
            if name == "send_process_input":
                content = args["text"]
                if not isinstance(content, str) or len(content) > 2048:
                    raise ValueError("Invalid input text")
                if session.process.poll() is not None or session.process.stdin is None:
                    raise ValueError("Process not running")
                session.process.stdin.write(content.encode("utf-8"))
                session.process.stdin.flush()
                return {"sent": len(content), "pid": pid}
            if session.process.poll() is None:
                session.process.terminate()
                try:
                    session.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    session.process.kill()
                    session.process.wait(timeout=3)
            if session.process.stdin:
                session.process.stdin.close()
            session.thread.join(timeout=1)
            if session.process.stdout:
                session.process.stdout.close()
            del self.sessions[pid]
            return {"stopped": pid}
        if name == "hotkey":
            codes = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B,
                     "enter": 0x0D, "escape": 0x1B, "tab": 0x09,
                     "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
                     "backspace": 0x08, "delete": 0x2E, "space": 0x20,
                     "f4": 0x73, "f5": 0x74}
            keys = args["keys"]
            if not isinstance(keys, list) or not keys or len(keys) > 4:
                raise ValueError("Invalid key combination")
            mapping = []
            for key in keys:
                if not isinstance(key, str) or len(key) > 12:
                    raise ValueError("Invalid key")
                code = codes.get(key.lower(), ord(key.upper()) if len(key) == 1 and key.isascii() and key.isalnum() else None)
                if code is None:
                    raise ValueError("Unsupported key")
                mapping.append(code)
            user32 = ctypes.windll.user32
            try:
                for code in mapping:
                    user32.keybd_event(code, 0, 0, 0)
            finally:
                for code in reversed(mapping):
                    user32.keybd_event(code, 0, 2, 0)
            return {"keys_sent": keys}
        if name == "scroll_mouse":
            steps = args["steps"]
            if type(steps) is not int or not -10 <= steps <= 10:
                raise ValueError("Invalid steps")
            ctypes.windll.user32.mouse_event(0x0800, 0, 0, (steps * 120) & 0xffffffff, 0)
            return {"scrolled": steps}
        raise ValueError("Unknown tool")
