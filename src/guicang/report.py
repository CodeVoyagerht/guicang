"""报告：给终端看的体检单。

不写「共 N 条」就完事——素材库的问题永远是**分布**的问题：某个月一条航拍
都没有、某个地点突然冒出两千条、待定堆到 300 条没人管。所以报告按分布说话。
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from .config import Config
from .paths import human_size
from .store import Record, Store


def _bar(value: int, total: int, width: int = 18) -> str:
    if total <= 0:
        return ""
    filled = max(1, round(width * value / total)) if value else 0
    return "█" * filled


def _table(title: str, counts: Counter, total: int, wide: int = 28) -> list[str]:
    if not counts:
        return [f"{title}：无"]
    lines = [f"{title}："]
    for name, count in counts.most_common(12):
        lines.append(f"  {str(name)[:wide]:<{wide}} {count:>6}  {_bar(count, total)}")
    return lines


def text_report(config: Config, store: Store) -> str:
    records = store.all()
    total = len(records)
    lines = [f"归藏 · {config.root}", "=" * 52]
    if not total:
        lines.append("索引是空的。先跑 guicang scan。")
        return "\n".join(lines)

    bytes_total = sum(record.size or 0 for record in records)
    lines.append(f"素材 {total} 个，{human_size(bytes_total)}；"
                 f"索引 {config.index_path.name}，分类树 {config.tree}/")

    branch_names = {"region": "区域", "activity": "活动", "douyin": "抖音"}
    lines.extend(_table(
        "按分支",
        Counter(branch_names.get(record.branch or "", record.branch or "—") for record in records),
        total,
    ))
    lines.extend(_table("按拍摄类型", Counter(record.shot_type or "-" for record in records), total))
    lines.extend(_table("按状态", Counter(record.status or "-" for record in records), total))
    lines.extend(_table("按区域", Counter(record.region or "未识别" for record in records), total))
    # 省/市/园区区域是地点清单带来的上层，单独看一眼，方便发现清单漏了哪块
    provinces = Counter(record.province or "—" for record in records)
    if any(key != "—" for key in provinces):
        lines.extend(_table("按省份", provinces, total, wide=14))
    cities = Counter(f"{record.city or '—'}" for record in records)
    if any(key != "—" for key in cities):
        lines.extend(_table("按城市", cities, total, wide=14))
    areas = Counter(record.area or "—" for record in records)
    if any(key != "—" for key in areas):
        lines.extend(_table("按园区区域", areas, total, wide=20))
    lines.extend(_table("按活动", Counter(record.activity or "—" for record in records), total))

    years = Counter(str(record.year or "未知") for record in records)
    lines.extend(_table("按年份", years, total))
    weeks = Counter(record.week or "未知" for record in records)
    recent_weeks = sorted((w for w in weeks if w != "未知"), reverse=True)[:8]
    if recent_weeks:
        lines.append("最近 8 个周期（周六 → 周五）：")
        for week in recent_weeks:
            lines.append(f"  {week}  {weeks[week]:>6}  {_bar(weeks[week], total)}")
    months = Counter(record.month or "未知" for record in records)
    recent = sorted((m for m in months if m != "未知"), reverse=True)[:12]
    if recent:
        lines.append("最近 12 个月：")
        for month in recent:
            lines.append(f"  {month}  {months[month]:>6}  {_bar(months[month], total)}")

    cameras = Counter(record.camera or "未知机型" for record in records)
    lines.extend(_table("按机型（前 12）", cameras, total, wide=34))

    capture = Counter(record.captured_source or "未知" for record in records)
    lines.extend(_table("时间来源", capture, total, wide=16))

    review = [record for record in records if record.status == "needs_review"]
    if review:
        lines.append(f"待定 {len(review)} 条，前 8 条：")
        for record in review[:8]:
            reason = []
            if not record.region and record.branch == "region":
                reason.append("地点没认出来")
            if (record.shot_conf or 0) < 0.75:
                reason.append(f"拍摄类型只有 {record.shot_conf:.2f}")
            lines.append(f"  {record.rel_path}")
            if reason:
                lines.append(f"      → {'，'.join(reason)}")
        lines.append("  处理：guicang review --list，或在 places.toml 补地点后 guicang rescan")
    return "\n".join(lines)


def pending_rows(store: Store, limit: int = 100000) -> list[Record]:
    from .store import Filters

    return store.query(Filters(status="needs_review"), limit=limit)


def write_pending_csv(store: Store, target: Path) -> Path:
    from .export import write_csv

    return write_csv(pending_rows(store), target)
