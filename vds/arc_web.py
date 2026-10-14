"""ARC Web: authenticated browser console for ARC Hub.

No external dependencies, no privileged filesystem access from the browser.
Browser sessions are server-side, short-lived and bound to the token hash.
"""
from __future__ import annotations

from collections import defaultdict, deque
from http.cookies import SimpleCookie
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time
import hashlib
from urllib.parse import urlsplit
from arc_pairing import Pairing

WEB_ROOT = Path(__file__).with_name("web")
SESSION_TTL = 8 * 60 * 60
USER_NAME = re.compile(r"^[A-Za-z0-9_-]{2,64}$")
ALLOWED_ROLES = {"viewer", "operator", "admin"}
ROLE_SCOPES = {
    "viewer": {"status"},
    "operator": {"status", "inspect", "files.read", "screen.read", "apps.launch", "input.control"},
    "admin": {"status", "inspect", "files.read", "screen.read", "apps.launch", "input.control", "files.write", "shell.exec"}
}

def _hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class WebState:
    def __init__(self, hub, config_path=None):
        self.hub = hub
        self.config_path = Path(config_path) if config_path else None
        self.sessions = {}
        self.failures = defaultdict(deque)
        self.lock = threading.RLock()
        self.update_dir = Path(os.environ.get("ARC_UPDATE_DIR", "/var/lib/arc-hub/updates"))
        self.pairing = Pairing(self)

    def login(self, name, credential):
        if not isinstance(name, str) or not isinstance(credential, str) or (
                not USER_NAME.fullmatch(name) or not 40 <= len(credential) <= 512):
            return None
        with self.lock:
            now = time.monotonic()
            attempts = self.failures[name]
            while attempts and now - attempts[0] > 300:
                attempts.popleft()
            if len(attempts) >= 8:
                return None
            user = self.hub.users.get(name)
            actual = user.get("token_hash", "") if user and user.get("enabled", True) else ""
            if not hmac.compare_digest(_hash(credential), actual):
                attempts.append(now)
                return None
            attempts.clear()
            sid = secrets.token_urlsafe(48)
            csrf = secrets.token_urlsafe(32)
            if len(self.sessions) >= 4096:
                self.sessions = {
                    key: val for key, val in self.sessions.items()
                    if val["expiry"] > now
                }
            self.sessions[_hash(sid)] = {
                "user": name, "credential_hash": actual,
                "expiry": now + SESSION_TTL, "csrf": csrf
            }
            return sid, csrf

    def get_session(self, cookie_header):
        try:
            parsed = SimpleCookie()
            parsed.load((cookie_header or "")[:4096])
            cookie = parsed.get("arc_session")
            sid = cookie.value if cookie else ""
            if not sid or len(sid) > 256:
                return None
            with self.lock:
                key = _hash(sid)
                entry = self.sessions.get(key)
                if not entry:
                    return None
                account = self.hub.users.get(entry["user"])
                if entry["expiry"] <= time.monotonic() or not account or not account.get("enabled", True) or (
                        account.get("token_hash") != entry["credential_hash"]):
                    self.sessions.pop(key, None)
                    return None
                return dict(entry)
        except Exception:
            return None

    def logout(self, cookie_header):
        parsed = SimpleCookie()
        try:
            parsed.load((cookie_header or "")[:4096])
            cookie = parsed.get("arc_session")
            if cookie:
                with self.lock:
                    self.sessions.pop(_hash(cookie.value), None)
        except Exception:
            pass

    def server_status(self, name):
        if name != "owner":
            raise PermissionError("only owner can manage server updates")
        base = self.update_dir
        state = {
            "state": "idle", "step": "idle", "percent": 0,
            "detail": "Обновление пока не запрашивалось",
            "updated_unix": None, "started_unix": None,
        }
        try:
            if (base / "status.json").is_file():
                loaded = json.loads((base / "status.json").read_text(
                    encoding="utf-8"))
                if not isinstance(loaded, dict):
                    raise ValueError("invalid update status")
                phase = loaded.get("state")
                stage = loaded.get("step")
                value = loaded.get("percent", 0)
                if phase not in ("idle", "queued", "running", "success", "failed"):
                    raise ValueError("invalid update phase")
                if stage not in ("idle", "queued", "prepare", "download", "test",
                                 "backup", "install", "restart", "health", "service",
                                 "rollback", "complete", "failed", None):
                    raise ValueError("invalid update stage")
                if not isinstance(value, int) or isinstance(value, bool):
                    raise ValueError("invalid update percent")
                detail = loaded.get("detail")
                state = {
                    "state": phase, "step": stage or phase,
                    "percent": max(0, min(100, value)),
                    "detail": str(detail or "")[:240],
                    "updated_unix": loaded.get("updated_unix"),
                    "started_unix": loaded.get("started_unix"),
                }
                # An interrupted updater should not leave an infinite spinner.
                stamp = state["updated_unix"]
                if phase == "running" and isinstance(stamp, int) and (
                        time.time() - stamp > 480):
                    state.update(state="failed", step="failed",
                                 detail="Нет обновлений статуса более 8 минут; "
                                        "проверьте arc-update.service на VDS.")
        except (OSError, ValueError, TypeError):
            state.update(state="failed", step="failed",
                         detail="Не удалось прочитать состояние обновления")
        if (base / "request").is_file():
            state.update(state="queued", step="queued", percent=0,
                         detail="Запрос на обновление ожидает выполнения")
        state["enabled"] = base.is_dir() and os.access(base, os.W_OK)
        return state

    def request_server_update(self, name):
        if name != "owner":
            raise PermissionError("only owner can request server updates")
        with self.lock:
            if not (self.update_dir.is_dir() and os.access(self.update_dir, os.W_OK)):
                raise RuntimeError("server update service not configured")
            current = self.server_status(name)
            if current["state"] in ("running", "queued"):
                raise ValueError("server update already in progress")
            # File contents are intentionally not interpreted by privileged updater.
            # Exactly one fixed root-owned updater is activated by systemd.path.
            try:
                with (self.update_dir / "request").open("x", encoding="ascii") as file:
                    file.write("owner-approved\n")
            except FileExistsError:
                raise ValueError("server update already queued")
            self.hub.audit(name, "-", "web_server_update", "queued")
            return {"state": "queued", "detail": "Upgrade queued. You may need to log in again after restart."}

    def is_admin(self, name):
        return self.hub.users.get(name, {}).get("role") == "admin"

    def list_users(self):
        return [{"username": name, "role": data["role"],
                 "enabled": data.get("enabled", True),
                 "grants": data.get("grants", {})}
                for name, data in sorted(self.hub.users.items())]

    def devices_visible_to(self, name):
        """Owners see unassigned registration records without gaining tool access.

        This affects only the Web management list, not the MCP permissions.
        The owner must still grant scopes before calling any remote tool.
        """
        current = self.hub.device_list(name)
        if name != "owner":
            return current
        indexed = {item["device_id"] for item in current}
        for device_id, dev in sorted(self.hub.devices.items()):
            if device_id not in indexed:
                current.append({
                    "device_id": device_id,
                    "name": dev["name"],
                    "online": time.time() - self.hub.seen.get(device_id, 0) < 35,
                    "permissions": [],
                    "control_busy": bool(
                        self.hub.leases.get(device_id, {}).get("expires", 0)
                        > time.monotonic()),
                    "registered_without_access": True,
                })
        return sorted(current, key=lambda device: device["name"].lower())

    def list_device_catalog(self):
        return [{"device_id": ident, "name": device["name"],
                 "allowed_scopes": device.get("allowed_scopes", [])}
                for ident, device in sorted(self.hub.devices.items())]

    def _mutate_registry(self, update):
        if not self.config_path:
            raise RuntimeError("web administration unavailable without a registry path")
        with self.lock:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            if data.get("version") != 2:
                raise ValueError("invalid registry")
            result = update(data)
            fd, temporary = tempfile.mkstemp(prefix=".arc-web-", dir=self.config_path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, indent=2, ensure_ascii=False)
                    fh.write("\n")
                    fh.flush()
                    os.fsync(fh.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, self.config_path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            # Device enrollment still goes through the admin CLI, which restarts the service.
            self.hub.users = data["users"]
            return result

    def remove_device(self, acting, device_id, confirm_name):
        """Explicit owner-only forget: revoke device token and every user grant.

        This never deletes files on a Windows PC; it removes only registration
        on ARC Hub. Active tasks are refused rather than silently interrupted.
        """
        if acting != "owner":
            raise PermissionError("only owner can remove registered devices")
        if not isinstance(device_id, str) or not isinstance(confirm_name, str):
            raise ValueError("device_id and confirmation name are required")
        with self.lock:
            stored = self.hub.devices.get(device_id)
            if not stored:
                raise ValueError("device not found (possibly already removed)")
            if stored.get("name") != confirm_name:
                raise ValueError("confirmation name does not match the device")
            with self.hub.lock:
                if any(task["device"] == device_id for task in self.hub.pending.values()):
                    raise ValueError("device has pending operations; try again later")
                lease = self.hub.leases.get(device_id)
                if lease and lease.get("expires", 0) > time.monotonic():
                    raise ValueError("release active remote control before removing device")

            def mutate(db):
                if device_id not in db["devices"]:
                    raise ValueError("device was removed by another administrator")
                del db["devices"][device_id]
                for user in db["users"].values():
                    user.get("grants", {}).pop(device_id, None)
                return {"device_id": device_id, "removed": True}

            result = self._mutate_registry(mutate)
            with self.hub.lock:
                self.hub.devices.pop(device_id, None)
                self.hub.queues.pop(device_id, None)
                self.hub.seen.pop(device_id, None)
                self.hub.leases.pop(device_id, None)
            self.hub.audit(acting, device_id, "device_removed", "ok")
            return result

    def set_app_launch(self, acting, device_id, confirm_name, enabled):
        """Owner-only, specifically confirmed application-launch grant.

        This grants only apps.launch (plus existing status/inspect) to owner.
        Other users keep their existing permissions and local agent must
        independently opt in to apps before any launch can execute.
        """
        if acting != "owner":
            raise PermissionError("only owner may change device policy")
        if not isinstance(device_id, str) or not isinstance(confirm_name, str) or type(enabled) is not bool:
            raise ValueError("device id, confirmation name and boolean required")
        with self.lock:
            def mutate(data):
                device = data["devices"].get(device_id)
                if not device or device.get("name") != confirm_name:
                    raise ValueError("device name confirmation required")
                allowed = set(device["allowed_scopes"])
                if enabled:
                    allowed.add("apps.launch")
                    owner = data["users"]["owner"]
                    existing = set(owner.get("grants", {}).get(device_id, []))
                    existing.update(allowed & {"status", "inspect", "apps.launch"})
                    owner.setdefault("grants", {})[device_id] = sorted(existing)
                else:
                    allowed.discard("apps.launch")
                    for user in data["users"].values():
                        grants = user.get("grants", {})
                        if device_id in grants:
                            grants[device_id] = sorted(set(grants[device_id]) - {"apps.launch"})
                device["allowed_scopes"] = sorted(allowed)
                return {"device_id": device_id, "apps_launch": enabled,
                        "allowed_scopes": device["allowed_scopes"]}
            result = self._mutate_registry(mutate)
            with self.hub.lock:
                self.hub.devices[device_id]["allowed_scopes"] = result["allowed_scopes"]
            self.hub.audit(acting, device_id, "device_apps_policy",
                           "enabled" if enabled else "disabled")
            return result

    def create_user(self, acting, name, role):
        if not self.is_admin(acting) or not isinstance(name, str) or not USER_NAME.fullmatch(name):
            raise PermissionError("administrator or valid username required")
        if role not in ALLOWED_ROLES or (role == "admin" and acting != "owner"):
            raise PermissionError("only the owner can create administrators")
        credential = secrets.token_urlsafe(48)
        def mutate(data):
            if name in data["users"]:
                raise ValueError("username already exists")
            data["users"][name] = {"role": role, "enabled": True,
                                  "token_hash": _hash(credential), "grants": {}}
            return {"username": name, "role": role, "token": credential}
        return self._mutate_registry(mutate)

    def grant(self, acting, name, device_id, scopes):
        if not self.is_admin(acting):
            raise PermissionError("administrator required")
        if not isinstance(scopes, list) or not all(isinstance(x, str) for x in scopes):
            raise ValueError("scopes must be an array")
        def mutate(data):
            account = data["users"].get(name)
            device = data["devices"].get(device_id)
            if not account or not device:
                raise ValueError("unknown user or device")
            allowed = ROLE_SCOPES[account["role"]] & set(device["allowed_scopes"])
            if set(scopes) - allowed:
                raise PermissionError("requested access exceeds role or device policy")
            if not scopes:
                account["grants"].pop(device_id, None)
            else:
                account["grants"][device_id] = sorted(set(scopes))
            return {"username": name, "device_id": device_id, "scopes": sorted(set(scopes))}
        return self._mutate_registry(mutate)

    def set_user_enabled(self, acting, name, enabled):
        if not self.is_admin(acting):
            raise PermissionError("administrator required")
        if not isinstance(enabled, bool) or name in ("owner", acting):
            raise ValueError("cannot modify owner or current account")
        def mutate(data):
            account = data["users"].get(name)
            if account is None:
                raise ValueError("unknown user")
            account["enabled"] = enabled
            return {"username": name, "enabled": enabled}
        return self._mutate_registry(mutate)


def _send(handler, status, body, kind="application/json; charset=utf-8", cookie=None, redirect=None):
    if isinstance(body, (dict, list)):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    elif isinstance(body, str):
        data = body.encode("utf-8")
    else:
        data = body
    handler.send_response(status)
    handler.send_header("Content-Type", kind)
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store, max-age=0")
    handler.send_header("Pragma", "no-cache")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("X-Frame-Options", "DENY")
    handler.send_header("Referrer-Policy", "no-referrer")
    handler.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    handler.send_header("Content-Security-Policy",
        "default-src 'none'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'")
    if cookie:
        cookies = [cookie] if isinstance(cookie, str) else cookie
        for one_cookie in cookies:
            handler.send_header("Set-Cookie", one_cookie)
    if redirect:
        handler.send_header("Location", redirect)
    handler.end_headers()
    try:
        if data:
            handler.wfile.write(data)
    except (BrokenPipeError, OSError):
        pass


def _error(handler, status, msg):
    _send(handler, status, {"error": msg})


def _session(handler):
    s = handler.server.web.get_session(handler.headers.get("Cookie"))
    if not s:
        _error(handler, 401, "login required")
    return s


def handle_get(handler, path):
    web = handler.server.web
    if path == "/":
        _send(handler, 302, b"", redirect="/web/")
        return
    assets = {
        "/web/": ("index.html", "text/html; charset=utf-8"),
        "/web": ("index.html", "text/html; charset=utf-8"),
        "/web/app.css": ("app.css", "text/css; charset=utf-8"),
        "/web/app.js": ("app.js", "application/javascript; charset=utf-8")
    }
    if path in assets:
        filename, mime = assets[path]
        _send(handler, 200, (WEB_ROOT / filename).read_bytes(), kind=mime)
        return
    if path not in ("/web/api/me", "/web/api/devices", "/web/api/users", "/web/api/audit", "/web/api/server"):
        _error(handler, 404, "not found")
        return
    sess = _session(handler)
    if not sess:
        return
    user = sess["user"]
    if path == "/web/api/me":
        _send(handler, 200, {"user": user, "role": web.hub.users[user]["role"],
                             "csrf": sess["csrf"]})
    elif path == "/web/api/devices":
        _send(handler, 200, {"devices": web.devices_visible_to(user)})
    elif path == "/web/api/users":
        if not web.is_admin(user):
            return _error(handler, 403, "administrator required")
        _send(handler, 200, {"users": web.list_users(), "catalog": web.list_device_catalog()})
    elif path == "/web/api/server":
        if user != "owner":
            return _error(handler, 403, "owner required")
        _send(handler, 200, web.server_status(user))
    elif path == "/web/api/audit":
        if not web.is_admin(user):
            return _error(handler, 403, "administrator required")
        path_audit = web.hub.audit_path
        lines = deque(maxlen=80)
        if path_audit and path_audit.exists():
            with path_audit.open(encoding="utf-8", errors="replace") as f:
                for line in f:
                    if len(line) < 4096:
                        try:
                            lines.append(json.loads(line))
                        except ValueError:
                            pass
        _send(handler, 200, {"entries": list(lines)})


def handle_post(handler, path):
    web = handler.server.web
    if path not in ("/web/api/login", "/web/api/logout", "/web/api/call",
                    "/web/api/control", "/web/api/users/create",
                    "/web/api/users/grant", "/web/api/users/enabled",
                    "/web/api/pairing/create",
                    "/web/api/devices/remove", "/web/api/devices/apps",
                    "/web/api/server/update"):
        return _error(handler, 404, "not found")
    # Browsers send Origin; require it to match the external HTTPS host.
    origin = handler.headers.get("Origin")
    host = handler.headers.get("Host", "")
    if origin and origin != "https://" + host and not (
            origin == "http://" + host and host.startswith(("127.0.0.1:", "localhost:"))):
        return _error(handler, 403, "invalid origin")
    try:
        body = handler.json_body()
        if not isinstance(body, dict):
            return _error(handler, 400, "object required")
    except (ValueError, json.JSONDecodeError, TypeError):
        return _error(handler, 400, "invalid JSON")
    if path == "/web/api/login":
        login = web.login(body.get("user"), body.get("token"))
        if not login:
            return _error(handler, 401, "invalid login or temporary lockout")
        session, _ = login
        name = body["user"]
        web.hub.audit(name, "-", "web_login", "ok")
        cookie = ("arc_session=" + session +
                  "; Path=/web; HttpOnly; Secure; SameSite=Strict; Max-Age=" + str(SESSION_TTL))
        return _send(handler, 200, {"ok": True}, cookie=cookie)
    sess = _session(handler)
    if not sess:
        return
    if not hmac.compare_digest(str(body.get("csrf", "")), sess["csrf"]):
        return _error(handler, 403, "invalid CSRF token")
    user = sess["user"]
    try:
        if path == "/web/api/server/update":
            result = web.request_server_update(user)
            return _send(handler, 202, result)
        if path == "/web/api/pairing/create":
            return _send(handler, 201, web.pairing.issue(user))
        if path == "/web/api/devices/remove":
            result = web.remove_device(user, body.get("device_id"),
                                       body.get("confirm_name"))
            return _send(handler, 200, result)
        if path == "/web/api/devices/apps":
            result = web.set_app_launch(user, body.get("device_id"),
                                        body.get("confirm_name"), body.get("enabled"))
            return _send(handler, 200, result)
        if path == "/web/api/logout":
            web.logout(handler.headers.get("Cookie"))
            web.hub.audit(user, "-", "web_logout", "ok")
            return _send(handler, 200, {"ok": True},
                         cookie=["arc_session=; Path=/web; HttpOnly; Secure; SameSite=Strict; Max-Age=0",
                                 "arc_session=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0"])
        if path == "/web/api/call":
            tool, dev, args = body.get("tool"), body.get("device_id"), body.get("arguments", {})
            if not isinstance(args, dict) or len(json.dumps(args)) > 24_000:
                return _error(handler, 400, "invalid arguments")
            value = web.hub.dispatch(user, dev, tool, args)
            return _send(handler, 200, {"result": value})
        if path == "/web/api/control":
            dev = body.get("device_id")
            action = body.get("action")
            if action == "take":
                value = web.hub.take_control(user, dev)
            elif action == "release":
                value = web.hub.release_control(user, dev)
            else:
                return _error(handler, 400, "invalid action")
            return _send(handler, 200, value)
        if path == "/web/api/users/create":
            result = web.create_user(user, body.get("username"), body.get("role"))
            web.hub.audit(user, "-", "web_create_user", "ok")
            return _send(handler, 201, result)
        if path == "/web/api/users/grant":
            result = web.grant(user, body.get("username"),
                               body.get("device_id"), body.get("scopes"))
            web.hub.audit(user, body.get("device_id"), "web_set_grant", "ok")
            return _send(handler, 200, result)
        if path == "/web/api/users/enabled":
            result = web.set_user_enabled(user, body.get("username"), body.get("enabled"))
            web.hub.audit(user, "-", "web_set_user_enabled", "ok")
            return _send(handler, 200, result)
    except PermissionError as exc:
        return _error(handler, 403, str(exc)[:180])
    except (TypeError, ValueError) as exc:
        return _error(handler, 400, str(exc)[:180])
    except (RuntimeError, TimeoutError, OSError) as exc:
        return _error(handler, 503, str(exc)[:180])
