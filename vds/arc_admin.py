"""ARC Hub credential and access management. Run only in a trusted admin shell.

No passwords/tokens are printed to stdout. Newly generated credentials are
written to separate owner-only files, never into the install ZIP.
"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile

from arc_hub import ROLE_SCOPES, TOOL_SCOPE, digest

NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{2,64}$")


def guard_name(name):
    if not NAME_RE.fullmatch(name):
        raise ValueError("name must contain 2-64 letters, digits, dashes or underscores")
    return name


def save_json(path, data, *, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive and path.exists():
        raise FileExistsError("refusing to overwrite credential file")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".arc-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def registry(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("version") != 2:
        raise ValueError("unsupported ARC registry version")
    return data


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True, type=Path)
    commands = p.add_subparsers(dest="command", required=True)
    b = commands.add_parser("bootstrap")
    b.add_argument("--owner-credentials", required=True, type=Path)
    a = commands.add_parser("add-user")
    a.add_argument("name", type=guard_name)
    a.add_argument("--role", choices=list(ROLE_SCOPES), default="viewer")
    a.add_argument("--credentials", required=True, type=Path)
    d = commands.add_parser("add-device")
    d.add_argument("device_id", type=guard_name)
    d.add_argument("--name", required=True)
    d.add_argument("--gateway-url", required=True)
    d.add_argument("--enrollment", required=True, type=Path)
    d.add_argument("--scopes", default="status,inspect")
    g = commands.add_parser("grant")
    g.add_argument("user", type=guard_name)
    g.add_argument("device_id", type=guard_name)
    g.add_argument("--scopes", required=True)
    x = commands.add_parser("revoke")
    x.add_argument("user", type=guard_name)
    x.add_argument("device_id", type=guard_name)
    l = commands.add_parser("list")
    args = p.parse_args()

    if args.command == "bootstrap":
        if args.config.exists() or args.owner_credentials.exists():
            raise FileExistsError("registry/credential already exists; refusing to overwrite")
        token = secrets.token_urlsafe(48)
        config = {"version": 2, "users": {"owner": {
            "role": "admin", "token_hash": digest(token),
            "enabled": True, "grants": {}}}, "devices": {}}
        save_json(args.owner_credentials, {"user": "owner", "token": token}, exclusive=True)
        save_json(args.config, config, exclusive=True)
        print("ARC_BOOTSTRAPPED; owner credential written privately")
        return

    data = registry(args.config)
    if args.command == "add-user":
        if args.name in data["users"] or args.credentials.exists():
            raise ValueError("user or credential file exists")
        token = secrets.token_urlsafe(48)
        data["users"][args.name] = {"role": args.role, "token_hash": digest(token),
                                   "enabled": True, "grants": {}}
        save_json(args.credentials, {"user": args.name, "token": token}, exclusive=True)
        save_json(args.config, data)
        print("USER_CREATED " + args.name + " role=" + args.role)
    elif args.command == "add-device":
        if args.device_id in data["devices"] or args.enrollment.exists():
            raise ValueError("device or enrollment file exists")
        from urllib.parse import urlsplit
        parsed = urlsplit(args.gateway_url)
        if (parsed.scheme != "https" or not parsed.netloc
                or parsed.username or parsed.password):
            raise ValueError("VDS gateway URL must be a valid HTTPS URL with no credentials")
        scopes = set(args.scopes.split(","))
        if scopes - set(TOOL_SCOPE.values()) or not scopes:
            raise ValueError("unrecognized device scopes")
        token = secrets.token_urlsafe(48)
        data["devices"][args.device_id] = {
            "name": args.name, "token_hash": digest(token),
            "enabled": True, "allowed_scopes": sorted(scopes)}
        save_json(args.enrollment, {"device_id": args.device_id, "name": args.name,
                                   "device_token": token, "gateway_url": args.gateway_url},
                  exclusive=True)
        save_json(args.config, data)
        print("DEVICE_CREATED " + args.device_id)
    elif args.command == "grant":
        user = data["users"].get(args.user)
        device = data["devices"].get(args.device_id)
        if not user or not device:
            raise ValueError("unknown user or device")
        scopes = set(args.scopes.split(","))
        if (not scopes or scopes - ROLE_SCOPES[user["role"]]
                or scopes - set(device["allowed_scopes"])):
            raise ValueError("grant exceeds user role or device policy")
        user["grants"][args.device_id] = sorted(scopes)
        save_json(args.config, data)
        print("GRANTED " + args.user + " -> " + args.device_id + ": " +
              ",".join(sorted(scopes)))
    elif args.command == "revoke":
        user = data["users"].get(args.user)
        if not user:
            raise ValueError("unknown user")
        user["grants"].pop(args.device_id, None)
        save_json(args.config, data)
        print("REVOKED " + args.user + " -> " + args.device_id)
    elif args.command == "list":
        print(json.dumps({
            "users": [{"name": u, "role": obj["role"],
                       "devices": sorted(obj["grants"])}
                      for u, obj in data["users"].items()],
            "devices": [{"id": d, "name": obj["name"],
                         "scopes": obj["allowed_scopes"]}
                        for d, obj in data["devices"].items()]},
            ensure_ascii=False, indent=2))
    if args.command not in ("list",):
        print("Restart ARC Hub service to reload registry changes.")


if __name__ == "__main__":
    main()
