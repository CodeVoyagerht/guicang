"""扫描：走一遍目录，把新东西读进来，把老东西跳过。

增量的判据是**大小 + 修改时间**，不是路径——路径会变（重命名、挪目录），
内容不会。旁车文件（DJI 的 .SRT）单独一轮挂靠：它有遥测，可以先补上照片里
没有的坐标和相对高度，再一起判定。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import manual, probe
from .classify import decide
from .config import Config
from .paths import kind_of, rel_posix
from .store import Record, Store, evidence_json
from .taxonomy import target_rel

# 这些目录里有几千个缓存文件，扫它们毫无意义
SKIP_DIR_NAMES = {"$RECYCLE.BIN", "System Volume Information", ".git", ".svn", "node_modules"}
SKIP_SUFFIXES = {".tmp", ".part", ".crdownload", ".db", ".db-wal", ".db-shm"}


@dataclass
class ScanReport:
    seen: int = 0
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    sidecars: int = 0
    skipped: int = 0
    needs_review: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"扫到 {self.seen} 个文件：新增 {self.added}，更新 {self.updated}，"
            f"未变 {self.unchanged}，目录里已不存在 {self.removed}；"
            f"挂靠旁车 {self.sidecars} 个，待定 {self.needs_review} 条，失败 {len(self.errors)} 条"
        )


def walk_media(root: Path, config: Config) -> tuple[list[Path], list[Path]]:
    """返回（主体文件, 旁车文件）。排除配置目录与分类树本身。"""
    media: list[Path] = []
    sidecars: list[Path] = []
    excluded = config.excluded_names
    for path in root.rglob("*"):
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        parts = path.relative_to(root).parts
        if any(part in excluded or part in SKIP_DIR_NAMES for part in parts[:-1]):
            continue
        name = path.name
        if name.startswith("~$") or name.startswith("._"):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        kind = kind_of(name)
        if kind == "sidecar":
            sidecars.append(path)
        elif kind in ("photo", "video"):
            media.append(path)
    media.sort()
    sidecars.sort()
    return media, sidecars


def group_sidecars(media: list[Path], sidecars: list[Path]) -> dict[str, list[Path]]:
    """按「同目录同名不同后缀」挂靠。DJI 的 ``DJI_0001.MP4`` 与 ``DJI_0001.SRT`` 就是这样配对的。"""
    index: dict[str, list[Path]] = {}
    for item in media:
        index.setdefault(f"{item.parent}|{item.stem}".lower(), []).append(item)
    attached: dict[str, list[Path]] = {}
    for sidecar in sidecars:
        key = f"{sidecar.parent}|{sidecar.stem}".lower()
        if key in index:
            attached.setdefault(key, []).append(sidecar)
    return attached


def _apply_sidecar_facts(probe_obj, sidecar_facts: dict) -> None:
    """遥测比 EXIF 更可信的地方才覆盖：坐标缺失时补，相对高度总是补。"""
    if not sidecar_facts:
        return
    if probe_obj.lat is None and isinstance(sidecar_facts.get("lat"), float):
        probe_obj.lat = sidecar_facts["lat"]
        probe_obj.lon = sidecar_facts.get("lon")
        probe_obj.gps_source = "srt"
        probe_obj.notes.append("坐标来自 DJI 遥测")
    if isinstance(sidecar_facts.get("alt"), float) and sidecar_facts.get("alt_kind") == "relative":
        probe_obj.alt = sidecar_facts["alt"]
    if probe_obj.captured_at is None and sidecar_facts.get("datetime"):
        probe_obj.captured_at = sidecar_facts["datetime"]
        probe_obj.captured_source = "srt"
        probe_obj.captured_conf = 0.9


def _from_record(record: Record) -> probe.Probe:
    """用索引里已有的读数重建 Probe，省掉一次读文件。

    索引里存的就是当初读出来的原始事实（时间/坐标/机型/尺寸），重建它就能
    重新走一遍判定链，不用再解析 EXIF。
    """
    when = None
    if record.captured_at:
        try:
            when = datetime.fromisoformat(record.captured_at)
        except ValueError:
            when = None
    return probe.Probe(
        path=Path(record.abs_path),
        kind=record.kind,
        size=record.size,
        mtime=record.mtime,
        captured_at=when,
        captured_source=record.captured_source or "",
        captured_conf=record.captured_conf or 0.0,
        lat=record.lat,
        lon=record.lon,
        alt=record.alt,
        gps_source=record.gps_source or "",
        make=record.make or "",
        model=record.model or "",
        width=record.width,
        height=record.height,
        duration=record.duration,
    )


def scan(root: Path | str, config: Config, store: Store, *, force: bool = False,
         progress=None) -> ScanReport:
    """扫一遍并写索引。

    ``force`` 让未变的文件也重读元数据。未变的文件不重读，但仍然会**重新判一遍**：
    人工钉住、地点清单、活动别名改了以后，没必要让用户再去记 ``--force``。
    """
    root = Path(root).expanduser().resolve()
    report = ScanReport()
    media, sidecars = walk_media(root, config)
    attached = group_sidecars(media, sidecars)
    manual_map = manual.load_manual(root)
    report.seen = len(media)
    existing: set[str] = set()

    for path in media:
        key = f"{path.parent}|{path.stem}".lower()
        try:
            rel = rel_posix(root, path)
            existing.add(rel)
            stat = path.stat()
        except OSError as error:
            report.errors.append(f"{path}: {error}")
            continue

        prior = store.get_asset(rel)
        reused = bool(prior) and not force and (prior.size, prior.mtime) == (stat.st_size, stat.st_mtime)

        if reused:
            item = _from_record(prior)
            report.unchanged += 1
        else:
            try:
                item = probe.probe_file(path, root=root)
            except Exception as error:  # 单个坏文件不该中断整次扫描
                report.errors.append(f"{rel}: 读取失败 {error}")
                continue

            siblings = attached.get(key) or []
            if siblings:
                facts: dict = {}
                for sidecar in siblings:
                    facts.update(probe.probe_sidecar(sidecar))
                    report.sidecars += 1
                _apply_sidecar_facts(item, facts)

        rel_parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        decision = decide(item, rel_parent, config)
        decision = manual.apply(decision, manual_map.get(rel), item.captured_at)
        target = target_rel(decision, item.captured_at, config, path.name)

        payload = {
            "rel_path": rel,
            "abs_path": str(path),
            "kind": item.kind,
            "size": item.size,
            "mtime": item.mtime,
            "captured_at": item.captured_at.isoformat(sep=" ", timespec="seconds") if item.captured_at else None,
            "captured_source": item.captured_source,
            "captured_conf": item.captured_conf,
            "lat": item.lat,
            "lon": item.lon,
            "alt": item.alt,
            "gps_source": item.gps_source,
            "make": item.make,
            "model": item.model,
            "width": item.width,
            "height": item.height,
            "duration": item.duration,
            "sidecar_of": None,
            "shot_type": decision.shot_type,
            "shot_conf": decision.shot_conf,
            "shot_evidence": evidence_json(decision.shot_evidence),
            "region": decision.region,
            "region_conf": decision.region_conf,
            "region_evidence": evidence_json(decision.region_evidence),
            "activity": decision.activity,
            "edition": decision.edition,
            "activity_conf": decision.activity_conf,
            "activity_evidence": evidence_json(decision.activity_evidence),
            "branch": decision.branch,
            "status": decision.status,
            "target_rel": target,
            "notes": "；".join([*item.notes, *decision.notes]),
        }
        try:
            store.save_asset(payload)
        except Exception as error:
            report.errors.append(f"{rel}: 写入索引失败 {error}")
            continue

        if prior is None:
            report.added += 1
        elif not reused or (prior.target_rel, prior.status) != (target, decision.status):
            report.updated += 1
        if decision.status == "needs_review":
            report.needs_review += 1
        if progress:
            progress(rel, decision)

    report.removed = store.delete_missing(existing)
    return report
