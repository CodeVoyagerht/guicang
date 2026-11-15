"""云端：两种后端，一套接口。

``local``  —— 同步到一个目录。网盘客户端（百度网盘、坚果云）、移动硬盘、
             局域网共享都吃这一套，不需要任何账号和费用。
``s3``     —— 任意 S3 兼容端点：AWS S3、MinIO、Cloudflare R2、七牛、腾讯 COS
             的 S3 兼容入口。签名自己算（AWS Signature V4），不引 boto3——
             核心零依赖是底线，而 V4 签名一共不到 80 行。

上传是**幂等**的：键是 ``前缀 + 相对路径``，库里记着大小与修改时间，
没变就不传。断了重跑不会重复传，也不会传半截（先传临时键再改名）。
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import mimetypes
import os
import shutil
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import SyncConfig
from .store import Record, Store

CHUNK = 1024 * 1024
SERVICE = "s3"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hmac_sha256(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _amz_now() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")


def _uri_encode(text: str, *, keep_slash: bool = True) -> str:
    safe = "/-_.~" if keep_slash else "-_.~"
    return urllib.parse.quote(text, safe=safe)


def canonical_request(
    method: str, uri_path: str, query: list[tuple[str, str]], headers: dict[str, str],
    payload_hash: str, *, signed: list[str] | None = None,
) -> tuple[str, str]:
    """返回（规范化请求, 参与签名的头列表）。头和查询都必须排好序，这是 V4 的死规矩。"""
    query_pairs = sorted((key, value) for key, value in query)
    canonical_query = "&".join(f"{_uri_encode(k, keep_slash=False)}={_uri_encode(v, keep_slash=False)}"
                               for k, v in query_pairs)
    lower = {key.lower(): str(value).strip() for key, value in headers.items()}
    names = sorted(signed if signed is not None else lower.keys())
    canonical_headers = "".join(f"{name}:{lower[name]}\n" for name in names)
    signed_headers = ";".join(names)
    return (
        "\n".join([
            method,
            _uri_encode(uri_path),
            canonical_query,
            canonical_headers,
            signed_headers,
            payload_hash,
        ]),
        signed_headers,
    )


def string_to_sign(amz_date: str, scope: str, canonical: str) -> str:
    return "\n".join([
        "AWS4-HMAC-SHA256",
        amz_date,
        scope,
        sha256_hex(canonical.encode("utf-8")),
    ])


def signing_key(secret_key: str, date_stamp: str, region: str, service: str = SERVICE) -> bytes:
    key = f"AWS4{secret_key}".encode("utf-8")
    key = hmac_sha256(key, date_stamp)
    key = hmac_sha256(key, region)
    key = hmac_sha256(key, service)
    return hmac_sha256(key, "aws4_request")


def sign_v4(
    *, secret_key: str, date_stamp: str, region: str, amz_date: str, scope: str, canonical: str,
    service: str = SERVICE,
) -> str:
    key = signing_key(secret_key, date_stamp, region, service)
    return hmac.new(key, string_to_sign(amz_date, scope, canonical).encode("utf-8"),
                    hashlib.sha256).hexdigest()


def authorization_header(access_key: str, scope: str, signed_headers: str, signature: str) -> str:
    return (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )


@dataclass
class SyncReport:
    uploaded: int = 0
    skipped: int = 0
    pending: int = 0
    failed: int = 0
    bytes_sent: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"同步完成：上传 {self.uploaded}，跳过 {self.skipped}，失败 {self.failed}，"
                f"共 {self.bytes_sent / 1024 / 1024:.1f}MB")


class LocalStorage:
    """同步到一个目录。软链、网络盘都行。"""

    backend = "local"

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory).expanduser()

    def key_for(self, rel_path: str, prefix: str = "") -> Path:
        return self.directory.joinpath(*f"{prefix}{rel_path}".split("/"))

    def put_file(self, source: Path, key: Path) -> int:
        key.parent.mkdir(parents=True, exist_ok=True)
        temporary = key.with_suffix(key.suffix + ".part")
        shutil.copy2(source, temporary)
        os.replace(temporary, key)
        return key.stat().st_size

    def exists(self, key: Path) -> bool:
        return key.is_file()

    def presign(self, *args, **kwargs) -> str | None:  # 本地后端由 webapp 直接发文件
        return None

    def describe(self) -> str:
        return f"本地目录 {self.directory}"


class S3Storage:
    """S3 兼容端点。路径式寻址（MinIO/R2 都认），不依赖虚拟主机风格。"""

    backend = "s3"

    def __init__(self, settings: SyncConfig) -> None:
        self.settings = settings
        endpoint = settings.endpoint.strip()
        if "://" not in endpoint:
            endpoint = "https://" + endpoint
        parsed = urllib.parse.urlparse(endpoint)
        self.scheme = parsed.scheme or "https"
        self.host = parsed.netloc
        self.base_path = parsed.path.rstrip("/")
        self.bucket = settings.bucket
        self.region = settings.region or "us-east-1"

    # ── 键与签名 ────────────────────────────────────────────────────────────
    def key_for(self, rel_path: str) -> str:
        prefix = self.settings.prefix or ""
        return f"{prefix}{rel_path}".lstrip("/")

    def _path(self, key: str) -> str:
        return f"{self.base_path}/{self.bucket}/{_uri_encode(key)}".replace("//", "/")

    def _scope(self, date_stamp: str) -> str:
        return f"{date_stamp}/{self.region}/{SERVICE}/aws4_request"

    def _connection(self) -> http.client.HTTPConnection:
        if self.scheme == "http":
            return http.client.HTTPConnection(self.host, timeout=60)
        return http.client.HTTPSConnection(self.host, timeout=60)

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(CHUNK), b""):
                digest.update(chunk)
        return digest.hexdigest()

    # ── 操作 ────────────────────────────────────────────────────────────────
    def put_file(self, source: Path, key: str) -> int:
        size = source.stat().st_size
        payload_hash = self._file_sha256(source)
        amz_date, date_stamp = _amz_now()
        scope = self._scope(date_stamp)
        content_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        headers = {
            "host": self.host,
            "content-length": str(size),
            "content-type": content_type,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        canonical, signed_headers = canonical_request(
            "PUT", self._path(key), [], headers, payload_hash
        )
        signature = sign_v4(
            secret_key=self.settings.secret_key, date_stamp=date_stamp, region=self.region,
            amz_date=amz_date, scope=scope, canonical=canonical,
        )
        headers["authorization"] = authorization_header(
            self.settings.access_key, scope, signed_headers, signature
        )

        connection = self._connection()
        try:
            connection.putrequest("PUT", self._path(key))
            for name, value in headers.items():
                if name != "host":
                    connection.putheader(name, value)
            connection.putheader("Host", self.host)
            connection.endheaders()
            sent = 0
            with open(source, "rb") as handle:
                for chunk in iter(lambda: handle.read(CHUNK), b""):
                    connection.send(chunk)
                    sent += len(chunk)
            response = connection.getresponse()
            body = response.read(2000)
            if response.status not in (200, 201, 204):
                raise OSError(f"S3 返回 {response.status}：{body[:300]!r}")
        finally:
            connection.close()
        return sent

    def exists(self, key: str) -> bool:
        amz_date, date_stamp = _amz_now()
        scope = self._scope(date_stamp)
        payload_hash = sha256_hex(b"")
        headers = {
            "host": self.host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        canonical, signed_headers = canonical_request(
            "HEAD", self._path(key), [], headers, payload_hash
        )
        signature = sign_v4(
            secret_key=self.settings.secret_key, date_stamp=date_stamp, region=self.region,
            amz_date=amz_date, scope=scope, canonical=canonical,
        )
        headers["authorization"] = authorization_header(
            self.settings.access_key, scope, signed_headers, signature
        )
        connection = self._connection()
        try:
            connection.request("HEAD", self._path(key), headers=headers)
            response = connection.getresponse()
            response.read(1)
            return response.status == 200
        except OSError:
            return False
        finally:
            connection.close()

    def presign(self, key: str, ttl: int = 3600) -> str:
        """预签名 GET，浏览器可以直接从云端拉流。"""
        amz_date, date_stamp = _amz_now()
        scope = self._scope(date_stamp)
        query = [
            ("X-Amz-Algorithm", "AWS4-HMAC-SHA256"),
            ("X-Amz-Credential", f"{self.settings.access_key}/{scope}"),
            ("X-Amz-Date", amz_date),
            ("X-Amz-Expires", str(int(ttl))),
            ("X-Amz-SignedHeaders", "host"),
        ]
        payload_hash = "UNSIGNED-PAYLOAD"
        canonical, signed_headers = canonical_request(
            "GET", self._path(key), query, {"host": self.host}, payload_hash, signed=["host"]
        )
        signature = sign_v4(
            secret_key=self.settings.secret_key, date_stamp=date_stamp, region=self.region,
            amz_date=amz_date, scope=scope, canonical=canonical,
        )
        query.append(("X-Amz-Signature", signature))
        encoded = urllib.parse.urlencode([(k, v) for k, v in query])
        return f"{self.scheme}://{self.host}{self._path(key)}?{encoded}"

    def describe(self) -> str:
        return f"S3 {self.scheme}://{self.host}/{self.bucket}（{self.region}，前缀 {self.settings.prefix}）"


def make_storage(settings: SyncConfig):
    """按配置造后端。没配好就返回 None，调用方自己决定要不要报错。"""
    if not settings.ready:
        return None
    if settings.backend == "s3":
        return S3Storage(settings)
    return LocalStorage(settings.local_dir)


def _state_key(backend: str) -> str:
    return f"sync:{backend}"


def load_state(store: Store, backend: str) -> dict:
    raw = store.get_kv(_state_key(backend), "")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def save_state(store: Store, backend: str, state: dict) -> None:
    store.set_kv(_state_key(backend), json.dumps(state, ensure_ascii=False))


def sync_records(
    config, store: Store, records: list[Record], *, dry_run: bool = False, progress=None
) -> SyncReport:
    """把记录对应的文件送到云端。大小与修改时间都没变就不重传。"""
    storage = make_storage(config.sync)
    report = SyncReport()
    if storage is None:
        report.errors.append("云端还没配置好（.guicang/guicang.toml 的 [sync] 段）")
        report.failed = len(records)
        return report

    state = load_state(store, storage.backend)
    for record in records:
        source = Path(record.abs_path)
        if not source.is_file():
            report.failed += 1
            report.errors.append(f"{record.rel_path}: 原件不在了")
            continue
        if storage.backend == "s3":
            key = storage.key_for(record.rel_path)
        else:
            key = storage.key_for(record.rel_path, "")
        signature = f"{record.size}:{int(record.mtime or 0)}"
        if state.get(record.rel_path) == signature:
            report.skipped += 1
            continue
        if dry_run:
            report.pending += 1
            continue
        try:
            sent = storage.put_file(source, key)
            state[record.rel_path] = signature
            report.uploaded += 1
            report.bytes_sent += int(sent or 0)
            if progress:
                progress(record.rel_path, sent or 0)
        except OSError as error:
            report.failed += 1
            report.errors.append(f"{record.rel_path}: {error}")
    if not dry_run:
        save_state(store, storage.backend, state)
    return report
