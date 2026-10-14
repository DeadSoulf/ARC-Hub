"""ARC Hub OAuth 2.1 authorization-code / PKCE gateway for ChatGPT MCP.

Public clients are registered only with allowlisted ChatGPT callback URLs.
Opaque bearer/refresh tokens and auth codes are stored as SHA-256 digests,
bound to the one MCP resource, client, user and current user credential hash.
The existing Hub remains responsible for per-device scopes and control leases.
"""
from __future__ import annotations

import base64
from http.cookies import SimpleCookie
import hashlib
import hmac
import html
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit

ACCESS_TTL = 3600
REFRESH_TTL = 14 * 86400
FLOW_TTL = 600
CODE_TTL = 180
PKCE = re.compile(r"^[A-Za-z0-9_-]{43}$")
VERIFIER = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
SCOPE = "arc:access"
CALLBACK = re.compile(r"^/connector/oauth/[A-Za-z0-9_-]{8,128}$")


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _one(params, key):
    value = params.get(key, [])
    return value[0] if len(value) == 1 else None


def _valid_redirect(value):
    if not isinstance(value, str) or len(value) > 350:
        return False
    try:
        url = urlsplit(value)
    except ValueError:
        return False
    # The issuer advertises RFC 9207 identification, so ChatGPT uses
    # one stable callback. Never let anonymous DCR callers invent arbitrary
    # callback IDs and exhaust client slots.
    return (url.scheme == "https" and url.netloc == "chatgpt.com"
            and not url.query and not url.fragment
            and url.path == "/connector_platform_oauth_redirect")


