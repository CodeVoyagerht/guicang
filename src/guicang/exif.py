"""三种元数据读取器，零依赖：JPEG/TIFF 的 EXIF、MP4/MOV 的盒子、DJI 的 .SRT 遥测。

不追求覆盖全部 tag，只取分类真正需要的四项：**拍摄时间、GPS、厂商与机型、尺寸**。
读不出来就返回空——「读不到」和「拍的是地拍」是两件事，不能混。

装了 Pillow 时 ``probe`` 会先走 Pillow 的快路（它认识 PNG/HEIC 插件），
Pillow 读不到或没装才落到这里。
"""

from __future__ import annotations

import os
import re
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path

_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}
_MAC_EPOCH = datetime(1904, 1, 1)  # 故意不带时区：mvhd 里写的是相机本地墙钟

_DT_RE = re.compile(r"(\d{4})[:\-](\d{2})[:\-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})")


def parse_datetime_text(text: str) -> datetime | None:
    """EXIF 的 ``2026:05:01 10:22:33`` 与 ISO 的 ``2026-05-01T10:22:33`` 都认。"""
    if not text:
        return None
    match = _DT_RE.search(str(text))
    if not match:
        return None
    parts = [int(x) for x in match.groups()]
    if parts[0] < 1970 or parts[1] == 0 or parts[2] == 0:
        return None
    try:
        return datetime(*parts)
    except ValueError:
        return None


class Tiff:
    """最小的 TIFF 目录读取器——EXIF 的本体就是它。"""

    def __init__(self, data: bytes) -> None:
        if len(data) < 8:
            raise ValueError("TIFF 头不足 8 字节")
        order = data[:2]
        if order == b"II":
            self.endian = "<"
        elif order == b"MM":
            self.endian = ">"
        else:
            raise ValueError("不是 TIFF 的字节序标记")
        self.data = data
        if self._u16(2) != 42:
            raise ValueError("TIFF magic 不是 42")
        self.ifd0 = self._u32(4)

    def _u16(self, off: int) -> int:
        return struct.unpack_from(self.endian + "H", self.data, off)[0]

    def _u32(self, off: int) -> int:
        return struct.unpack_from(self.endian + "I", self.data, off)[0]

    def entries(self, offset: int) -> dict[int, tuple[int, int, bytes]]:
        if offset <= 0 or offset + 2 > len(self.data):
            return {}
        count = self._u16(offset)
        result: dict[int, tuple[int, int, bytes]] = {}
        for index in range(count):
            base = offset + 2 + index * 12
            if base + 12 > len(self.data):
                break
            tag = self._u16(base)
            typ = self._u16(base + 2)
            num = self._u32(base + 4)
            size = _TYPE_SIZE.get(typ, 0) * num
            if size <= 0:
                continue
            if size <= 4:
                raw = self.data[base + 8: base + 8 + size]
            else:
                ptr = self._u32(base + 8)
                raw = self.data[ptr: ptr + size]
            result[tag] = (typ, num, raw)
        return result

    def value(self, entry: tuple[int, int, bytes]):
        typ, num, raw = entry
        try:
            if typ == 2:
                return raw.split(b"\x00")[0].decode("ascii", "ignore").strip()
            if typ in (1, 7):
                return raw
            if typ == 3:
                return list(struct.unpack_from(self.endian + f"{num}H", raw, 0))
            if typ == 4:
                return list(struct.unpack_from(self.endian + f"{num}I", raw, 0))
            if typ == 9:
                return list(struct.unpack_from(self.endian + f"{num}i", raw, 0))
            if typ in (5, 10):
                values = []
                fmt = self.endian + ("II" if typ == 5 else "ii")
                for pos in range(0, len(raw) - 7, 8):
                    numerator, denominator = struct.unpack_from(fmt, raw, pos)
                    values.append(numerator / denominator if denominator else 0.0)
                return values
        except struct.error:
            return None
        return raw


def _first(value, default=None):
    if isinstance(value, list) and value:
        return value[0]
    return default


def _dms(values, ref: str) -> float | None:
    if not values:
        return None
    if len(values) >= 3:
        degrees = float(values[0]) + float(values[1]) / 60 + float(values[2]) / 3600
    else:
        degrees = float(values[0])
    if str(ref).upper() in ("S", "W"):
        degrees = -degrees
    return degrees


