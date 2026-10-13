"""Safety and syntax checks for the copy/paste ARC VDS bootstrap installer."""
from pathlib import Path
import os
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-vds.sh"


@unittest.skipUnless(os.name == "posix", "Bash syntax checked on Linux CI")
class BootstrapTests(unittest.TestCase):
    def call(self, *args):
        return subprocess.run(
            ["bash", str(INSTALLER), *args],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=6)

    def test_bash_syntax(self):
        outcome = subprocess.run(
            ["bash", "-n", str(INSTALLER)],
            capture_output=True, text=True, timeout=5)
        self.assertEqual(outcome.returncode, 0, outcome.stderr)

    def test_help_does_not_install(self):
        outcome = self.call("--help")
        self.assertEqual(outcome.returncode, 0)
        self.assertIn("curl -fsSL", outcome.stdout)
        self.assertIn("sudo bash", outcome.stdout)

    def test_requires_real_domain(self):
        for domain in ("arc.example.com", "http://bad/path", "localhost", "a..b",
                       "semi;colon.test"):
            with self.subTest(domain=domain):
                outcome = self.call("--domain", domain)
                self.assertNotEqual(outcome.returncode, 0)
                self.assertIn("ARC ERROR:", outcome.stderr)

    def test_ref_is_validated_before_install(self):
        outcome = self.call("--domain", "arc.valid-domain.com",
                            "--ref", "../../bad")
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("invalid Git ref", outcome.stderr)

    def test_noninteractive_requires_domain(self):
        outcome = self.call()
        self.assertNotEqual(outcome.returncode, 0)
        self.assertIn("ARC ERROR:", outcome.stderr)


if __name__ == "__main__":
    unittest.main()
