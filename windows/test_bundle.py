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
