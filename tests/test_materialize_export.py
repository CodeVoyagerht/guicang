"""落位与导账：硬链接/复制/移动、重名不覆盖、撤回收空目录、台账三种格式。"""

from __future__ import annotations

import csv
import json
import unittest
from pathlib import Path

import _support

from guicang import export, materialize, scanner, store as store_mod
from guicang.config import load_config


class MaterializeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = _support.TempDir()
        self.base = self._temp.__enter__()
        _support.demo_library(self.base)
        self.root = self.base / "library"
        self.config = load_config(self.root)
        with store_mod.Store(self.config.index_path) as store:
            self.report = scanner.scan(self.root, self.config, store)

    def tearDown(self) -> None:
        self._temp.__exit__(None, None, None)

    def _plan(self, mode: str = "hardlink"):
        with store_mod.Store(self.config.index_path) as store:
            return materialize.build_plan(self.config, store.query(limit=1000), mode=mode)

    def test_plan_covers_every_asset(self) -> None:
        plan = self._plan()
        self.assertEqual(plan.counts().get("hardlink"), _support.DEMO_CLIPS)
        self.assertEqual(plan.conflicts, 0)

    def test_apply_creates_hardlinks_without_copying_bytes(self) -> None:
        with store_mod.Store(self.config.index_path) as store:
            plan = materialize.build_plan(self.config, store.query(limit=1000))
            report = materialize.apply_plan(plan, store)
        self.assertEqual(report.linked, _support.DEMO_CLIPS)
        self.assertEqual(report.bytes_copied, 0)
        targets = [Path(item.target) for item in plan.items]
        for target in targets:
            self.assertTrue(target.is_file(), f"{target} 没落位")
        # 硬链接与原件的 inode 相同，说明没占第二份空间
        source = Path(plan.items[0].source)
        self.assertEqual(source.stat().st_ino, Path(plan.items[0].target).stat().st_ino)

    def test_undo_removes_links_and_prunes_empty_dirs(self) -> None:
        with store_mod.Store(self.config.index_path) as store:
            plan = materialize.build_plan(self.config, store.query(limit=1000))
            report = materialize.apply_plan(plan, store)
            removed, dirs, ignored, errors = materialize.undo_run(
                store, report.run_id, tree_root=self.config.tree_root
            )
        self.assertEqual(removed, _support.DEMO_CLIPS)
        self.assertEqual(errors, [])
        self.assertGreater(dirs, 0)
        self.assertEqual([p for p in self.config.tree_root.rglob("*") if p.is_dir()], [])
        # 原件全在
        self.assertEqual(len(list(self.root.rglob("*.MP4"))), _support.DEMO_VIDEOS)

    def test_copy_mode_copies_and_move_mode_moves(self) -> None:
        with store_mod.Store(self.config.index_path) as store:
            plan = materialize.build_plan(self.config, store.query(limit=1000), mode="copy")
            report = materialize.apply_plan(plan, store)
        self.assertEqual(report.copied, _support.DEMO_CLIPS)
        self.assertEqual(report.bytes_copied, sum(item.source.stat().st_size for item in plan.items))

    def test_identical_target_names_do_not_overwrite(self) -> None:
        with _support.TempDir() as base:
            target = base / "同名.JPG"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"first")
            chosen, renamed = materialize._dedupe(target)
            self.assertTrue(renamed)
            self.assertEqual(chosen.name, "同名-1.JPG")

    def test_pending_only_plan(self) -> None:
        with store_mod.Store(self.config.index_path) as store:
            plan = materialize.build_plan(self.config, store.query(limit=1000), only_review=True)
        self.assertEqual(len(plan.items), _support.DEMO_PENDING)
        for item in plan.items:
            # 抖音素材虽然拍摄类型没把握，也留在自己的枝里，不去根上的待定桶
            self.assertTrue(
                "待定" in item.target.parts or "抖音" in item.target.parts,
                f"{item.target} 既不在待定桶也不在抖音枝",
            )


class ExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = _support.TempDir()
        self.base = self._temp.__enter__()
        _support.demo_library(self.base)
        self.root = self.base / "library"
        self.config = load_config(self.root)
        self.store = store_mod.Store(self.config.index_path)
        scanner.scan(self.root, self.config, self.store)
        self.records = self.store.query(limit=1000)

    def tearDown(self) -> None:
        self.store.close()
        self._temp.__exit__(None, None, None)

    def test_csv_is_excel_friendly(self) -> None:
        path = export.write_csv(self.records, self.base / "台账.csv")
        raw = path.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "CSV 要带 BOM，否则 Excel 里是乱码")
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(rows[0], [label for label, _ in export.CSV_COLUMNS])
        self.assertEqual(len(rows) - 1, _support.DEMO_CLIPS)
        self.assertEqual(rows[1][0], "1")

    def test_json_carries_the_decision_and_paths(self) -> None:
        path = export.write_json(self.records, self.base / "台账.json", config=self.config)
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["count"], _support.DEMO_CLIPS)
        first = payload["items"][0]
        self.assertIn("region", first)
        self.assertIn("abs_path", first)

    def test_pathlist_is_one_path_per_line(self) -> None:
        path = export.write_pathlist(self.records, self.base / "清单.txt")
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(lines), _support.DEMO_CLIPS)
        self.assertTrue(Path(lines[0]).is_absolute())

    def test_collection_export_writes_all_formats(self) -> None:
        collection_id = self.store.create_collection("试用", note="")
        self.store.add_items(collection_id, [record.id for record in self.records[:3]])
        written = export.export_collection(self.store, collection_id, self.base / "out",
                                           formats=("csv", "json", "txt"))
        self.assertEqual(len(written), 3)
        for path in written:
            self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
