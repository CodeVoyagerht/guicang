"""落树：把索引里的判定变成磁盘上的目录。

原件不动是硬原则，所以默认用**硬链接**——同一个卷上零额外占用，删掉分类树
也不会伤到原件。跨卷（分类树在另一个盘）才退回复制，并把这件事写进日志，
不让人以为复制出来的那份还能省空间。

冲突不覆盖：目标已存在时依次试 ``-1`` ``-2``。覆盖是唯一可能丢数据的操作，
所以这里一次也不做。
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import Config
from .paths import same_volume
from .store import Record, Store

MODES = ("hardlink", "copy", "move")


@dataclass(frozen=True)
class PlannedItem:
    asset_id: int
    rel_path: str
    source: Path
    target: Path
    action: str          # link | copy | move | skip
    note: str = ""


@dataclass
class Plan:
    root: Path
    mode: str
    items: list[PlannedItem] = field(default_factory=list)
    conflicts: int = 0
    skipped: int = 0

    @property
    def total_bytes(self) -> int:
        return sum(item.source.stat().st_size for item in self.items
                   if item.action in ("copy", "move"))

    def counts(self) -> dict:
        result: dict[str, int] = {}
        for item in self.items:
            result[item.action] = result.get(item.action, 0) + 1
        return result

    def summary(self) -> str:
        counts = self.counts()
        parts = [f"{key} {value}" for key, value in sorted(counts.items())]
        detail = "，".join(parts) if parts else "无事可做"
        extra = f"；重名改序 {self.conflicts} 个" if self.conflicts else ""
        return f"计划 {len(self.items)} 项：{detail}{extra}"


def _dedupe(target: Path) -> tuple[Path, bool]:
    """目标被占了就找下一个空位。返回（可用路径, 是否改过名）。"""
    if not target.exists():
        return target, False
    stem, suffix = target.stem, target.suffix
    for index in range(1, 1000):
        candidate = target.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate, True
    raise RuntimeError(f"目标重名太多，无法落位：{target}")


def build_plan(config: Config, records: list[Record], mode: str = "hardlink",
               *, only_review: bool = False) -> Plan:
    """只算，不动磁盘。CLI 的 ``plan`` 就是它。"""
    if mode not in MODES:
        raise ValueError(f"未知模式 {mode}，可选 {MODES}")
    tree_root = config.tree_root
    plan = Plan(root=config.root, mode=mode)
    for record in records:
        if only_review and record.status != "needs_review":
            continue
        if not record.target_rel:
            plan.skipped += 1
            continue
        source = Path(record.abs_path)
        if not source.is_file():
            plan.skipped += 1
            continue
        target = tree_root.joinpath(*record.target_rel.split("/"))
        action = mode
        note = ""
        if mode == "hardlink" and not same_volume(source, tree_root):
            action = "copy"
            note = "分类树与原件不在同一个卷，硬链接不可用，改为复制"
        chosen, renamed = _dedupe(target)
        if renamed:
            plan.conflicts += 1
            note = (note + "；" if note else "") + f"同名已存在，改名为 {chosen.name}"
        plan.items.append(
            PlannedItem(
                asset_id=record.id, rel_path=record.rel_path, source=source,
                target=chosen, action=action, note=note,
            )
        )
    return plan


@dataclass
class ApplyReport:
    run_id: str = ""
    linked: int = 0
    copied: int = 0
    moved: int = 0
    skipped: int = 0
    bytes_copied: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"落位完成：硬链接 {self.linked}，复制 {self.copied}，移动 {self.moved}，"
            f"跳过 {self.skipped}，新占用 {self.bytes_copied / 1024 / 1024:.1f}MB，"
            f"失败 {len(self.errors)}"
        )


def _link(source: Path, target: Path) -> None:
    os.link(source, target)


def apply_plan(plan: Plan, store: Store, *, dry_run: bool = False) -> ApplyReport:
    """照着计划落盘，每条操作写进操作日志——出问题可以照着日志撤。"""
    stamp = datetime.now()
    report = ApplyReport(run_id=stamp.strftime("%Y%m%d-%H%M%S"))
    for item in plan.items:
        target = item.target
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                report.skipped += 1
                continue
            if dry_run:
                store.log("dry-run", item.rel_path, str(target), item.action)
                continue
            if item.action == "hardlink":
                _link(item.source, target)
                report.linked += 1
            elif item.action == "copy":
                shutil.copy2(item.source, target)
                report.copied += 1
                report.bytes_copied += target.stat().st_size
            elif item.action == "move":
                shutil.move(str(item.source), str(target))
                report.moved += 1
            else:
                report.skipped += 1
                continue
            store.log(item.action, item.rel_path, str(target), f"run:{report.run_id}")
        except OSError as error:
            report.errors.append(f"{item.rel_path} → {target}: {error}")
    if not dry_run:
        store.log("apply", "", "", json.dumps(report.__dict__, ensure_ascii=False))
    return report


def planned_targets(plan: Plan) -> list[str]:
    return [str(item.target) for item in plan.items]


BACKUP_MODES = ("hardlink", "copy")


@dataclass
class BackupReport:
    run_id: str = ""
    root: str = ""
    linked: int = 0
    copied: int = 0
    skipped: int = 0
    bytes_copied: int = 0
    empty_dirs: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def done(self) -> int:
        return self.linked + self.copied

    def summary(self) -> str:
        if not self.done and not self.errors:
            return f"备份盘已经是最新的：跳过 {self.skipped} 个，什么都没干"
        return (
            f"备份完成：硬链接 {self.linked}，复制 {self.copied}，"
            f"跳过 {self.skipped}，新占用 {self.bytes_copied / 1024 / 1024:.1f}MB，"
            f"失败 {len(self.errors)}"
        )


def _mirror_one(source: Path, target: Path, mode: str) -> tuple[str, int]:
    """把树里的一个文件放到备份盘。同盘硬链接零占用，跨盘自动退回复制。"""
    if mode == "hardlink":
        try:
            os.link(source, target)
            return "linked", 0
        except OSError:
            pass                      # 跨盘（EXDEV）或者不支持硬链接，退到复制
    shutil.copy2(source, target)
    return "copied", target.stat().st_size


def mirror_tree(config: Config, store: Store, target_root: Path, *, mode: str = "hardlink",
                dry_run: bool = False, progress=None) -> BackupReport:
    """把 ``_归藏`` 这棵树原样镜像到备份盘。

    备份盘上得到的是**同一套分类结构**，不是另一套算法：主库怎么分的，备份盘就怎么分。
    按大小+mtime 判重，跑一半断了再跑会从断点继续，不会重抄一遍几百 GB。
    """
    source_root = config.tree_root
    target_root = Path(target_root).expanduser()
    report = BackupReport(run_id=datetime.now().strftime("%Y%m%d-%H%M%S"), root=str(target_root))
    if not source_root.is_dir():
        report.errors.append(f"还没有分类树：{source_root}（先跑 plan 看计划，再 apply 落位）")
        return report
    if target_root == source_root or source_root in target_root.parents:
        report.errors.append("备份目录不能放在分类树里面——那样会自己备份自己")
        return report
    if mode not in BACKUP_MODES:
        report.errors.append(f"不认识的模式：{mode}（可选 {' / '.join(BACKUP_MODES)}）")
        return report

    for source in sorted(source_root.rglob("*")):
        if not source.is_file():
            continue
        rel = source.relative_to(source_root)
        target = target_root / rel
        try:
            if target.exists():
                if target.stat().st_size == source.stat().st_size:
                    report.skipped += 1
                    continue
                target.unlink()       # 大小不一致：当成半个文件重来
            if dry_run:
                report.skipped += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            action, size = _mirror_one(source, target, mode)
            if action == "linked":
                report.linked += 1
            else:
                report.copied += 1
                report.bytes_copied += size
            store.log(f"backup:{action}", rel.as_posix(), str(target), f"run:{report.run_id}")
        except OSError as error:
            report.errors.append(f"{rel.as_posix()} → {target}: {error}")
        if progress:
            progress(report.done + report.skipped, rel.as_posix())

    # 分类树里的空目录（比如只剩待定桶）也一并带过去，免得备份盘看着像少了分支
    for source in sorted((p for p in source_root.rglob("*") if p.is_dir()), reverse=True):
        rel = source.relative_to(source_root)
        target = target_root / rel
        if target.is_dir() and not any(target.iterdir()):
            continue
        if dry_run:
            continue
        target.mkdir(parents=True, exist_ok=True)
        report.empty_dirs += 1
    return report


def _prune_empty_dirs(targets: list[Path], tree_root: Path | None) -> int:
    """撤完之后把空掉的年月目录一起收走，不留一地空文件夹。

    只往上删到 ``tree_root`` 为止，绝不碰 ``tree_root`` 本身和它外面。
    先收集所有祖先目录、深的先删、删不动就下一轮再试——同一层里往往有两个
    兄弟目录，只按删除顺序走一遍会让先试的那个永远删不掉。
    """
    if tree_root is None:
        return 0
    try:
        root = Path(tree_root).resolve()
    except OSError:
        return 0
    candidates: dict[Path, int] = {}
    for target in targets:
        parent = Path(target).parent
        depth = 0
        while True:
            try:
                if parent.resolve() == root:
                    break
                parent.relative_to(root)
            except (OSError, ValueError):
                break
            candidates.setdefault(parent, depth)
            depth += 1
            parent = parent.parent
    removed = 0
    for _ in range(8):  # 目录最深不过七八层，够用
        stuck = False
        for directory in sorted(candidates, key=lambda p: len(p.parts), reverse=True):
            try:
                directory.rmdir()
                removed += 1
            except FileNotFoundError:
                pass
            except OSError:
                stuck = True
        if not stuck:
            break
    return removed


def undo_run(
    store: Store, run_id: str = "", *, dry_run: bool = False, tree_root: Path | None = None
) -> tuple[int, int, int, list[str]]:
    """撤掉一次落位：只删本次创建的文件（硬链接与副本），原件绝不动。"""
    rows = store.conn.execute(
        "SELECT * FROM journal WHERE op IN ('hardlink', 'copy', 'link', 'move') "
        "AND detail LIKE ? ORDER BY id DESC",
        (f"run:{run_id}%" if run_id else "run:%",),
    ).fetchall()
    if not rows:
        return 0, 0, 0, []
    if not run_id:
        run_id = str(rows[0]["detail"]).split("run:", 1)[-1]
        rows = [row for row in rows if str(row["detail"]).endswith(run_id)]

    removed = 0
    ignored = 0
    errors: list[str] = []
    deleted: list[Path] = []
    for row in rows:
        target = Path(row["dst_rel"] or "")
        if row["op"] == "move":
            errors.append(f"{target} 是移动来的，撤销会把文件搬回去，跳过")
            continue
        try:
            if target.is_file():
                if dry_run:
                    removed += 1
                    continue
                target.unlink()
                deleted.append(target)
                removed += 1
            else:
                ignored += 1
        except OSError as error:
            errors.append(f"{target}: {error}")
    dirs_removed = 0
    if not dry_run:
        dirs_removed = _prune_empty_dirs(deleted, tree_root)
        ids = [row["id"] for row in rows]
        store.conn.executemany("DELETE FROM journal WHERE id = ?", [(i,) for i in ids])
        store.log("undo", "", "", f"run:{run_id}")
        store.conn.commit()
    return removed, dirs_removed, ignored, errors
