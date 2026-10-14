"""ARC Hub one-time device pairing. Owner issues a code in ARC Web.

The code grants registration of ONE NEW device with status/inspection only.
A code is short-lived, consumed atomically, never stored in registry or logs.
"""
from __future__ import annotations

from collections import deque
import hashlib
import hmac
import queue
import re
import secrets
import time

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_CHARS = 12
CODE_TTL = 600
DEVICE_ID = re.compile(r"^[a-fA-F0-9]{32}$")


def normalize(code):
    if not isinstance(code, str):
        return ""
    return code.replace("-", "").replace(" ", "").upper()


def code_digest(code):
    return hashlib.sha256(code.encode("ascii")).hexdigest()


class Pairing:
    def __init__(self, web):
        self.web = web
        self.pending = {}
        self.failures = deque()

    def _prune(self, now):
        self.pending = {k: v for k, v in self.pending.items()
                        if v["expires"] > now}
        while self.failures and now - self.failures[0] > 60:
            self.failures.popleft()

    def issue(self, user):
        if user != "owner" or not self.web.is_admin(user):
            raise PermissionError("only owner can pair devices")
        if not self.web.config_path:
            raise RuntimeError("registry path is not configured")
        with self.web.lock:
            now = time.monotonic()
            self._prune(now)
            if len(self.pending) >= 3:
                raise ValueError("at most 3 pairing codes can be active")
            code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_CHARS))
            self.pending[code_digest(code)] = {
                "expires": now + CODE_TTL, "owner": user
            }
            self.web.hub.audit(user, "-", "device_pairing_created", "ok")
            return {"code": "-".join(code[i:i+4] for i in range(0, CODE_CHARS, 4)),
                    "expires_in_seconds": CODE_TTL,
                    "permissions": ["status", "inspect"]}

    def accept(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("invalid pairing request")
        code = normalize(payload.get("code"))
        identity = payload.get("device_id")
        name = payload.get("name")
        if (len(code) != CODE_CHARS or any(c not in ALPHABET for c in code) or
            not isinstance(identity, str) or not DEVICE_ID.fullmatch(identity) or
            not isinstance(name, str) or not 1 <= len(name) <= 80 or
            not name.isprintable() or any(c in "<>" for c in name)):
            raise ValueError("invalid pairing request")
        identity = identity.lower()
        with self.web.lock:
            now = time.monotonic()
            self._prune(now)
            if len(self.failures) >= 20:
                raise PermissionError("too many pairing attempts; retry later")
            key = code_digest(code)
            record = self.pending.get(key)
            if not record or record["expires"] <= now:
                self.failures.append(now)
                raise PermissionError("invalid or expired pairing code")
            if identity in self.web.hub.devices:
                # Never rotate an existing device's credentials through a code.
                raise ValueError("device is already enrolled")
            credential = secrets.token_urlsafe(48)
            device = {"name": name, "token_hash": hashlib.sha256(
                credential.encode("utf-8")).hexdigest(),
                "enabled": True, "allowed_scopes": ["status", "inspect"]}
            def register(data):
                if identity in data["devices"]:
                    raise ValueError("device is already enrolled")
                data["devices"][identity] = device
                owner = data["users"].get("owner")
                if not owner or not owner.get("enabled", True):
                    raise PermissionError("owner disabled")
                owner["grants"][identity] = ["status", "inspect"]
                return True
            # Do not consume the pairing code if a disk write fails.
            self.web._mutate_registry(register)
            self.web.hub.devices[identity] = device
            self.web.hub.queues[identity] = queue.Queue(maxsize=32)
            self.pending.pop(key, None)
            self.web.hub.audit("owner", identity, "device_paired", "ok")
            return {"device_id": identity, "device_token": credential,
                    "name": name, "permissions": ["status", "inspect"]}
