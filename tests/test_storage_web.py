"""云端与网页：SigV4 签名、本地后端增量同步、token 鉴权、接口形状。"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import _support

from guicang import scanner, storage, store as store_mod, webapp
from guicang.config import SyncConfig, load_config


class SignatureTests(unittest.TestCase):
    SCOPE = "20261115/us-east-1/s3/aws4_request"

    def _auth(self, payload_hash: str = "UNSIGNED-PAYLOAD") -> str:
        canonical, signed = storage.canonical_request(
            "PUT",
            "/bucket/guicang/a.JPG",
            "",
            {"Host": "example.com", "x-amz-content-sha256": payload_hash},
            payload_hash,
        )
        signature = storage.sign_v4(
            secret_key="secret",
            date_stamp="20261115",
            region="us-east-1",
            amz_date="20261115T100000Z",
            scope=self.SCOPE,
            canonical=canonical,
        )
        return storage.authorization_header(
            access_key="AKIDEXAMPLE",
            scope=self.SCOPE,
            signed_headers=signed,
            signature=signature,
        )

    def test_header_has_all_required_parts(self) -> None:
        header = self._auth()
        self.assertIn("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20261115/", header)
        self.assertIn("SignedHeaders=", header)
        self.assertIn("Signature=", header)

    def test_signature_is_deterministic(self) -> None:
        self.assertEqual(self._auth(), self._auth())

    def test_payload_change_changes_signature(self) -> None:
        self.assertNotEqual(self._auth("UNSIGNED-PAYLOAD"),
                            self._auth(storage.sha256_hex(b"hello")))

    def test_signing_key_is_bytes_of_expected_length(self) -> None:
        key = storage.signing_key("secret", "20261115", "us-east-1", "s3")
        self.assertEqual(len(key), 32)


class SyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = _support.TempDir()
        self.base = self._temp.__enter__()
        _support.demo_library(self.base)
        self.root = self.base / "library"
        self.config = load_config(self.root, overrides={
            "sync": SyncConfig(backend="local", local_dir=str(self.base / "cloud"))
        })
        self.store = store_mod.Store(self.config.index_path)
        scanner.scan(self.root, self.config, self.store)
        self.records = self.store.query(limit=1000)

    def tearDown(self) -> None:
        self.store.close()
        self._temp.__exit__(None, None, None)

    def test_first_sync_uploads_everything(self) -> None:
        report = storage.sync_records(self.config, self.store, self.records)
        self.assertEqual(report.uploaded, 8)
        self.assertEqual(report.failed, 0)
        cloud = Path(self.config.sync.local_dir)
        self.assertEqual(len([p for p in cloud.rglob("*") if p.is_file()]), 8)

    def test_second_sync_skips_unchanged(self) -> None:
        storage.sync_records(self.config, self.store, self.records)
        again = storage.sync_records(self.config, self.store, self.records)
        self.assertEqual(again.uploaded, 0)
        self.assertEqual(again.skipped, 8)

    def test_dry_run_uploads_nothing(self) -> None:
        report = storage.sync_records(self.config, self.store, self.records, dry_run=True)
        self.assertEqual(report.uploaded, 0)
        self.assertEqual(report.pending, 8)
        self.assertFalse(Path(self.config.sync.local_dir).exists())

    def test_missing_config_reports_instead_of_crashing(self) -> None:
        config = load_config(self.root, overrides={"sync": SyncConfig(backend="s3")})
        report = storage.sync_records(config, self.store, self.records[:2])
        self.assertEqual(report.failed, 2)
        self.assertTrue(report.errors)


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temp = _support.TempDir()
        cls.base = cls._temp.__enter__()
        _support.demo_library(cls.base)
        cls.root = cls.base / "library"
        cls.config = load_config(cls.root)
        cls.config.web_token = "t0k"
        with store_mod.Store(cls.config.index_path) as store:
            scanner.scan(cls.root, cls.config, store)
        cls.httpd = webapp.serve(cls.config, host="127.0.0.1", port=0)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        cls._temp.__exit__(None, None, None)

    def _get(self, path: str, *, token: str = "t0k", raw: bool = False):
        url = f"http://127.0.0.1:{self.port}{path}"
        if token:
            url += ("&" if "?" in path else "?") + f"t={token}"
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                body = response.read()
                return response.status, (body if raw else json.loads(body.decode("utf-8")))
        except urllib.error.HTTPError as error:
            return error.code, (error.read() if raw else error.read().decode("utf-8"))

    def test_without_token_is_rejected(self) -> None:
        status, _ = self._get("/api/assets", token="")
        self.assertEqual(status, 401)

    def test_wrong_token_is_rejected(self) -> None:
        status, _ = self._get("/api/assets", token="nope")
        self.assertEqual(status, 401)

    def test_index_page_is_served(self) -> None:
        status, body = self._get("/", raw=True)
        self.assertEqual(status, 200)
        self.assertIn(b"<", body)

    def test_assets_endpoint_shape(self) -> None:
        status, payload = self._get("/api/assets?limit=5")
        self.assertEqual(status, 200)
        self.assertEqual(payload["total"], 8)
        self.assertEqual(len(payload["items"]), 5)
        self.assertIn("rel_path", payload["items"][0])

    def test_filters_work_over_http(self) -> None:
        status, payload = self._get("/api/assets?shot=" + urllib.parse.quote("航拍"))
        self.assertEqual(status, 200)
        # 三条都是无人机拍的：两条自动落位、一条待定（DJI_0002 读不到机型）
        self.assertEqual(payload["total"], 3)

    def test_asset_detail_carries_evidence(self) -> None:
        status, payload = self._get("/api/asset?id=1")
        self.assertEqual(status, 200)
        self.assertIn("evidence", payload)
        self.assertIn("shot", payload["evidence"])

    def test_facets_endpoint(self) -> None:
        status, payload = self._get("/api/facets")
        self.assertEqual(status, 200)
        self.assertIn("shot_type", payload)

    def test_video_stream_supports_range(self) -> None:
        with store_mod.Store(self.config.index_path) as store:
            videos = store.query(store_mod.Filters(kind="video"), limit=1)
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/media?id={videos[0].id}&t=t0k",
            headers={"Range": "bytes=0-99"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(len(response.read()), 100)


if __name__ == "__main__":
    unittest.main()
