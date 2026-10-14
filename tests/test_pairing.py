"""Regression tests for one-time ARC Agent enrollment."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vds"))
from arc_hub import Hub, Server, digest


class PairingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.registry = Path(self.temp.name) / "registry.json"
        self.owner_token = "owner_" + "x" * 50
        self.registry.write_text(json.dumps({
            "version": 2, "devices": {},
            "users": {
                "owner": {"role": "admin", "enabled": True,
                          "token_hash": digest(self.owner_token), "grants": {}},
                "viewer": {"role": "viewer", "enabled": True,
                           "token_hash": digest("viewer_" + "y" * 50),
                           "grants": {}},
            }
        }), encoding="utf-8")
        self.hub = Hub(json.loads(self.registry.read_text(encoding="utf-8")))
        self.srv = Server(("127.0.0.1", 0), self.hub, self.registry)
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:" + str(self.srv.server_port)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def request(self, route, body, cookie=None):
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        req = Request(self.url + route, data=json.dumps(body).encode(),
                      headers=headers, method="POST")
        try:
            with urlopen(req, timeout=3) as response:
                return response.status, json.loads(response.read()), response.headers
        except HTTPError as exc:
            return exc.code, json.loads(exc.read()), exc.headers

    def login(self, name="owner"):
        token = self.owner_token if name == "owner" else "viewer_" + "y" * 50
        code, body, headers = self.request("/web/api/login", {
            "user": name, "token": token
        })
        self.assertEqual(code, 200)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        req = Request(self.url + "/web/api/me", headers={"Cookie": cookie})
        with urlopen(req, timeout=3) as response:
            csrf = json.loads(response.read())["csrf"]
        return cookie, csrf

    def code(self):
        cookie, csrf = self.login()
        status, payload, _ = self.request("/web/api/pairing/create", {"csrf":csrf}, cookie)
        self.assertEqual(status, 201, payload)
        return payload["code"]

    def test_pairing_flow_and_one_time_use(self):
        code = self.code()
        self.assertEqual(len(code.replace("-", "")), 12)
        self.assertNotIn(code, self.registry.read_text(encoding="utf-8"))
        dev_id = "b" * 32
        status, response, _ = self.request("/agent/enroll", {
            "device_id": dev_id, "name": "Office PC", "code": code})
        self.assertEqual(status, 201, response)
        self.assertEqual(response["device_id"], dev_id)
        self.assertGreaterEqual(len(response["device_token"]), 48)
        db = json.loads(self.registry.read_text(encoding="utf-8"))
        self.assertNotIn(response["device_token"], self.registry.read_text(encoding="utf-8"))
        self.assertEqual(db["devices"][dev_id]["allowed_scopes"], ["status", "inspect"])
        self.assertEqual(db["users"]["owner"]["grants"][dev_id], ["status", "inspect"])
        self.assertTrue(self.hub.authenticate_device(dev_id, response["device_token"]))
        self.assertIn(dev_id, self.hub.queues)
        self.assertNotIn("viewer", [n for n, x in db["users"].items() if dev_id in x["grants"]])
        status, _, _ = self.request("/agent/enroll", {
            "device_id": "c" * 32, "name": "Second", "code": code})
        self.assertEqual(status, 403)
        status, _, _ = self.request("/agent/enroll", {
            "device_id": dev_id, "name": "Office PC", "code": self.code()})
        self.assertEqual(status, 400)

    def test_viewer_cannot_create_code_and_csrf_required(self):
        cookie, csrf = self.login("viewer")
        status, _, _ = self.request("/web/api/pairing/create", {"csrf": csrf}, cookie)
        self.assertEqual(status, 403)
        owner, _ = self.login()
        status, _, _ = self.request("/web/api/pairing/create", {}, owner)
        self.assertEqual(status, 403)

    def test_expired_invalid_and_rate_limits(self):
        code = self.code()
        obj = self.srv.web.pairing
        key = next(iter(obj.pending))
        obj.pending[key]["expires"] = time.monotonic() - 1
        status, _, _ = self.request("/agent/enroll", {
            "device_id": "f"*32, "name": "Test", "code": code})
        self.assertEqual(status, 403)
        for _ in range(20):
            status, _, _ = self.request("/agent/enroll", {
                "device_id": "f"*32, "name": "Test", "code": "AAAAAAAAAAAA"})
            self.assertEqual(status, 403)
        status, body, _ = self.request("/agent/enroll", {
            "device_id": "f"*32, "name": "Test", "code": "AAAAAAAAAAAA"})
        self.assertEqual(status, 403)
        self.assertIn("too many", body["error"])
        self.assertEqual(self.hub.devices, {})

    def test_invalid_identity_cannot_consume_pairing(self):
        code = self.code()
        status, _, _ = self.request("/agent/enroll", {
            "device_id": "not-valid", "name": "Fake", "code": code})
        self.assertEqual(status, 400)
        status, _, _ = self.request("/agent/enroll", {
            "device_id": "e"*32, "name": "Valid", "code": code})
        self.assertEqual(status, 201)


if __name__ == "__main__":
    unittest.main()
