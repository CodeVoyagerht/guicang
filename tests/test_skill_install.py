"""技能安装脚本：文件名与 name 字段的校验、拒绝覆盖别人的目录。"""
from __future__ import annotations

import os
import subprocess
import sys

from _support import TempDir, quiet
import unittest

INSTALL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "skills", "guicang", "scripts", "install.py")


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, INSTALL, *args],
                          capture_output=True, text=True, encoding="utf-8")


class InstallScriptTests(unittest.TestCase):
    def test_install_py_exists(self) -> None:
        self.assertTrue(os.path.isfile(INSTALL))

    def test_dry_run_writes_nothing(self) -> None:
        with TempDir() as tmp:
            parent = str(tmp / "skills")
            proc = run("--target", "dir", "--dir", parent, "--dry-run")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("会写入", proc.stdout)
            self.assertFalse(os.path.exists(os.path.join(parent, "guicang")))

    def test_copies_skill_into_target(self) -> None:
        with TempDir() as tmp:
            parent = str(tmp / "skills")
            proc = run("--target", "dir", "--dir", parent)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            dest = os.path.join(parent, "guicang")
            self.assertTrue(os.path.isfile(os.path.join(dest, "SKILL.md")))
            self.assertTrue(os.path.isdir(os.path.join(dest, "references")))

    def test_refuses_to_overwrite_a_foreign_directory(self) -> None:
        with TempDir() as tmp:
            parent = tmp / "skills"
            foreign = parent / "guicang"
            foreign.mkdir(parents=True)
            (foreign / "keep.txt").write_text("我的项目，不是技能", encoding="utf-8")
            proc = run("--target", "dir", "--dir", str(parent), "--force")
            self.assertIn("拒绝覆盖", proc.stdout)
            self.assertTrue((foreign / "keep.txt").exists())

    def test_overwrites_our_own_installation(self) -> None:
        with TempDir() as tmp:
            parent = str(tmp / "skills")
            self.assertEqual(run("--target", "dir", "--dir", parent).returncode, 0)
            proc = run("--target", "dir", "--dir", parent, "--force")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("已复制", proc.stdout)

    def test_unknown_target_is_rejected(self) -> None:
        proc = run("--target", "nope")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("未知 target", proc.stderr)


if __name__ == "__main__":
    unittest.main()
