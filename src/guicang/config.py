"""配置：一个素材库根目录下三份 TOML 就是全部约定。

``.guicang/`` 里放配置和索引，``_归藏/`` 里放分类树。两边都在扫描时排除，
否则硬链接出来的副本会在下一次扫描里被当成新素材，越滚越多。

配置永远不强制存在：没有 ``.guicang/`` 也能扫，只是没有地点清单和活动清单，
分类全靠文件夹名与元数据——先能跑起来，再慢慢补清单。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

CONFIG_DIR = ".guicang"
CONFIG_FILE = "guicang.toml"
PLACES_FILE = "places.toml"
ACTIVITIES_FILE = "activities.toml"
INDEX_FILE = "index.db"
DEFAULT_TREE = "_归藏"

TIME_DEPTHS = ("year", "month", "day")


@dataclass(frozen=True)
class Place:
    """一个可反复去的地点。坐标是圆心，``radius_km`` 是吸附半径。"""

    name: str
    aliases: tuple[str, ...] = ()
    lat: float | None = None
    lon: float | None = None
    radius_km: float = 3.0

    def names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)

    def mentions(self, text: str) -> bool:
        """路径或文件名里提到它了吗。"""
        if not text:
            return False
        lowered = text.lower()
        return any(alias and alias.lower() in lowered for alias in self.names())


@dataclass(frozen=True)
class Activity:
    """会重复举办的活动。``edition`` 不写死在配置里，从路径里的「第N届」认。"""

    name: str
    aliases: tuple[str, ...] = ()
    editions: int | None = None

    def names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)

    def mentions(self, text: str) -> bool:
        if not text:
            return False
        lowered = text.lower()
        return any(alias and alias.lower() in lowered for alias in self.names())


@dataclass
class SyncConfig:
    """云存储。``local`` 是「同步到一个目录」，``s3`` 是 S3 兼容端点。"""

    backend: str = "local"
    local_dir: str = ""
    endpoint: str = ""
    region: str = "us-east-1"
    bucket: str = ""
    access_key: str = ""
    secret_key: str = ""
    prefix: str = "guicang/"

    @property
    def ready(self) -> bool:
        if self.backend == "local":
            return bool(self.local_dir)
        return all((self.endpoint, self.bucket, self.access_key, self.secret_key))


@dataclass
class Config:
    root: Path
    tree: str = DEFAULT_TREE
    time_depth: str = "month"
    region_enabled: bool = True
    activity_enabled: bool = True
    places: list[Place] = field(default_factory=list)
    activities: list[Activity] = field(default_factory=list)
    web_host: str = "127.0.0.1"
    web_port: int = 8787
    web_token: str = ""
    sync: SyncConfig = field(default_factory=SyncConfig)
    source: Path | None = None

    # ── 目录 ────────────────────────────────────────────────────────────────
    @property
    def config_dir(self) -> Path:
        return self.root / CONFIG_DIR

    @property
    def index_path(self) -> Path:
        return self.config_dir / INDEX_FILE

    @property
    def tree_root(self) -> Path:
        return self.root / self.tree

    @property
    def excluded_names(self) -> set[str]:
        return {CONFIG_DIR, self.tree}

    def with_root(self, root: Path) -> "Config":
        return replace(self, root=Path(root).resolve(), source=None)


def _as_str(value, default: str = "") -> str:
    return str(value) if value is not None else default


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _str_list(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    return tuple(str(item) for item in value if str(item).strip())


class ConfigError(Exception):
    """配置写错了。宁可停下来指出来，也别默默按默认值跑一夜。"""


def _read_toml(path: Path) -> dict:
    """读一份 TOML。缺文件当空，写坏了就大声报错——手改配置打错一个引号，
    不该变成「你的云端配置悄悄消失了」。"""
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path} 写得不对：{error}") from error
    except OSError as error:
        raise ConfigError(f"{path} 读不了：{error}") from error


def _load_places(path: Path) -> list[Place]:
    data = _read_toml(path)
    places = []
    for item in data.get("place", []) or []:
        name = _as_str(item.get("name")).strip()
        if not name:
            continue
        places.append(
            Place(
                name=name,
                aliases=_str_list(item.get("aliases")),
                lat=_as_float(item.get("lat")),
                lon=_as_float(item.get("lon")),
                radius_km=_as_float(item.get("radius_km")) or 3.0,
            )
        )
    return places


def _load_activities(path: Path) -> list[Activity]:
    data = _read_toml(path)
    items = []
    for item in data.get("activity", []) or []:
        name = _as_str(item.get("name")).strip()
        if not name:
            continue
        editions = item.get("editions")
        items.append(
            Activity(
                name=name,
                aliases=_str_list(item.get("aliases")),
                editions=int(editions) if isinstance(editions, int) else None,
            )
        )
    return items


def load_config(root: Path | str, *, overrides: dict | None = None) -> Config:
    """读 ``<root>/.guicang/`` 下的三份清单；缺了就返回默认配置。"""
    root_path = Path(root).expanduser().resolve()
    directory = root_path / CONFIG_DIR
    settings: dict = {}
    source = None
    for candidate in (directory / CONFIG_FILE, root_path / CONFIG_FILE):
        if candidate.is_file():
            settings = _read_toml(candidate)
            source = candidate
            break

    library = settings.get("library", {}) or {}
    time_block = settings.get("time", {}) or {}
    region_block = settings.get("region", {}) or {}
    activity_block = settings.get("activity", {}) or {}
    web_block = settings.get("web", {}) or {}
    sync_block = settings.get("sync", {}) or {}

    depth = _as_str(time_block.get("depth"), "month").strip().lower()
    if depth not in TIME_DEPTHS:
        depth = "month"

    sync_local = sync_block.get("local", {}) or {}
    sync_s3 = sync_block.get("s3", {}) or {}
    sync = SyncConfig(
        backend=_as_str(sync_block.get("backend"), "local").strip().lower() or "local",
        local_dir=_as_str(sync_local.get("dir")),
        endpoint=_as_str(sync_s3.get("endpoint")),
        region=_as_str(sync_s3.get("region"), "us-east-1"),
        bucket=_as_str(sync_s3.get("bucket")),
        access_key=_as_str(sync_s3.get("access_key")),
        secret_key=_as_str(sync_s3.get("secret_key")),
        prefix=_as_str(sync_s3.get("prefix"), "guicang/"),
    )

    config = Config(
        root=root_path,
        tree=_as_str(library.get("tree"), DEFAULT_TREE).strip() or DEFAULT_TREE,
        time_depth=depth,
        region_enabled=bool(region_block.get("enabled", True)),
        activity_enabled=bool(activity_block.get("enabled", True)),
        places=_load_places(directory / PLACES_FILE),
        activities=_load_activities(directory / ACTIVITIES_FILE),
        web_host=_as_str(web_block.get("host"), "127.0.0.1"),
        web_port=int(web_block.get("port") or 8787),
        web_token=_as_str(web_block.get("token")),
        sync=sync,
        source=source,
    )
    for key, value in (overrides or {}).items():
        if hasattr(config, key) and value is not None:
            setattr(config, key, value)
    return config


CONFIG_TEMPLATE = """\
# 归藏配置。放在素材根目录的 .guicang/ 下，整个库跟着这份文件走。
#
# 路径约定：
#   .guicang/  配置与索引（不参与扫描）
#   _归藏/     分类树（不参与扫描）
[library]
tree = "_归藏"

