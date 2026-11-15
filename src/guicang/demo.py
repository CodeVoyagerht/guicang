"""造一个假的素材库，用来把整条链路跑通，也用来给第一次装的人看效果。

为什么要自己写 EXIF 和 MP4：这台机器没有 ffmpeg、没有 exiftool，测试如果依赖
它们，「装不上就跑不了」的毛病会从工具传染到测试。这里只用标准库拼出真实结构
的元数据——JPEG 是真的 JPEG（1×1 的底图 + 自己拼的 APP1），MP4 是结构合法但
画面是占位字节的空壳。照片能缩略图、能被 Pillow 打开；视频能读出拍摄时间与
GPS，但播不出画面，因为在没有编码器的情况下造一条真的 H.264 是另一件事。

用法：
    python -m guicang.demo <目录>
    guicang --root <目录> scan
"""

from __future__ import annotations

import base64
import struct
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .config import CONFIG_DIR, write_templates

MP4_EPOCH = datetime(1904, 1, 1)

# 1×1 的 JPEG。底图是真的，EXIF 由下面的 _exif_app1 拼进去。
TINY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
    "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAA"
    "AAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AVN//2Q=="
)

TIFF_TYPES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8}

TAG_MAKE = 0x010F
TAG_MODEL = 0x0110
TAG_DATETIME = 0x0132
TAG_EXIF_IFD = 0x8769
TAG_GPS_IFD = 0x8825
TAG_EXIF_DATETIME = 0x9003
TAG_PIXEL_X = 0xA002
TAG_PIXEL_Y = 0xA003
TAG_GPS_VERSION = 0x0000
TAG_GPS_LAT_REF = 0x0001
TAG_GPS_LAT = 0x0002
TAG_GPS_LON_REF = 0x0003
TAG_GPS_LON = 0x0004
TAG_GPS_ALT_REF = 0x0005
TAG_GPS_ALT = 0x0006


# ── EXIF 拼装 ───────────────────────────────────────────────────────────────

def _encode_ifd(entries: list[tuple[int, int, object]], start: int) -> bytes:
    """把一个 IFD 编成字节。``start`` 是这个 IFD 在 TIFF 头之后的绝对偏移。"""
    entries = sorted(entries, key=lambda item: item[0])
    data_start = start + 2 + 12 * len(entries) + 4
    body = b""
    blob = b""
    for tag, typ, value in entries:
        if typ == 2:  # ASCII，含结尾 0
            payload = (value.encode("utf-8") if isinstance(value, str) else value) + b"\x00"
            count = len(payload)
        elif typ == 1:  # BYTE
            payload = bytes(value)
            count = len(payload)
        elif typ == 3:  # SHORT
            values = list(value) if isinstance(value, (list, tuple)) else [value]
            payload = b"".join(struct.pack("<H", int(item)) for item in values)
            count = len(values)
        elif typ == 4:  # LONG
            values = list(value) if isinstance(value, (list, tuple)) else [value]
            payload = b"".join(struct.pack("<I", int(item)) for item in values)
            count = len(values)
        elif typ == 5:  # RATIONAL
            payload = b"".join(struct.pack("<II", int(num), int(den)) for num, den in value)
            count = len(value)
        else:  # pragma: no cover - 生成器只用上面几种
            raise ValueError(f"不支持的 TIFF 类型 {typ}")
        if len(payload) <= 4:
            field = payload.ljust(4, b"\x00")
        else:
            field = struct.pack("<I", data_start + len(blob))
            blob += payload
        body += struct.pack("<HHI", tag, typ, count) + field
    return struct.pack("<H", len(entries)) + body + struct.pack("<I", 0) + blob


def _degrees(value: float) -> list[tuple[int, int]]:
    value = abs(value)
    degrees = int(value)
    minutes = int((value - degrees) * 60)
    seconds = (value - degrees - minutes / 60) * 3600
    return [(degrees, 1), (minutes, 1), (int(round(seconds * 100)), 100)]


