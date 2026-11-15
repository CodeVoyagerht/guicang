"""本机网页：手机上翻库、电脑上挑素材。只读 + 勾选，不改原件。

为什么自己写 HTTP 服务而不上框架：这东西要装在别人的 Windows 上、双击就能跑，
多一个 Flask 就多一个装不上的理由。标准库够用——路由一共十来个，
视频拖动靠 Range，缩略图靠 Pillow（没装就退回原图）。

安全上只有三条，但都硬：只发索引里登记过的文件（不能凭路径读任意文件）、
绑定 0.0.0.0 时必须带 token、不接受任何写盘请求（除了导出与选集）。
"""

from __future__ import annotations

import json
import mimetypes
import posixpath
import shutil
import subprocess
import threading
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import Config
from .export import export_collection, export_filters, write_csv
from .paths import human_size
from .store import Filters, Record, Store
from .storage import make_storage

WEB_DIR = Path(__file__).parent / "web"
THUMB_SIZE = 560
MEDIA_TYPES = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}

try:  # 可选依赖：没有 Pillow 也能跑，只是照片缩略图退回原图
    from PIL import Image, ImageOps
    _HAS_PILLOW = True
except ImportError:  # pragma: no cover - 环境相关
    _HAS_PILLOW = False

_FFMPEG = shutil.which("ffmpeg")


def _thumb_dir(config: Config) -> Path:
    return config.config_dir / "thumbs"


def make_thumbnail(source: Path, target: Path) -> bool:
    """照片走 Pillow，视频抓第 1 秒的一帧。都不行就返回 False，前端显示图标。"""
    if target.is_file() and target.stat().st_mtime >= source.stat().st_mtime:
        return True
    target.parent.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower()
    try:
        if suffix in MEDIA_TYPES:
            if not _FFMPEG:
                return False
            done = subprocess.run(
                [_FFMPEG, "-v", "error", "-y", "-ss", "1", "-i", str(source),
                 "-frames:v", "1", "-vf", f"scale={THUMB_SIZE}:-2", str(target)],
                capture_output=True, timeout=30, check=False,
            )
            return done.returncode == 0 and target.is_file()
        if not _HAS_PILLOW:
            return False
        with Image.open(source) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail((THUMB_SIZE, THUMB_SIZE))
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            image.save(target, "JPEG", quality=82, optimize=True)
        return True
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def _record_json(record: Record) -> dict:
    return {
        "id": record.id,
        "name": Path(record.rel_path).name,
        "rel_path": record.rel_path,
        "abs_path": record.abs_path,
        "kind": record.kind,
        "size": record.size,
        "size_human": human_size(record.size or 0),
        "captured_at": record.captured_at,
        "year": record.year,
        "month": record.month,
        "shot_type": record.shot_type,
        "shot_conf": record.shot_conf,
        "region": record.region,
        "region_conf": record.region_conf,
        "activity": record.activity,
        "edition": record.edition,
        "activity_conf": record.activity_conf,
        "branch": record.branch,
        "status": record.status,
        "camera": record.camera,
        "width": record.width,
        "height": record.height,
        "duration": record.duration,
        "target_rel": record.target_rel,
        "notes": record.notes,
    }


def _evidence_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