[time]
# 时间粒度：year | month | day
depth = "month"

[region]
enabled = true

[activity]
enabled = true

[web]
# guicang serve 监听地址。想用手机看就把 host 改成 0.0.0.0 并设 token。
host = "127.0.0.1"
port = 8787
token = ""

[sync]
# local = 同步到一个目录（网盘/移动硬盘）；s3 = 任意 S3 兼容端点
backend = "local"

[sync.local]
dir = ""

[sync.s3]
endpoint = ""
region = "us-east-1"
bucket = ""
access_key = ""
secret_key = ""
prefix = "guicang/"
"""

PLACES_TEMPLATE = """\
# 地点清单。坐标是圆心，radius_km 是 GPS 吸附半径。
# 只写你常去的点，越具体越好——「郑州奥体中心」比「郑州」有用得多。
# 区域主枝下就是这些名字，不再套城市层级。

[[place]]
name = "郑州奥体中心"
aliases = ["奥体", "郑州奥体"]
lat = 34.7412
lon = 113.7210
radius_km = 3.0

[[place]]
name = "开封古城"
aliases = ["开封", "清明上河园", "鼓楼"]
lat = 34.7978
lon = 114.3072
radius_km = 6.0

[[place]]
name = "洛阳老君山"
aliases = ["老君山", "栾川"]
lat = 33.7396
lon = 111.6411
radius_km = 8.0
"""

ACTIVITIES_TEMPLATE = """\
# 活动清单。会重复举办的写在这里，届次从目录名里的「第N届」自动认，
# 认不出就按年份当届次。

[[activity]]
name = "郑州马拉松"
aliases = ["郑马", "Zhengzhou Marathon"]

[[activity]]
name = "开封菊花文化节"
aliases = ["菊花节", "菊展", "菊花文化节"]

[[activity]]
name = "洛阳牡丹文化节"
aliases = ["牡丹节", "牡丹花会"]
"""


def write_templates(root: Path | str, *, force: bool = False) -> list[Path]:
    """写一份可改的配置骨架，返回真正写下去的文件。已存在的不覆盖。"""
    directory = Path(root).expanduser().resolve() / CONFIG_DIR
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, body in (
        (CONFIG_FILE, CONFIG_TEMPLATE),
        (PLACES_FILE, PLACES_TEMPLATE),
        (ACTIVITIES_FILE, ACTIVITIES_TEMPLATE),
    ):
        target = directory / name
        if target.is_file() and not force:
            continue
        target.write_text(body, encoding="utf-8")
        written.append(target)
    return written
