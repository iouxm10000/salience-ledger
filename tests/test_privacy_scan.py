import tempfile
import unittest
from pathlib import Path

from tools.privacy_scan import scan


class PrivacyScanTests(unittest.TestCase):
    def test_clean_fixture_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("Synthetic agent memory example.", encoding="utf-8")
            self.assertEqual(scan(root), [])

    def test_private_path_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private_path = "C" + ":\\Users\\person\\file"
            (root / "leak.md").write_text("private path " + private_path, encoding="utf-8")
            self.assertTrue(any("windows_private_path" in item for item in scan(root)))


if __name__ == "__main__":
    unittest.main()
