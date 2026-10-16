"""No-network tests for ARC Lite; no real application is launched."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from arc_lite import Agent

SCRIPT = Path(__file__).with_name("arc_lite.py")


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "shared"
        self.agent = Agent(self.folder)

    def test_default_tools_are_readonly(self):
        names = {tool["name"] for tool in self.agent.tools()}
        self.assertEqual(names, {"pc_status", "list_shared_files", "read_shared_text"})
        self.assertTrue(self.agent.call("pc_status", {})["online"])
        with self.assertRaises(ValueError):
            self.agent.call("open_notepad", {"request_id": "testing-1234"})

    def test_shared_text_and_directory(self):
        (self.folder / "sample.txt").write_text("hello", encoding="utf-8")
        self.assertEqual(self.agent.call("read_shared_text", {"name": "sample.txt"})["text"], "hello")
        self.assertEqual(self.agent.call("list_shared_files", {})["files"][0]["name"], "sample.txt")

    def test_directory_traversal_is_rejected(self):
        (Path(self.temp.name) / "outside.txt").write_text("PRIVATE", encoding="utf-8")
        with self.assertRaises(PermissionError):
            self.agent.call("read_shared_text", {"name": "../outside.txt"})

    def test_extra_parameters_and_unavailable_tools(self):
        with self.assertRaises(ValueError):
            self.agent.call("pc_status", {"command": "cmd"})
        with self.assertRaises(ValueError):
            self.agent.call("run_command", {"command": "cmd"})

    def test_notepad_requires_explicit_enable_and_request_id(self):
        agent = Agent(self.folder, enable_apps=True)
        self.assertIn("open_notepad", {x["name"] for x in agent.tools()})
        with self.assertRaises(ValueError):
            agent.call("open_notepad", {"request_id": "no"})
        with self.assertRaises(ValueError):
            agent.call("open_notepad", {"request_id": "testing-1234", "cmd": "whoami"})

    def test_stdio_json_rpc(self):
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "pc_status", "arguments": {}}},
        ]
        wire = "\n".join(json.dumps(x) for x in messages) + "\n"
        out = subprocess.run(
            [sys.executable, "-u", str(SCRIPT), "--shared", str(self.folder)],
            input=wire, text=True, capture_output=True, timeout=12,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        replies = [json.loads(line) for line in out.stdout.splitlines()]
        self.assertEqual(len(replies), 3)
        self.assertEqual([r["id"] for r in replies], [1, 2, 3])
        self.assertEqual(replies[0]["result"]["serverInfo"]["name"], "arc-lite")
        self.assertEqual(len(replies[1]["result"]["tools"]), 3)
        result = json.loads(replies[2]["result"]["content"][0]["text"])
        self.assertTrue(result["online"])


if __name__ == "__main__":
    unittest.main()
