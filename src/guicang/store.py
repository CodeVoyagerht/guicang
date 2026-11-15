"""索引：SQLite 是真相。磁盘上的原件只是一堆事实，索引才是「谁在哪」。

为什么不是每次现扫：一个 4T 的素材库扫一遍要十几分钟，而「找 2025 年开封的
航拍」这种问题一天要问二十遍。所以扫描只做增量（大小 + 修改时间变了才重读），
判定结果也存进来——重新分类时不必再摸磁盘。

证据存成 JSON 文本而不是拆表：它只用来解释，不做查询条件。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id              INTEGER PRIMARY KEY,
    rel_path        TEXT NOT NULL UNIQUE,
    abs_path        TEXT NOT NULL,
    kind            TEXT NOT NULL,
    size            INTEGER NOT NULL DEFAULT 0,
    mtime           REAL NOT NULL DEFAULT 0,
    captured_at     TEXT,
    captured_source TEXT,
    captured_conf   REAL DEFAULT 0,
    lat             REAL,
    lon             REAL,
    alt             REAL,
    alt_kind        TEXT,
    gps_source      TEXT,
    make            TEXT,
    model           TEXT,
    width           INTEGER,
    height          INTEGER,
    duration        REAL,
    sidecar_of      TEXT,
    shot_type       TEXT,
    shot_conf       REAL DEFAULT 0,
    shot_evidence   TEXT,
    region          TEXT,
    region_conf     REAL DEFAULT 0,
    region_evidence TEXT,
    province        TEXT,
    city            TEXT,
    area            TEXT,
    activity        TEXT,
    edition         TEXT,
    activity_conf   REAL DEFAULT 0,
    activity_evidence TEXT,
    branch          TEXT,
    status          TEXT,
    target_rel      TEXT,
    notes           TEXT,
    updated_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_assets_region ON assets(region);
CREATE INDEX IF NOT EXISTS idx_assets_area ON assets(area);
CREATE INDEX IF NOT EXISTS idx_assets_activity ON assets(activity);
CREATE INDEX IF NOT EXISTS idx_assets_status ON assets(status);
CREATE INDEX IF NOT EXISTS idx_assets_captured ON assets(captured_at);

CREATE TABLE IF NOT EXISTS collections (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    note       TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collection_items (
    collection_id INTEGER NOT NULL,
    asset_id      INTEGER NOT NULL,
    position      INTEGER NOT NULL DEFAULT 0,
    added_at      TEXT NOT NULL,
    PRIMARY KEY (collection_id, asset_id)
);

CREATE TABLE IF NOT EXISTS journal (
    id      INTEGER PRIMARY KEY,
    ts      TEXT NOT NULL,
    op      TEXT NOT NULL,
    src_rel TEXT,
    dst_rel TEXT,
    detail  TEXT
);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


@dataclass
class Record:
    """索引里的一行，给报告、网页、导出共用。"""

    id: int
    rel_path: str
    abs_path: str
    kind: str
    size: int = 0
    mtime: float = 0.0
    captured_at: str | None = None
    captured_source: str | None = None
    captured_conf: float = 0.0
    lat: float | None = None
    lon: float | None = None
    alt: float | None = None
    alt_kind: str | None = None
    gps_source: str | None = None
    make: str | None = None
    model: str | None = None
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    sidecar_of: str | None = None
    shot_type: str | None = None
    shot_conf: float = 0.0
    shot_evidence: str | None = None
    region: str | None = None
    region_conf: float = 0.0
    region_evidence: str | None = None
    province: str | None = None
    city: str | None = None
    area: str | None = None
    activity: str | None = None
    edition: str | None = None
    activity_conf: float = 0.0
    activity_evidence: str | None = None
    branch: str | None = None
    status: str | None = None
    target_rel: str | None = None
    notes: str | None = None
    updated_at: str | None = None

    @property
    def captured_dt(self) -> datetime | None:
        if not self.captured_at:
            return None
        try:
            return datetime.fromisoformat(self.captured_at)
        except ValueError:
            return None

    @property
    def year(self) -> int | None:
        when = self.captured_dt
        return when.year if when else None

    @property
    def month(self) -> str | None:
        when = self.captured_dt
        return f"{when.year:04d}-{when.month:02d}" if when else None

    @property
    def camera(self) -> str:
        from .model import camera_name

        return camera_name(self.make, self.model)

    def as_dict(self) -> dict:
        payload = {}
        for key, value in self.__dict__.items():
            payload[key] = value
        return payload


@dataclass
class Collection:
    id: int
    name: str
    note: str
    created_at: str
    updated_at: str
    count: int = 0
    total_size: int = 0


@dataclass
class Filters:
    """网页与 CLI 共用的一套筛选条件。空值就是不过滤。"""

    region: str | None = None
    area: str | None = None
    activity: str | None = None
    shot_type: str | None = None
    year: int | None = None
    month: str | None = None
    kind: str | None = None
    status: str | None = None
    branch: str | None = None
    query: str | None = None
    collection_id: int | None = None

    def where(self) -> tuple[str, list]:
        clauses: list[str] = []
        args: list = []
        for column, value in (
            ("region", self.region),
            ("area", self.area),
            ("activity", self.activity),
            ("shot_type", self.shot_type),
            ("kind", self.kind),
            ("status", self.status),
            ("branch", self.branch),
        ):
            if value:
                clauses.append(f"{column} = ?")
                args.append(value)
        if self.year:
            clauses.append("substr(captured_at, 1, 4) = ?")
            args.append(f"{self.year:04d}")
        if self.month:
            clauses.append("substr(captured_at, 1, 7) = ?")
            args.append(self.month)
        if self.query:
            clauses.append("(rel_path LIKE ? OR make LIKE ? OR model LIKE ?)")
            needle = f"%{self.query}%"
            args.extend([needle, needle, needle])
        if self.collection_id:
            clauses.append("id IN (SELECT asset_id FROM collection_items WHERE collection_id = ?)")
            args.append(self.collection_id)
        if not clauses:
            return "", args
        return " WHERE " + " AND ".join(clauses), args


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def _ensure_columns(conn) -> None:
    """老索引补列。0.1.0 建的库没有省/市/区域三列，直接 ALTER 加上，
    别让用户为了三个新字段重建索引、丢掉所有人工钉住。"""
    present = {row["name"] for row in conn.execute("PRAGMA table_info(assets)")}
    for column in ("province", "city", "area", "alt_kind"):
        if column not in present:
            conn.execute(f"ALTER TABLE assets ADD COLUMN {column} TEXT")


class Store:
    """索引的读写口。用 with 块打开，退出即提交。"""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=15)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        _ensure_columns(self.conn)
        self.conn.commit()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        try:
            self.conn.commit()
        finally:
            self.conn.close()

    # ── 素材 ────────────────────────────────────────────────────────────────
    def get_asset(self, rel_path: str) -> Record | None:
        row = self.conn.execute("SELECT * FROM assets WHERE rel_path = ?", (rel_path,)).fetchone()
        return Record(**dict(row)) if row else None

    def fingerprint(self, rel_path: str) -> tuple[int, float] | None:
        row = self.conn.execute(
            "SELECT size, mtime FROM assets WHERE rel_path = ?", (rel_path,)
        ).fetchone()
        return (row["size"], row["mtime"]) if row else None

    def save_asset(self, payload: dict) -> int:
        payload = dict(payload)
        payload["updated_at"] = _now()
        columns = ", ".join(payload)
        placeholders = ", ".join("?" for _ in payload)
        updates = ", ".join(f"{key} = excluded.{key}" for key in payload if key != "rel_path")
        self.conn.execute(
            f"INSERT INTO assets ({columns}) VALUES ({placeholders}) "
            f"ON CONFLICT(rel_path) DO UPDATE SET {updates}",
            list(payload.values()),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id FROM assets WHERE rel_path = ?", (payload["rel_path"],)
        ).fetchone()
        return int(row["id"])

    def count(self, filters: Filters | None = None) -> int:
        where, args = (filters or Filters()).where()
        return int(self.conn.execute(f"SELECT COUNT(*) AS n FROM assets{where}", args).fetchone()["n"])

    def query(self, filters: Filters | None = None, *, limit: int = 200, offset: int = 0,
              order: str = "captured_at DESC, rel_path") -> list[Record]:
        filters = filters or Filters()
        where, args = filters.where()
        rows = self.conn.execute(
            f"SELECT * FROM assets{where} ORDER BY {order} LIMIT ? OFFSET ?",
            [*args, limit, offset],
        ).fetchall()
        return [Record(**dict(row)) for row in rows]

    def get(self, asset_id: int) -> Record | None:
        row = self.conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        return Record(**dict(row)) if row else None

    def all(self) -> list[Record]:
        rows = self.conn.execute("SELECT * FROM assets ORDER BY rel_path").fetchall()
        return [Record(**dict(row)) for row in rows]

    def facets(self) -> dict:
        """筛选器要显示的面板：每个维度有哪些值、各多少条。"""
        def group(column: str) -> list[dict]:
            rows = self.conn.execute(
                f"SELECT {column} AS value, COUNT(*) AS n FROM assets "
                f"WHERE {column} IS NOT NULL AND {column} != '' GROUP BY {column} ORDER BY n DESC"
            ).fetchall()
            return [{"value": row["value"], "count": row["n"]} for row in rows]

        months = self.conn.execute(
            "SELECT substr(captured_at, 1, 7) AS value, COUNT(*) AS n FROM assets "
            "WHERE captured_at IS NOT NULL GROUP BY value ORDER BY value DESC"
        ).fetchall()
        return {
            "region": group("region"),
            "area": group("area"),
            "city": group("city"),
            "activity": group("activity"),
            "shot_type": group("shot_type"),
            "status": group("status"),
            "kind": group("kind"),
            "month": [{"value": row["value"], "count": row["n"]} for row in months],
        }

    def stats(self) -> dict:
        total = self.conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS bytes FROM assets"
        ).fetchone()
        by_shot = {
            row["shot_type"] or "未定": row["n"]
            for row in self.conn.execute(
                "SELECT shot_type, COUNT(*) AS n FROM assets GROUP BY shot_type"
            )
        }
        by_status = {
            row["status"] or "未定": row["n"]
            for row in self.conn.execute("SELECT status, COUNT(*) AS n FROM assets GROUP BY status")
        }
        by_branch = {
            row["branch"] or "未定": row["n"]
            for row in self.conn.execute("SELECT branch, COUNT(*) AS n FROM assets GROUP BY branch")
        }
        return {
            "assets": total["n"],
            "bytes": total["bytes"],
            "shot_type": by_shot,
            "status": by_status,
            "branch": by_branch,
        }

    def delete_missing(self, existing: set[str]) -> int:
        rows = self.conn.execute("SELECT rel_path FROM assets").fetchall()
        gone = [row["rel_path"] for row in rows if row["rel_path"] not in existing]
        for rel_path in gone:
            self.conn.execute("DELETE FROM assets WHERE rel_path = ?", (rel_path,))
        self.conn.commit()
        return len(gone)

    def set_target(self, rel_path: str, target_rel: str) -> None:
        self.conn.execute(
            "UPDATE assets SET target_rel = ?, updated_at = ? WHERE rel_path = ?",
            (target_rel, _now(), rel_path),
        )

    def set_status(self, rel_path: str, status: str) -> None:
        self.conn.execute(
            "UPDATE assets SET status = ?, updated_at = ? WHERE rel_path = ?",
            (status, _now(), rel_path),
        )
        self.conn.commit()

    # ── 选集 ────────────────────────────────────────────────────────────────
    def create_collection(self, name: str, note: str = "") -> int:
        stamp = _now()
        cursor = self.conn.execute(
            "INSERT INTO collections (name, note, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (name, note, stamp, stamp),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def find_collection(self, name: str) -> Collection | None:
        row = self.conn.execute(
            "SELECT c.*, (SELECT COUNT(*) FROM collection_items i WHERE i.collection_id = c.id) AS n "
            "FROM collections c WHERE name = ?", (name,)
        ).fetchone()
        if not row:
            return None
        return Collection(
            id=row["id"], name=row["name"], note=row["note"] or "",
            created_at=row["created_at"], updated_at=row["updated_at"],
            count=row["n"], total_size=0,
        )

    def collections(self) -> list[Collection]:
        rows = self.conn.execute(
            "SELECT c.*, "
            "  (SELECT COUNT(*) FROM collection_items i WHERE i.collection_id = c.id) AS n, "
            "  (SELECT COALESCE(SUM(a.size), 0) FROM collection_items i "
            "     JOIN assets a ON a.id = i.asset_id WHERE i.collection_id = c.id) AS bytes "
            "FROM collections c ORDER BY c.updated_at DESC"
        ).fetchall()
        return [
            Collection(
                id=row["id"], name=row["name"], note=row["note"] or "",
                created_at=row["created_at"], updated_at=row["updated_at"],
                count=row["n"], total_size=row["bytes"],
            )
            for row in rows
        ]

    def add_items(self, collection_id: int, asset_ids: list[int]) -> int:
        stamp = _now()
        position = self.conn.execute(
            "SELECT COALESCE(MAX(position), 0) AS p FROM collection_items WHERE collection_id = ?",
            (collection_id,),
        ).fetchone()["p"]
        added = 0
        for asset_id in asset_ids:
            position += 1
            cursor = self.conn.execute(
                "INSERT OR IGNORE INTO collection_items (collection_id, asset_id, position, added_at) "
                "VALUES (?, ?, ?, ?)",
                (collection_id, asset_id, position, stamp),
            )
            added += cursor.rowcount
        self.conn.execute(
            "UPDATE collections SET updated_at = ? WHERE id = ?", (stamp, collection_id)
        )
        self.conn.commit()
        return added

    def remove_items(self, collection_id: int, asset_ids: list[int]) -> int:
        removed = 0
        for asset_id in asset_ids:
            cursor = self.conn.execute(
                "DELETE FROM collection_items WHERE collection_id = ? AND asset_id = ?",
                (collection_id, asset_id),
            )
            removed += cursor.rowcount
        self.conn.commit()
        return removed

    def collection_items(self, collection_id: int) -> list[Record]:
        rows = self.conn.execute(
            "SELECT a.* FROM assets a JOIN collection_items i ON i.asset_id = a.id "
            "WHERE i.collection_id = ? ORDER BY i.position", (collection_id,)
        ).fetchall()
        return [Record(**dict(row)) for row in rows]

    def delete_collection(self, collection_id: int) -> None:
        self.conn.execute("DELETE FROM collection_items WHERE collection_id = ?", (collection_id,))
        self.conn.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
        self.conn.commit()

    # ── 操作日志 ────────────────────────────────────────────────────────────
    def log(self, op: str, src_rel: str = "", dst_rel: str = "", detail: str = "") -> None:
        self.conn.execute(
            "INSERT INTO journal (ts, op, src_rel, dst_rel, detail) VALUES (?, ?, ?, ?, ?)",
            (_now(), op, src_rel, dst_rel, detail),
        )

    def journal(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM journal ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]

    # ── 键值 ────────────────────────────────────────────────────────────────
    def set_kv(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    def get_kv(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def evidence_json(evidence: list) -> str:
    return json.dumps([item.as_dict() for item in evidence], ensure_ascii=False)
