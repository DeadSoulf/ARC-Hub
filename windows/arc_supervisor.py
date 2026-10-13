"""ARC Agent supervisor, per-user, console-free. Windows only."""
from __future__ import annotations
import ctypes
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

BASE = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ARC"
BIN = BASE / "bin"
CONFIG = BASE / "agent.json"
STOPPED = False


def health(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as response:
            payload = json.load(response)
            return response.status == 200 and payload.get("status") == "ok"
    except Exception:
        return False


def load_config():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    if not isinstance(cfg.get("device_id"), str) or len(cfg["device_id"]) < 8:
        raise ValueError("invalid device id")
    if not 1024 <= cfg.get("port", 8876) <= 65535:
        raise ValueError("invalid port")
    if not isinstance(cfg.get("roots"), list) or not cfg["roots"] or not all(
        Path(root).is_absolute() for root in cfg["roots"]
    ):
        raise ValueError("allowed roots must be absolute")
    return cfg


def mutex_for(device_id):
    # The handle must stay alive until process exit.
    name = "Local\\ARC-Agent-" + device_id
    k = ctypes.windll.kernel32
    k.CreateMutexW.restype = ctypes.c_void_p
    handle = k.CreateMutexW(None, False, name)
    if not handle:
        raise OSError("mutex initialization failed")
    if k.GetLastError() == 183:
        k.CloseHandle(handle)
        return None
    return handle


def setup_log():
    logs = BASE / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("arc.agent")
    log.setLevel(logging.INFO)
    h = RotatingFileHandler(logs / "supervisor.log", maxBytes=2_000_000,
                            backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h)
    return log


def spawn(args, log_name):
    path = BASE / "logs" / log_name
    with path.open("ab", buffering=0) as out:
        return subprocess.Popen(args, cwd=str(BIN), stdin=subprocess.DEVNULL,
                                stdout=out, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def local_argv(cfg):
    command = [sys.executable, str(BIN / "arc_http_full.py"),
               "--port", str(cfg.get("port", 8876))]
    for path in cfg["roots"]:
        command.extend(["--allow-root", path])
    for key, flag in {"screen": "--enable-screen", "apps": "--enable-apps",
                      "input": "--enable-input", "write": "--allow-write",
                      "shell": "--allow-shell"}.items():
        if cfg.get("permissions", {}).get(key, False):
            command.append(flag)
    return command


def supervisor():
    log = setup_log()
    cfg = load_config()
    lock = mutex_for(cfg["device_id"])
    if lock is None:
        return
    local = None
    worker = None
    next_local = 0
    next_worker = 0
    retry_local = 0
    retry_worker = 0
    last_config = None
    log.info("ARC Agent started device=%s", cfg["device_id"])
    try:
        while True:
            now = time.monotonic()
            try:
                cfg = load_config()
                serialized = json.dumps(cfg, sort_keys=True)
                if last_config is not None and serialized != last_config:
                    log.info("device configuration updated; restarting owned children")
                    for child in (worker, local):
                        if child is not None and child.poll() is None:
                            child.terminate()
                    worker = local = None
                last_config = serialized
            except Exception as exc:
                log.warning("configuration reload failed: %s", type(exc).__name__)
                time.sleep(5)
                continue

            if local is not None and local.poll() is not None:
                log.warning("local agent exit=%s", local.returncode)
                local = None
                retry_local += 1
                next_local = now + min(60, 2 ** min(retry_local, 6))
            if local is None and now >= next_local:
                port = cfg.get("port", 8876)
                if health(port):
                    log.warning("port %s already occupied; refusing to replace existing agent", port)
                    next_local = now + 20
                else:
                    try:
                        local = spawn(local_argv(cfg), "local.log")
                        retry_local = 0
                        log.info("local agent pid=%s", local.pid)
                    except Exception as exc:
                        log.error("local start error: %s", type(exc).__name__)
                        next_local = now + 20

            enrolled = bool(cfg.get("gateway_url") and cfg.get("device_token"))
            if worker is not None and worker.poll() is not None:
                log.warning("worker exit=%s", worker.returncode)
                worker = None
                retry_worker += 1
                next_worker = now + min(60, 2 ** min(retry_worker, 6))
            if enrolled and worker is None and now >= next_worker:
                try:
                    worker = spawn([sys.executable, str(BIN / "arc_worker.py")], "worker-output.log")
                    retry_worker = 0
                    log.info("hub worker pid=%s", worker.pid)
                except Exception as exc:
                    log.error("worker start error: %s", type(exc).__name__)
                    next_worker = now + 20
            time.sleep(5)
    except (KeyboardInterrupt, SystemExit):
        log.info("agent stopping")
    finally:
        for child in (worker, local):
            if child and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
        ctypes.windll.kernel32.CloseHandle(lock)


if __name__ == "__main__":
    supervisor()
