"""命令行：整条链路走一遍 main()，确认子命令的名字、参数、退出码都对。"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path

import _support

from guicang import cli


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = _support.TempDir()
        self.base = self._temp.__enter__()
        _support.demo_library(self.base)
        self.root = self.base / "library"

    def tearDown(self) -> None:
        self._temp.__exit__(None, None, None)

    def run_cli(self, *args: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = cli.main(["--root", str(self.root), *args])
        return code, buffer.getvalue()

    def test_scan_report_plan_flow(self) -> None:
        code, out = self.run_cli("scan")
        self.assertEqual(code, 0)
        self.assertIn(f"扫到 {_support.DEMO_CLIPS} 个文件", out)

        code, out = self.run_cli("report")
        self.assertEqual(code, 0)
        self.assertIn("待定", out)

        code, out = self.run_cli("plan", "--json")
        self.assertEqual(code, 0)
        payload = json.loads(out[out.index("{"):])
        self.assertEqual(len(payload["items"]), _support.DEMO_CLIPS)

    def test_apply_then_undo(self) -> None:
        self.run_cli("scan")
        code, out = self.run_cli("apply", "--yes")
        self.assertEqual(code, 0)
        tree = self.root / "_归藏"
        self.assertTrue(any(tree.rglob("*.MP4")), "分类树里应该有视频")

        code, out = self.run_cli("undo", "--yes")
        self.assertEqual(code, 0)
        self.assertIn("已删除", out)
        self.assertFalse([p for p in tree.rglob("*") if p.is_file()])
        # 撤销后原件一个不少
        self.assertEqual(len(list(self.root.rglob("*.MP4"))), _support.DEMO_VIDEOS)

    def test_apply_without_yes_is_refused(self) -> None:
        self.run_cli("scan")
        code, out = self.run_cli("apply")
        self.assertEqual(code, 1)
        self.assertIn("已取消", out)
        self.assertFalse([p for p in (self.root / "_归藏").rglob("*") if p.is_file()])

    def test_root_option_works_on_both_sides(self) -> None:
        # 全局位置（--root 在子命令前）和子命令位置都要能用；用户记不住 argparse 的规矩。
        code, out = self.run_cli("doctor")
        self.assertEqual(code, 0)
        self.assertIn("素材根目录", out)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli.main(["--root", str(self.root), "doctor"])
        self.assertEqual(code, 0)
        self.assertIn("素材根目录", buffer.getvalue())

    def test_review_lists_and_pins(self) -> None:
        self.run_cli("scan")
        code, out = self.run_cli("review")
        self.assertEqual(code, 0)
        self.assertIn(f"待定 {_support.DEMO_PENDING} 个", out)
        rel = "2025-01-08 随手拍/IMG_1001.JPG"
        code, out = self.run_cli("review", "--set", rel, "--region", "开封古城", "--shot", "地拍")
        self.assertEqual(code, 0)
        code, out = self.run_cli("review")
        self.assertIn(f"待定 {_support.DEMO_PENDING - 1} 个", out)

    def test_review_can_force_douyin_branch(self) -> None:
        self.run_cli("scan")
        rel = "2024-08-03 泡泡塘/抖音竖屏/DJI_0008.MP4"
        code, out = self.run_cli("review", "--set", rel, "--branch", "抖音")
        self.assertEqual(code, 0)
        code, out = self.run_cli("explain", rel)
        self.assertEqual(code, 0)
        self.assertIn("抖音", out)
        self.assertIn("人工", out)

    def test_backup_mirrors_the_tree(self) -> None:
        self.run_cli("scan")
        self.run_cli("apply", "--yes")
        target = self.base / "备份盘"
        code, out = self.run_cli("backup", "--to", str(target), "--yes")
        self.assertEqual(code, 0)
        mirrored = [p for p in target.rglob("*") if p.is_file()]
        source = [p for p in (self.root / "_归藏").rglob("*") if p.is_file()]
        self.assertEqual(len(mirrored), len(source))
        self.assertTrue(any(p.suffix == ".MP4" for p in mirrored))
        # 再跑一次：全都已存在，不该重复搬
        code, out = self.run_cli("backup", "--to", str(target), "--yes")
        self.assertIn("跳过", out)

    def test_explain_shows_evidence(self) -> None:
        self.run_cli("scan")
        code, out = self.run_cli("explain", "2024-07-10 开封古城/第39届菊花文化节/DJI_0003.MP4")
        self.assertEqual(code, 0)
        self.assertIn("航拍", out)
        self.assertIn("遥测", out)

    def test_export_writes_ledger(self) -> None:
        self.run_cli("scan")
        out_dir = self.base / "out"
        code, out = self.run_cli("export", "--out", str(out_dir), "--format", "csv")
        self.assertEqual(code, 0)
        written = list(out_dir.glob("*.csv"))
        self.assertEqual(len(written), 1)

    def test_doctor_reports_environment(self) -> None:
        code, out = self.run_cli("doctor")
        self.assertEqual(code, 0)
        self.assertIn("Python", out)
        self.assertIn("素材根目录", out)

    def test_init_creates_config_once(self) -> None:
        with _support.TempDir() as empty:
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = cli.main(["--root", str(empty), "init"])
            self.assertEqual(code, 0)
            self.assertTrue((empty / ".guicang" / "guicang.toml").is_file())
            self.assertTrue((empty / ".guicang" / "places.toml").is_file())

    def test_bad_toml_reports_and_exits_2(self) -> None:
        config = self.root / ".guicang" / "guicang.toml"
        config.write_text("library = [ oops", encoding="utf-8")
        code, out = self.run_cli("scan")
        self.assertEqual(code, 2)
        self.assertIn("配置有问题", out)


if __name__ == "__main__":
    unittest.main()
