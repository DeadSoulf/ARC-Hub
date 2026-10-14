"""HTTP/browser security tests for ARC Web: login, sessions, roles and CSRF."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vds"))
from arc_hub import Hub, Server, digest


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "registry.json"
        self.log = Path(self.temp.name) / "audit.jsonl"
        self.registry = {"version": 2,
            "devices": {
                "asus": {"name": "Asus", "token_hash": digest("device-secret"),
                         "enabled": True,
                         "allowed_scopes": ["status", "inspect", "screen.read", "shell.exec"]},
                "office": {"name": "Office", "token_hash": digest("office-secret"),
                           "enabled": True, "allowed_scopes": ["status"]}},
            "users": {
                "owner": {"role": "admin", "enabled": True,
                          "token_hash": digest("owner-"+("x"*50)),
                          "grants": {"asus": ["status", "inspect", "shell.exec"]}},
                "bob": {"role": "viewer", "enabled": True,
                        "token_hash": digest("bob-"+("z"*50)),
                        "grants": {"office": ["status"]}}
            }
        }
        self.path.write_text(json.dumps(self.registry), encoding="utf-8")
        self.hub = Hub(self.registry, self.log)
        self.server = Server(("127.0.0.1", 0), self.hub, self.path)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.temp.cleanup()

    def request(self, path, body=None, cookie=None, origin=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if cookie:
            headers["Cookie"] = cookie
        if origin:
            headers["Origin"] = origin
        req = urllib.request.Request(self.base + path, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=3) as response:
                content = response.read()
                return response.status, dict(response.headers), (
                    json.loads(content) if path.startswith("/web/api/") else content)
        except urllib.error.HTTPError as err:
            content = err.read()
            return err.code, dict(err.headers), (json.loads(content) if content else {})

    def login(self, username="owner", token=None):
        token = token if token is not None else username + "-" + ("x" if username=="owner" else "z")*50
        status, headers, body = self.request("/web/api/login", {"user":username, "token":token})
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"].split(";",1)[0]
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("Secure", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        status, _, me = self.request("/web/api/me", cookie=cookie)
        self.assertEqual(status, 200)
        return cookie, me["csrf"]

    def test_public_static_private_api(self):
        status, headers, data = self.request("/")
        self.assertEqual(status, 200)  # urllib follows redirect
        self.assertIn(b"ARC", data)
        self.assertIn("Content-Security-Policy", headers)
        status, _, css = self.request("/web/app.css")
        self.assertEqual(status, 200)
        self.assertIn(b"sidebar", css)
        status, _, data = self.request("/web/api/devices")
        self.assertEqual(status, 401)

    def test_auth_not_trusting_wrong_token(self):
        status, _, _ = self.request("/web/api/login", {"user":"owner", "token":"wrong"})
        self.assertEqual(status, 401)
        status, _, _ = self.request("/web/api/login", {"user":"unknown", "token":"x"*52})
        self.assertEqual(status, 401)

    def test_user_isolation(self):
        cookie, csrf = self.login("bob")
        status, _, devices = self.request("/web/api/devices", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual([x["device_id"] for x in devices["devices"]], ["office"])
        status, _, _ = self.request("/web/api/users", cookie=cookie)
        self.assertEqual(status, 403)
        status, _, _ = self.request("/web/api/call", {
            "csrf":csrf, "device_id":"asus", "tool":"computer_status", "arguments":{}
        }, cookie=cookie)
        self.assertEqual(status, 403)

    def test_csrf_and_origin_on_mutation(self):
        cookie, csrf = self.login()
        status, _, _ = self.request("/web/api/users/create",
                                    {"username":"user1", "role":"viewer"}, cookie=cookie)
        self.assertEqual(status, 403)
        status, _, _ = self.request("/web/api/users/create",
                                    {"csrf":csrf, "username":"user1", "role":"viewer"},
                                    cookie=cookie, origin="https://evil.example")
        self.assertEqual(status, 403)
        self.assertNotIn("user1", self.hub.users)

    def test_create_grant_disable_and_session_revocation(self):
        cookie, csrf = self.login()
        status, _, result = self.request("/web/api/users/create", {
            "csrf":csrf, "username":"newuser", "role":"viewer"}, cookie=cookie)
        self.assertEqual(status, 201)
        self.assertGreaterEqual(len(result["token"]), 48)
        registry = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertNotIn(result["token"], self.path.read_text(encoding="utf-8"))
        self.assertIn("newuser", registry["users"])
        status, _, result2 = self.request("/web/api/users/grant", {
            "csrf":csrf, "username":"newuser", "device_id":"asus",
            "scopes":["status"]}, cookie=cookie)
        self.assertEqual(status, 200)
        status, _, _ = self.request("/web/api/users/grant", {
            "csrf":csrf, "username":"newuser", "device_id":"asus",
            "scopes":["shell.exec"]}, cookie=cookie)
        self.assertEqual(status, 403)
        child, child_csrf = self.login("newuser", result["token"])
        status, _, owned = self.request("/web/api/devices", cookie=child)
        self.assertEqual(status, 200)
        self.assertEqual(owned["devices"][0]["device_id"], "asus")
        status, _, _ = self.request("/web/api/users/enabled", {
            "csrf":csrf, "username":"newuser", "enabled":False}, cookie=cookie)
        self.assertEqual(status, 200)
        status, _, _ = self.request("/web/api/me", cookie=child)
        self.assertEqual(status, 401)

    def test_owner_only_update_queue(self):
        location = Path(self.temp.name) / "update-queue"
        location.mkdir()
        self.server.web.update_dir = location
        owner, csrf = self.login()
        status, _, info = self.request("/web/api/server", cookie=owner)
        self.assertEqual(status, 200)
        self.assertTrue(info["enabled"])
        self.assertEqual(info["state"], "idle")
        status, _, _ = self.request("/web/api/server/update",
                                    {}, cookie=owner)
        self.assertEqual(status, 403)  # CSRF required
        status, _, result = self.request("/web/api/server/update",
                                         {"csrf": csrf}, cookie=owner)
        self.assertEqual(status, 202)
        self.assertEqual(result["state"], "queued")
        self.assertTrue((location / "request").is_file())
        status, _, _ = self.request("/web/api/server/update",
                                    {"csrf": csrf}, cookie=owner)
        self.assertEqual(status, 400)  # no duplicate queueing
        bob, bob_csrf = self.login("bob")
        status, _, _ = self.request("/web/api/server", cookie=bob)
        self.assertEqual(status, 403)
        status, _, _ = self.request("/web/api/server/update",
                                    {"csrf": bob_csrf}, cookie=bob)
        self.assertEqual(status, 403)
        # Queue data is a marker only: no shell command or URL can be passed.
        self.assertNotIn("curl", (location / "request").read_text())

    def test_logout_and_owner_protection(self):
        cookie, csrf = self.login()
        status, _, _ = self.request("/web/api/users/enabled", {
            "csrf":csrf, "username":"owner", "enabled":False}, cookie=cookie)
        self.assertEqual(status, 400)
        status, headers, _ = self.request("/web/api/logout",
                                            {"csrf":csrf}, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        status, _, _ = self.request("/web/api/me", cookie=cookie)
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
