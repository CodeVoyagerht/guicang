"""分类树在哪：把一次判定变成一个相对路径。

树的形状（区域主枝只有一层地点，不再套城市）：

    _归藏/区域/郑州奥体中心/2026/2026-05/航拍/DJI_0001.MP4
    _归藏/活动/郑州马拉松/2026-第3届/2026-05/航拍/DJI_0002.MP4
    _归藏/待定/2026/2026-05/地拍/DSC_0003.JPG

时间在最后一条之前只出现一次：年，然后年-月。届次拼进年份里，
因为「第几届」本身就是一个年份级别的标识，拆开会让目录多一层。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .config import Config
from .model import Decision
from .paths import sanitize_segment

REGION_BRANCH = "区域"
ACTIVITY_BRANCH = "活动"
PENDING_BRANCH = "待定"
UNKNOWN_PLACE = "未识别地点"
UNKNOWN_ACTIVITY = "未识别活动"


def time_parts(when: datetime | None, depth: str = "month") -> list[str]:
    """按粒度给时间层级。没有时间就给「未知时间」，不猜。"""
    if when is None:
        return ["未知时间"]
    parts = [f"{when.year:04d}"]
    if depth in ("month", "day"):
        parts.append(f"{when.year:04d}-{when.month:02d}")
    if depth == "day":
        parts.append(f"{when.year:04d}-{when.month:02d}-{when.day:02d}")
    return parts


def edition_segment(when: datetime | None, edition: str | None) -> str:
    """``2026-第3届``。没有届次就只剩年份；两样都没有就是未知。"""
    year = f"{when.year:04d}" if when else ""
    if edition and edition.endswith("届"):
        if year:
            return f"{year}-{sanitize_segment(edition)}"
        return sanitize_segment(edition)
    return year or "未知时间"


def region_parts(place: str | None, when: datetime | None, shot_type: str, config: Config) -> list[str]:
    parts = [REGION_BRANCH, sanitize_segment(place or UNKNOWN_PLACE, fallback=UNKNOWN_PLACE)]
    parts.extend(sanitize_segment(segment) for segment in time_parts(when, config.time_depth))
    parts.append(sanitize_segment(shot_type, fallback="其他"))
    return parts


def activity_parts(
    activity: str | None, when: datetime | None, edition: str | None,
    shot_type: str, config: Config,
) -> list[str]:
    parts = [ACTIVITY_BRANCH, sanitize_segment(activity or UNKNOWN_ACTIVITY, fallback=UNKNOWN_ACTIVITY)]
    # 届次段本身就带着年份（2026-第3届），所以时间层只从「年」之后接，
    # 否则会出现 活动/郑州马拉松/2026/2026/2026-05 这种一年说三遍的路径。
    parts.append(sanitize_segment(edition_segment(when, edition), fallback="未知时间"))
    if config.time_depth in ("month", "day"):
        parts.extend(sanitize_segment(segment) for segment in time_parts(when, config.time_depth)[1:])
    parts.append(sanitize_segment(shot_type, fallback="其他"))
    return parts


def pending_parts(when: datetime | None, shot_type: str, config: Config) -> list[str]:
    parts = [PENDING_BRANCH]
    parts.extend(sanitize_segment(segment) for segment in time_parts(when, config.time_depth))
    parts.append(sanitize_segment(shot_type, fallback="其他"))
    return parts


def target_parts(decision: Decision, when: datetime | None, config: Config) -> list[str]:
    """相对 ``_归藏/`` 的层级列表。待定的不进区域也不进活动，单独一桶。"""
    if decision.status == "needs_review":
        return pending_parts(when, decision.shot_type, config)
    if decision.branch == "activity" and decision.activity:
        return activity_parts(
            decision.activity, when, decision.edition, decision.shot_type, config
        )
    if decision.region:
        return region_parts(decision.region, when, decision.shot_type, config)
    return pending_parts(when, decision.shot_type, config)


def target_rel(decision: Decision, when: datetime | None, config: Config,
               filename: str = "") -> str:
    """落位后的相对路径。给了 ``filename`` 就是文件路径，不给就是那层目录。"""
    parts = target_parts(decision, when, config)
    if filename:
        parts = [*parts, filename]
    return "/".join(parts)


def target_dir(decision: Decision, when: datetime | None, config: Config) -> Path:
    return config.tree_root.joinpath(*target_parts(decision, when, config))


def short_branch(decision: Decision) -> str:
    """给人看的一行：``区域/开封古城/2026-05/航拍``。"""
    if decision.status == "needs_review":
        return f"待定/{decision.shot_type}"
    if decision.branch == "activity":
        return f"活动/{decision.activity}/{decision.edition or '-'}/{decision.shot_type}"
    return f"区域/{decision.region or UNKNOWN_PLACE}/{decision.shot_type}"
