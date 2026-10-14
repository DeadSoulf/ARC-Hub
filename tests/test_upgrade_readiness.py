"""Regression tests for ARC Web updater's systemd/HTTP readiness race."""
import http.server
from pathlib import Path
import os
import shutil
import subprocess
import threading
import time
import unittest


INSTALLER = Path(__file__).resolve().parents[1] / "scripts" / "update-web.sh"


def wait_function():
    content = INSTALLER.read_text(encoding="utf-8")
    start = content.index("wait_for_http() {")
    end = content.index("\n}\n", start) + 2
    return content[start:end]


@unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "requires Linux/Bash")
class UpgradeReadinessTests(unittest.TestCase):
    def run_wait(self, address, attempts):
        command = wait_function() + "\n" + (
            'wait_for_http "http://127.0.0.1:%d/healthz" %d "test ARC Hub"\n'
            % (address, attempts)
        )
        return subprocess.run(
            ["bash", "-c", command], capture_output=True,
            text=True, timeout=12,
        )

    def test_delayed_http_becomes_ready(self):
        start = time.monotonic()

        class DelayedHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                status = 503 if time.monotonic() - start < 0.4 else 200
                self.send_response(status)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"OK")

            def log_message(self, *_):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), DelayedHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = self.run_wait(server.server_port, 4)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("ready (attempt 2/4)", result.stdout)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_unhealthy_http_fails_instead_of_claiming_success(self):
        class UnhealthyHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *_):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), UnhealthyHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = self.run_wait(server.server_port, 2)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not ready", result.stderr)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
