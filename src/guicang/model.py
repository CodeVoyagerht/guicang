"""数据形状：探针读到的原始事实、判定过程留下的证据、一次分类的结论。

原则：**事实与判断分开存**。``Probe`` 只放机器读到的东西（EXIF 说了什么、
文件多大），``Decision`` 才是判断（航拍还是地拍、多确定、凭什么）。这样
重新分类时不需要重扫磁盘，也不会把上一次的猜测当成事实。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class Evidence:
    """一条依据。``detail`` 要能被人看懂，不能只写规则编号。"""

    rule: str
    detail: str
    weight: float = 0.0

    def as_dict(self) -> dict:
        return {"rule": self.rule, "detail": self.detail, "weight": self.weight}


def camera_name(make: str | None, model: str | None) -> str:
    """机型写成一个能读的名字。Canon 会往 Model 里也写一遍 Canon，别拼两回。"""
    make = (make or "").strip()
    model = (model or "").strip()
    if make and model:
        if model.lower().startswith(make.lower()):
            return model
        return f"{make} {model}"
    return make or model


@dataclass
class Probe:
    """一次扫描读到的原始事实。没有的字段就留空，不要猜。"""

    path: Path
    kind: str                      # photo | video | other
    size: int = 0
    mtime: float = 0.0
    captured_at: datetime | None = None
    captured_source: str = ""      # exif:DateTimeOriginal | quicktime | filename | mtime
    captured_conf: float = 0.0
    lat: float | None = None
    lon: float | None = None
    alt: float | None = None        # 高度
    alt_kind: str = ""             # relative（相对起飞点，DJI 遥测）| absolute（海拔）
    gps_source: str = ""           # exif | srt | quicktime
    make: str = ""
    model: str = ""
    width: int | None = None
    height: int | None = None
    duration: float | None = None  # 秒，仅视频
    sidecar_of: str | None = None  # 旁车文件指向的主体
    notes: list[str] = field(default_factory=list)

    @property
    def camera(self) -> str:
        return camera_name(self.make, self.model)


@dataclass
class Decision:
    """分类结论。每个维度都带置信度与证据，低置信就是待定，不是「大概吧」。"""

    shot_type: str = "其他"
    shot_conf: float = 0.0
    shot_evidence: list[Evidence] = field(default_factory=list)

    region: str | None = None       # 地点名，如「泡泡塘森林溪谷」
    region_conf: float = 0.0
    region_evidence: list[Evidence] = field(default_factory=list)
    province: str = ""              # 地点所属省，来自 places.toml
    city: str = ""                  # 地点所属地级市，来自 places.toml
    area: str = ""                  # 园区里的区域，如「水世界」

    activity: str | None = None     # 活动类型名
    edition: str | None = None      # 届次或年份
    activity_conf: float = 0.0
    activity_evidence: list[Evidence] = field(default_factory=list)

    branch: str = "region"          # region | activity
    status: str = "auto"            # auto | needs_review | ignored
    notes: list[str] = field(default_factory=list)  # 落到待定时用大白话说清为什么

    def chain(self) -> list[str]:
        """地点这一串层级：省 / 市 / 地点 / 区域，空的自动跳过。"""
        return [part for part in (self.province, self.city, self.region, self.area) if part]

    def summary(self) -> str:
        if self.branch == "activity":
            return f"活动/{self.activity}/{self.edition or '-'}/{self.shot_type}"
        return f"区域/{'/'.join(self.chain()) or '-'}/{self.shot_type}"


AUTO_THRESHOLD = 0.75
"""到了这条线才敢自动落位；以下一律进待定队列。"""


def needs_review(*confidences: float) -> bool:
    return any(c < AUTO_THRESHOLD for c in confidences)
