"""判定规则与目录形状：航拍还是地拍、落在哪一枝、待定该不该待定。"""

from __future__ import annotations

import unittest
from datetime import datetime

import _support  # noqa: F401

from guicang import classify, taxonomy
from guicang.config import Activity, Place, load_config, write_templates
from guicang.model import Probe


def make_probe(name: str = "file.MP4", **kwargs) -> Probe:
    from pathlib import Path

    fields = dict(path=Path("素材") / name, kind="video", size=1024, mtime=0.0)
    fields.update(kwargs)
    return Probe(**fields)


class ConfigFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = _support.TempDir()
        self.root = self._temp.__enter__()
        write_templates(self.root)
        self.config = load_config(self.root)
        self.config.places = [
            Place(name="郑州奥体中心", aliases=("奥体",), lat=34.7412, lon=113.7210, radius_km=3.0),
            Place(name="开封古城", aliases=("开封",), lat=34.7978, lon=114.3072, radius_km=6.0),
        ]
        self.config.activities = [
            Activity(name="开封菊花文化节", aliases=("菊花节", "菊花文化节"), editions=None),
            Activity(name="郑州马拉松", aliases=("郑马",), editions=None),
        ]

    def tearDown(self) -> None:
        self._temp.__exit__(None, None, None)


class ShotTypeTests(ConfigFixture):
    def _shot(self, rel: str, **probe_fields) -> tuple[str, float]:
        item = make_probe(**probe_fields)
        shot, conf, _evidence = classify.classify_shot(item, rel)
        return shot, conf

    def test_drone_model_is_aerial(self) -> None:
        self.assertEqual(self._shot("a/DJI_0003.JPG", make="DJI", model="Mavic 3 Pro")[0], "航拍")

    def test_camera_model_is_ground(self) -> None:
        self.assertEqual(self._shot("a/IMG_8842.JPG", kind="photo", make="Canon",
                                    model="Canon EOS R5")[0], "地拍")

    def test_directory_keyword_beats_model(self) -> None:
        # 人写的目录名比机器型号更可信：地拍目录里放着无人机拍的照片就是地拍
        shot, _conf = self._shot("2024-09-22 演唱会/地拍/DJI_0001.JPG",
                                 make="DJI", model="Mavic 3 Pro")
        self.assertEqual(shot, "地拍")

    def test_delay_and_fpv_keywords(self) -> None:
        self.assertEqual(self._shot("延时/DJI_0005.MP4")[0], "延时")
        self.assertEqual(self._shot("穿越机/AVATA.mp4")[0], "FPV")

    def test_dji_prefix_without_model_is_pending_not_ground(self) -> None:
        # 读不到机型时别急着按地拍算：DJI_ 命名说明是飞机拍的，该落到待定
        shot, conf = self._shot("开封古城/DJI_0002.MP4")
        self.assertEqual(shot, "航拍")
        self.assertLess(conf, 0.75)

    def test_high_relative_altitude_means_aerial(self) -> None:
        shot, _conf = self._shot("a/DJI_0001.MP4", alt=92.0, gps_source="srt")
        self.assertEqual(shot, "航拍")

    def test_low_relative_altitude_is_not_aerial(self) -> None:
        shot, _conf = self._shot("a/VID_0001.MP4", alt=2.0, gps_source="srt")
        self.assertEqual(shot, "地拍")

    def test_no_information_falls_back_to_ground_with_low_confidence(self) -> None:
        shot, conf = self._shot("a/VID_0001.MP4")
        self.assertEqual(shot, "地拍")
        self.assertLess(conf, 0.75)


