"""读元数据：EXIF、MP4 盒子、DJI 遥测，以及读不到时怎么降级。"""

from __future__ import annotations

import unittest
from datetime import datetime

import _support  # noqa: F401

from guicang import exif, probe as probe_mod
from guicang.demo import write_photo, write_srt, write_video


class ExifTests(unittest.TestCase):
    def test_photo_roundtrip(self) -> None:
        with _support.TempDir() as base:
            path = base / "IMG_8842.JPG"
            when = datetime(2024, 5, 19, 10, 30, 15)
            write_photo(path, when, make="Canon", model="Canon EOS R5",
                        gps=(34.7978, 114.3072, 72.5))
            data = exif.read_exif(path)
            self.assertEqual(data["datetime"], when)
            self.assertEqual(data["make"], "Canon")
            self.assertEqual(data["model"], "Canon EOS R5")
            self.assertAlmostEqual(data["lat"], 34.7978, places=4)
            self.assertAlmostEqual(data["lon"], 114.3072, places=4)
            self.assertAlmostEqual(data["alt"], 72.5, places=1)

    def test_southern_and_western_hemisphere_signs(self) -> None:
        with _support.TempDir() as base:
            path = base / "南半球.JPG"
            write_photo(path, datetime(2024, 1, 2, 3, 4, 5), gps=(-33.8688, -151.2093, 12.0))
            data = exif.read_exif(path)
            self.assertAlmostEqual(data["lat"], -33.8688, places=4)
            self.assertAlmostEqual(data["lon"], -151.2093, places=4)

    def test_missing_file_returns_empty(self) -> None:
        with _support.TempDir() as base:
            self.assertEqual(exif.read_exif(base / "没有这个.JPG"), {})


class Mp4Tests(unittest.TestCase):
    def test_creation_time_and_iso6709_location(self) -> None:
        with _support.TempDir() as base:
            path = base / "DJI_0005.MP4"
            when = datetime(2024, 8, 5, 19, 40, 0)
            write_video(path, when, location=(34.7412, 113.7210, 88.0), seconds=12)
            data = exif.read_mp4_meta(path)
            self.assertEqual(data["datetime"], when)
            self.assertAlmostEqual(data["lat"], 34.7412, places=4)
            self.assertAlmostEqual(data["lon"], 113.7210, places=4)
            self.assertAlmostEqual(data["alt"], 88.0, places=1)
            self.assertAlmostEqual(data["duration"], 12.0, places=1)

    def test_time_is_local_time_not_utc(self) -> None:
        # 相机把拍摄地的本地时间写进 mvhd；再按 UTC 转一次会把清晨挪到下午
        with _support.TempDir() as base:
            path = base / "清晨.MP4"
            when = datetime(2024, 5, 18, 5, 30, 0)
            write_video(path, when)
            self.assertEqual(exif.read_mp4_meta(path)["datetime"], when)


class SrtTests(unittest.TestCase):
    def test_dji_telemetry_fields(self) -> None:
        with _support.TempDir() as base:
            path = base / "DJI_0001.SRT"
            write_srt(path, datetime(2024, 5, 18, 9, 12, 33),
                      lat=34.7978, lon=114.3072, rel_alt=92.0)
            data = exif.read_srt(path)
            self.assertAlmostEqual(data["lat"], 34.7978, places=4)
            self.assertAlmostEqual(data["lon"], 114.3072, places=4)
            self.assertAlmostEqual(data["alt"], 92.0, places=1)
            self.assertEqual(data["alt_kind"], "relative")

    def test_date_line_is_used_when_the_file_carries_one(self) -> None:
        # 有些版本的 SRT 带一行创建时间，带了就用，没有也不强求——时间的主来源
        # 是 MP4 的 mvhd，SRT 只负责高度和坐标。
        with _support.TempDir() as base:
            path = base / "带日期.SRT"
            path.write_text(
                "2024-05-18 09:12:33\n"
                "1\n00:00:00,000 --> 00:00:00,033\n"
                "[latitude: 34.7978] [longitude: 114.3072] [rel_alt: 92.0]\n",
                encoding="utf-8",
            )
            self.assertEqual(exif.read_srt(path)["datetime"], datetime(2024, 5, 18, 9, 12, 33))

    def test_altitude_comes_from_the_field_not_the_bracket(self) -> None:
        # 真实 DJI 文件里一排字段挤在同一对方括号里，不能要求 ] 紧跟数字
        with _support.TempDir() as base:
            path = base / "x.SRT"
            path.write_text(
                "1\n00:00:00,000 --> 00:00:00,033\n"
                "<font size=\"28\">[latitude: 34.7978] [longitude: 114.3072] "
                "[rel_alt: 92.0 abs_alt: 132.0] [focal_len: 24.0]</font>\n",
                encoding="utf-8",
            )
            data = exif.read_srt(path)
            self.assertEqual(data["alt_kind"], "relative")
            self.assertAlmostEqual(data["alt"], 92.0, places=1)


class ProbeTests(unittest.TestCase):
    def test_photo_probe_uses_exif_first(self) -> None:
        with _support.TempDir() as base:
            path = base / "2019-01-01 假名字.JPG"
            when = datetime(2024, 5, 19, 10, 30, 15)
            write_photo(path, when, make="SONY", model="ILCE-7M4",
                        gps=(34.7978, 114.3072, 72.5))
            item = probe_mod.probe_file(path, root=base)
            self.assertEqual(item.captured_at, when)
            self.assertEqual(item.captured_source, "exif")
            self.assertEqual(item.camera, "SONY ILCE-7M4")
            self.assertEqual(item.kind, "photo")

    def test_video_falls_back_to_filename_date(self) -> None:
        with _support.TempDir() as base:
            path = base / "2023-11-12 老君山 航拍.MP4"
            path.write_bytes(b"\x00\x00\x00\x08ftypisom" + b"\x00" * 64)
            item = probe_mod.probe_file(path, root=base)
            self.assertEqual(item.captured_source, "filename")
            self.assertEqual(item.captured_at.date().isoformat(), "2023-11-12")

    def test_no_metadata_falls_back_to_mtime_with_low_confidence(self) -> None:
        with _support.TempDir() as base:
            path = base / "无名照片.JPG"
            path.write_bytes(b"\xff\xd8\xff\xd9")
            item = probe_mod.probe_file(path, root=base)
            self.assertIsNotNone(item.captured_at)
            self.assertEqual(item.captured_source, "mtime")
            self.assertLess(item.captured_conf, 0.5)

    def test_camera_name_does_not_repeat_the_make(self) -> None:
        with _support.TempDir() as base:
            path = base / "IMG_8842.JPG"
            write_photo(path, datetime(2024, 5, 19, 10, 0, 0),
                        make="Canon", model="Canon EOS R5")
            self.assertEqual(probe_mod.probe_file(path, root=base).camera, "Canon EOS R5")


if __name__ == "__main__":
    unittest.main()
