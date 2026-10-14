"""OAuth/MCP end-to-end regression tests: CSRF, PKCE, audience, expiry, roles."""
import base64
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import time
import threading
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit, parse_qs
from urllib.request import HTTPRedirectHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vds"))
from arc_hub import Hub, Server, digest
from arc_oauth import OAuth


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class OAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.registry = {"version": 2, "devices": {
            "asus": {"name": "Asus", "enabled": True,
                     "token_hash": digest("device-secret"),
                     "allowed_scopes": ["status", "inspect", "apps.launch"]}},
            "users": {"owner": {"enabled": True, "role": "admin",
                    "token_hash": digest("owner-" + "x" * 48),
                    "grants": {"asus": ["status", "inspect", "apps.launch"]}}}}
        self.hub = Hub(self.registry)
        self.server = Server(("127.0.0.1", 0), self.hub)
        self.base = "http://127.0.0.1:" + str(self.server.server_port)
        self.oauth = OAuth(self.hub, self.server.web, self.base,
                           Path(self.tmp.name) / "oauth.sqlite3",
                           allow_test_http=True)
        self.server.oauth = self.oauth
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.opener = build_opener(NoRedirect)
        self.redirect = "https://chatgpt.com/connector_platform_oauth_redirect"
        self.verifier = "u" * 52
        self.challenge = base64.urlsafe_b64encode(hashlib.sha256(
            self.verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.oauth.db.close()
        self.tmp.cleanup()

    def request(self, path, data=None, *, mime=None, headers=None):
        req = Request(self.base + path,
                      data=data,
                      headers={"Content-Type": mime, **(headers or {})} if mime else (headers or {}))
        try:
            response = self.opener.open(req, timeout=3)
        except HTTPError as err:
            response = err
        with response:
            body = response.read()
            kind = response.headers.get("Content-Type", "")
            if kind.startswith("application/json") and body:
                body = json.loads(body)
            return response.status, response.headers, body

    def register(self, redirects=None):
        body = json.dumps({"client_name": "ChatGPT",
                           "token_endpoint_auth_method": "none",
                           "redirect_uris": redirects or [self.redirect]}).encode()
        code, _, data = self.request("/oauth/register", body, mime="application/json")
        self.assertEqual(code, 201, data)
        return data["client_id"]

    def authorization(self, client=None, **changes):
        fields = {"client_id": client or self.register(),
                  "redirect_uri": self.redirect,
                  "response_type": "code", "code_challenge": self.challenge,
                  "code_challenge_method": "S256",
                  "resource": self.base + "/mcp",
                  "scope": "arc:access", "state": "client-state"}
        fields.update(changes)
        return "/oauth/authorize?" + urlencode(fields)

    def approve(self, path, *, password=None, csrf_override=None, origin=None):
        status, headers, page = self.request(path)
        self.assertEqual(status, 200, page)
        text = page.decode()
        flow = re.search(r'name="flow" value="([^"]+)"', text).group(1)
        csrf = re.search(r'name="csrf" value="([^"]+)"', text).group(1)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        form = urlencode({
            "flow": flow, "csrf": csrf if csrf_override is None else csrf_override,
            "username": "owner", "credential": password or ("owner-" + "x" * 48),
            "decision": "approve"
        }).encode()
        status, hdr, body = self.request(
            "/oauth/authorize", form, mime="application/x-www-form-urlencoded",
            headers={"Cookie": cookie, "Origin": origin or self.base})
        return status, hdr, body

    def exchange(self, code, client, verifier=None, resource=None):
        form = urlencode({"grant_type": "authorization_code",
                          "client_id": client, "code": code,
                          "redirect_uri": self.redirect,
                          "code_verifier": verifier or self.verifier,
                          "resource": resource or (self.base + "/mcp")}).encode()
        return self.request("/oauth/token", form, mime="application/x-www-form-urlencoded")

    def authorize_token(self):
        client = self.register()
        status, hdr, _ = self.approve(self.authorization(client))
        self.assertEqual(status, 302)
        location = hdr["Location"]
        self.assertEqual(urlsplit(location).netloc, "chatgpt.com")
        query = parse_qs(urlsplit(location).query)
        self.assertEqual(query["state"], ["client-state"])
        self.assertEqual(query["iss"], [self.base])
        self.assertEqual(len(query["code"]), 1)
        status, _, token = self.exchange(query["code"][0], client)
        self.assertEqual(status, 200, token)
        return client, query["code"][0], token

    def test_metadata_and_challenge(self):
        status, _, doc = self.request("/.well-known/oauth-protected-resource")
        self.assertEqual(status, 200)
        self.assertEqual(doc["resource"], self.base + "/mcp")
        status, headers, _ = self.request("/mcp")
        self.assertEqual(status, 405)
        self.assertEqual(headers["Allow"], "POST")
        status, _, meta = self.request("/.well-known/oauth-authorization-server")
        self.assertEqual(status, 200)
        self.assertEqual(meta["code_challenge_methods_supported"], ["S256"])
        self.assertTrue(meta["authorization_response_iss_parameter_supported"])
        mcp = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
        status, _, result = self.request("/mcp", mcp, mime="application/json")
        self.assertEqual(status, 200)
        self.assertEqual(len(result["result"]["tools"]), 5)
        self.assertEqual(result["result"]["tools"][0]["securitySchemes"][0]["type"], "oauth2")
        call = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                           "params": {"name": "arc_devices", "arguments": {}}}).encode()
        status, _, challenge = self.request("/mcp", call, mime="application/json")
        self.assertEqual(status, 200)
        error = challenge["result"]
        self.assertTrue(error["isError"])
        self.assertIn("mcp/www_authenticate", error["_meta"])
        self.assertIn("resource_metadata", error["_meta"]["mcp/www_authenticate"][0])
        self.assertIn('error="invalid_token"', error["_meta"]["mcp/www_authenticate"][0])

    def test_registration_supports_refresh_and_reuses_client(self):
        metadata = {"client_name": "ChatGPT", "redirect_uris": [self.redirect],
                    "token_endpoint_auth_method": "none",
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"]}
        payload = json.dumps(metadata).encode()
        status, _, first = self.request("/oauth/register", payload, mime="application/json")
        self.assertEqual(status, 201, first)
        status, _, second = self.request("/oauth/register", payload, mime="application/json")
        self.assertEqual(status, 201, second)
        self.assertEqual(first["client_id"], second["client_id"])
        self.assertEqual(self.oauth.db.execute("SELECT COUNT(*) FROM clients").fetchone()[0], 1)

    def test_legacy_arc_bearer_does_not_bypass_oauth(self):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "arc_devices", "arguments": {}}}).encode()
        status, _, denied = self.request(
            "/mcp", body, mime="application/json",
            headers={"Authorization": "Bearer owner-" + "x" * 48})
        self.assertEqual(status, 200)
        self.assertTrue(denied["result"]["isError"])
        self.assertIn("mcp/www_authenticate", denied["result"]["_meta"])

    def test_client_redirect_allowlist_and_flow_validation(self):
        rejected = json.dumps({"redirect_uris": ["https://evil.example/callback"]}).encode()
        status, _, body = self.request("/oauth/register", rejected, mime="application/json")
        self.assertEqual(status, 400)
        client = self.register()
        status, _, _ = self.request(self.authorization(client, redirect_uri="https://evil.example"))
        self.assertEqual(status, 400)
        status, _, _ = self.request(self.authorization(client, code_challenge_method="plain"))
        self.assertEqual(status, 400)
        status, _, _ = self.request(self.authorization(client, resource="https://evil.example/mcp"))
        self.assertEqual(status, 400)
        status, _, _ = self.request(self.authorization(client, scope="shell.exec"))
        self.assertEqual(status, 400)

    def test_bad_csrf_origin_and_login(self):
        client = self.register()
        path = self.authorization(client)
        status, _, _ = self.approve(path, csrf_override="wrong")
        self.assertEqual(status, 403)
        status, _, _ = self.approve(path, origin="https://evil.example")
        self.assertEqual(status, 403)
        status, _, _ = self.approve(path, password="bad")
        self.assertEqual(status, 200)  # Login retry shown, never redirected.

    def test_valid_access_and_replay_rejection(self):
        client, code, token = self.authorize_token()
        self.assertEqual(token["scope"], "arc:access")
        status, _, _ = self.exchange(code, client)
        self.assertEqual(status, 400)
        body = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                           "params": {"name": "arc_devices", "arguments": {}}}).encode()
        status, _, response = self.request("/mcp", body, mime="application/json",
                                          headers={"Authorization": "Bearer " + token["access_token"]})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(response["result"]["content"][0]["text"])[0]["device_id"], "asus")
        status, _, response = self.request("/mcp", body, mime="application/json",
                                          headers={"Authorization": "Bearer bogus"})
        self.assertEqual(status, 200)
        self.assertTrue(response["result"]["isError"])
        self.assertIn("mcp/www_authenticate", response["result"]["_meta"])

    def test_pkce_mismatch_consumes_code(self):
        client = self.register()
        status, hdr, _ = self.approve(self.authorization(client))
        code = parse_qs(urlsplit(hdr["Location"]).query)["code"][0]
        status, _, _ = self.exchange(code, client, verifier="x" * 52)
        self.assertEqual(status, 400)
        status, _, _ = self.exchange(code, client)
        self.assertEqual(status, 400)

    def test_refresh_rotation_and_user_disable(self):
        client, _, token = self.authorize_token()
        form = urlencode({"grant_type": "refresh_token", "client_id": client,
                          "resource": self.base + "/mcp",
                          "refresh_token": token["refresh_token"]}).encode()
        status, _, fresh = self.request("/oauth/token", form, mime="application/x-www-form-urlencoded")
        self.assertEqual(status, 200)
        status, _, _ = self.request("/oauth/token", form, mime="application/x-www-form-urlencoded")
        self.assertEqual(status, 400)
        self.assertEqual(self.oauth.authenticate(fresh["access_token"]), "owner")
        self.hub.users["owner"]["enabled"] = False
        self.assertIsNone(self.oauth.authenticate(fresh["access_token"]))
        form2 = urlencode({"grant_type": "refresh_token", "client_id": client,
                           "resource": self.base + "/mcp",
                           "refresh_token": fresh["refresh_token"]}).encode()
        status, _, _ = self.request("/oauth/token", form2, mime="application/x-www-form-urlencoded")
        self.assertEqual(status, 400)

    def test_entire_open_notepad_control_flow_is_user_scoped(self):
        # Fake Windows agent handles the dispatched task; no real GUI is used.
        client, _, token = self.authorize_token()

        def invoke(tool, arguments):
            req = json.dumps({"jsonrpc": "2.0", "id": 100,
                "method": "tools/call", "params": {"name": tool, "arguments": arguments}}).encode()
            status, _, result = self.request(
                "/mcp", req, mime="application/json",
                headers={"Authorization": "Bearer " + token["access_token"]})
            self.assertEqual(status, 200)
            return result["result"]

        action = {"device_id": "asus", "tool": "open_notepad", "arguments": {}}
        denied = invoke("arc_call", action)
        self.assertTrue(denied["isError"])  # Lease is mandatory.
        lease = invoke("arc_take_control", {"device_id": "asus"})
        self.assertFalse(lease.get("isError", False))
        self.assertTrue(json.loads(lease["content"][0]["text"])["exclusive"])

        def fake_agent():
            task = self.hub.poll("asus")
            self.assertEqual(task["tool"], "open_notepad")
            self.assertTrue(self.hub.result("asus", {
                "task_id": task["task_id"],
                "result": {"launched": "notepad", "source": "fake-agent"}}))

        self.hub.seen["asus"] = time.time()
        worker = threading.Thread(target=fake_agent, daemon=True)
        worker.start()
        opened = invoke("arc_call", action)
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertFalse(opened.get("isError", False))
        self.assertEqual(json.loads(opened["content"][0]["text"])["launched"], "notepad")
        released = invoke("arc_release_control", {"device_id": "asus"})
        self.assertFalse(released.get("isError", False))
        again = invoke("arc_call", action)
        self.assertTrue(again["isError"])

    def test_wrong_resource_and_expired_token(self):
        client, _, token = self.authorize_token()
        self.oauth.db.execute("UPDATE access_tokens SET expires = 1")
        self.oauth.db.commit()
        self.assertIsNone(self.oauth.authenticate(token["access_token"]))
        form = urlencode({"grant_type": "refresh_token", "client_id": client,
                          "resource": "https://elsewhere.example/mcp",
                          "refresh_token": token["refresh_token"]}).encode()
        status, _, _ = self.request("/oauth/token", form, mime="application/x-www-form-urlencoded")
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