def parse_tiff(data: bytes) -> dict:
    """返回 ``{datetime, make, model, lat, lon, alt, width, height}``，缺项不出现。"""
    try:
        tiff = Tiff(data)
    except (ValueError, struct.error):
        return {}

    ifd0 = tiff.entries(tiff.ifd0)
    exif: dict = {}
    if 0x8769 in ifd0:
        pointer = _first(tiff.value(ifd0[0x8769]))
        if pointer:
            exif = tiff.entries(int(pointer))

    out: dict = {}

    def text(entries: dict, tag: int) -> str:
        entry = entries.get(tag)
        if not entry:
            return ""
        value = tiff.value(entry)
        return value if isinstance(value, str) else ""

    make = text(ifd0, 0x010F)
    model = text(ifd0, 0x0110)
    if make:
        out["make"] = make
    if model:
        out["model"] = model

    for container, tag in ((exif, 0x9003), (exif, 0x9004), (ifd0, 0x0132)):
        stamp = parse_datetime_text(text(container, tag))
        if stamp:
            out["datetime"] = stamp
            break

    width = _first(tiff.value(exif[0xA002])) if 0xA002 in exif else _first(
        tiff.value(ifd0[0x0100]) if 0x0100 in ifd0 else None)
    height = _first(tiff.value(exif[0xA003])) if 0xA003 in exif else _first(
        tiff.value(ifd0[0x0101]) if 0x0101 in ifd0 else None)
    if isinstance(width, int) and width > 0:
        out["width"] = width
    if isinstance(height, int) and height > 0:
        out["height"] = height

    if 0x8825 in ifd0:
        pointer = _first(tiff.value(ifd0[0x8825]))
        gps = tiff.entries(int(pointer)) if pointer else {}
        if gps:
            lat_ref = tiff.value(gps[0x0001]) if 0x0001 in gps else "N"
            lon_ref = tiff.value(gps[0x0003]) if 0x0003 in gps else "E"
            lat = _dms(tiff.value(gps[0x0002]) if 0x0002 in gps else None, lat_ref)
            lon = _dms(tiff.value(gps[0x0004]) if 0x0004 in gps else None, lon_ref)
            if lat is not None and lon is not None:
                out["lat"] = lat
                out["lon"] = lon
                if 0x0006 in gps:
                    altitude = _first(tiff.value(gps[0x0006]))
                    if isinstance(altitude, float):
                        # GPSAltitudeRef 是单字节：1 表示海平面以下
                        ref = tiff.value(gps[0x0005]) if 0x0005 in gps else b"\x00"
                        out["alt"] = -altitude if ref in (b"\x01", 1, "1") else altitude
    return out


