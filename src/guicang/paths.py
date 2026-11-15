"""路径工具：归一化、相对化、目录名净化、旁车文件识别。

Windows 是主战场，所以按最严的一套规则净化目录名：保留字（CON/PRN/…）、
结尾的点和空格、``<>:"/\\|?*`` 与控制字符。超长路径也要在源头截断——
不然会在某个莫名其妙的层级上失败，而那时已经拷了一半。
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

# Windows 设备名，做目录名会直接失败
RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_SPACES = re.compile(r"\s+")
_MAX_SEGMENT = 80

# 跟着素材一起搬的旁车文件：DJI 的遥测、Lightroom 的调整、iOS 的编辑记录
SIDECAR_SUFFIXES = {".srt", ".lrf", ".thm", ".xmp", ".aae", ".lrv", ".dop", ".xml"}

# 相机直出与手机拍的照片；其余按「其他」处理
IMAGE_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".heic", ".heif",
    ".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".orf", ".rw2", ".pef",
}
VIDEO_SUFFIXES = {
    ".mp4", ".mov", ".m4v", ".avi", ".mkv", ".mxf", ".insv", ".webm", ".mts", ".m2ts",
}


def nfc(text: str) -> str:
    """统一成 NFC：macOS 的 NFD 中文目录名会让同一地点被拆成两份。"""
    return unicodedata.normalize("NFC", text)


def sanitize_segment(name: str, fallback: str = "_") -> str:
    """把任意字符串变成一层可用的目录名。"""
    text = nfc(str(name)).strip()
    text = _ILLEGAL.sub("_", text)
    text = _SPACES.sub(" ", text).strip()
    text = text.rstrip(". ")
    if not text:
        return fallback
    stem = text.split(".")[0].upper()
    if stem in RESERVED_NAMES:
        text = "_" + text
    if len(text) > _MAX_SEGMENT:
        text = text[:_MAX_SEGMENT].rstrip(". ")
    return text or fallback


def is_sidecar(name: str) -> bool:
    return Path(name).suffix.lower() in SIDECAR_SUFFIXES


def kind_of(name: str) -> str:
    """photo | video | sidecar | other"""
    suffix = Path(name).suffix.lower()
    if suffix in SIDECAR_SUFFIXES:
        return "sidecar"
    if suffix in IMAGE_SUFFIXES:
        return "photo"
    if suffix in VIDEO_SUFFIXES:
        return "video"
    return "other"


def rel_posix(root: Path | str, path: Path | str) -> str:
    """相对路径，统一成 ``/`` 分隔——索引里只存这一种形状。"""
    rel = os.path.relpath(str(path), str(root))
    return Path(rel).as_posix()


def volume_of(path: Path | str) -> str:
    """盘符或 UNC 根。硬链接只在同一个卷内成立，靠它判断。"""
    return Path(os.path.abspath(str(path))).anchor


def same_volume(a: Path | str, b: Path | str) -> bool:
    return volume_of(a).lower() == volume_of(b).lower()


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024 or unit == "TB":
            return f"{num:.1f}{unit}" if unit != "B" else f"{int(num)}B"
        num /= 1024
    return f"{num:.1f}TB"