class RegionTests(ConfigFixture):
    def test_gps_inside_radius_wins(self) -> None:
        item = make_probe(lat=34.7412, lon=113.7210, gps_source="exif")
        region, conf, _ = classify.classify_region(item, "a/B.MP4", self.config)
        # 现在返回的是 Place 而不是地名：上层要拿它的省/市/园区区域
        self.assertEqual(region.name, "郑州奥体中心")
        self.assertGreater(conf, 0.9)

    def test_path_name_is_used_without_gps(self) -> None:
        region, conf, _ = classify.classify_region(make_probe(), "2024-05-18 开封/菊花节/a.MP4", self.config)
        self.assertEqual(region.name, "开封古城")
        self.assertLess(conf, 0.95)

    def test_unknown_when_nothing_matches(self) -> None:
        region, conf, _ = classify.classify_region(make_probe(), "随手拍/a.MP4", self.config)
        self.assertIsNone(region)
        self.assertEqual(conf, 0.0)


class ActivityTests(ConfigFixture):
    def test_edition_from_directory(self) -> None:
        self.assertEqual(classify.extract_edition("第39届菊花节/DJI_0001.MP4"), "第39届")

    def test_chinese_numerals_are_normalised_to_digits(self) -> None:
        # 目录里写「第十一届」，落到树上一律写成「第11届」，同届不会长出两种写法
        self.assertEqual(classify.extract_edition("第十一届郑马/a.MP4"), "第11届")
        self.assertEqual(classify.extract_edition("第二十届/a.MP4"), "第20届")

    def test_edition_falls_back_to_year(self) -> None:
        activity, edition, conf, _ = classify.classify_activity(
            make_probe(captured_at=datetime(2025, 3, 30, 8, 0)),
            "2025-03-30 郑州马拉松/IMG_2002.JPG", self.config,
        )
        self.assertIsNotNone(activity)
        self.assertEqual(activity.name, "郑州马拉松")
        self.assertEqual(edition, "2025年")
        self.assertGreater(conf, 0.7)


class DecisionTests(ConfigFixture):
    def _decide(self, rel: str, **probe_fields):
        return classify.decide(make_probe(**probe_fields), rel, self.config)

    def test_clear_case_is_auto(self) -> None:
        decision = self._decide(
            "2024-05-19 开封古城/城墙/IMG_8842.JPG", kind="photo",
            make="Canon", model="Canon EOS R5", lat=34.7978, lon=114.3072,
            gps_source="exif", captured_at=datetime(2024, 5, 19, 10, 0),
        )
        self.assertEqual(decision.status, "auto")
        self.assertEqual(decision.region, "开封古城")
        self.assertEqual(decision.shot_type, "地拍")

    def test_activity_branch_needs_confidence(self) -> None:
        decision = self._decide(
            "2025-03-30 郑州马拉松/IMG_2002.JPG", kind="photo",
            make="FUJIFILM", model="X-T5", lat=34.7465, lon=113.7275,
            gps_source="exif", captured_at=datetime(2025, 3, 30, 8, 15),
        )
        self.assertEqual(decision.branch, "activity")
        self.assertEqual(decision.activity, "郑州马拉松")

    def test_unknown_region_goes_to_review(self) -> None:
        decision = self._decide("随手拍/IMG_1001.JPG", kind="photo",
                                make="Apple", model="iPhone 15 Pro",
                                captured_at=datetime(2025, 1, 8, 15, 2))
        self.assertEqual(decision.status, "needs_review")
        self.assertTrue(any("地点" in note for note in decision.notes))