def _jpeg_exif_payload(path: Path) -> bytes | None:
    """只读 APP1 段，不把整张 60MB 的图读进内存。"""
    with open(path, "rb") as handle:
        if handle.read(2) != b"\xff\xd8":
            return None
        while True:
            head = handle.read(2)
            if len(head) < 2 or head[0] != 0xFF:
                return None
            marker = head[1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                continue
            if marker == 0xDA:  # 图像数据开始，EXIF 不会在它后面
                return None
            length_bytes = handle.read(2)
            if len(length_bytes) < 2:
                return None
            length = struct.unpack(">H", length_bytes)[0]
            if length < 2:
                return None
            payload = handle.read(length - 2)
            if marker == 0xE1 and payload[:6] == b"Exif\x00\x00":
                return payload[6:]


def read_exif(path: Path | str) -> dict:
    """按后缀挑最省事的读法；读不出来返回空字典。"""
    path = Path(path)
    suffix = path.suffix.lower()
    try:
        if suffix in {".jpg", ".jpeg"}:
            payload = _jpeg_exif_payload(path)
            return parse_tiff(payload) if payload else {}
        head = path.read_bytes()[:4 * 1024 * 1024]
        if suffix in {".tif", ".tiff", ".cr2", ".nef", ".arw", ".dng", ".rw2", ".orf", ".pef"}:
            return parse_tiff(head)
        # HEIC / 未知容器：在文件头里找 Exif 段，是个便宜的兜底
        marker = head.find(b"Exif\x00\x00")
        if marker >= 0:
            return parse_tiff(head[marker + 6:])
    except (OSError, ValueError, struct.error):
        return {}
    return {}


# ── MP4 / MOV ─────────────────────────────────────────────────────────────────

_ISO6709_RE = re.compile(r"([+-]\d{2,3}\.\d+)([+-]\d{2,3}\.\d+)([+-]\d{1,6}(?:\.\d+)?)?")


def _iter_boxes(data: bytes, start: int, end: int):
    offset = start
    while offset + 8 <= end:
        size = struct.unpack_from(">I", data, offset)[0]
        typ = data[offset + 4: offset + 8]
        header = 8
        if size == 1:
            if offset + 16 > end:
                break
            size = struct.unpack_from(">Q", data, offset + 8)[0]
            header = 16
        elif size == 0:
            size = end - offset
        if size < header:
            break
        yield typ, offset + header, offset + size
        offset += size


def _read_moov(path: Path) -> bytes:
    """moov 一般在文件头或尾，通常几十 KB —— 按盒子跳读，不吞整段视频。"""
    file_size = os.path.getsize(path)
    with open(path, "rb") as handle:
        offset = 0
        while offset + 8 <= file_size:
            handle.seek(offset)
            head = handle.read(16)
            if len(head) < 8:
                break
            size = struct.unpack_from(">I", head, 0)[0]
            typ = head[4:8]
            header = 8
            if size == 1:
                if len(head) < 16:
                    break
                size = struct.unpack_from(">Q", head, 8)[0]
                header = 16
            elif size == 0:
                size = file_size - offset
            if size < header:
                break
            if typ == b"moov":
                handle.seek(offset + header)
                return handle.read(size - header)
            offset += size
    return b""


def read_mp4_meta(path: Path | str) -> dict:
    """读 mvhd 创建时间、``©xyz``/``ISO6709`` 坐标、ISO 时间串。

    DJI 与 iPhone 都把坐标塞在 moov 的 udta/ilst 里，形状不同但都是 ISO6709，
    所以直接对 moov 做一次文本化扫描比逐个盒子解析更稳。
    """
    path = Path(path)
    try:
        moov = _read_moov(path)
    except OSError:
        return {}
    if not moov:
        return {}

    out: dict = {}
    for typ, start, _end in _iter_boxes(moov, 0, len(moov)):
        if typ != b"mvhd" or start + 8 > len(moov):
            continue
        version = moov[start]
        try:
            if version == 1:
                created = struct.unpack_from(">Q", moov, start + 4)[0]
                timescale, duration = struct.unpack_from(">IQ", moov, start + 20)
            else:
                created = struct.unpack_from(">I", moov, start + 4)[0]
                timescale, duration = struct.unpack_from(">II", moov, start + 12)
        except struct.error:
            break
        if created > 0:
            try:
                # 不换算时区：相机往 mvhd 里写的多半就是拍摄地本地时间，
                # 按 UTC 再转北京时间会白白多出 8 小时，把清晨的片子挪到下午。
                out["datetime"] = _MAC_EPOCH + timedelta(seconds=created)
            except (OverflowError, OSError, ValueError):
                pass
        if timescale:
            # mvhd 里的时长够显示个大概，省得为了这一行数字去装 ffprobe
            out["duration"] = round(duration / timescale, 2)
        break

    text = moov.decode("latin-1")
    for marker in ("location.ISO6709", "ISO6709", "\xa9xyz"):
        index = text.find(marker)
        if index < 0:
            continue
        match = _ISO6709_RE.search(text[index: index + 96])
        if match:
            try:
                out["lat"] = float(match.group(1))
                out["lon"] = float(match.group(2))
                if match.group(3):
                    out["alt"] = float(match.group(3))
            except ValueError:
                pass
            break

    stamp = re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:?\d{2}|Z)?", text)
    if stamp:
        raw = stamp.group(0).replace("Z", "+00:00")
        try:
            out["datetime"] = datetime.fromisoformat(raw)
        except ValueError:
            pass
    return out


# ── DJI 遥测 ──────────────────────────────────────────────────────────────────

_SRT_FIELDS = re.compile(
    r"\[(latitude|longitude|rel_alt|abs_alt|focal_len)\s*:\s*([+-]?[\d.]+)", re.I
)


def read_srt(path: Path | str) -> dict:
    """DJI 的 .SRT 是逐帧遥测。它比 EXIF 更诚实：``rel_alt`` 是相对起飞点的高度。"""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")[:64 * 1024]
    except OSError:
        return {}
    out: dict = {}
    fields = {key.lower(): float(value) for key, value in _SRT_FIELDS.findall(text)}
    if "latitude" in fields and "longitude" in fields:
        out["lat"] = fields["latitude"]
        out["lon"] = fields["longitude"]
        out["gps_source"] = "srt"
        if "rel_alt" in fields:
            out["alt"] = fields["rel_alt"]
            out["alt_kind"] = "relative"
        elif "abs_alt" in fields:
            out["alt"] = fields["abs_alt"]
            out["alt_kind"] = "absolute"
    stamp = re.search(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})", text)
    if stamp:
        out["datetime"] = parse_datetime_text(f"{stamp.group(1)} {stamp.group(2)}")
    if "focal_len" in fields:
        out["focal_len"] = fields["focal_len"]
    return out
