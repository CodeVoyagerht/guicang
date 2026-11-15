"""路径净化与地理吸附：这两件小事错了，整棵树都会脏。"""

from __future__ import annotations

import unittest

import _support  # noqa: F401  把 src 挂上路径

from guicang import geo
from guicang.config import Place
from guicang.paths import (
    RESERVED_NAMES,
    kind_of,
    rel_posix,
    sanitize_segment,
)


class SanitizeTests(unittest.TestCase):
    def test_illegal_characters_become_underscores(self) -> None:
        self.assertEqual(sanitize_segment('开封:第39届"菊花文化节"'), "开封_第39届_菊花文化节_")

    def test_trailing_dot_and_space_are_stripped(self) -> None:
        # Windows 上以点或空格结尾的目录名是非法路径，必须先剪掉
        self.assertEqual(sanitize_segment("2024-05 开封 "), "2024-05 开封")
        self.assertEqual(sanitize_segment("郑州奥体中心."), "郑州奥体中心")

    def test_reserved_names_get_a_suffix(self) -> None:
        for name in ("CON", "NUL", "COM1"):
            self.assertIn(name, RESERVED_NAMES)
            self.assertNotEqual(sanitize_segment(name).upper(), name)

    def test_empty_or_dots_use_fallback(self) -> None:
        self.assertEqual(sanitize_segment(""), "_")
        self.assertEqual(sanitize_segment("  "), "_")
        self.assertEqual(sanitize_segment("..."), "_")
        self.assertEqual(sanitize_segment("", fallback="其他"), "其他")

    def test_long_names_are_truncated(self) -> None:
        self.assertLessEqual(len(sanitize_segment("长" * 200)), 80)


class KindTests(unittest.TestCase):
    def test_media_kinds(self) -> None:
        self.assertEqual(kind_of("DJI_0001.MP4"), "video")
        self.assertEqual(kind_of("IMG_8842.JPG"), "photo")
        self.assertEqual(kind_of("DJI_0001.SRT"), "sidecar")
        self.assertEqual(kind_of("说明.txt"), "other")

    def test_rel_posix_uses_forward_slashes(self) -> None:
        from pathlib import Path

        rel = rel_posix(Path("/a/b"), Path("/a/b/c/d.jpg"))
        self.assertEqual(rel, "c/d.jpg")


class GeoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.places = [
            Place(name="郑州奥体中心", aliases=("奥体",), lat=34.7412, lon=113.7210, radius_km=3.0),
            Place(name="开封古城", aliases=("开封",), lat=34.7978, lon=114.3072, radius_km=6.0),
        ]

    def test_haversine_is_in_km_of_reality(self) -> None:
        # 郑州到开封大约 60 公里，误差留 3 公里
        distance = geo.haversine_km(34.7412, 113.7210, 34.7978, 114.3072)
        self.assertAlmostEqual(distance, 54.0, delta=3.0)

    def test_nearest_place_inside_radius(self) -> None:
        hit = geo.nearest_place(self.places, 34.7420, 113.7220)
        self.assertIsNotNone(hit)
        self.assertEqual(hit.place.name, "郑州奥体中心")
        self.assertGreater(hit.confidence, 0.9)

    def test_nearest_place_outside_radius_returns_none(self) -> None:
        # 两地中间的空地，谁都不该认领
        self.assertIsNone(geo.nearest_place(self.places, 34.7690, 114.0100))

    def test_confidence_drops_towards_the_edge(self) -> None:
        centre = geo.confidence_for(0.1, 3.0)
        edge = geo.confidence_for(2.95, 3.0)
        self.assertGreater(centre, edge)
        self.assertGreaterEqual(edge, 0.6)

    def test_place_in_text_matches_alias(self) -> None:
        hit = geo.place_in_text(self.places, "2024-05-18/开封/菊花节/DJI_0001.MP4")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.place.name, "开封古城")

    def test_place_in_text_prefers_longer_alias(self) -> None:
        places = [
            Place(name="开封", aliases=(), lat=None, lon=None),
            Place(name="开封古城", aliases=(), lat=None, lon=None),
        ]
        hit = geo.place_in_text(places, "2024-05-18/开封古城/城墙/IMG_8842.JPG")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.place.name, "开封古城")


if __name__ == "__main__":
    unittest.main()
