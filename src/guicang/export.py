"""交出去：台账（CSV/JSON）与选集清单（纯路径文本）。

剪辑软件认的东西不一样——达芬奇吃路径列表，剪映要先有文件再说，Excel 里
要看得懂。所以同一批素材导出三种形状，内容一致，只是给人或给机器。

CSV 用 ``utf-8-sig``：Excel 打开中文不乱码的唯一办法。
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from .paths import human_size
from .store import Collection, Filters, Record, Store

CSV_COLUMNS = [
    ("序号", "index"),
    ("文件名", "name"),
    ("拍摄时间", "captured_at"),
    ("年", "year"),
    ("月", "month"),
    ("拍摄类型", "shot_type"),
    ("区域", "region"),
    ("活动", "activity"),
    ("届次", "edition"),
    ("分支", "branch"),
    ("状态", "status"),
    ("机型", "camera"),
    ("分辨率", "resolution"),
    ("时长(秒)", "duration"),
    ("大小", "size_human"),
    ("相对路径", "rel_path"),
    ("绝对路径", "abs_path"),
]


def _row(record: Record, index: int) -> dict:
    resolution = ""
    if record.width and record.height:
        resolution = f"{record.width}x{record.height}"
    return {
        "index": index,
        "name": Path(record.rel_path).name,
        "captured_at": record.captured_at or "",
        "year": record.year or "",
        "month": record.month or "",
        "shot_type": record.shot_type or "",
        "region": record.region or "",
        "activity": record.activity or "",
        "edition": record.edition or "",
        "branch": record.branch or "",
        "status": record.status or "",
        "camera": record.camera,
        "resolution": resolution,
        "duration": f"{record.duration:.1f}" if record.duration else "",
        "size_human": human_size(record.size or 0),
        "rel_path": record.rel_path,
        "abs_path": record.abs_path,
    }


def write_csv(records: list[Record], target: Path) -> Path:
    """写台账。表头用中文——这份 CSV 是给人看的，不是给程序读的。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([label for label, _ in CSV_COLUMNS])
        for index, record in enumerate(records, start=1):
            row = _row(record, index)
            writer.writerow([row.get(key, "") for _, key in CSV_COLUMNS])
    return target


def write_json(
    records: list[Record], target: Path, *, config=None, collection: Collection | None = None
) -> Path:
    payload = {
        "exported_at": datetime.now().replace(microsecond=0).isoformat(sep=" "),
        "library": str(getattr(config, "root", "") or ""),
        "collection": (
            {"id": collection.id, "name": collection.name, "note": collection.note}
            if collection else None
        ),
        "count": len(records),
        "total_bytes": sum(record.size or 0 for record in records),
        "items": [_row(record, index) for index, record in enumerate(records, start=1)],
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def write_pathlist(records: list[Record], target: Path, *, posix: bool = False) -> Path:
    """一行一个绝对路径。达芬奇、Pr、剪映都能直接拖进去或粘进去。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for record in records:
        path = record.abs_path
        lines.append(path.replace("\\", "/") if posix else path)
    target.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return target


def export_collection(
    store: Store, collection_id: int, out_dir: Path, *, formats: tuple[str, ...] = ("csv", "json", "txt")
) -> list[Path]:
    collection = next((c for c in store.collections() if c.id == collection_id), None)
    if collection is None:
        raise ValueError(f"没有 id={collection_id} 的选集")
    records = store.collection_items(collection_id)
    safe = "".join(ch if ch not in '\\/:*?"<>|' else "_" for ch in collection.name).strip() or "选集"
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    if "csv" in formats:
        written.append(write_csv(records, out_dir / f"{safe}-{stamp}.csv"))
    if "json" in formats:
        written.append(write_json(records, out_dir / f"{safe}-{stamp}.json", collection=collection))
    if "txt" in formats:
        written.append(write_pathlist(records, out_dir / f"{safe}-{stamp}.txt"))
    return written


def export_filters(store: Store, filters: Filters, target: Path) -> Path:
    """按筛选条件直接导出台账——「2025 年开封全部航拍」这种一锤子买卖。"""
    return write_csv(store.query(filters, limit=100000), target)
