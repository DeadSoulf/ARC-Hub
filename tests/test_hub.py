"""ARC Hub access separation, protocol and negative tests."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vds"))
import json
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from arc_hub import Hub, Server, digest


def sample_config():
    return {
        "version": 2,
        "devices": {
            "asus": {"name": "Asus", "token_hash": digest("asus-secret"),
                     "enabled": True, "allowed_scopes": ["status", "inspect", "screen.read", "shell.exec"]},
            "pc02": {"name": "Office", "token_hash": digest("pc02-secret"),
                     "enabled": True, "allowed_scopes": ["status", "files.read"]},
        },
        "users": {
            "alice": {"role": "admin", "token_hash": digest("alice-secret"), "grants": {
                "asus": ["status", "inspect", "screen.read", "shell.exec"], "pc02": ["status"]}},
            "bob": {"role": "operator", "token_hash": digest("bob-secret"), "grants": {
                "pc02": ["status", "files.read"]}},
            "guest": {"role": "viewer", "token_hash": digest("guest-secret"), "grants": {
                "asus": ["status"]}},
            "charlie": {"role": "admin", "token_hash": digest("charlie-secret"), "grants": {
                "asus": ["status", "shell.exec"]}},
        }
    }


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.hub = Hub(sample_config())

    def test_user_individual_credentials(self):
        self.assertEqual(self.hub.authenticate_user("bob-secret"), "bob")
        self.assertIsNone(self.hub.authenticate_user("incorrect"))
        self.assertNotEqual(self.hub.authenticate_user("guest-secret"), "alice")

    def test_device_tokens_separated(self):
        self.assertTrue(self.hub.authenticate_device("asus", "asus-secret"))
        self.assertFalse(self.hub.authenticate_device("pc02", "asus-secret"))

    def test_devices_hidden_between_users(self):
        self.assertEqual([d["device_id"] for d in self.hub.device_list("bob")], ["pc02"])
        self.assertEqual([d["device_id"] for d in self.hub.device_list("guest")], ["asus"])
        self.assertEqual(len(self.hub.device_list("alice")), 2)

    def test_readonly_guest(self):
        self.hub.seen["asus"] = time.time()
        self.assertEqual(self.hub.device_tools("guest", "asus"), ["computer_status"])
        with self.assertRaises(PermissionError):
            self.hub.dispatch("guest", "asus", "run_command", {"command": "whoami"})
        with self.assertRaises(PermissionError):
            self.hub.dispatch("guest", "asus", "capture_screen", {})

    def test_operator_isolated_from_other_computers(self):
        self.hub.seen["asus"] = time.time()
        with self.assertRaises(PermissionError):
            self.hub.dispatch("bob", "asus", "computer_status", {})
        self.assertIn("read_text_file", self.hub.device_tools("bob", "pc02"))

    def test_not_cross_post_task_result(self):
        self.hub.seen["asus"] = time.time()
        errors = []
        def complete():
            task = self.hub.poll("asus")
            if task is None:
                errors.append("no task")
                return
            if self.hub.result("pc02", {"task_id": task["task_id"], "result": "wrong"}):
                errors.append("cross-device accepted")
            if not self.hub.result("asus", {"task_id": task["task_id"], "result": {"host": "asus"}}):
                errors.append("device result rejected")
        thread = threading.Thread(target=complete)
        thread.start()
        result = self.hub.dispatch("alice", "asus", "computer_status", {})
        thread.join(3)
        self.assertFalse(errors)
        self.assertEqual(result, {"host": "asus"})

    def test_exclusive_control_lease(self):
        self.hub.seen["asus"] = time.time()
        with self.assertRaises(PermissionError):
            self.hub.dispatch("alice", "asus", "run_command", {"command": "whoami"})
        lease = self.hub.take_control("alice", "asus")
        self.assertTrue(lease["exclusive"])
        with self.assertRaises(PermissionError):
            self.hub.take_control("charlie", "asus")
        self.hub.release_control("alice", "asus")
        self.assertTrue(self.hub.take_control("charlie", "asus")["exclusive"])
        with self.assertRaises(PermissionError):
            self.hub.take_control("guest", "asus")

    def test_http_auth_for_each_user(self):
        s = Server(("127.0.0.1", 0), self.hub)
        thread = threading.Thread(target=s.serve_forever, daemon=True)
        thread.start()
        url = "http://127.0.0.1:" + str(s.server_port) + "/mcp"
        def call(token, name="arc_devices", args=None):
            body = json.dumps({"jsonrpc": "2.0", "id": 1,
                               "method": "tools/call", "params": {
                                   "name": name, "arguments": args or {}}}).encode("utf-8")
            request = Request(url, data=body, headers={
                "Authorization": "Bearer " + token, "Content-Type": "application/json"})
            with urlopen(request, timeout=6) as response:
                return json.load(response)
        try:
            output = call("bob-secret")
            raw = output["result"]["content"][0]["text"]
            self.assertNotIn("asus", raw)
            self.assertIn("pc02", raw)
            err = call("guest-secret", "arc_call", {
                "device_id": "asus", "tool": "run_command", "arguments": {"command": "whoami"}})
            self.assertTrue(err["result"]["isError"])
            with self.assertRaises(HTTPError) as cm:
                call("invalid")
            self.assertEqual(cm.exception.code, 401)
        finally:
            s.shutdown()
            s.server_close()
            thread.join(2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