class TaxonomyTests(ConfigFixture):
    def _decide(self, **fields):
        return classify.decide(make_probe(**fields), "2024-05-19 开封古城/城墙/a.JPG", self.config)

    def test_region_path_shape(self) -> None:
        decision = self._decide(kind="photo", make="Canon", model="EOS R5",
                                lat=34.7978, lon=114.3072, gps_source="exif")
        when = datetime(2024, 5, 19, 10, 0)
        # 时间层是「先分年份，再分备份周期」：05-19 是周日，属于 05-18 那个周六起的周期
        self.assertEqual(taxonomy.target_rel(decision, when, self.config),
                         "区域/开封古城/2024/2024-05-18_05-24/地拍")
        # 给了文件名，它就是文件的落位路径——不带文件名会把文件叫成「地拍」
        self.assertEqual(taxonomy.target_rel(decision, when, self.config, "IMG_8842.JPG"),
                         "区域/开封古城/2024/2024-05-18_05-24/地拍/IMG_8842.JPG")

    def test_activity_path_shape_mentions_the_year_once(self) -> None:
        decision = self._decide(kind="photo", make="SONY", model="ILCE-7M4",
                                lat=34.7465, lon=113.7275, gps_source="exif")
        decision.activity, decision.edition, decision.activity_conf = "郑州马拉松", "第3届", 0.9
        decision.branch = "activity"
        rel = taxonomy.target_rel(decision, datetime(2025, 3, 30, 8, 0), self.config)
        self.assertEqual(rel, "活动/郑州马拉松/2025-第3届/2025/2025-03-29_04-04/地拍")

    def test_activity_without_edition_does_not_repeat_the_year(self) -> None:
        from guicang.model import Decision

        # demo 里郑州马拉松没写「第N届」，届次只能退回年份 —— 年份不能再挂一遍
        decision = Decision(status="auto", shot_type="地拍", branch="activity",
                            activity="郑州马拉松", edition="2025年", activity_conf=0.9)
        rel = taxonomy.target_rel(decision, datetime(2025, 3, 16, 8, 0), self.config)
        self.assertEqual(rel, "活动/郑州马拉松/2025/2025-03-15_03-21/地拍")

    def test_douyin_is_its_own_tree(self) -> None:
        from guicang.model import Decision

        decision = Decision(status="auto", shot_type="地拍", branch="douyin",
                            branch_conf=0.9)
        self.assertEqual(
            taxonomy.target_rel(decision, datetime(2024, 8, 3, 11, 0), self.config),
            "抖音/2024/2024-08-03_08-09/地拍",
        )

    def test_douyin_pending_shot_stays_in_its_branch(self) -> None:
        from guicang.model import Decision

        # 抖音素材拍摄类型没把握：留在抖音枝里，最后一层写「待定」，
        # 不许丢进根上的待定桶——那样剪辑在抖音文件夹里就找不着它了
        decision = Decision(status="needs_review", shot_type="航拍", branch="douyin")
        self.assertEqual(
            taxonomy.target_rel(decision, datetime(2024, 8, 3, 17, 0), self.config),
            "抖音/2024/2024-08-03_08-09/待定",
        )

    def test_pending_bucket_for_unknowns(self) -> None:
        from guicang.model import Decision

        pending = Decision(status="needs_review", shot_type="航拍")
        self.assertEqual(
            taxonomy.target_rel(pending, datetime(2024, 5, 18, 9, 0), self.config),
            "待定/2024/2024-05-18_05-24/航拍",
        )

    def test_time_depth_year_month_day_and_week(self) -> None:
        from guicang.model import Decision

        decision = Decision(status="auto", shot_type="地拍", region="开封古城", branch="region")
        when = datetime(2024, 5, 19, 10, 0)
        configs = {}
        for depth in ("year", "month", "day", "week"):
            config = load_config(self.root)
            config.time_depth = depth
            configs[depth] = config
        self.assertEqual(taxonomy.target_rel(decision, when, configs["year"]),
                         "区域/开封古城/2024/地拍")
        self.assertEqual(taxonomy.target_rel(decision, when, configs["month"]),
                         "区域/开封古城/2024-05/地拍")
        self.assertEqual(taxonomy.target_rel(decision, when, configs["day"]),
                         "区域/开封古城/2024-05-19/地拍")
        self.assertEqual(taxonomy.target_rel(decision, when, configs["week"]),
                         "区域/开封古城/2024/2024-05-18_05-24/地拍")

    def test_week_runs_saturday_to_friday(self) -> None:
        # 天天的规矩：每个周六是周期起点，周五是终点
        saturday = datetime(2026, 8, 1, 9, 0)
        friday = datetime(2026, 8, 7, 23, 0)
        weekday_before = datetime(2026, 7, 31, 23, 0)
        self.assertEqual(taxonomy.week_label(saturday), "2026-08-01_08-07")
        self.assertEqual(taxonomy.week_label(friday), "2026-08-01_08-07")
        self.assertEqual(taxonomy.week_label(weekday_before), "2026-07-25_07-31")

    def test_week_across_new_year_stays_in_the_starting_year(self) -> None:
        # 2024-12-28（周六）到 2025-01-03（周五）是同一个周期，
        # 整段放在起始年下面，不然一个周期会被拆成两个年文件夹
        label = taxonomy.week_label(datetime(2025, 1, 3, 10, 0))
        self.assertEqual(label, "2024-12-28_2025-01-03")
        self.assertEqual(taxonomy.time_parts(datetime(2025, 1, 3, 10, 0), "week"),
                         ["2024", "2024-12-28_2025-01-03"])

    def test_unknown_time_is_named_not_guessed(self) -> None:
        from guicang.model import Decision

        decision = Decision(status="auto", shot_type="地拍", region="开封古城", branch="region")
        self.assertEqual(taxonomy.target_rel(decision, None, self.config),
                         "区域/开封古城/未知时间/地拍")


