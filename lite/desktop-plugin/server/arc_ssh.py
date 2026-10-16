"""ARC Lite SSH tools. Local OpenSSH subprocesses; no sockets or dependencies.

All SSH destinations come from the user's local allowlist. No tool accepts a
free-form target address, password, proxy, port forward or SSH options.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

HOST_ALIAS = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")
HOSTNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")
USERNAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
PATH = re.compile(r"^/(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+$")
MAX_TEXT = 65536
MAX_COMMAND = 8192
MAX_HOSTS = 32
MAX_OUTPUT = 65536


def tool(name, description, properties=None, required=None, *, read_only=False):
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object", "properties": properties or {},
            "required": required or [], "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": read_only, "destructiveHint": not read_only,
            "idempotentHint": read_only, "openWorldHint": True,
        },
    }


class SSHBridge:
    """Stateless SSH calls using Windows OpenSSH, restricted to local aliases."""

    def __init__(self, config_path: Path):
        self.config_path = Path(config_path).expanduser().resolve()
        # Fail early on malformed or suspicious configuration.
        self._hosts()

    def tools(self):
        host = {"type": "string", "minLength": 1, "maxLength": 40}
        path = {"type": "string", "minLength": 2, "maxLength": 1024}
        timeout = {"type": "integer", "minimum": 1, "maximum": 60}
        return [
            tool("ssh_list_hosts", "List approved SSH aliases and capabilities (no passwords or private keys).", read_only=True),
            tool("ssh_test", "Check non-interactive SSH authentication to a configured host. Never accept new host keys automatically.",
                 {"host": host, "timeout_seconds": timeout}, ["host"], read_only=True),
            tool("ssh_exec", "Execute a shell command on an allowlisted SSH host. Remote commands may change files or services; require user approval.",
                 {"host": host, "command": {"type": "string", "minLength": 1, "maxLength": MAX_COMMAND},
                  "timeout_seconds": timeout}, ["host", "command"]),
            tool("ssh_read_file", "Read a UTF-8 file via SFTP from a configured SSH host (max 64 KiB; absolute simple path).",
                 {"host": host, "path": path}, ["host", "path"], read_only=True),
            tool("ssh_write_file", "Write UTF-8 text via SFTP to a configured SSH host, only if write enabled and confirm=true.",
                 {"host": host, "path": path,
                  "content": {"type": "string", "maxLength": MAX_TEXT},
                  "confirm": {"type": "boolean"}}, ["host", "path", "content", "confirm"]),
        ]

    @staticmethod
    def _optional_path(value, label):
        if value is None:
            return None
        if not isinstance(value, str) or not value or "\x00" in value:
            raise ValueError(f"Invalid {label}")
        p = Path(value).expanduser()
        if not p.is_absolute():
            raise ValueError(f"{label} must be an absolute path")
        return p

    def _hosts(self):
        if not self.config_path.is_file():
            return {}
        data = json.loads(self.config_path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or set(data) != {"hosts"} or not isinstance(data["hosts"], list):
            raise ValueError("SSH config must be an object containing only a hosts array")
        if len(data["hosts"]) > MAX_HOSTS:
            raise ValueError("Too many SSH hosts")
        allowed = {"alias", "hostname", "user", "port", "identity_file",
                   "known_hosts_file", "allow_exec", "allow_read", "allow_write"}
        result = {}
        for entry in data["hosts"]:
            if not isinstance(entry, dict) or set(entry) - allowed:
                raise ValueError("Unknown SSH host fields")
            alias, hostname, user = (entry.get(x) for x in ("alias", "hostname", "user"))
            if not isinstance(alias, str) or not HOST_ALIAS.fullmatch(alias):
                raise ValueError("Invalid SSH alias")
            if not isinstance(hostname, str) or not HOSTNAME.fullmatch(hostname) or ".." in hostname:
                raise ValueError("Invalid SSH hostname (DNS or IPv4 only)")
            if not isinstance(user, str) or not USERNAME.fullmatch(user):
                raise ValueError("Invalid SSH username")
            port = entry.get("port", 22)
            if type(port) is not int or not 1 <= port <= 65535:
                raise ValueError("Invalid SSH port")
            for key in ("allow_exec", "allow_read", "allow_write"):
                if type(entry.get(key, False)) is not bool:
                    raise ValueError(f"{key} must be a boolean")
            if alias in result:
                raise ValueError("Duplicate SSH alias")
            identity = self._optional_path(entry.get("identity_file"), "identity_file")
            known_hosts = self._optional_path(entry.get("known_hosts_file"), "known_hosts_file")
            if known_hosts is None:
                known_hosts = Path.home() / ".ssh" / "known_hosts"
            result[alias] = {
                "alias": alias, "hostname": hostname, "user": user, "port": port,
                "identity_file": identity, "known_hosts_file": known_hosts,
                "allow_exec": entry.get("allow_exec", False),
                "allow_read": entry.get("allow_read", False),
                "allow_write": entry.get("allow_write", False),
            }
        return result

    def _host(self, alias):
        if not isinstance(alias, str) or not HOST_ALIAS.fullmatch(alias):
            raise ValueError("Invalid SSH host alias")
        hosts = self._hosts()  # Reload edited configuration without restart.
        if alias not in hosts:
            raise PermissionError("Host is not in the SSH allowlist")
        return hosts[alias]

    @staticmethod
    def _validate_timeout(value):
        if type(value) is not int or not 1 <= value <= 60:
            raise ValueError("timeout_seconds must be an integer from 1 to 60")
        return value

    @staticmethod
    def _validate_remote_path(value):
        if not isinstance(value, str) or len(value) > 1024 or not PATH.fullmatch(value):
            raise ValueError("Use an absolute POSIX path with letters, digits, /, _, -, or . (no spaces)")
        if any(segment in (".", "..") for segment in value.split("/")):
            raise ValueError("Path traversal is not allowed")
        return value

    @staticmethod
    def _find_binary(name):
        binary = shutil.which(name)
        if not binary:
            raise RuntimeError(f"{name} is missing: install the OpenSSH Client")
        return binary

    def _cmd(self, host, *, sftp=False):
        exe = self._find_binary("sftp" if sftp else "ssh")
        cmd = [
            exe, "-F", "none",
            "-o", "BatchMode=yes",
            "-o", "PasswordAuthentication=no",
            "-o", "KbdInteractiveAuthentication=no",
            "-o", "PreferredAuthentications=publickey",
            "-o", "StrictHostKeyChecking=yes",
            "-o", "UpdateHostKeys=no",
            "-o", f"UserKnownHostsFile={host['known_hosts_file']}",
            "-o", "ConnectTimeout=10",
            "-o", "ConnectionAttempts=1",
            "-o", "ClearAllForwardings=yes",
            "-o", "ForwardAgent=no",
            "-o", "PermitLocalCommand=no",
            "-o", "ProxyCommand=none",
            "-o", "ProxyJump=none",
        ]
        if host["identity_file"]:
            cmd += ["-o", "IdentitiesOnly=yes", "-i", str(host["identity_file"])]
        cmd += ["-P" if sftp else "-p", str(host["port"])]
        if sftp:
            cmd += ["-b", "-", f"{host['user']}@{host['hostname']}"]
        else:
            cmd += ["-T", "-l", host["user"], host["hostname"]]
        return cmd

    @staticmethod
    def _run(command, *, input_data=None, timeout=30, cwd=None):
        try:
            res = subprocess.run(command, input=input_data, capture_output=True,
                                 timeout=timeout, cwd=cwd, check=False)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"SSH operation timed out after {timeout} seconds") from exc
        return {
            "exit_code": res.returncode,
            "stdout": res.stdout[:MAX_OUTPUT].decode("utf-8", errors="replace"),
            "stderr": res.stderr[:MAX_OUTPUT].decode("utf-8", errors="replace"),
            "truncated": len(res.stdout) > MAX_OUTPUT or len(res.stderr) > MAX_OUTPUT,
        }

    def call(self, name, args):
        if name == "ssh_list_hosts":
            return {"hosts": [
                {key: val for key, val in host.items()
                 if key in ("alias", "hostname", "user", "port", "allow_exec", "allow_read", "allow_write")}
                for host in self._hosts().values()
            ]}
        host = self._host(args["host"])
        if name == "ssh_test":
            timeout = self._validate_timeout(args.get("timeout_seconds", 15))
            out = self._run(self._cmd(host) + ["exit 0"], timeout=timeout)
            return {"host": host["alias"], "connected": out["exit_code"] == 0,
                    "exit_code": out["exit_code"], "stderr": out["stderr"]}
        if name == "ssh_exec":
            if not host["allow_exec"]:
                raise PermissionError("SSH command execution is disabled for this host")
            command = args["command"]
            if not isinstance(command, str) or not command.strip() or len(command) > MAX_COMMAND or "\x00" in command:
                raise ValueError("Invalid SSH command")
            timeout = self._validate_timeout(args.get("timeout_seconds", 30))
            out = self._run(self._cmd(host) + [command], timeout=timeout)
            return {"host": host["alias"], **out}
        if name == "ssh_read_file":
            if not host["allow_read"]:
                raise PermissionError("SSH file reading is disabled for this host")
            path = self._validate_remote_path(args["path"])
            with tempfile.TemporaryDirectory(prefix="arc-lite-ssh-") as tmp:
                output = self._run(self._cmd(host, sftp=True), cwd=tmp,
                                   input_data=f"get {path} arc-data.txt\n".encode("ascii"), timeout=30)
                if output["exit_code"] != 0:
                    raise OSError("SFTP read failed: " + output["stderr"][-1000:])
                local = Path(tmp) / "arc-data.txt"
                if not local.is_file() or local.stat().st_size > MAX_TEXT:
                    raise ValueError("Remote file missing or exceeds 64 KiB")
                return {"host": host["alias"], "path": path,
                        "content": local.read_text(encoding="utf-8")}
        if name == "ssh_write_file":
            if not host["allow_write"]:
                raise PermissionError("SSH file writing is disabled for this host")
            if args["confirm"] is not True:
                raise PermissionError("Explicit confirm=true is required")
            path = self._validate_remote_path(args["path"])
            content = args["content"]
            if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_TEXT:
                raise ValueError("Text exceeds 64 KiB")
            payload = content.encode("utf-8")
            with tempfile.TemporaryDirectory(prefix="arc-lite-ssh-") as tmp:
                (Path(tmp) / "arc-data.txt").write_bytes(payload)
                output = self._run(self._cmd(host, sftp=True), cwd=tmp,
                                   input_data=f"put arc-data.txt {path}\n".encode("ascii"), timeout=30)
                if output["exit_code"] != 0:
                    raise OSError("SFTP write failed: " + output["stderr"][-1000:])
                return {"host": host["alias"], "path": path, "bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(), "written": True}
        raise ValueError("Unsupported SSH operation")
