"""索引与扫描：增量、旁车挂靠、人工钉住、选集。"""

from __future__ import annotations

import unittest

import _support

from guicang import manual, scanner, store as store_mod
from guicang.config import load_config


class ScannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = _support.TempDir()
        self.root = self._temp.__enter__()
        _support.demo_library(self.root)
        self.config = load_config(self.root / "library")

    def tearDown(self) -> None:
        self._temp.__exit__(None, None, None)

    def _scan(self, *, force: bool = False):
        with store_mod.Store(self.config.index_path) as store:
            return scanner.scan(self.root / "library", self.config, store, force=force)

    def test_full_scan_counts(self) -> None:
        report = self._scan()
        self.assertEqual(report.seen, 8)
        self.assertEqual(report.sidecars, 2)
        self.assertEqual(report.needs_review, 2)
        self.assertEqual(report.errors, [])

    def test_rescan_is_incremental(self) -> None:
        self._scan()
        again = self._scan()
        self.assertEqual(again.added, 0)
        self.assertEqual(again.unchanged, 8)

    def test_tree_and_index_are_not_rescanned(self) -> None:
        self._scan()
        with store_mod.Store(self.config.index_path) as store:
            from guicang.materialize import apply_plan, build_plan

            apply_plan(build_plan(self.config, store.query(limit=100)), store)
        again = self._scan()
        # 分类树里的硬链接是副本，不能再被当成新素材收进来
        self.assertEqual(again.seen, 8)

    def test_manual_pin_promotes_to_auto(self) -> None:
        self._scan()
        with store_mod.Store(self.config.index_path) as store:
            pending = store.query(store_mod.Filters(status="needs_review"), limit=10)
        self.assertTrue(pending)
        target = pending[0]
        manual.set_manual(self.config.root, target.rel_path,
                          region="开封古城", shot_type="地拍")
        report = self._scan(force=True)
        self.assertEqual(report.needs_review, 1)
        with store_mod.Store(self.config.index_path) as store:
            after = store.get_asset(target.rel_path)
        self.assertEqual(after.region, "开封古城")
        self.assertEqual(after.status, "auto")
        self.assertGreaterEqual(after.region_conf, 0.99)


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = _support.TempDir()
        self.base = self._temp.__enter__()
        self.store = store_mod.Store(self.base / "index.db")
        for index in range(3):
            self.store.save_asset({
                "rel_path": f"2024-05-1{index} 开封古城/{index}.JPG",
                "abs_path": str(self.base / f"{index}.JPG"),
                "kind": "photo",
                "size": 100 * (index + 1),
                "mtime": float(index),
                "captured_at": f"2024-05-1{index} 10:00:00",
                "shot_type": "地拍" if index else "航拍",
                "region": "开封古城",
                "status": "auto" if index else "needs_review",
                "branch": "region",
            })

    def tearDown(self) -> None:
        self.store.close()
        self._temp.__exit__(None, None, None)

    def test_filters_narrow_by_dimension(self) -> None:
        self.assertEqual(self.store.count(), 3)
        self.assertEqual(self.store.count(store_mod.Filters(shot_type="航拍")), 1)
        self.assertEqual(self.store.count(store_mod.Filters(status="auto")), 2)
        self.assertEqual(self.store.count(store_mod.Filters(region="开封古城")), 3)

    def test_query_text_search(self) -> None:
        found = self.store.query(store_mod.Filters(query="2024-05-11"), limit=10)
        self.assertEqual(len(found), 1)
        self.assertIn("2024-05-11", found[0].rel_path)

    def test_facets_count_each_value(self) -> None:
        facets = self.store.facets()
        shots = {row["value"]: row["count"] for row in facets["shot_type"]}
        self.assertEqual(shots["地拍"], 2)
        self.assertEqual(shots["航拍"], 1)

    def test_collections_roundtrip(self) -> None:
        collection_id = self.store.create_collection("五月开封", note="备用")
        self.store.add_items(collection_id, [1, 2])
        self.assertEqual(len(self.store.collection_items(collection_id)), 2)
        found = self.store.find_collection("五月开封")
        self.assertIsNotNone(found)
        self.assertEqual(found.id, collection_id)
        self.assertEqual(found.count, 2)
        self.store.remove_items(collection_id, [1])
        self.assertEqual(len(self.store.collection_items(collection_id)), 1)

    def test_delete_missing_prunes_the_index(self) -> None:
        keep = {record.rel_path for record in self.store.all()[:2]}
        removed = self.store.delete_missing(keep)
        self.assertEqual(removed, 1)
        self.assertEqual(self.store.count(), 2)

    def test_journal_records_operations(self) -> None:
        self.store.log("hardlink", "a/b.JPG", "_归藏/区域/x/b.JPG", "run:test")
        rows = self.store.journal(limit=5)
        self.assertEqual(rows[0]["op"], "hardlink")


if __name__ == "__main__":
    unittest.main()
