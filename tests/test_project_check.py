"""Regression checks for publication/privacy boundaries, not marketing reasoning."""

import importlib.util
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


spec = importlib.util.spec_from_file_location("project_check", Path(__file__).resolve().parents[1] / "scripts/check_project.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class PublicationChecks(unittest.TestCase):
    def test_links_work_after_moving_to_unicode_directory(self):
        with tempfile.TemporaryDirectory(prefix="gofind-") as temp:
            root = Path(temp) / "另一台电脑 项目"
            root.mkdir()
            (root / "方法.md").write_text('<a id="sample"></a>\n# 搜索与测量\n', encoding="utf-8")
            self.assertEqual([], checker.link_errors(root, Path("README.md"), "[方法](方法.md#sample)"))
            self.assertTrue(checker.link_errors(root, Path("README.md"), "[缺失](missing.md)"))
            self.assertTrue(checker.link_errors(root, Path("README.md"), "[缺失](方法.md#wrong)"))
            self.assertTrue(checker.link_errors(root, Path("README.md"), "[越界](../outside.md)"))

    def test_ignoring_private_directory_does_not_hide_tracked_leak(self):
        if not shutil.which("git"):
            self.skipTest("Git not available")
        with tempfile.TemporaryDirectory(prefix="gofind-") as temp:
            root = Path(temp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / ".gitignore").write_text("/workspaces/\n", encoding="utf-8")
            (root / "workspaces").mkdir()
            private = root / "workspaces" / "客户.md"
            private.write_text("private test fixture", encoding="utf-8")
            self.assertNotIn(Path("workspaces/客户.md"), checker.public_files(root))
            subprocess.run(["git", "-C", str(root), "add", "-f", "workspaces/客户.md"], check=True)
            errors, _ = checker.check(root)
            self.assertTrue(any("private file in publication set" in e for e in errors))

    def test_credential_scan_does_not_echo_credential(self):
        with tempfile.TemporaryDirectory(prefix="gofind-") as temp:
            root = Path(temp)
            fake = "sk-" + "x" * 30
            (root / "accidental.txt").write_text(fake, encoding="utf-8")
            errors, _ = checker.check(root)
            self.assertTrue(any("possible credential" in e for e in errors))
            self.assertNotIn(fake, "\n".join(errors))

    def test_business_attachments_cannot_be_linked_from_public_docs(self):
        with tempfile.TemporaryDirectory(prefix="gofind-") as temp:
            root = Path(temp)
            (root / "workspaces").mkdir()
            (root / "workspaces/client.md").write_text("private fixture", encoding="utf-8")
            self.assertTrue(checker.link_errors(root, Path("README.md"), "[客户](workspaces/client.md)"))

    def test_web_sources_are_not_misread_as_windows_drive_paths(self):
        self.assertIsNone(checker.ABSOLUTE.search("[来源](https://example.org/source)"))
        self.assertIsNotNone(checker.ABSOLUTE.search("`" + "Z" + ":/private/file.md`"))


if __name__ == "__main__":
    unittest.main()
