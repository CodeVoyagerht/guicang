"""网页里改判定：两条写接口 + 弹窗要用的清单。

敢让浏览器写盘，是因为它只写 `.guicang/manual.json`——素材一根手指都不碰。
这一组测试盯的就是这条线：写进去能生效、写错地方要拒绝、清除要真的清掉。
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request

import _support  # noqa: F401  （把 src 挂上 sys.path）

from guicang import manual, scanner, webapp
from guicang.config import load_config
from guicang.store import Filters, Store


class ReviewApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = _support.TempDir()
        self.base = self._temp.__enter__()
        _support.demo_library(self.base)
        self.root = self.base / "library"
        self.config = load_config(self.root)
        with Store(self.config.index_path) as store:
            scanner.scan(self.root, self.config, store)
        self.httpd = webapp.serve(self.config, host="127.0.0.1", port=0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        self._temp.__exit__(None, None, None)

    # ── 工具 ────────────────────────────────────────────────────────────────
    def _post(self, path: str, payload: dict):
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    def _get(self, path: str):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def _pending(self) -> list:
        with Store(self.config.index_path) as store:
            return store.query(Filters(status="needs_review"), limit=100)

    # ── 改判定 ──────────────────────────────────────────────────────────────
    def test_review_pins_a_pending_asset_and_rescans(self) -> None:
        pending = self._pending()
        self.assertTrue(pending, "demo 库应该有判不出来的素材")
        record = pending[0]

        status, body = self._post("/api/review", {
            "paths": [record.rel_path],
            "shot_type": "航拍",
            "region": "开封古城",
        })

        self.assertEqual(status, 200, body)
        self.assertEqual(body["reviewed"], 1)
        self.assertEqual(body["action"], "set")
        self.assertEqual(body["fields"], ["region", "shot_type"])
        # 清单一落地就立刻重扫，落位与状态跟着变
        self.assertIn("待定", body["summary"])

        entries = manual.load_manual(self.root)
        self.assertIn(record.rel_path, entries)
        self.assertEqual(entries[record.rel_path]["shot_type"], "航拍")

        with Store(self.config.index_path) as store:
            saved = store.get_asset(record.rel_path)
        self.assertEqual(saved.shot_type, "航拍")
        self.assertEqual(saved.region, "开封古城")
        self.assertEqual(saved.status, "auto")
        self.assertNotIn("待定", saved.target_rel)

    def test_review_batch_of_paths(self) -> None:
        with Store(self.config.index_path) as store:
            rows = store.query(Filters(status="needs_review"), limit=100)
        paths = [row.rel_path for row in rows[:2]]
        if len(paths) < 2:
            self.skipTest("待定素材不足两条")

        status, body = self._post("/api/review", {"paths": paths, "branch": "抖音"})

        self.assertEqual(status, 200, body)
        self.assertEqual(body["reviewed"], 2)
        entries = manual.load_manual(self.root)
        for rel in paths:
            # 中文写得、也存成内部键——manual.json 里存的是 douyin / region / activity
            self.assertEqual(entries[rel]["branch"], "douyin")

    def test_review_on_unknown_path_is_rejected(self) -> None:
        status, body = self._post("/api/review", {
            "paths": ["不存在的目录/DJI_9999.MP4"],
            "shot_type": "航拍",
        })
        self.assertEqual(status, 400)
        self.assertIn("索引里没有", body["error"])

    def test_review_without_any_field_is_rejected(self) -> None:
        record = self._pending()[0]
        status, body = self._post("/api/review", {"paths": [record.rel_path], "action": "set"})
        self.assertEqual(status, 400)
        self.assertIn("要改哪一项", body["error"])

    def test_review_without_paths_is_rejected(self) -> None:
        status, body = self._post("/api/review", {"shot_type": "航拍"})
        self.assertEqual(status, 400)
        self.assertIn("paths", body["error"])

    def test_clear_removes_the_manual_entry(self) -> None:
        record = self._pending()[0]
        self._post("/api/review", {"paths": [record.rel_path], "shot_type": "航拍"})
        self.assertIn(record.rel_path, manual.load_manual(self.root))

        status, body = self._post("/api/review", {"paths": [record.rel_path], "action": "clear"})

        self.assertEqual(status, 200, body)
        self.assertEqual(body["action"], "clear")
        self.assertNotIn(record.rel_path, manual.load_manual(self.root))

    def test_review_leaves_media_untouched(self) -> None:
        """判定只写清单——素材本身一个字节都不该动。"""
        record = self._pending()[0]
        source = self.root / record.rel_path
        before = (source.stat().st_size, source.read_bytes()[:64])

        self._post("/api/review", {"paths": [record.rel_path], "shot_type": "地拍"})

        after = (source.stat().st_size, source.read_bytes()[:64])
        self.assertEqual(before, after)

    # ── 重新扫描 ────────────────────────────────────────────────────────────
    def test_rescan_reports_counts_and_stamps_last_scan(self) -> None:
        status, body = self._post("/api/rescan", {})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["scan"]["unchanged"], body["scan"]["seen"])
        self.assertTrue(body["last_scan"], "扫描完要写下时间戳，网页上那一行才有东西显示")

        status, info = self._get("/api/status")
        self.assertEqual(status, 200)
        self.assertTrue(info["last_scan"])

    def test_rescan_picks_up_new_files(self) -> None:
        before = self._post("/api/rescan", {})[1]["scan"]["seen"]
        # 从库里已有的照片复制一份新的出去，模拟「又拍了一天」
        sample = next(self.root.rglob("*.JPG"), None) or next(self.root.rglob("*.jpg"))
        target = self.root / "新加的一天" / "IMG_7001.JPG"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(sample.read_bytes())

        status, body = self._post("/api/rescan", {})

        self.assertEqual(status, 200, body)
        self.assertEqual(body["scan"]["seen"], before + 1)
        self.assertEqual(body["scan"]["added"], 1)

    # ── 弹窗要用的清单 ──────────────────────────────────────────────────────
    def test_status_exposes_lists_for_the_form(self) -> None:
        status, info = self._get("/api/status")
        self.assertEqual(status, 200)
        self.assertTrue(info["places"], "地点下拉框要有东西可选")
        self.assertTrue(info["activities"])
        self.assertIn("泡泡塘森林溪谷", info["places"])


if __name__ == "__main__":
    unittest.main()
