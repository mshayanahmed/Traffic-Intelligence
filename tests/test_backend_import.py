import subprocess
import sys
import unittest
from pathlib import Path


class BackendImportTest(unittest.TestCase):
    def test_app_import_does_not_block(self):
        backend_dir = (Path(__file__).resolve().parent.parent / "backend").resolve()
        python = sys.executable
        cmd = [
            python,
            "-c",
            f"import sys; sys.path.insert(0, {str(backend_dir)!r}); import app; print('OK')",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        self.assertEqual(proc.returncode, 0, msg=(proc.stdout + proc.stderr))
        self.assertIn("OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
