"""ARC Agent worker: authenticated outbound long polling to central ARC Hub.

The hub can authorize a call, but THIS agent independently enforces the
local opt-in permissions. No public inbound Windows port is required.
"""
from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import time
from urllib.parse import quote, urlsplit
import urllib.request

from arc_local import TOKEN_FILE

ROOT = Path(__file__).resolve().parent
CONFIG = Path(os.environ.get("LOCALAPPDATA", str(ROOT))) / "ARC" / "agent.json"

SCOPES = {
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
OPTIONS = {"status": "status", "inspect": "inspect", "files.read": "read_files",
           "screen.read": "screen", "apps.launch": "apps",
           "input.control": "input", "files.write": "write",
           "shell.exec": "shell"}


def http_json(url, data=None, headers=None, timeout=20):
    body = None if data is None else json.dumps(data).encode("utf-8")
    hdrs = dict(headers or {})
    if body is not None:
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read(12_000_000))


def load_config():
    config = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    u = urlsplit(config["gateway_url"])
    if not (u.scheme == "https" or (u.scheme == "http" and
                                   u.hostname in ("127.0.0.1", "localhost"))):
        raise ValueError("ARC Hub must be HTTPS outside localhost")
    if u.username or u.password or u.query or u.fragment:
        raise ValueError("invalid ARC Hub URL")
    if len(config.get("device_token", "")) < 48 or not config.get("device_id"):
        raise ValueError("device has not been enrolled")
    return config


def main():
    cfg = load_config()
    logdir = CONFIG.parent / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, handlers=[
        RotatingFileHandler(logdir / "worker.log", maxBytes=2_000_000,
                            backupCount=3, encoding="utf-8")])
    log = logging.getLogger("arc.worker")
    port = int(cfg.get("port", 8876))
    token = TOKEN_FILE.read_text(encoding="ascii").strip()
    base = cfg["gateway_url"].rstrip("/") + "/agent"
    q = "?device_id=" + quote(cfg["device_id"], safe="")
    headers = {"X-ARC-Device-Token": cfg["device_token"]}
    local = "http://127.0.0.1:%d/mcp" % port
    permissions = cfg.get("permissions", {})
    count = 0
    while True:
        try:
            reply = http_json(base + "/poll" + q, headers=headers, timeout=16)
            count = 0
            task = reply.get("task")
            if not task:
                continue
            name = task.get("tool")
            key = task.get("task_id")
            scope = SCOPES.get(name)
            # Explicit double authorization: also enforce local PC permission.
            allowed = bool(scope and permissions.get(OPTIONS[scope], False))
            if not allowed:
                response = {"task_id": key, "error": "local device policy denied tool"}
            else:
                try:
                    req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": name, "arguments": task.get("arguments", {})}}
                    result = http_json(local, req,
                                       {"Authorization": "Bearer " + token},
                                       timeout=36)
                    if result.get("error"):
                        raise RuntimeError("local MCP tool failed")
                    response = {"task_id": key, "result": result["result"]}
                except Exception as exc:
                    log.warning("local tool %s failed: %s", name, type(exc).__name__)
                    response = {"task_id": key, "error": "local MCP failure"}
            ack = http_json(base + "/result" + q, response, headers, timeout=12)
            if not ack.get("accepted"):
                log.warning("hub rejected stale or unknown task")
        except Exception as exc:
            count += 1
            log.warning("connection issue: %s", type(exc).__name__)
            time.sleep(min(30, count * 2))


if __name__ == "__main__":
    main()
