"""探针：一个文件 → 一份 ``Probe``。只记录读到的，不解释、不判断。

读的顺序讲道理：**相机写的元数据 > 文件名里的日期 > 文件时间**。
文件时间最不可信——拷来拷去、从网盘下载、解压都会把它清零。
高度也一样：DJI 的 ``rel_alt`` 是相对起飞点的高度，8 米就能说明是航拍；
而 EXIF 里的 GPS 海拔是绝对高度，河南平原本身就有 80 米，拿它判断会全错，
所以只有来源是遥测时才采用。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from . import exif
from .model import Probe
from .paths import kind_of

_FILENAME_DATE = re.compile(
    r"(?<!\d)(20\d{2})[-_]?(0[1-9]|1[0-2])[-_]?(0[1-9]|[12]\d|3[01])"
    r"(?:[-_ T]?([01]\d|2[0-3])([0-5]\d)([0-5]\d))?(?!\d)"
)

_FFPROBE = shutil.which("ffprobe")


def _from_filename(name: str) -> datetime | None:
    """``DJI_20260501102233_0001_D.MP4``、``2026-05-01 开封.mp4`` 都认。"""
    for match in _FILENAME_DATE.finditer(name):
        year, month, day, hour, minute, second = match.groups()
        try:
            return datetime(
                int(year), int(month), int(day),
                int(hour or 0), int(minute or 0), int(second or 0),
            )
        except ValueError:
            continue
    return None


def _ffprobe_meta(path: Path) -> dict:
    """装了 ffprobe 就用它补时长与旋转；没有就跳过，绝不因此失败。"""
    if not _FFPROBE:
        return {}
    try:
        done = subprocess.run(
            [
                _FFPROBE, "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", str(path),
            ],
            capture_output=True, timeout=20, check=False,
        )
        if done.returncode != 0:
            return {}
        data = json.loads(done.stdout.decode("utf-8", "ignore") or "{}")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {}
    out: dict = {}
    streams = data.get("streams") or []
    for stream in streams:
        if stream.get("codec_type") == "video":
            width, height = stream.get("width"), stream.get("height")
            rotation = 0
            for side in stream.get("side_data_list") or []:
                if "rotation" in side:
                    rotation = int(side["rotation"])
            if rotation % 180:
                width, height = height, width
            if isinstance(width, int) and isinstance(height, int):
                out["width"], out["height"] = width, height
            break
    duration = (data.get("format") or {}).get("duration")
    try:
        out["duration"] = float(duration)
    except (TypeError, ValueError):
        pass
    created = (data.get("format") or {}).get("tags", {}).get("creation_time")
    stamp = exif.parse_datetime_text(created or "")
    if stamp:
        out["datetime"] = stamp
    return out


def probe_file(path: Path | str, *, root: Path | str | None = None) -> Probe:
    """读一个主体文件（照片或视频）。旁车文件由 scanner 负责挂靠。"""
    path = Path(path)
    kind = kind_of(path.name)
    probe = Probe(path=path, kind="video" if kind == "video" else "photo")

    try:
        stat = path.stat()
        probe.size = stat.st_size
        probe.mtime = stat.st_mtime
    except OSError as error:
        probe.notes.append(f"无法读取文件信息：{error}")

    meta: dict = {}
    if kind == "photo":
        meta = exif.read_exif(path)
    elif kind == "video":
        meta = exif.read_mp4_meta(path)
        extra = _ffprobe_meta(path)
        for key, value in extra.items():
            meta.setdefault(key, value)

    if meta.get("make"):
        probe.make = str(meta["make"]).strip()
    if meta.get("model"):
        probe.model = str(meta["model"]).strip()
    if isinstance(meta.get("width"), int):
        probe.width = meta["width"]
    if isinstance(meta.get("height"), int):
        probe.height = meta["height"]
    if isinstance(meta.get("duration"), float):
        probe.duration = round(meta["duration"], 3)

    if isinstance(meta.get("lat"), float) and isinstance(meta.get("lon"), float):
        probe.lat, probe.lon = meta["lat"], meta["lon"]
        probe.gps_source = str(meta.get("gps_source") or ("exif" if kind == "photo" else "quicktime"))
        if isinstance(meta.get("alt"), float):
            probe.alt = meta["alt"]
            if meta.get("alt_kind") == "relative" or probe.gps_source == "srt":
                probe.notes.append(f"高度为相对起飞点 {probe.alt:.1f}m")

    captured = meta.get("datetime")
    if isinstance(captured, datetime):
        probe.captured_at = captured
        probe.captured_source = "exif" if kind == "photo" else "quicktime"
        probe.captured_conf = 0.97

    if probe.captured_at is None:
        stamp = _from_filename(path.stem)
        if stamp:
            probe.captured_at = stamp
            probe.captured_source = "filename"
            probe.captured_conf = 0.72
            probe.notes.append("时间取自文件名")

    if probe.captured_at is None and probe.mtime > 0:
        probe.captured_at = datetime.fromtimestamp(probe.mtime)
        probe.captured_source = "mtime"
        probe.captured_conf = 0.35
        probe.notes.append("时间取自文件修改时间，可能已被拷贝动作改写")

    if kind not in ("photo", "video"):
        probe.notes.append("不是照片也不是视频")
    return probe


def probe_sidecar(path: Path | str) -> dict:
    """旁车文件里能读到的东西（目前只有 DJI 遥测值得读）。"""
    path = Path(path)
    if path.suffix.lower() == ".srt":
        return exif.read_srt(path)
    return {}
