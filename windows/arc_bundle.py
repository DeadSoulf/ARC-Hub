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
from urllib.parse import urlsplit
import urllib.request
import urllib.error

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


def enroll_agent(enrollment_file: str) -> str:
    """Legacy offline enrollment from an administrator-issued JSON file."""
    payload = json.loads(Path(enrollment_file).read_text(encoding="utf-8-sig"))
    return apply_enrollment_payload(payload)


def apply_enrollment_payload(payload: dict) -> str:
    """Persist device credentials from a verified enrollment response."""
    if not CONFIG.is_file():
        raise RuntimeError("Install ARC Agent on this PC before enrollment")
    cfg = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("Enrollment JSON must contain an object")
    device_id = payload.get("device_id")
    token = payload.get("device_token")
    address = payload.get("gateway_url")
    if not (isinstance(device_id, str) and device_id == cfg.get("device_id")):
        raise ValueError("Wrong device_id: enrollment belongs to another computer")
    if not isinstance(token, str) or len(token) < 48 or len(token) > 512:
        raise ValueError("Invalid device credential")
    if not isinstance(address, str):
        raise ValueError("Invalid gateway URL")
    u = urlsplit(address)
    if u.scheme != "https" or not u.hostname or u.username or u.password or u.query or u.fragment:
        raise ValueError("Only HTTPS ARC Hub URLs without embedded credentials are accepted")
    if cfg.get("device_token") and cfg["device_token"] != token:
        raise ValueError("Already registered with different credentials; ask administrator to revoke the old enrollment")
    cfg["gateway_url"] = address.rstrip("/")
    cfg["device_token"] = token
    if isinstance(payload.get("name"), str) and len(payload["name"]) <= 128:
        cfg["name"] = payload["name"]
    # Restrict new secret file before atomic replacement.
    temporary = CONFIG.with_name("agent.json.enrolling")
    with temporary.open("x", encoding="utf-8") as file:
        json.dump(cfg, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    try:
        user = os.environ.get("USERDOMAIN", "") + "\\" + os.environ.get("USERNAME", "")
        result = subprocess.run(
            ["icacls.exe", str(temporary), "/inheritance:r",
             "/grant:r", user + ":(F)"],
            capture_output=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            raise RuntimeError("Unable to secure enrollment file permissions")
        os.replace(temporary, CONFIG)
    finally:
        if temporary.exists():
            temporary.unlink()
    return ("ARC Agent connected to " + cfg["gateway_url"] + "\n"
            "Keep the enrollment token private and delete the transferred JSON copy.")


def enroll_by_code(gateway_url: str, code: str) -> str:
    """One-time pairing with ARC Hub; token is delivered only over verified TLS."""
    if not CONFIG.exists():
        raise RuntimeError("Установите ARC Agent перед подключением")
    cfg = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    if cfg.get("device_token"):
        raise RuntimeError("Компьютер уже зарегистрирован. Обратитесь к администратору для переподключения.")
    if not isinstance(gateway_url, str):
        raise ValueError("Укажите HTTPS-адрес ARC Hub")
    u = urlsplit(gateway_url.strip())
    if (u.scheme != "https" or not u.hostname or u.username or u.password
        or u.query or u.fragment or u.path not in ("", "/")):
        raise ValueError("Адрес ARC Hub должен быть вида https://vpn.homeaicore.ru")
    # Reject local/insecure or ambiguous hostnames and invalid ports.
    try:
        _ = u.port
    except ValueError as exc:
        raise ValueError("Некорректный HTTPS-порт") from exc
    if not isinstance(code, str) or len(code.strip()) > 32:
        raise ValueError("Некорректный одноразовый код")
    clean_code = code.upper().replace("-", "").replace(" ", "").strip()
    if len(clean_code) != 12 or not clean_code.isalnum():
        raise ValueError("Введите 12 символов одноразового кода")
    payload = {"device_id": cfg["device_id"], "name": cfg.get("name") or "Windows PC",
               "code": clean_code}
    remote = gateway_url.strip().rstrip("/")
    request = urllib.request.Request(
        remote + "/agent/enroll", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.status != 201:
                raise RuntimeError("ARC Hub не подтвердил регистрацию")
            result = json.loads(response.read(4096))
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 403):
            raise RuntimeError("Код недействителен, просрочен или устройство уже зарегистрировано") from None
        raise RuntimeError("Сервер отклонил регистрацию (HTTP " + str(exc.code) + ")") from None
    except urllib.error.URLError:
        raise RuntimeError("Не удалось подключиться к ARC Hub по HTTPS") from None
    if not isinstance(result, dict) or result.get("device_id") != cfg["device_id"]:
        raise RuntimeError("Некорректный ответ ARC Hub")
    result["gateway_url"] = remote
    message = apply_enrollment_payload(result)
    return "Компьютер подключён к ARC Hub!\n" + message


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
    window.geometry("490x475")
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

    def browse_enrollment():
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="ARC Hub: выбрать файл регистрации",
            filetypes=[("ARC enrollment JSON", "*.json"), ("Все файлы", "*.*")],
        )
        if path and messagebox.askokcancel(
            "ARC Hub",
            "Применить секретный файл регистрации именно к этому компьютеру?"
        ):
            action(lambda: enroll_agent(path))

    def connect_with_code():
        dialog = tk.Toplevel(window)
        dialog.title("ARC Hub — подключить по коду")
        dialog.geometry("440x240")
        dialog.resizable(False, False)
        dialog.transient(window)
        dialog.grab_set()
        host = tk.StringVar(value="https://vpn.homeaicore.ru")
        code = tk.StringVar()
        tk.Label(dialog, text="Адрес ARC Hub (HTTPS)").pack(pady=(15, 2))
        tk.Entry(dialog, textvariable=host, width=49).pack()
        tk.Label(dialog, text="Одноразовый код из ARC Web").pack(pady=(10, 2))
        tk.Entry(dialog, textvariable=code, width=28).pack()
        tk.Label(dialog, text="Код действует 10 минут и подходит только для одного ПК.").pack(pady=7)
        def submit():
            try:
                message = enroll_by_code(host.get(), code.get())
                result.set(message)
                dialog.destroy()
                messagebox.showinfo("ARC Hub", message)
            except Exception as exc:
                messagebox.showerror("ARC Hub", str(exc))
        ttk.Button(dialog, text="Подключить компьютер", command=submit).pack(pady=4)

    ttk.Button(window, text="Подключить по одноразовому коду",
               command=connect_with_code).pack(pady=8)
    ttk.Button(window, text="Подключить через файл регистрации (резервный способ)",
               command=browse_enrollment).pack(pady=5)
    window.mainloop()


def main():
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--mode", choices=("supervisor", "local", "worker"))
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--enroll", metavar="PATH")
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
    if args.enroll:
        enroll_agent(args.enroll)
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
