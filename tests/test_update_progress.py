"""ARC updater stage reporting regression tests (no root or VDS needed)."""
from pathlib import Path
import json
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "vds" / "arc-update-runner.sh"
UPDATER = ROOT / "scripts" / "update-web.sh"
STAGES = ["prepare", "download", "test", "backup", "install",
          "restart", "health", "service", "complete"]


@unittest.skipUnless(os.name == "posix", "Bash integration tests require Linux")
class UpdateRunnerProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.status_dir = self.home / "updates"
        self.status_dir.mkdir()
        (self.status_dir / "request").write_text("owner-approved\n", encoding="utf-8")
        self.log = self.home / "update.log"
        self.fake = self.home / "fixed-updater.sh"
        self.runner = self.home / "runner.sh"

    def tearDown(self):
        self.tmp.cleanup()

    def execute(self, failing=False):
        script = '#!/bin/bash\n'
        for stage in STAGES:
            script += 'printf "ARC_PROGRESS:%s\\n" "' + stage + '"\n'
        script += 'echo "SECRET_SHOULD_NOT_BE_RETURNED_TO_BROWSER"\n'
        if failing:
            script += 'echo "ARC_PROGRESS:rollback"\nexit 17\n'
        else:
            script += 'exit 0\n'
        self.fake.write_text(script, encoding="utf-8")
        self.fake.chmod(0o700)
        runner = RUNNER.read_text(encoding="utf-8")
        runner = runner.replace(
            'REQUEST=/var/lib/arc-hub/updates/request',
            'REQUEST="' + str(self.status_dir / "request") + '"'
        ).replace('DIR=/var/lib/arc-hub/updates',
                  'DIR="' + str(self.status_dir) + '"'
        ).replace('LOG=/var/log/arc/update.log',
                  'LOG="' + str(self.log) + '"'
        ).replace('/usr/local/libexec/arc-update-apply', str(self.fake))
        runner = runner.replace('chown arc-hub:arc-hub "$temp"', ':')
        self.runner.write_text(runner, encoding="utf-8")
        result = subprocess.run(
            ["bash", str(self.runner)], capture_output=True,
            text=True, timeout=8
        )
        state = json.loads(
            (self.status_dir / "status.json").read_text(encoding="utf-8"))
        return result, state

    def test_finished_update_has_complete_progress(self):
        result, status = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(status["state"], "success")
        self.assertEqual(status["step"], "complete")
        self.assertEqual(status["percent"], 100)
        self.assertIsInstance(status["started_unix"], int)
        self.assertIsInstance(status["updated_unix"], int)
        self.assertFalse((self.status_dir / "request").exists())
        self.assertNotIn("SECRET_SHOULD_NOT_BE_RETURNED", json.dumps(status))

    def test_failure_does_not_claim_success(self):
        result, status = self.execute(failing=True)
        self.assertEqual(result.returncode, 17)
        self.assertEqual(status["state"], "failed")
        self.assertIn("Ошибка обновления", status["detail"])
        self.assertEqual(status["step"], "rollback")

    def test_scripts_only_emit_known_stages(self):
        source = UPDATER.read_text(encoding="utf-8")
        for stage in STAGES:
            self.assertIn("progress " + stage, source)
        self.assertIn("progress rollback", source)
        runner = RUNNER.read_text(encoding="utf-8")
        for stage in STAGES + ["rollback"]:
            self.assertIn(stage + ")", runner)


if __name__ == "__main__":
    unittest.main()