class LayoutTests(ConfigFixture):
    """只有一层里真的有两个以上取值时才列举——省/市/机型都按这条走。"""

    def _cfg(self, places) -> "object":
        config = load_config(self.root)
        config.places = list(places)
        return config

    def _decision(self, place: Place, **overrides):
        from guicang.model import Decision

        decision = Decision(
            status="auto", shot_type="航拍", branch="region",
            region=place.name, province=place.province, city=place.city,
        )
        for key, value in overrides.items():
            setattr(decision, key, value)
        return decision

    def test_single_province_and_city_are_collapsed(self) -> None:
        place = Place(name="泡泡塘森林溪谷", province="河南", city="郑州")
        config = self._cfg([place])
        decision = self._decision(place)
        when = datetime(2024, 5, 18, 9, 0)
        layout = taxonomy.build_layout([(decision, when, "DJI Mavic 3 Pro")], config)
        self.assertFalse(layout.use_province)
        self.assertFalse(layout.use_city)
        self.assertEqual(
            taxonomy.target_rel(decision, when, config, layout=layout, camera="DJI Mavic 3 Pro"),
            "区域/泡泡塘森林溪谷/2024/2024-05-18_05-24/航拍",
        )

    def test_city_layer_appears_only_with_two_cities(self) -> None:
        one = Place(name="泡泡塘森林溪谷", province="河南", city="郑州")
        two = Place(name="开封古城", province="河南", city="开封")
        config = self._cfg([one, two])
        when = datetime(2024, 5, 18, 9, 0)
        a, b = self._decision(one), self._decision(two)
        layout = taxonomy.build_layout([(a, when, ""), (b, when, "")], config)
        self.assertTrue(layout.use_city)
        self.assertFalse(layout.use_province)          # 同一个省，省还是折叠
        self.assertEqual(taxonomy.target_rel(a, when, config, layout=layout),
                         "区域/郑州/泡泡塘森林溪谷/2024/2024-05-18_05-24/航拍")

    def test_province_layer_appears_only_with_two_provinces(self) -> None:
        one = Place(name="泡泡塘森林溪谷", province="河南", city="郑州")
        two = Place(name="老茶馆", province="云南", city="大理")
        config = self._cfg([one, two])
        when = datetime(2024, 5, 18, 9, 0)
        a, b = self._decision(one), self._decision(two)
        layout = taxonomy.build_layout([(a, when, ""), (b, when, "")], config)
        self.assertTrue(layout.use_province)
        self.assertEqual(taxonomy.target_rel(a, when, config, layout=layout),
                         "区域/河南/郑州/泡泡塘森林溪谷/2024/2024-05-18_05-24/航拍")

    def test_camera_layer_only_for_the_day_that_has_two_cameras(self) -> None:
        place = Place(name="泡泡塘森林溪谷", province="河南", city="郑州")
        config = self._cfg([place])
        mixed = datetime(2024, 5, 19, 9, 0)
        solo = datetime(2024, 6, 1, 9, 0)
        a = self._decision(place, shot_type="地拍")
        b = self._decision(place, shot_type="地拍")
        c = self._decision(place, shot_type="地拍")
        layout = taxonomy.build_layout(
            [(a, mixed, "Canon EOS R5"), (b, mixed, "SONY ILCE-7M4"), (c, solo, "Canon EOS R5")],
            config,
        )
        self.assertTrue(layout.camera)
        self.assertEqual(taxonomy.target_rel(a, mixed, config, layout=layout, camera="Canon EOS R5"),
                         "区域/泡泡塘森林溪谷/2024/2024-05-18_05-24/地拍/Canon EOS R5")
        # 单独一天只有一台机器：不分机型
        self.assertEqual(taxonomy.target_rel(c, solo, config, layout=layout, camera="Canon EOS R5"),
                         "区域/泡泡塘森林溪谷/2024/2024-06-01_06-07/地拍")

    def test_unknown_camera_falls_into_its_own_bucket(self) -> None:
        place = Place(name="泡泡塘森林溪谷", province="河南", city="郑州")
        config = self._cfg([place])
        when = datetime(2024, 5, 19, 9, 0)
        a, b = self._decision(place), self._decision(place)
        layout = taxonomy.build_layout([(a, when, "Canon EOS R5"), (b, when, "")], config)
        self.assertEqual(taxonomy.target_rel(b, when, config, layout=layout, camera=""),
                         "区域/泡泡塘森林溪谷/2024/2024-05-18_05-24/航拍/未知机型")


