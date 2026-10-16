"""ARC Lite full Windows capability tests. No real GUI clicks or application starts."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from arc_lite import FullAccessAgent

WIN = os.name == "nt"


@unittest.skipUnless(WIN, "Windows-only full-access backend")
class FullAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.agent = FullAccessAgent(self.root / "shared", roots=[self.root])

    def test_capability_catalog(self):
        names = {tool["name"] for tool in self.agent.tools()}
        expected = {
            "pc_status", "list_directory", "read_text_file", "write_file",
            "edit_file", "move_path", "delete_file", "create_directory",
            "search_files", "file_info", "list_processes", "run_command",
            "start_process", "read_process_output", "send_process_input",
            "stop_process", "computer_status", "capture_screen",
            "list_windows", "focus_window", "click_mouse", "move_mouse",
            "scroll_mouse", "hotkey", "type_text", "open_notepad",
            "open_application", "list_shared_files", "read_shared_text",
        }
        self.assertEqual(names, expected)
        self.assertEqual(len(names), len(self.agent.tools()))

    def test_file_create_edit_move_and_confirmed_delete(self):
        d = self.root / "newfolder"
        a = d / "original.txt"
        b = d / "renamed.txt"
        self.agent.call("create_directory", {"path": str(d)})
        self.agent.call("write_file", {"path": str(a), "content": "ARC test one"})
        self.assertEqual(self.agent.call("read_text_file", {"path": str(a)})["content"], "ARC test one")
        self.agent.call("edit_file", {
            "path": str(a), "old_text": "one", "new_text": "two"})
        self.assertEqual(a.read_text(encoding="utf-8"), "ARC test two")
        self.assertTrue(self.agent.call("file_info", {"path": str(a)})["is_file"])
        self.agent.call("move_path", {"source": str(a), "destination": str(b)})
        self.assertFalse(a.exists())
        with self.assertRaises(PermissionError):
            self.agent.call("delete_file", {"path": str(b), "confirm": False})
        self.assertTrue(b.exists())
        self.agent.call("delete_file", {"path": str(b), "confirm": True})
        self.assertFalse(b.exists())

    def test_full_mode_respects_explicit_roots(self):
        outside = self.root.parent / (self.root.name + "-outside-arc.txt")
        with self.assertRaises(PermissionError):
            self.agent.call("write_file", {"path": str(outside), "content": "never"})
        self.assertFalse(outside.exists())
        with self.assertRaises(PermissionError):
            self.agent.call("list_directory", {"path": str(self.root.parent)})

    def test_powershell_and_limits(self):
        output = self.agent.call("run_command", {
            "command": "Write-Output ARC_LITE_POWERSHELL_OK",
            "cwd": str(self.root), "timeout_seconds": 10})
        self.assertEqual(output["exit_code"], 0)
        self.assertIn("ARC_LITE_POWERSHELL_OK", output["stdout"])
        with self.assertRaises(ValueError):
            self.agent.call("run_command", {
                "command": "Write-Output X", "timeout_seconds": 100})
        with self.assertRaises(ValueError):
            self.agent.call("run_command", {"command": ""})

    def test_invalid_input_has_no_gui_side_effect(self):
        for name, args in [
            ("click_mouse", {"x": -999, "y": -999, "button": "left"}),
            ("type_text", {"text": ""}),
            ("move_mouse", {"x": -1, "y": -1}),
            ("hotkey", {"keys": ["invalid-key"]}),
            ("open_application", {"name": "arbitrary.exe"}),
            ("open_notepad", {"request_id": "bad"}),
        ]:
            with self.subTest(name=name), self.assertRaises((ValueError, TypeError)):
                self.agent.call(name, args)

    def test_screen_rpc_has_mcp_image_type(self):
        request = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                   "params": {"name": "capture_screen", "arguments": {}}}
        with patch.object(self.agent.backend, "call", return_value={"image": "aGVsbG8="}):
            result = self.agent.handle(request)
        content = result["content"]
        self.assertEqual(len(content), 1)
        self.assertEqual(content[0]["type"], "image")
        self.assertEqual(content[0]["mimeType"], "image/png")
        self.assertEqual(base64.b64decode(content[0]["data"]), b"hello")

    def test_no_unexpected_tools_and_rpc_protocol(self):
        with self.assertRaises(ValueError):
            self.agent.call("unsupported_operation", {})
        msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        self.assertEqual(len(self.agent.handle(msg)["tools"]), 29)
        self.assertEqual(self.agent.handle({"jsonrpc": "2.0", "id": 9,
                                            "method": "initialize"})["serverInfo"]["name"], "arc-lite")


if __name__ == "__main__":
    unittest.main()

[executed on device: Asus (83c48dfc-6b5f-4e6b-bb8d-6074171cc980)]
