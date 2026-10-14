"""ARC Agent: a single-file Windows setup/runtime built with PyInstaller.

Double-click to install into %LOCALAPPDATA%\ARC. The same signed-by-workflow
bundle is launched in background as supervisor, MCP server and outbound worker.
No system-wide Python installation or admin rights are required.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import uuid

VERSION = "0.3.0-preview"
BASE = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ARC"
CONFIG = BASE / "agent.json"
PROGRAM = BASE / "ARC-Agent.exe"
TASK = "ARC Agent"


def local_task(script: str) -> subprocess.CompletedProcess:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive",
         "-EncodedCommand", encoded],
        capture_output=True, text=True, timeout=25,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def is_port_free(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        return sock.connect_ex(("127.0.0.1", port)) != 0
    finally:
        sock.close()


def initial_configuration() -> dict:
    port = next((port for port in (8876, 8896, 8906, 8916) if is_port_free(port)), None)
    if port is None:
        raise RuntimeError("No available ARC local port; choose one manually.")
    return {
        "device_id": uuid.uuid4().hex,
        "name": os.environ.get("COMPUTERNAME") or socket.gethostname(),
        "port": port,
        "roots": [str(Path.home())],
        "permissions": {
            "status": True, "inspect": True, "read_files": False,
            "screen": False, "apps": False, "input": False,
            "write": False, "shell": False,
        },
        "gateway_url": "",
        "device_token": "",
    }


def save_config(config: dict):
    BASE.mkdir(parents=True, exist_ok=True)
    # Only create new configuration; existing identities and credentials remain.
    with CONFIG.open("x", encoding="utf-8") as output:
        json.dump(config, output, ensure_ascii=False, indent=2)
    who = os.environ.get("USERDOMAIN", "") + "\\" + os.environ.get("USERNAME", "")
    if who.strip("\\"):
        subprocess.run(
            ["icacls.exe", str(CONFIG), "/inheritance:r",
             "/grant:r", who + ":(F)"],
            capture_output=True, check=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


def install_agent() -> str:
    if os.name != "nt":
        raise RuntimeError("ARC Agent installer supports Windows only")
    BASE.mkdir(parents=True, exist_ok=True)
    (BASE / "logs").mkdir(exist_ok=True)
    if not CONFIG.exists():
        save_config(initial_configuration())
    cfg = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    if not cfg.get("device_id") or not 1024 <= int(cfg.get("port", 0)) <= 65535:
        raise RuntimeError("Existing ARC configuration invalid; installation aborted")
    if Path(sys.executable).resolve() != PROGRAM.resolve():
        staged = BASE / "ARC-Agent.new.exe"
        shutil.copyfile(sys.executable, staged)
        # Stop only the existing ARC scheduled task, not other legacy ARC agents.
        current = local_task("Stop-ScheduledTask -TaskName 'ARC Agent' -ErrorAction SilentlyContinue")
        if current.returncode != 0:
            raise RuntimeError("Unable to stop previous ARC task for upgrade")
        os.replace(staged, PROGRAM)
    executable = str(PROGRAM).replace("'", "''")
    code = (
        "$ErrorActionPreference='Stop';"
        "$user=[Security.Principal.WindowsIdentity]::GetCurrent().Name;"
        "$action=New-ScheduledTaskAction -Execute '" + executable +
        "' -Argument '--mode supervisor' -WorkingDirectory '" +
        str(BASE).replace("'", "''") + "';"
        "$login=New-ScheduledTaskTrigger -AtLogOn -User $user;"
        "$periodic=New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) "
        "-RepetitionInterval (New-TimeSpan -Minutes 5);"
        "$principal=New-ScheduledTaskPrincipal -UserId $user "
        "-LogonType Interactive -RunLevel Limited;"
        "$settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew "
        "-ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 "
        "-RestartInterval (New-TimeSpan -Minutes 1);"
        "Register-ScheduledTask -TaskName 'ARC Agent' -Action $action "
        "-Trigger @($login,$periodic) -Principal $principal "
        "-Settings $settings -Force | Out-Null;"
        "Start-ScheduledTask -TaskName 'ARC Agent';"
    )
    result = local_task(code)
    if result.returncode != 0:
        raise RuntimeError("Cannot register ARC scheduled task: " + result.stderr[-350:])
    return "ARC Agent installed. Device ID: " + cfg["device_id"] + (
        "\nTo join ARC Hub, request a device enrollment file from its administrator."
    )


def remove_task() -> str:
    result = local_task(
        "Stop-ScheduledTask -TaskName 'ARC Agent' -ErrorAction SilentlyContinue;"
        "Unregister-ScheduledTask -TaskName 'ARC Agent' -Confirm:$false "
        "-ErrorAction Stop"
    )
    if result.returncode != 0:
        raise RuntimeError("Failed to unregister task")
    return "ARC Agent autostart removed. Local configuration and logs were retained."


def gui():
    import tkinter as tk
    from tkinter import messagebox, ttk

    window = tk.Tk()
    window.title("ARC Agent — Setup")
    window.geometry("430x310")
    window.resizable(False, False)
    window.configure(bg="#121e2e")

    def label(value, size=11, weight="normal"):
        tk.Label(window, text=value, fg="#e7f0ff", bg="#121e2e",
                 font=("Segoe UI", size, weight), wraplength=400,
                 justify="center").pack(pady=7)

    label("ARC Agent", 23, "bold")
    label("Версия " + VERSION)
    label("Один установщик для Windows. Работает в фоне,\n"
          "без окна PowerShell, запускается вместе с Windows.")
    result = tk.StringVar(value="Ожидает установки")
    tk.Label(window, textvariable=result, fg="#58d0b3", bg="#121e2e",
             wraplength=385, justify="center").pack(pady=5)

    def action(fn):
        try:
            value = fn()
            result.set(value)
            messagebox.showinfo("ARC Agent", value)
        except Exception as exc:
            messagebox.showerror("ARC Agent", str(exc))

    row = tk.Frame(window, bg="#121e2e")
    row.pack(pady=12)
    ttk.Button(row, text="Установить / обновить", command=lambda: action(install_agent)).pack(
        side="left", padx=7)
    ttk.Button(row, text="Отключить автозапуск", command=lambda: action(remove_task)).pack(
        side="left", padx=7)
    window.mainloop()


def main():
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--mode", choices=("supervisor", "local", "worker"))
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args, remainder = parser.parse_known_args()

    if args.self_test:
        from arc_full_ops import FullAgent
        from arc_http_full import FullLocalMCP
        from arc_supervisor import local_argv
        from arc_worker import SCOPES
        assert callable(FullLocalMCP) and callable(FullAgent)
        assert "computer_status" in SCOPES and callable(local_argv)
        return 0
    if args.mode:
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w", encoding="utf-8")
        if args.mode == "supervisor":
            from arc_supervisor import supervisor
            supervisor()
        elif args.mode == "worker":
            from arc_worker import main as run_worker
            run_worker()
        else:
            from arc_http_full import main as run_local
            sys.argv = [sys.argv[0], *remainder]
            run_local()
        return 0
    if args.install:
        install_agent()
        return 0
    if args.uninstall:
        remove_task()
        return 0
    gui()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        logdir = BASE / "logs"
        try:
            logdir.mkdir(parents=True, exist_ok=True)
            with (logdir / "setup-error.log").open("a", encoding="utf-8") as fh:
                fh.write(type(exc).__name__ + ": " + str(exc)[:500] + "\n")
        except OSError:
            pass
        sys.exit(1)