class AreaTests(ConfigFixture):
    """园区里的各个区域：配置里列了就必须认出来，认不出就落待定。"""

    def setUp(self) -> None:
        super().setUp()
        self.config.places = [
            Place(name="泡泡塘森林溪谷", province="河南", city="郑州",
                  lat=34.7412, lon=113.7210, radius_km=3.0,
                  areas=("水世界|水上乐园", "露营区", "花海")),
        ]

    def _decide(self, rel: str):
        # 机型给足，好让「拍摄类型」这条不是短板——这里要测的是园区区域
        return classify.decide(
            make_probe(lat=34.7412, lon=113.7210, gps_source="exif",
                       make="DJI", model="Mavic 3 Pro"),
            rel, self.config,
        )

    def test_area_name_in_path_is_picked_up(self) -> None:
        decision = self._decide("2024-05-18 泡泡塘森林溪谷/水上乐园/DJI_0001.MP4")
        self.assertEqual(decision.region, "泡泡塘森林溪谷")
        self.assertEqual(decision.area, "水世界")     # 别名归到正式名
        self.assertEqual(decision.status, "auto")

    def test_missing_area_goes_to_review_with_a_reason(self) -> None:
        decision = self._decide("2024-05-18 泡泡塘森林溪谷/DJI_0001.MP4")
        self.assertEqual(decision.area, "")
        self.assertEqual(decision.status, "needs_review")
        self.assertTrue(any("区域" in note for note in decision.notes))

    def test_settle_place_fills_province_and_city(self) -> None:
        from guicang.model import Decision

        decision = Decision(status="auto", shot_type="航拍", branch="region", region="泡泡塘森林溪谷")
        classify.settle_place(decision, self.config)
        self.assertEqual((decision.province, decision.city), ("河南", "郑州"))
        # 园区区域还没定，状态要拉回待定，别让它悄悄落到地点根上
        self.assertEqual(decision.status, "needs_review")


if __name__ == "__main__":
    unittest.main()
