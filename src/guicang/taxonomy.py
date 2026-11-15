"""分类树在哪：把一次判定变成一条相对路径。

树的形状（省/市两层会按实际素材折叠）：

    _归藏/区域/泡泡塘森林溪谷/水世界/2026/2026-08-01_08-07/航拍/DJI_0001.MP4
    _归藏/区域/河南/郑州/泡泡塘森林溪谷/露营区/2026/2026-08-01_08-07/地拍/DSC_0002.JPG
    _归藏/活动/郑州马拉松/2026-第3届/2026-03-14_03-20/地拍/IMG_2002.JPG
    _归藏/抖音/2026/2026-08-01_08-07/地拍/DY_0031.MP4
    _归藏/待定/2026/2026-08-01_08-07/地拍/IMG_1001.JPG

四条规则：

1. **一层只放一个名字的层级不列**。库里只有一个省，就不会出现「河南」这一层；
   整个库只有一个地点时，连「省/市」都省掉，直接从地点名字开始。
2. **时间先分年，再分周期**：周期是「周六 → 周五」，就是备份周期
   （8.1 周六到 8.7 周五算一个周期）。也可以退成按月或按天。
3. **抖音类素材单独一棵枝**：`抖音/年/周期/拍摄类型`，不与区域、活动混在一起。
4. **拍摄类型之后按机型分，只在同一个周期同一类里真有两个机型时**：那个周期只用了一台
   机器，就没人想在每条路径上都多看一遍机型名。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .config import Config
from .model import Decision
from .paths import sanitize_segment

REGION_BRANCH = "区域"
ACTIVITY_BRANCH = "活动"
DOUYIN_BRANCH = "抖音"
PENDING_BRANCH = "待定"
UNKNOWN_PLACE = "未识别地点"
UNKNOWN_ACTIVITY = "未识别活动"
UNKNOWN_TIME = "未知时间"
UNKNOWN_CAMERA = "未知机型"
# 抖音枝里拍摄类型没把握时用的那一层，别回根上的待定桶
PENDING_SHOT = "待定"

# 周期从周六开始，到周五结束（备份周期就是这么算的）
WEEK_START = 5  # Monday=0 … Saturday=5


def week_bounds(when: datetime) -> tuple[datetime, datetime]:
    """这条素材落在哪个周期里：返回 (周六, 下周五)。"""
    offset = (when.weekday() - WEEK_START) % 7
    start = (when - timedelta(days=offset)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=6)


def week_label(when: datetime) -> str:
    """``2026-08-01_08-07``。跨年的周期把结束年的年份也写出来，免得看错。"""
    start, end = week_bounds(when)
    if start.year == end.year:
        return f"{start:%Y-%m-%d}_{end:%m-%d}"
    return f"{start:%Y-%m-%d}_{end:%Y-%m-%d}"


def time_parts(when: datetime | None, depth: str = "week") -> list[str]:
    """按粒度给时间层级。没有时间就给「未知时间」，不猜。"""
    if when is None:
        return [UNKNOWN_TIME]
    if depth == "week":
        # 年这一层取**周期起点**的年：跨年的那一个周期（12-28→01-03）整段留在
        # 起始年下面，否则同一个备份周期会被劈成两个年份的文件夹。
        start, _ = week_bounds(when)
        return [f"{start.year:04d}", week_label(when)]
    if depth == "year":
        return [f"{when.year:04d}"]
    if depth == "month":
        return [f"{when.year:04d}-{when.month:02d}"]
    return [f"{when.year:04d}-{when.month:02d}-{when.day:02d}"]


def edition_segment(when: datetime | None, edition: str | None) -> str:
    """``2025-第3届``。没有届次就只剩年份；两样都没有就是未知。"""
    year = f"{when.year:04d}" if when else ""
    if edition and edition.endswith("届"):
        if year:
            return f"{year}-{sanitize_segment(edition)}"
        return sanitize_segment(edition)
    return year or UNKNOWN_TIME


@dataclass(frozen=True)
class Layout:
    """这一次落位要用的树形参数。由整库的素材决定，不由单条决定。"""

    use_province: bool = False
    use_city: bool = False
    camera: bool = False
    camera_groups: frozenset[tuple] = frozenset()

    @property
    def collapsed(self) -> tuple[str, ...]:
        """省、市这两层里被折叠掉的。"""
        dropped = []
        if not self.use_province:
            dropped.append("省")
        if not self.use_city:
            dropped.append("市")
        return tuple(dropped)


def split_key(decision: Decision, when: datetime | None, config: Config) -> tuple:
    """判断「这一天这一处这一类型里是不是有多种机型」用的分组键。

    键里不含省/市，也不含折叠结果——折叠只改路径，不该改分组。
    """
    return (
        decision.branch,
        decision.region or "",
        decision.area or "",
        decision.activity or "",
        decision.edition or "",
        *time_parts(when, config.time_depth),
        decision.shot_type,
    )


def build_layout(items, config: Config) -> Layout:
    """扫一遍所有判定，决定这次落位要不要带省/市、要不要分机型。

    ``items`` 是 ``(decision, when, camera)`` 的可迭代对象。
    """
    provinces: set[str] = set()
    cities: set[tuple[str, str]] = set()
    cameras: dict[tuple, set[str]] = {}
    for decision, when, camera in items:
        if decision.branch == "region" and decision.status != "needs_review":
            if decision.province:
                provinces.add(decision.province)
            if decision.city:
                cities.add((decision.province, decision.city))
        if config.camera_layer:
            bucket = cameras.setdefault(split_key(decision, when, config), set())
            bucket.add(camera or UNKNOWN_CAMERA)

    groups = frozenset(key for key, models in cameras.items() if len(models) > 1)
    return Layout(
        use_province=len(provinces) > 1,
        use_city=len(cities) > 1,
        camera=bool(config.camera_layer),
        camera_groups=groups,
    )


def _chain(decision: Decision, layout: Layout) -> list[str]:
    """地点那一串：省 / 市 / 地点 / 园区区域，按折叠结果裁剪。"""
    parts: list[str] = []
    if layout.use_province and decision.province:
        parts.append(decision.province)
    if layout.use_city and decision.city:
        parts.append(decision.city)
    parts.append(decision.region or UNKNOWN_PLACE)
    if decision.area:
        parts.append(decision.area)
    return parts


def _camera_segment(decision: Decision, when: datetime | None, config: Config,
                    layout: Layout, camera: str) -> list[str]:
    if not layout.camera or split_key(decision, when, config) not in layout.camera_groups:
        return []
    return [camera or UNKNOWN_CAMERA]


def region_parts(decision: Decision, when: datetime | None, config: Config,
                 layout: Layout | None = None, camera: str = "") -> list[str]:
    layout = layout or Layout()
    parts = [REGION_BRANCH]
    parts.extend(_chain(decision, layout))
    parts.extend(time_parts(when, config.time_depth))
    parts.append(decision.shot_type)
    parts.extend(_camera_segment(decision, when, config, layout, camera))
    return [sanitize_segment(part, fallback=UNKNOWN_TIME) for part in parts]


def _edition_parts(decision: Decision, when: datetime | None) -> list[str]:
    """届次段。

    活动没写「第N届」时，``edition_segment`` 只能退回年份（``2025``）。可下一层的
    时间已经在年份上，再挂一遍就成了 ``活动/郑州马拉松/2025/2025/2025-03-15_03-21``。
    所以这种「届次其实就是年份」的情况不再单独出一层。
    """
    segment = edition_segment(when, decision.edition)
    if when is not None and segment == f"{when.year:04d}":
        return []
    return [segment]


def activity_parts(decision: Decision, when: datetime | None, config: Config,
                   layout: Layout | None = None, camera: str = "") -> list[str]:
    layout = layout or Layout()
    parts = [ACTIVITY_BRANCH, decision.activity or UNKNOWN_ACTIVITY]
    parts.extend(_edition_parts(decision, when))
    parts.extend(time_parts(when, config.time_depth))
    parts.append(decision.shot_type)
    parts.extend(_camera_segment(decision, when, config, layout, camera))
    return [sanitize_segment(part, fallback=UNKNOWN_TIME) for part in parts]


def douyin_parts(decision: Decision, when: datetime | None, config: Config,
                 layout: Layout | None = None, camera: str = "") -> list[str]:
    """抖音枝：``抖音/年/周期/拍摄类型``，不挂地点也不挂活动。

    这条枝只认「是不是抖音拍摄类」，拍摄类型没把握时不能把它踢回根上的待定桶
    —— 那样剪辑在抖音文件夹里就找不到它了。所以留在枝里，最后一层写「待定」。
    """
    layout = layout or Layout()
    parts = [DOUYIN_BRANCH]
    parts.extend(time_parts(when, config.time_depth))
    parts.append(decision.shot_type if decision.status == "auto" else PENDING_SHOT)
    parts.extend(_camera_segment(decision, when, config, layout, camera))
    return [sanitize_segment(part, fallback=UNKNOWN_TIME) for part in parts]


def pending_parts(decision: Decision, when: datetime | None, config: Config) -> list[str]:
    parts = [PENDING_BRANCH]
    parts.extend(time_parts(when, config.time_depth))
    parts.append(decision.shot_type)
    return [sanitize_segment(part, fallback=UNKNOWN_TIME) for part in parts]


def target_parts(decision: Decision, when: datetime | None, config: Config,
                 layout: Layout | None = None, camera: str = "") -> list[str]:
    """相对 ``_归藏/`` 的层级列表。待定的不进区域也不进活动，单独一桶。

    抖音枝排在最前面：它是一棵自成体系的树，拍摄类型没把握也不回根上的待定桶。
    """
    if decision.branch == "douyin":
        return douyin_parts(decision, when, config, layout, camera)
    if decision.status == "needs_review":
        return pending_parts(decision, when, config)
    if decision.branch == "activity" and decision.activity:
        return activity_parts(decision, when, config, layout, camera)
    if decision.region:
        return region_parts(decision, when, config, layout, camera)
    return pending_parts(decision, when, config)


def target_rel(decision: Decision, when: datetime | None, config: Config,
               filename: str = "", *, layout: Layout | None = None,
               camera: str = "") -> str:
    """落位后的相对路径。给了 ``filename`` 就是文件路径，不给就是那层目录。"""
    parts = target_parts(decision, when, config, layout, camera)
    if filename:
        parts = [*parts, filename]
    return "/".join(parts)


def target_dir(decision: Decision, when: datetime | None, config: Config,
               *, layout: Layout | None = None, camera: str = "") -> Path:
    return config.tree_root.joinpath(*target_parts(decision, when, config, layout, camera))


def short_branch(decision: Decision) -> str:
    """给人看的一行：``区域/泡泡塘森林溪谷/水世界/航拍``。"""
    if decision.status == "needs_review":
        return f"待定/{decision.shot_type}"
    if decision.branch == "douyin":
        return f"抖音/{decision.shot_type}"
    if decision.branch == "activity":
        return f"活动/{decision.activity}/{decision.edition or '-'}/{decision.shot_type}"
    chain = "/".join(decision.chain()) or UNKNOWN_PLACE
    return f"区域/{chain}/{decision.shot_type}"
