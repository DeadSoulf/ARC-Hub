"""Test enrollment of a frozen ARC Agent without requiring real credentials."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent))
import arc_bundle


class BundleEnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.config = self.dir / "agent.json"
        self.initial = {"device_id": "a" * 32, "name": "TEST-PC",
                        "port": 8876, "roots": [str(self.dir)],
                        "permissions": {"status": True, "inspect": True},
                        "gateway_url": "", "device_token": ""}
        self.config.write_text(json.dumps(self.initial), encoding="utf-8")
        self.patch_cfg = patch.object(arc_bundle, "CONFIG", self.config)
        self.patch_cfg.start()
        self.patch_subprocess = patch.object(
            arc_bundle.subprocess, "run",
            return_value=SimpleNamespace(returncode=0))
        self.patch_subprocess.start()

    def tearDown(self):
        self.patch_subprocess.stop()
        self.patch_cfg.stop()
        self.temp.cleanup()

    def envelope(self, device_id="a"*32, token="x"*60, url="https://arc.example.invalid"):
        entry = self.dir / "entry.json"
        entry.write_text(json.dumps({
            "device_id": device_id, "device_token": token,
            "gateway_url": url, "name": "Asus"}), encoding="utf-8")
        return str(entry)

    def test_enrollment_preserves_device_identity_and_rights(self):
        msg = arc_bundle.enroll_agent(self.envelope())
        after = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(after["device_id"], self.initial["device_id"])
        self.assertEqual(after["permissions"], self.initial["permissions"])
        self.assertEqual(after["gateway_url"], "https://arc.example.invalid")
        self.assertEqual(after["device_token"], "x"*60)
        self.assertIn("connected", msg)
        self.assertFalse((self.dir / "agent.json.enrolling").exists())

    def test_pair_by_code_without_json_file(self):
        class FakeHttpResponse:
            status = 201
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def read(self, _limit):
                return json.dumps({
                    "device_id": "a"*32,
                    "device_token": "k"*64,
                    "name": "Asus",
                    "permissions": ["status", "inspect"]
                }).encode("utf-8")
        with patch.object(arc_bundle.urllib.request, "urlopen",
                          return_value=FakeHttpResponse()) as mock_http:
            message = arc_bundle.enroll_by_code(
                "https://vpn.homeaicore.ru", "ABCD-EFGH-JKLM")
        self.assertIn("подключён", message)
        request = mock_http.call_args.args[0]
        self.assertEqual(request.full_url, "https://vpn.homeaicore.ru/agent/enroll")
        body = json.loads(request.data)
        self.assertEqual(body["device_id"], "a"*32)
        self.assertEqual(body["code"], "ABCDEFGHJKLM")
        config = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(config["gateway_url"], "https://vpn.homeaicore.ru")
        self.assertEqual(config["device_token"], "k"*64)
        self.assertEqual(config["permissions"], self.initial["permissions"])

    def test_pair_rejects_http_and_existing_enrollment(self):
        with self.assertRaises(ValueError):
            arc_bundle.enroll_by_code("http://vpn.homeaicore.ru", "ABCD-EFGH-JKLM")
        self.assertFalse(self.config.read_text().count("k"*64))
        arc_bundle.enroll_agent(self.envelope(token="x"*60))
        with self.assertRaises(RuntimeError):
            arc_bundle.enroll_by_code("https://vpn.homeaicore.ru", "ABCD-EFGH-JKLM")

    def test_wrong_computer_denied(self):
        with self.assertRaises(ValueError):
            arc_bundle.enroll_agent(self.envelope(device_id="b"*32))
        self.assertEqual(json.loads(self.config.read_text())["device_token"], "")

    def test_plain_http_denied(self):
        with self.assertRaises(ValueError):
            arc_bundle.enroll_agent(self.envelope(url="http://vds.example.invalid"))
        self.assertEqual(json.loads(self.config.read_text())["device_token"], "")

    def test_credential_rotation_not_silent(self):
        arc_bundle.enroll_agent(self.envelope(token="x"*60))
        with self.assertRaises(ValueError):
            arc_bundle.enroll_agent(self.envelope(token="y"*60))


if __name__ == "__main__":
    unittest.main()