class Handler(BaseHTTPRequestHandler):
    server_version = "guicang/0.1"
    protocol_version = "HTTP/1.1"

    # 注入进来
    config: Config
    verbose: bool = False

    # ── 基础 ────────────────────────────────────────────────────────────────
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        if self.verbose:
            print(f"[{self.log_date_time_string()}] {fmt % args}")

    def _store(self) -> Store:
        return Store(self.config.index_path)

    def _query(self) -> dict:
        parsed = urllib.parse.urlparse(self.path)
        return {key: values[-1] for key, values in urllib.parse.parse_qs(parsed.query).items()}

    def _path_only(self) -> str:
        return urllib.parse.urlparse(self.path).path

    def _authorized(self, query: dict) -> bool:
        token = self.config.web_token
        if not token:
            return True
        supplied = (
            query.get("t")
            or self.headers.get("X-Guicang-Token")
            or self._cookie_token()
        )
        return supplied == token

    def _cookie_token(self) -> str:
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            key, _, value = part.strip().partition("=")
            if key == "guicang_token":
                return value
        return ""

    def _send_json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, body: bytes, content_type: str, status: int = 200,
                    headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._send_json({"error": message}, status)

    # ── 路由 ────────────────────────────────────────────────────────────────
    def do_GET(self) -> None:  # noqa: N802
        path = self._path_only()
        query = self._query()
        if not self._authorized(query):
            return self._error(401, "需要 token：在网址后加 ?t=你的token")
        try:
            if path in ("/", "/index.html"):
                return self._page()
            if path == "/ui.css":
                return self._asset("ui.css", "text/css; charset=utf-8")
            if path == "/ui.js":
                return self._asset("ui.js", "application/javascript; charset=utf-8")
            if path == "/api/status":
                return self._api_status()
            if path == "/api/facets":
                return self._api_facets()
            if path == "/api/assets":
                return self._api_assets(query)
            if path == "/api/thumb":
                return self._thumb(query)
            if path == "/api/media":
                return self._media(query)
            if path == "/api/asset":
                return self._api_asset(query)
            if path == "/api/collections":
                return self._api_collections()
            if path.startswith("/api/collections/"):
                return self._api_collection_detail(path)
            if path == "/api/export":
                return self._api_export(query)
            return self._error(404, "没有这个地址")
        except BrokenPipeError:
            return None
        except Exception as error:  # 网页不该因为一个请求崩掉
            return self._error(500, f"{type(error).__name__}: {error}")

    def do_POST(self) -> None:  # noqa: N802
        path = self._path_only()
        query = self._query()
        if not self._authorized(query):
            return self._error(401, "需要 token")
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return self._error(400, "请求体不是 JSON")
        try:
            if path == "/api/collections":
                return self._create_collection(payload)
            if path.startswith("/api/collections/") and path.endswith("/items"):
                return self._add_items(path, payload, remove=False)
            return self._error(404, "没有这个地址")
        except Exception as error:
            return self._error(500, f"{type(error).__name__}: {error}")

    def do_DELETE(self) -> None:  # noqa: N802
        path = self._path_only()
        if not self._authorized(self._query()):
            return self._error(401, "需要 token")
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            payload = {}
        try:
            if path.startswith("/api/collections/") and path.endswith("/items"):
                return self._add_items(path, payload, remove=True)
            if path.startswith("/api/collections/"):
                collection_id = int(path.rsplit("/", 1)[-1])
                with self._store() as store:
                    store.delete_collection(collection_id)
                return self._send_json({"deleted": collection_id})
            return self._error(404, "没有这个地址")
        except Exception as error:
            return self._error(500, f"{type(error).__name__}: {error}")

    # ── 页面与静态 ──────────────────────────────────────────────────────────
    def _asset(self, name: str, content_type: str) -> None:
        target = WEB_DIR / name
        if not target.is_file():
            return self._error(404, f"缺少 {name}")
        self._send_bytes(target.read_bytes(), content_type, headers={"Cache-Control": "no-cache"})

    def _page(self) -> None:
        target = WEB_DIR / "ui.html"
        if not target.is_file():
            return self._error(500, "web/ui.html 不在了")
        html = target.read_text(encoding="utf-8")
        html = html.replace("{{TITLE}}", f"归藏 · {self.config.root.name or '素材库'}")
        body = html.encode("utf-8")
        headers = {"Cache-Control": "no-cache"}
        if self.config.web_token:
            headers["Set-Cookie"] = f"guicang_token={self.config.web_token}; Path=/; SameSite=Lax"
        self._send_bytes(body, "text/html; charset=utf-8", headers=headers)

    # ── API ─────────────────────────────────────────────────────────────────
    def _api_status(self) -> None:
        with self._store() as store:
            stats = store.stats()
            last_scan = store.get_kv("last_scan", "")
            collections = store.collections()
        self._send_json({
            "library": str(self.config.root),
            "tree": str(self.config.tree_root),
            "stats": stats,
            "last_scan": last_scan,
            "places": [place.name for place in self.config.places],
            "activities": [activity.name for activity in self.config.activities],
            "sync": {
                "backend": self.config.sync.backend,
                "ready": bool(make_storage(self.config.sync)),
            },
            "collections": [
                {"id": c.id, "name": c.name, "count": c.count, "bytes": c.total_size}
                for c in collections
            ],
        })

    def _api_facets(self) -> None:
        with self._store() as store:
            self._send_json(store.facets())

    @staticmethod
    def _filters(query: dict) -> Filters:
        year = query.get("year")
        return Filters(
            region=query.get("region") or None,
            activity=query.get("activity") or None,
            shot_type=query.get("shot") or None,
            year=int(year) if year and year.isdigit() else None,
            month=query.get("month") or None,
            kind=query.get("kind") or None,
            status=query.get("status") or None,
            branch=query.get("branch") or None,
            query=query.get("q") or None,
            collection_id=int(query["collection"]) if (query.get("collection") or "").isdigit() else None,
        )

    def _api_assets(self, query: dict) -> None:
        filters = self._filters(query)
        limit = min(int(query.get("limit") or 120), 500)
        offset = max(int(query.get("offset") or 0), 0)
        with self._store() as store:
            total = store.count(filters)
            records = store.query(filters, limit=limit, offset=offset)
            items = [_record_json(record) for record in records]
        self._send_json({"total": total, "limit": limit, "offset": offset, "items": items})

    def _lookup(self, query: dict) -> Record | None:
        raw = query.get("id") or ""
        if not raw.isdigit():
            return None
        with self._store() as store:
            return store.get(int(raw))

    def _api_asset(self, query: dict) -> None:
        """单条素材的全部判定依据——「为什么它是航拍、为什么归到老君山」都在这里。"""
        record = self._lookup(query)
        if record is None:
            return self._error(404, "没有这条素材")
        payload = _record_json(record)
        payload["evidence"] = {
            "shot": _evidence_list(record.shot_evidence),
            "region": _evidence_list(record.region_evidence),
            "activity": _evidence_list(record.activity_evidence),
        }
        self._send_json(payload)

    def _thumb(self, query: dict) -> None:
        record = self._lookup(query)
        if record is None:
            return self._error(404, "没有这条素材")
        source = Path(record.abs_path)
        if not source.is_file():
            return self._error(404, "原件不在了")
        target = _thumb_dir(self.config) / f"{record.id}.jpg"
        if make_thumbnail(source, target):
            body = target.read_bytes()
            return self._send_bytes(body, "image/jpeg", headers={
                "Cache-Control": "public, max-age=86400",
            })
        if record.kind == "photo" and source.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
            body = source.read_bytes()
            content_type = mimetypes.guess_type(source.name)[0] or "image/jpeg"
            return self._send_bytes(body, content_type, headers={"Cache-Control": "public, max-age=3600"})
        return self._error(404, "没有可用的缩略图")

    def _media(self, query: dict) -> None:
        record = self._lookup(query)
        if record is None:
            return self._error(404, "没有这条素材")
        storage = make_storage(self.config.sync)
        if storage is not None and storage.backend == "s3":
            url = storage.presign(storage.key_for(record.rel_path))
            self.send_response(302)
            self.send_header("Location", url)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        source = Path(record.abs_path)
        if not source.is_file():
            return self._error(404, "原件不在了")
        self._stream_file(source)

    def _stream_file(self, source: Path) -> None:
        """支持 Range：视频没有它就不能拖进度条。"""
        size = source.stat().st_size
        content_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        start, end = 0, size - 1
        status = 200
        header_range = self.headers.get("Range") or ""
        if header_range.startswith("bytes="):
            piece = header_range.split("=", 1)[1].split(",")[0]
            head, _, tail = piece.partition("-")
            try:
                if head:
                    start = int(head)
                if tail:
                    end = int(tail)
            except ValueError:
                start, end = 0, size - 1
            if start >= size or start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            end = min(end, size - 1)
            status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "private, max-age=600")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(source, "rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _api_collections(self) -> None:
        with self._store() as store:
            items = [
                {"id": c.id, "name": c.name, "note": c.note, "count": c.count,
                 "bytes": c.total_size, "updated_at": c.updated_at}
                for c in store.collections()
            ]
        self._send_json({"items": items})

    def _api_collection_detail(self, path: str) -> None:
        piece = path.rsplit("/", 1)[-1]
        if not piece.isdigit():
            return self._error(400, "选集 id 不对")
        collection_id = int(piece)
        with self._store() as store:
            collection = next((c for c in store.collections() if c.id == collection_id), None)
            if collection is None:
                return self._error(404, "没有这个选集")
            items = [_record_json(record) for record in store.collection_items(collection_id)]
        self._send_json({
            "id": collection.id, "name": collection.name, "note": collection.note,
            "count": len(items), "items": items,
        })

    def _create_collection(self, payload: dict) -> None:
        name = (payload.get("name") or "").strip()
        if not name:
            return self._error(400, "选集要有个名字")
        ids = [int(x) for x in payload.get("ids") or [] if str(x).isdigit()]
        with self._store() as store:
            existing = store.find_collection(name)
            collection_id = existing.id if existing else store.create_collection(
                name, payload.get("note") or ""
            )
            added = store.add_items(collection_id, ids) if ids else 0
        self._send_json({"id": collection_id, "name": name, "added": added})

    def _add_items(self, path: str, payload: dict, *, remove: bool) -> None:
        piece = path.split("/")[3] if len(path.split("/")) > 3 else ""
        if not piece.isdigit():
            return self._error(400, "选集 id 不对")
        collection_id = int(piece)
        ids = [int(x) for x in payload.get("ids") or [] if str(x).isdigit()]
        with self._store() as store:
            changed = (store.remove_items if remove else store.add_items)(collection_id, ids)
        self._send_json({"collection": collection_id, "changed": changed})

    def _api_export(self, query: dict) -> None:
        out_dir = self.config.config_dir / "exports"
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        collection = query.get("collection") or ""
        with self._store() as store:
            if collection.isdigit():
                paths = export_collection(store, int(collection), out_dir)
            else:
                records = store.query(self._filters(query), limit=100000)
                target = out_dir / f"台账-{stamp}.csv"
                paths = [write_csv(records, target)]
        self._send_json({
            "files": [{"path": str(path), "name": path.name, "size": path.stat().st_size}
                      for path in paths],
            "folder": str(out_dir),
        })


def serve(config: Config, *, host: str | None = None, port: int | None = None,
          verbose: bool = False) -> ThreadingHTTPServer:
    """起服务。返回 server，调用方负责 serve_forever 或 close。"""
    handler = type("BoundHandler", (Handler,), {
        "config": config,
        "verbose": verbose,
    })
    address = (host or config.web_host, port or config.web_port)
    httpd = ThreadingHTTPServer(address, handler)
    httpd.daemon_threads = True
    return httpd


def run(config: Config, *, host: str | None = None, port: int | None = None,
        verbose: bool = False, open_browser: bool = False) -> None:
    httpd = serve(config, host=host, port=port, verbose=verbose)
    actual_host, actual_port = httpd.server_address[:2]
    shown = "127.0.0.1" if actual_host in ("0.0.0.0", "::") else actual_host
    token = f"?t={config.web_token}" if config.web_token else ""
    url = f"http://{shown}:{actual_port}/{token}"
    print(f"归藏已启动：{url}")
    print(f"素材库：{config.root}")
    if actual_host in ("0.0.0.0", "::") and not config.web_token:
        print("警告：监听在所有网卡上却没有设 token，同一局域网的人都能访问。")
    if open_browser:
        import webbrowser

        threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n再见。")
    finally:
        httpd.server_close()