class OAuth:
    """Thread-safe, single-node OAuth provider with SQLite persistence."""

    def __init__(self, hub, web, issuer, db_path, *, allow_test_http=False):
        uri = urlsplit(issuer)
        if (uri.scheme != "https" and not (allow_test_http
                and uri.scheme == "http" and uri.hostname in ("localhost", "127.0.0.1"))):
            raise ValueError("OAuth issuer must use HTTPS")
        if not uri.netloc or uri.path not in ("", "/") or uri.query or uri.fragment or uri.username:
            raise ValueError("OAuth issuer must be a bare HTTPS origin")
        self.issuer = issuer.rstrip("/")
        self.resource = self.issuer + "/mcp"
        self.hub = hub
        self.web = web
        self.lock = threading.RLock()
        self.flows = {}
        if db_path != ":memory:":
            path = Path(db_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            db_path = str(path)
        self.db = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
        if db_path != ":memory:":
            os.chmod(db_path, 0o600)
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS clients(
                id TEXT PRIMARY KEY, redirects TEXT NOT NULL, created INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS codes(
                hash TEXT PRIMARY KEY, client TEXT NOT NULL, redirect TEXT NOT NULL,
                challenge TEXT NOT NULL, user TEXT NOT NULL, credential TEXT NOT NULL,
                resource TEXT NOT NULL, scope TEXT NOT NULL, expires INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS access_tokens(
                hash TEXT PRIMARY KEY, client TEXT NOT NULL, user TEXT NOT NULL,
                credential TEXT NOT NULL, resource TEXT NOT NULL, scope TEXT NOT NULL,
                expires INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS refresh_tokens(
                hash TEXT PRIMARY KEY, client TEXT NOT NULL, user TEXT NOT NULL,
                credential TEXT NOT NULL, resource TEXT NOT NULL, scope TEXT NOT NULL,
                expires INTEGER NOT NULL);
        """)

    def protected_metadata(self):
        return {"resource": self.resource,
                "authorization_servers": [self.issuer],
                "scopes_supported": [SCOPE]}

    def authorization_metadata(self):
        return {"issuer": self.issuer,
                "authorization_response_iss_parameter_supported": True,
                "authorization_endpoint": self.issuer + "/oauth/authorize",
                "token_endpoint": self.issuer + "/oauth/token",
                "registration_endpoint": self.issuer + "/oauth/register",
                "token_endpoint_auth_methods_supported": ["none"],
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "code_challenge_methods_supported": ["S256"],
                "scopes_supported": [SCOPE],
                "service_documentation": self.issuer + "/web/"}

    def challenge(self):
        return 'Bearer resource_metadata="' + self.issuer + '/.well-known/oauth-protected-resource", scope="' + SCOPE + '"'

    def _respond(self, handler, status, content, *, kind="application/json; charset=utf-8",
                 headers=None):
        if isinstance(content, (dict, list)):
            data = json.dumps(content, ensure_ascii=False).encode("utf-8")
        elif isinstance(content, str):
            data = content.encode("utf-8")
        else:
            data = content
        handler.send_response(status)
        handler.send_header("Content-Type", kind)
        handler.send_header("Content-Length", str(len(data)))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.send_header("X-Frame-Options", "DENY")
        handler.send_header("Referrer-Policy", "no-referrer")
        handler.send_header("Content-Security-Policy",
                            "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        for key, value in (headers or []):
            handler.send_header(key, value)
        handler.end_headers()
        try:
            handler.wfile.write(data)
        except (BrokenPipeError, OSError):
            pass

    def _error(self, handler, status, error):
        self._respond(handler, status, {"error": error})

    def _body(self, handler, mime):
        if handler.headers.get("Content-Type", "").split(";")[0].strip() != mime:
            raise ValueError("invalid content type")
        try:
            size = int(handler.headers.get("Content-Length", "-1"))
        except ValueError as exc:
            raise ValueError("invalid length") from exc
        if not 0 <= size <= 8192:
            raise ValueError("invalid request size")
        raw = handler.rfile.read(size)
        if mime == "application/json":
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("expected object")
            return data
        return parse_qs(raw.decode("utf-8"), keep_blank_values=True, max_num_fields=30)

    def _cleanup(self):
        now = int(time.time())
        for table in ("codes", "access_tokens", "refresh_tokens"):
            self.db.execute("DELETE FROM " + table + " WHERE expires < ?", (now,))
        self.flows = {key: val for key, val in self.flows.items() if val["expires"] >= now}

    def _client(self, client_id, redirect=None):
        with self.lock:
            row = self.db.execute("SELECT redirects FROM clients WHERE id=?", (client_id,)).fetchone()
        if row is None:
            return False
        return redirect is None or redirect in json.loads(row[0])

    def register(self, handler):
        if handler.headers.get("Origin"):
            return self._error(handler, 403, "browser registration not allowed")
        try:
            body = self._body(handler, "application/json")
            redirects = body.get("redirect_uris")
            if (not isinstance(redirects, list) or not 1 <= len(redirects) <= 4
                    or len(set(redirects)) != len(redirects)
                    or not all(_valid_redirect(u) for u in redirects)):
                raise ValueError("invalid ChatGPT redirect_uris")
            if body.get("token_endpoint_auth_method", "none") != "none":
                raise ValueError("only public PKCE clients are allowed")
            grants = body.get("grant_types", ["authorization_code"])
            if (not isinstance(grants, list) or not 1 <= len(grants) <= 2
                    or len(set(grants)) != len(grants)
                    or "authorization_code" not in grants
                    or not set(grants) <= {"authorization_code", "refresh_token"}):
                raise ValueError("unsupported grant type")
            if body.get("response_types", ["code"]) != ["code"]:
                raise ValueError("unsupported response type")
            with self.lock, self.db:
                # Reuse DCR public client identity for identical allowed
                # redirect URIs, preventing trivial registration exhaustion.
                canonical = json.dumps(sorted(redirects))
                existing = self.db.execute(
                    "SELECT id FROM clients WHERE redirects=? LIMIT 1",
                    (canonical,)).fetchone()
                if existing:
                    client = existing[0]
                else:
                    count = self.db.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
                    if count >= 256:
                        raise ValueError("client registrations full")
                    client = "arc_" + secrets.token_urlsafe(24)
                    self.db.execute("INSERT INTO clients VALUES(?,?,?)",
                                    (client, canonical, int(time.time())))
            return self._respond(handler, 201, {
                "client_id": client, "client_id_issued_at": int(time.time()),
                "client_name": "ChatGPT ARC",
                "redirect_uris": redirects,
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"]})
        except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
            return self._error(handler, 400, "invalid_client_metadata")

    def _page(self, handler, flow, session=None, error=""):
        name = session["user"] if session else None
        greeting = ("Пользователь: " + html.escape(name)) if name else "Войдите в ARC, чтобы разрешить доступ."
        fields = "" if name else ("""
            <label>Пользователь ARC <input name="username" autocomplete="username" required></label>
            <label>Персональный токен ARC <input name="credential" type="password" autocomplete="off" required></label>
        """)
        failure = ("<p role='alert'>" + html.escape(error) + "</p>") if error else ""
        page = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <title>ARC — подтверждение доступа ChatGPT</title></head><body>
        <main><h1>ARC Hub — подключение ChatGPT</h1>
        <p>Клиент: ChatGPT. Доступ только к устройствам и действиям,
        которые разрешены вашей учётной записи ARC.</p>
        <p>Запрашиваемое разрешение: arc:access.</p><p>%s</p>%s
        <form method="post" action="/oauth/authorize">
        <input type="hidden" name="flow" value="%s">
        <input type="hidden" name="csrf" value="%s">
        %s
        <button name="decision" value="approve">Разрешить ChatGPT</button>
        <button name="decision" value="deny">Отказать</button>
        </form></main></body></html>""" % (
            greeting, failure, html.escape(flow["id"]), html.escape(flow["csrf"]), fields)
        self._respond(handler, 200, page, kind="text/html; charset=utf-8",
                      headers=[("Set-Cookie", "arc_oauth_flow=" + flow["id"]
                                + "; Path=/oauth; HttpOnly; Secure; SameSite=Lax; Max-Age=600")])

    def authorize_get(self, handler):
        q = parse_qs(urlsplit(handler.path).query, keep_blank_values=True)
        client, redirect = _one(q, "client_id"), _one(q, "redirect_uri")
        code_challenge = _one(q, "code_challenge")
        response_type, method = _one(q, "response_type"), _one(q, "code_challenge_method")
        resource, scope = _one(q, "resource"), _one(q, "scope")
        state = _one(q, "state")
        if (not isinstance(client, str) or not isinstance(redirect, str)
                or not self._client(client, redirect) or response_type != "code"
                or method != "S256" or resource != self.resource
                or not isinstance(code_challenge, str) or not PKCE.fullmatch(code_challenge)
                or scope not in (None, "", SCOPE)
                or not isinstance(state, str) or len(state) > 512):
            return self._error(handler, 400, "invalid_authorization_request")
        with self.lock:
            self._cleanup()
            if len(self.flows) >= 256:
                return self._error(handler, 429, "too_many_pending_flows")
            ident = secrets.token_urlsafe(32)
            flow = {"id": ident, "csrf": secrets.token_urlsafe(24),
                    "client": client, "redirect": redirect,
                    "challenge": code_challenge, "resource": resource,
                    "scope": SCOPE, "state": state,
                    "expires": int(time.time()) + FLOW_TTL}
            self.flows[ident] = flow
        session = self.web.get_session(handler.headers.get("Cookie"))
        return self._page(handler, flow, session)

    def authorize_post(self, handler):
        if handler.headers.get("Origin") != self.issuer:
            return self._error(handler, 403, "invalid origin")
        try:
            form = self._body(handler, "application/x-www-form-urlencoded")
            flow_id, csrf = _one(form, "flow"), _one(form, "csrf")
            cookies = SimpleCookie()
            cookies.load((handler.headers.get("Cookie") or "")[:4096])
            flow_cookie = cookies.get("arc_oauth_flow")
            if (not isinstance(flow_id, str) or not flow_cookie
                    or not hmac.compare_digest(flow_id, flow_cookie.value)):
                return self._error(handler, 403, "invalid authorization session")
            with self.lock:
                flow = self.flows.get(flow_id)
                if not flow or flow["expires"] < time.time() or (
                        not isinstance(csrf, str) or not hmac.compare_digest(csrf, flow["csrf"])):
                    return self._error(handler, 403, "authorization session expired")
            decision = _one(form, "decision")
            if decision == "deny":
                with self.lock:
                    self.flows.pop(flow_id, None)
                return self._redirect(handler, flow, error="access_denied")
            if decision != "approve":
                return self._error(handler, 400, "invalid decision")
            session = self.web.get_session(handler.headers.get("Cookie"))
            extra_headers = []
            if session is None:
                login = self.web.login(_one(form, "username"), _one(form, "credential"))
                if login is None:
                    return self._page(handler, flow, error="Неверные данные входа или временная блокировка.")
                sid, _ = login
                extra_headers.append(("Set-Cookie", "arc_session=" + sid +
                                      "; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=28800"))
                session = self.web.get_session("arc_session=" + sid)
            user = session["user"]
            account = self.hub.users.get(user)
            if not account or not account.get("enabled", True):
                return self._error(handler, 403, "account disabled")
            with self.lock, self.db:
                # One successful approval for each browser-bound flow.
                if self.flows.pop(flow_id, None) is None:
                    return self._error(handler, 403, "authorization already used")
                code = secrets.token_urlsafe(40)
                self.db.execute("INSERT INTO codes VALUES(?,?,?,?,?,?,?,?,?)",
                                (digest(code), flow["client"], flow["redirect"],
                                 flow["challenge"], user, account["token_hash"],
                                 flow["resource"], flow["scope"], int(time.time()) + CODE_TTL))
            self.hub.audit(user, "-", "oauth_authorized", "ok")
            return self._redirect(handler, flow, code=code, headers=extra_headers)
        except (ValueError, TypeError, UnicodeError):
            return self._error(handler, 400, "invalid_authorization_request")

    def _redirect(self, handler, flow, *, code=None, error=None, headers=None):
        args = {"state": flow["state"], "iss": self.issuer}
        if code:
            args["code"] = code
        else:
            args["error"] = error or "access_denied"
        url = flow["redirect"] + "?" + urlencode(args)
        self._respond(handler, 302, b"", headers=[("Location", url)] + (headers or []))

    def _issue_tokens(self, client, user, credential, resource, scope):
        token, refresh = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
        now = int(time.time())
        self.db.execute("INSERT INTO access_tokens VALUES(?,?,?,?,?,?,?)",
                        (digest(token), client, user, credential, resource, scope, now + ACCESS_TTL))
        self.db.execute("INSERT INTO refresh_tokens VALUES(?,?,?,?,?,?,?)",
                        (digest(refresh), client, user, credential, resource, scope, now + REFRESH_TTL))
        return {"access_token": token, "refresh_token": refresh, "token_type": "Bearer",
                "expires_in": ACCESS_TTL, "scope": scope}

    def token(self, handler):
        if handler.headers.get("Origin"):
            return self._error(handler, 403, "browser token requests are not allowed")
        try:
            form = self._body(handler, "application/x-www-form-urlencoded")
            grant, client, resource = (_one(form, "grant_type"), _one(form, "client_id"),
                                      _one(form, "resource"))
            if not client or not self._client(client):
                return self._error(handler, 400, "invalid_client")
            # Authorization codes require the exact MCP resource. Refresh
            # tokens are already audience-bound in the database; allow a
            # missing resource on refresh, but never another resource.
            if (grant == "authorization_code" and resource != self.resource) or (
                    grant == "refresh_token" and resource not in (None, self.resource)):
                return self._error(handler, 400, "invalid_target")
            with self.lock, self.db:
                self._cleanup()
                if grant == "authorization_code":
                    code, redirect, verifier = (_one(form, "code"),
                                               _one(form, "redirect_uri"),
                                               _one(form, "code_verifier"))
                    if (not isinstance(code, str) or not isinstance(redirect, str)
                            or not isinstance(verifier, str) or not VERIFIER.fullmatch(verifier)):
                        return self._error(handler, 400, "invalid_grant")
                    row = self.db.execute(
                        "SELECT client,redirect,challenge,user,credential,resource,scope,expires "
                        "FROM codes WHERE hash=?", (digest(code),)).fetchone()
                    # Consume even on invalid verifier to prevent replay/brute-force.
                    self.db.execute("DELETE FROM codes WHERE hash=?", (digest(code),))
                    if not row:
                        return self._error(handler, 400, "invalid_grant")
                    c, r, challenge, user, credential, audience, scope, expiry = row
                    proof = base64.urlsafe_b64encode(hashlib.sha256(
                        verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
                    if (c != client or r != redirect or audience != resource
                            or expiry < time.time() or not hmac.compare_digest(challenge, proof)):
                        return self._error(handler, 400, "invalid_grant")
                elif grant == "refresh_token":
                    refresh = _one(form, "refresh_token")
                    if not isinstance(refresh, str):
                        return self._error(handler, 400, "invalid_grant")
                    row = self.db.execute(
                        "SELECT client,user,credential,resource,scope,expires FROM refresh_tokens "
                        "WHERE hash=?", (digest(refresh),)).fetchone()
                    self.db.execute("DELETE FROM refresh_tokens WHERE hash=?", (digest(refresh),))
                    if not row:
                        return self._error(handler, 400, "invalid_grant")
                    c, user, credential, audience, scope, expiry = row
                    if c != client or audience != resource or expiry < time.time():
                        return self._error(handler, 400, "invalid_grant")
                else:
                    return self._error(handler, 400, "unsupported_grant_type")
                account = self.hub.users.get(user)
                if (not account or not account.get("enabled", True)
                        or not hmac.compare_digest(account["token_hash"], credential)):
                    return self._error(handler, 400, "invalid_grant")
                result = self._issue_tokens(client, user, credential, self.resource, scope)
            return self._respond(handler, 200, result)
        except (ValueError, TypeError, UnicodeError):
            return self._error(handler, 400, "invalid_request")

    def authenticate(self, raw):
        if not isinstance(raw, str) or not 16 <= len(raw) <= 512:
            return None
        with self.lock:
            row = self.db.execute(
                "SELECT user,credential,resource,scope,expires FROM access_tokens WHERE hash=?",
                (digest(raw),)).fetchone()
        if row is None:
            return None
        user, credential, resource, scope, expires = row
        account = self.hub.users.get(user)
        if (expires < time.time() or resource != self.resource or scope != SCOPE
                or not account or not account.get("enabled", True)
                or not hmac.compare_digest(account["token_hash"], credential)):
            return None
        return user

    def get(self, handler, path):
        if path in ("/.well-known/oauth-protected-resource",
                    "/.well-known/oauth-protected-resource/mcp"):
            return self._respond(handler, 200, self.protected_metadata())
        if path == "/.well-known/oauth-authorization-server":
            return self._respond(handler, 200, self.authorization_metadata())
        if path == "/oauth/authorize":
            return self.authorize_get(handler)
        return self._error(handler, 404, "not_found")

    def post(self, handler, path):
        if path == "/oauth/register":
            return self.register(handler)
        if path == "/oauth/token":
            return self.token(handler)
        if path == "/oauth/authorize":
            return self.authorize_post(handler)
        return self._error(handler, 404, "not_found")
