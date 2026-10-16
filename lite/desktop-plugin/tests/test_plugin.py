"""ARC Lite packaged local MCP/CLI tests, safe for Windows CI."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
sys.path.insert(0, str(SERVER))

import arc_cli
from arc_lite import FullAccessAgent

WINDOWS = os.name == "nt"


@unittest.skipUnless(WINDOWS, "Windows backend")
class PackagedPluginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="arc-lite-ci-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.agent = FullAccessAgent(self.directory / "shared", roots=[self.directory])

    def test_full_capabilities_available(self):
        tools = {tool["name"] for tool in self.agent.tools()}
        for name in ("pc_status", "list_windows", "capture_screen",
                     "write_file", "read_text_file", "run_command",
                     "open_application", "click_mouse", "hotkey"):
            self.assertIn(name, tools)
        self.assertGreaterEqual(len(tools), 29)
        self.assertTrue(self.agent.call("pc_status", {})["online"])

    def test_real_file_roundtrip_in_temporary_folder(self):
        target = self.directory / "verification.txt"
        self.agent.call("write_file", {"path": str(target), "content": "ARC_OK"})
        content = self.agent.call("read_text_file", {"path": str(target)})
        self.assertEqual(content["content"], "ARC_OK")
        with self.assertRaises(PermissionError):
            self.agent.call("delete_file", {"path": str(target), "confirm": False})
        self.agent.call("delete_file", {"path": str(target), "confirm": True})
        self.assertFalse(target.exists())

    def test_cli_write_requires_confirmation(self):
        target = self.directory / "no_write.txt"
        parsed = arc_cli.parse(["write", str(target), "--content", "NO"])
        with self.assertRaises(PermissionError):
            arc_cli.dispatch(parsed, self.agent)
        self.assertFalse(target.exists())

    def test_cli_disallows_gui_from_sandbox_desktop(self):
        parsed = arc_cli.parse(["app", "calculator", "--confirm"])
        with patch.object(arc_cli, "current_desktop_name",
                          return_value="CodexSandboxDesktop"):
            with self.assertRaises(PermissionError):
                arc_cli.dispatch(parsed, self.agent)

    def test_cli_help_has_real_action_names(self):
        result = subprocess.run(
            [sys.executable, str(SERVER / "arc_cli.py"), "--help"],
            capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        # argparse wraps long help strings at terminal width.
        help_text = result.stdout.replace("\n                        ", "")
        self.assertIn("ls/cat/write/tool", help_text)
        self.assertNotIn("/files/tool", help_text)

    def test_mcp_stdio_request_and_response(self):
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "pc_status", "arguments": {}}},
        ]
        data = ("\n".join(json.dumps(x) for x in messages) + "\n").encode("utf-8")
        process = subprocess.run(
            [sys.executable, "-u", str(SERVER / "arc_lite.py"),
             "--shared", str(self.directory / "shared"),
             "--full-access", "--allow-root", str(self.directory)],
            input=data, capture_output=True, timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr.decode(errors="replace"))
        replies = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual([x["id"] for x in replies], [1, 2, 3])
        self.assertEqual(replies[0]["result"]["serverInfo"]["name"], "arc-lite")
        self.assertGreaterEqual(len(replies[1]["result"]["tools"]), 29)
        result = json.loads(replies[2]["result"]["content"][0]["text"])
        self.assertTrue(result["online"])


if __name__ == "__main__":
    unittest.main()
