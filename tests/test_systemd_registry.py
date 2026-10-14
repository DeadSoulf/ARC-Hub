"""ARC Hub Linux service sandbox security regression.

Pairing and web administration atomically update /etc/arc/registry.json.
ProtectSystem=strict must grant only the config directory, not the entire
filesystem.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ServicePermissionTests(unittest.TestCase):
    def test_registry_directory_write_enabled_in_systemd(self):
        unit = (ROOT / "vds/arc-hub.service").read_text(encoding="utf-8")
        self.assertIn("ProtectSystem=strict", unit)
        lines = [line for line in unit.splitlines()
                 if line.startswith("ReadWritePaths=")]
        self.assertEqual(len(lines), 1)
        roots = set(lines[0].split("=", 1)[1].split())
        self.assertIn("/etc/arc", roots)
        self.assertIn("/var/log/arc", roots)
        self.assertIn("/var/lib/arc-hub/updates", roots)
        self.assertNotIn("/", roots)
        self.assertNotIn("/etc", roots)
        self.assertNotIn("/usr", roots)

    def test_installer_and_updater_reload_systemd(self):
        installer = (ROOT / "vds/install-vds.sh").read_text(encoding="utf-8")
        updater = (ROOT / "scripts/update-web.sh").read_text(encoding="utf-8")
        self.assertIn("install -m 0644 arc-hub.service", installer)
        self.assertIn("systemctl daemon-reload", installer)
        self.assertIn("systemctl daemon-reload", updater)
        self.assertIn("systemctl restart arc-hub", updater)


if __name__ == "__main__":
    unittest.main()