def _tiff(when: datetime, make: str, model: str,
          gps: tuple[float, float, float] | None, size: tuple[int, int]) -> bytes:
    stamp = when.strftime("%Y:%m:%d %H:%M:%S")

    # 先排三块的顺序：IFD0、Exif 子 IFD、GPS 子 IFD。长度只跟条目数有关，
    # 所以可以先摆占位偏移、拿到长度后再回填真实偏移。
    base = 8
    exif_entries = [
        (TAG_EXIF_DATETIME, 2, stamp),
        (TAG_PIXEL_X, 4, size[0]),
        (TAG_PIXEL_Y, 4, size[1]),
    ]
    gps_entries: list[tuple[int, int, object]] = []
    if gps is not None:
        lat, lon, alt = gps
        gps_entries = [
            (TAG_GPS_VERSION, 1, b"\x02\x03\x00\x00"),
            (TAG_GPS_LAT_REF, 2, "N" if lat >= 0 else "S"),
            (TAG_GPS_LAT, 5, _degrees(lat)),
            (TAG_GPS_LON_REF, 2, "E" if lon >= 0 else "W"),
            (TAG_GPS_LON, 5, _degrees(lon)),
            (TAG_GPS_ALT_REF, 1, b"\x00" if alt >= 0 else b"\x01"),
            (TAG_GPS_ALT, 5, [(int(round(abs(alt) * 100)), 100)]),
        ]

    ifd0_stub = _encode_ifd([
        (TAG_MAKE, 2, make),
        (TAG_MODEL, 2, model),
        (TAG_DATETIME, 2, stamp),
        (TAG_EXIF_IFD, 4, 0),
        (TAG_GPS_IFD, 4, 0),
    ], base)
    exif_offset = base + len(ifd0_stub)
    exif_ifd = _encode_ifd(exif_entries, exif_offset)
    gps_offset = exif_offset + len(exif_ifd)
    gps_ifd = _encode_ifd(gps_entries, gps_offset) if gps_entries else b""

    ifd0 = _encode_ifd([
        (TAG_MAKE, 2, make),
        (TAG_MODEL, 2, model),
        (TAG_DATETIME, 2, stamp),
        (TAG_EXIF_IFD, 4, exif_offset),
        (TAG_GPS_IFD, 4, gps_offset if gps_entries else 0),
    ], base)
    return b"II" + struct.pack("<HI", 42, 8) + ifd0 + exif_ifd + gps_ifd


def _exif_app1(tiff: bytes) -> bytes:
    payload = b"Exif\x00\x00" + tiff
    return b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload


def write_photo(path: Path, when: datetime, *, make: str = "DJI", model: str = "FC8482",
                gps: tuple[float, float, float] | None = None,
                size: tuple[int, int] = (4000, 3000)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    app1 = _exif_app1(_tiff(when, make, model, gps, size))
    path.write_bytes(TINY_JPEG[:2] + app1 + TINY_JPEG[2:])
    return path


# ── MP4 拼装 ────────────────────────────────────────────────────────────────

def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload) + 8) + kind + payload


def write_video(path: Path, when: datetime, *, location: tuple[float, float, float] | None = None,
                seconds: int = 8, brand: bytes = b"isom") -> Path:
    """结构合法、能读出拍摄时间与 ISO6709 坐标，但画面是空的。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    ftyp = _box(b"ftyp", brand + struct.pack(">I", 512) + brand + b"iso2mp41")
    created = int((when - MP4_EPOCH).total_seconds())
    mvhd = _box(
        b"mvhd",
        b"\x00\x00\x00\x00"                      # version 0 + flags
        + struct.pack(">II", created, created)   # creation / modification
        + struct.pack(">II", 1000, seconds * 1000)  # timescale / duration
        + b"\x00\x01\x00\x00"                    # rate 1.0
        + b"\x01\x00"                            # volume
        + b"\x00" * 10                           # reserved
        + b"\x00" * 36                           # matrix
        + b"\x00" * 24                           # pre_defined
        + struct.pack(">I", 2)                   # next_track_id
    )
    parts = [mvhd]
    if location is not None:
        lat, lon, alt = location
        text = f"location.ISO6709:{lat:+.4f}{lon:+.4f}{alt:+.3f}/"
        parts.append(_box(b"free", text.encode("ascii")))
    moov = _box(b"moov", b"".join(parts))
    mdat = _box(b"mdat", b"\x00" * 4096)
    path.write_bytes(ftyp + moov + mdat)
    return path


def write_srt(path: Path, when: datetime, *, lat: float, lon: float, rel_alt: float) -> Path:
    """DJI 逐帧遥测的最小样本。``rel_alt`` 是判航拍最硬的证据。"""
    lines = []
    for index in range(3):
        lines.append(f"{index + 1:02d}")
        lines.append(f"00:00:{index:02d},000 --> 00:00:{index:02d},033")
        lines.append(
            f"[latitude: {lat:.6f}] [longitude: {lon:.6f}] "
            f"[rel_alt: {rel_alt:.1f} abs_alt: {rel_alt + 40:.1f}] [focal_len: 24.0]"
        )
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ── 素材库 ──────────────────────────────────────────────────────────────────

def build(target: Path | str, *, clean: bool = False) -> Path:
    """铺一个覆盖各种判定的素材库。返回根目录。

    这个例子故意只有**一个省**（河南），所以「河南」那一层会被折叠掉；
    有两个市（郑州、开封），所以市这一层会留着——正好演示「一层只放一个
    名字的层级不列」。

    日期都落在真实的备份周期里：2024-05-18 是周六，和 05-19（周日）同一个周期；
    06-01 也是周六，自己一个周期。抖音素材单独一棵枝，靠目录名里的「抖音」认。
    """
    root = Path(target).expanduser().resolve()
    if clean and root.exists():
        import shutil
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    write_templates(root)

    park = root / "2024-05-18 泡泡塘森林溪谷"

    # 1. 有 SRT 遥测的航拍：相对高度 92 米，坐标就在园区里
    clip = write_video(park / "水世界" / "DJI_0001.MP4", datetime(2024, 5, 18, 9, 12, 33))
    write_srt(clip.with_suffix(".SRT"), datetime(2024, 5, 18, 9, 12, 33),
              lat=34.7413, lon=113.7208, rel_alt=92.0)

    # 2. 同目录、没 SRT、只有 DJI_ 前缀：判定只有 0.55 的把握 → 待定
    write_video(park / "水世界" / "DJI_0002.MP4", datetime(2024, 5, 18, 16, 40, 12))

    # 3+4. 同一天、同一片露营区、两台不同相机地拍 → 这一组才分机型目录
    camp = root / "2024-05-19 泡泡塘森林溪谷" / "露营区"
    write_photo(camp / "IMG_8842.JPG", datetime(2024, 5, 19, 18, 30, 5),
                make="Canon", model="Canon EOS R5", gps=(34.7411, 113.7205, 96.0))
    write_photo(camp / "DSC_8843.JPG", datetime(2024, 5, 19, 18, 42, 11),
                make="SONY", model="ILCE-7M4", gps=(34.7410, 113.7203, 95.0))

    # 5. 延时：目录名写着延时，只有一台机器 → 不再分机型
    write_video(root / "2024-06-01 泡泡塘森林溪谷" / "花海" / "延时" / "DJI_0005.MP4",
                datetime(2024, 6, 1, 19, 40, 0), seconds=25,
                location=(34.7414, 113.7212, 88.0))

    # 6. 活动 + 届次：目录名里带「第39届」，届次段拼进年份
    fair = root / "2024-07-10 开封古城" / "第39届菊花文化节" / "DJI_0003.MP4"
    write_video(fair, datetime(2024, 7, 10, 10, 5, 0), location=(34.7968, 114.3075, 70.0))
    write_srt(fair.with_suffix(".SRT"), datetime(2024, 7, 10, 10, 5, 0),
              lat=34.7968, lon=114.3075, rel_alt=60.0)

    # 7. 手机随手拍：没有坐标、没有机型线索 → 区域识别不出，进「待定」
    write_photo(root / "2025-01-08 随手拍" / "IMG_1001.JPG",
                datetime(2025, 1, 8, 15, 2, 0),
                make="Apple", model="iPhone 15 Pro", size=(4032, 3024))

    # 8. 活动届次没写「第N届」 → 按拍摄年份当届次
    write_photo(root / "2025-03-16 郑州马拉松" / "IMG_2002.JPG",
                datetime(2025, 3, 16, 8, 15, 22),
                make="FUJIFILM", model="X-T5",
                gps=(34.7465, 113.7275, 95.0))

    # 9+10. 抖音拍摄类：目录名里写着「抖音」，整棵枝单独出来，不挂地点也不挂活动。
    #       一条是手机竖拍（地拍），一条是无人机（航拍），顺带验证同枝里也分拍摄类型。
    douyin = root / "2024-08-03 泡泡塘" / "抖音竖屏"
    write_photo(douyin / "DY_0007.JPG", datetime(2024, 8, 3, 11, 20, 0),
                make="Apple", model="iPhone 15 Pro", size=(3024, 4032))
    write_video(douyin / "DJI_0008.MP4", datetime(2024, 8, 3, 17, 5, 0),
                location=(34.7413, 113.7208, 60.0))

    print(f"示例素材库已建好：{root}")
    print(f"  照片 {_count(root, '*.JPG')} 个（真 EXIF：时间/机型/GPS）")
    print(f"  视频 {_count(root, '*.MP4')} 个（真拍摄时间，画面是占位字节）")
    print(f"  遥测 {_count(root, '*.SRT')} 个（DJI 相对高度）")
    print(f"  配置在 {root / CONFIG_DIR}")
    print("\n下一步：")
    print(f"  guicang --root \"{root}\" scan")
    print(f"  guicang --root \"{root}\" report")
    print(f"  guicang --root \"{root}\" plan")
    return root


def _count(root: Path, pattern: str) -> int:
    return sum(1 for _ in root.rglob(pattern))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    target = Path(args[0]) if args else Path.cwd() / "归藏示例库"
    build(target, clean="--clean" in args)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
