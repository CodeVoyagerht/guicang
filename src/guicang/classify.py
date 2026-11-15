"""判定：拍摄类型、区域、活动。每条结论都必须带「凭什么」。

规则是按优先级排的证据链，第一个命中就定论，但**所有**命中的证据都会留下，
所以「为什么它是航拍」永远回答得出来。落在 0.75 以下的一律进待定：
树里放错一个 4K 素材，比多一个待定文件夹难查得多。
"""

from __future__ import annotations

import re
from pathlib import Path

from . import geo
from .config import Activity, Config, Place
from .model import AUTO_THRESHOLD, Decision, Evidence, Probe

SHOT_TYPES = ("航拍", "地拍", "延时", "FPV", "其他")
ACTIVITY_THRESHOLD = 0.7
# 抖音枝：只认路径里的字，不认画幅比例
_DOUYIN_RE = re.compile(r"抖音|douyin|竖屏", re.I)

# ── 拍摄类型规则 ──────────────────────────────────────────────────────────────

_DELAY_WORDS = ("延时", "縮時", "缩时", "timelapse", "time-lapse", "hyperlapse", "lapse")
_FPV_WORDS = ("fpv", "avata", "goggles", "穿越机")
_AERIAL_WORDS = ("航拍", "航摄", "aerial", "空中", "无人机")
_GROUND_WORDS = ("地拍", "地面", "ground", "手持", "机位")

_DRONE_RE = re.compile(
    r"(fc\d{4}|mavic|mini\s?\d|air\s?\d|phantom|inspire|matrice|dji\s?neo|"
    r"御\s?\d|精灵|悟\s?\d|晓)",
    re.I,
)
_GROUND_DEVICE_RE = re.compile(
    r"(osmo|pocket|action\s?\d|ronin|如影|灵眸|iphone|ipad|pixel\s?\d|galaxy|"
    r"canon|nikon|sony|fuji|fujifilm|panasonic|lumix|leica|hasselblad|"
    r"eos|ilce|a7[mrs]?\d*|dsc-|x-t\d)",
    re.I,
)

# 读不到机型时，文件名前缀还能说明「这是从什么机器里出来的」。
# 手机和相机的默认命名都是地面设备，GoPro 也是——无人机不会用这些前缀（DJI 自己的是 DJI_）。
_PHONE_PREFIX_RE = re.compile(
    r"^(pxl_|img[-_ ]?e?\d|vid[-_ ]?\d|screen[ _-]?shot|screenshot|wx_camera_|mmexport)",
    re.I,
)
_CAMERA_PREFIX_RE = re.compile(
    r"^(mvi[-_]?\d|mov[-_]?\d|dsc[fo]?[-_ ]?\d|_dsc\d|dscn\d|p\d{7})", re.I
)
_ACTION_PREFIX_RE = re.compile(r"^(gopr|gp\d{2}|gx\d{2}|hero\d)", re.I)


def _shot_from_filename(name: str) -> tuple[str, float, str, str] | None:
    """按文件名前缀猜拍摄类型。够不上「铁证」，但比默认值强得多。"""
    if _ACTION_PREFIX_RE.match(name):
        return "地拍", 0.8, "path:action_prefix", "文件名是 GoPro 那类运动相机命名，属地面设备"
    if _PHONE_PREFIX_RE.match(name):
        return "地拍", 0.8, "path:phone_prefix", "文件名是手机默认命名（IMG_/PXL_/VID_/截图），一定是地拍"
    if _CAMERA_PREFIX_RE.match(name):
        return "地拍", 0.78, "path:camera_prefix", "文件名是相机默认命名（MVI_/DSC_/P1xxxxxx），属地面设备"
    return None


def _is_dji_named(path, rel_path: str) -> bool:
    """文件名是不是 DJI_ 那套无人机命名。"""
    if re.search(r"(^|/)dji[_-]?\d", rel_path, re.I):
        return True
    return path.stem.lower().startswith("dji_") or path.stem.lower().startswith("dji-")



def _add(bucket: list[Evidence], rule: str, detail: str, weight: float) -> None:
    bucket.append(Evidence(rule=rule, detail=detail, weight=round(weight, 3)))


def classify_shot(probe: Probe, rel_path: str) -> tuple[str, float, list[Evidence]]:
    """拍摄类型。人写的目录名最可信，其次是机器型号，最次是默认值。"""
    evidence: list[Evidence] = []
    haystack = f"{rel_path} {probe.path.stem}".lower()
    camera = probe.camera

    if any(word in haystack for word in _FPV_WORDS):
        _add(evidence, "path:fpv", f"路径或文件名含 FPV/穿越机字样", 0.9)
        return "FPV", 0.9, evidence
    if any(word in haystack for word in _DELAY_WORDS):
        _add(evidence, "path:delay", "路径或文件名含延时/时间流逝字样", 0.88)
        return "延时", 0.88, evidence
    if any(word in haystack for word in _AERIAL_WORDS):
        _add(evidence, "path:aerial", "路径或文件名写明航拍/空中", 0.86)
        return "航拍", 0.86, evidence
    if any(word in haystack for word in _GROUND_WORDS):
        _add(evidence, "path:ground", "路径或文件名写明地拍/地面", 0.84)
        return "地拍", 0.84, evidence

    if camera and _DRONE_RE.search(camera):
        _add(evidence, "camera:drone", f"机型 {camera} 是无人机", 0.84)
        return "航拍", 0.84, evidence

    if probe.alt is not None and probe.alt_kind == "relative" and probe.alt > 8:
        _add(evidence, "telemetry:altitude", f"遥测相对高度 {probe.alt:.1f}m，超过 8m", 0.8)
        return "航拍", 0.8, evidence

    if camera and _GROUND_DEVICE_RE.search(camera):
        _add(evidence, "camera:ground", f"机型 {camera} 是地面设备", 0.8)
        return "地拍", 0.8, evidence

    if camera:
        # 机型认不出来，但文件名前缀还在——手机的 IMG_、相机的 MVI_/DSC_ 都说明是地面设备。
        guessed = _shot_from_filename(probe.path.name)
        if guessed:
            label, conf, rule, detail = guessed
            _add(evidence, rule, f"{detail}（机型「{camera}」不在已知清单里）", conf)
            return label, conf, evidence
        # 机型只知道个牌子（DJI）时，「认不出来」不等于「地拍」：DJI_ 命名是从
        # 无人机里出来的，跟「机型为空」是同一份证据，该走同一档待定分数。
        if _is_dji_named(probe.path, rel_path):
            _add(
                evidence, "path:dji_prefix",
                f"文件名是 DJI_ 前缀，属无人机素材，但只认到牌子「{camera}」、读不到型号",
                0.55,
            )
            return "航拍", 0.55, evidence
        _add(evidence, "camera:unknown", f"机型 {camera} 不在已知的无人机或地面设备清单里", 0.62)
        return "地拍", 0.62, evidence

    guessed = _shot_from_filename(probe.path.name)
    if guessed:
        label, conf, rule, detail = guessed
        _add(evidence, rule, detail, conf)
        return label, conf, evidence

    if _is_dji_named(probe.path, rel_path):
        # DJI_ 前缀说明是从无人机里拿出来的，但读不到具体机型——够不上自动，
        # 又比「什么都没有」强，所以给航拍一个待定的分数，让人一眼看到。
        _add(evidence, "path:dji_prefix", "文件名是 DJI_ 前缀，属无人机素材，但读不到机型", 0.55)
        return "航拍", 0.55, evidence

    if probe.kind == "video":
        _add(evidence, "default:video", "没有任何机型信息，视频默认按地拍处理", 0.5)
        return "地拍", 0.5, evidence

    _add(evidence, "default:photo", "没有任何机型信息，照片默认按地拍处理", 0.45)
    return "地拍", 0.45, evidence


def classify_region(
    probe: Probe, rel_path: str, config: Config
) -> tuple[Place | None, float, list[Evidence]]:
    """认出是哪个地点。GPS 优先，路径里的地名其次。"""
    evidence: list[Evidence] = []
    if not config.region_enabled:
        return None, 0.0, evidence

    hit = geo.nearest_place(config.places, probe.lat, probe.lon)
    if hit:
        _add(
            evidence, "gps:place",
            f"坐标 ({probe.lat:.5f}, {probe.lon:.5f}) 距「{hit.place.name}」{hit.distance_km:.2f}km",
            hit.confidence,
        )
        return hit.place, hit.confidence, evidence

    if probe.lat is not None and probe.lon is not None:
        suggestions = geo.suggest_places(config.places, probe.lat, probe.lon)
        if suggestions:
            closest = suggestions[0]
            _add(
                evidence, "gps:outside",
                f"坐标距最近的地点「{closest.place.name}」还有 {closest.distance_km:.1f}km，超出半径",
                0.0,
            )

    path_hit = geo.place_in_text(config.places, rel_path)
    if path_hit:
        _add(evidence, "path:place", f"相对路径里出现「{path_hit.place.name}」", path_hit.confidence)
        return path_hit.place, path_hit.confidence, evidence

    return None, 0.0, evidence


def classify_area(
    place: Place | None, rel_path: str
) -> tuple[str, float, list[Evidence]]:
    """园区里的区域（水世界、露营区这种）。名字只在路径里找——EXIF 里没有这一层。

    一个地点没在配置里列区域，就不分这一层；列了却没对上，交给 decide() 判待定。
    """
    evidence: list[Evidence] = []
    if place is None or not place.areas:
        return "", 1.0, evidence

    lowered = rel_path.lower()
    best: tuple[int, str, str] | None = None
    for entry in place.areas:
        aliases = place.area_aliases(entry)
        for alias in aliases:
            needle = alias.lower()
            if needle and needle in lowered and (best is None or len(alias) > best[0]):
                best = (len(alias), aliases[0], alias)

    if best is None:
        return "", 0.0, evidence

    _, canonical, matched = best
    _add(evidence, "path:area", f"相对路径里出现「{matched}」，归到园区区域「{canonical}」", 0.9)
    return canonical, 0.9, evidence


_EDITION_RE = re.compile(r"第\s*([0-9]{1,3}|[一二三四五六七八九十]{1,3})\s*届")
_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_number(text: str) -> int | None:
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if text.startswith("十"):
        return 10 + _CN_DIGITS.get(text[1:], 0)
    if text.endswith("十"):
        return _CN_DIGITS.get(text[:-1], 0) * 10
    if "十" in text:
        head, _, tail = text.partition("十")
        return _CN_DIGITS.get(head, 0) * 10 + _CN_DIGITS.get(tail, 0)
    return _CN_DIGITS.get(text)


def extract_edition(rel_path: str) -> str | None:
    """「第3届」「第三届」→ 号数；认不出返回 None，由年份兜底。"""
    match = _EDITION_RE.search(rel_path)
    if not match:
        return None
    number = _cn_number(match.group(1))
    return f"第{number}届" if number else None


def classify_activity(
    probe: Probe, rel_path: str, config: Config
) -> tuple[Activity | None, str | None, float, list[Evidence]]:
    """活动只在路径里认——活动名不会写进 EXIF。"""
    evidence: list[Evidence] = []
    if not config.activity_enabled or not config.activities:
        return None, None, 0.0, evidence

    lowered = rel_path.lower()
    best: tuple[Activity, float] | None = None
    for activity in config.activities:
        for alias in activity.names():
            needle = alias.lower()
            if needle and needle in lowered:
                conf = min(0.92, 0.74 + 0.02 * len(alias))
                if best is None or conf > best[1]:
                    best = (activity, round(conf, 3))
                break

    if best is None:
        return None, None, 0.0, evidence

    activity, confidence = best
    edition = extract_edition(rel_path)
    if edition:
        _add(evidence, "path:activity", f"相对路径里出现「{activity.name}」与{edition}", confidence)
    else:
        year = probe.captured_at.year if probe.captured_at else None
        edition = f"{year}年" if year else None
        _add(
            evidence, "path:activity",
            f"相对路径里出现「{activity.name}」，届次未标注，按拍摄年份记为 {edition or '未知'}",
            round(confidence - 0.04, 3),
        )
        confidence = round(confidence - 0.04, 3)
    return activity, edition, confidence, evidence


def classify_douyin(rel_path: str) -> tuple[bool, float, list[Evidence]]:
    """这条素材是不是「抖音拍摄类」。

    只看路径里的字：抖音 / douyin / 竖屏。**不按画幅比例猜**——手机竖着拍的日常素材
    不该一股脑冲进抖音枝。看不出来的留在区域或活动枝，需要时 ``review --branch 抖音`` 钉一次。
    """
    evidence: list[Evidence] = []
    match = _DOUYIN_RE.search(rel_path)
    if not match:
        return False, 0.0, evidence
    evidence.append(
        Evidence("path:douyin", f"路径里出现「{match.group(0)}」，按抖音素材单独归档", 0.9)
    )
    return True, 0.9, evidence


def decide(probe: Probe, rel_path: str, config: Config) -> Decision:
    """一次分类的完整结论。三条证据链各自独立，互不污染置信度。"""
    shot_type, shot_conf, shot_evidence = classify_shot(probe, rel_path)
    place, region_conf, region_evidence = classify_region(probe, rel_path, config)
    area, _, area_evidence = classify_area(place, rel_path)
    activity, edition, activity_conf, activity_evidence = classify_activity(probe, rel_path, config)
    is_douyin, douyin_conf, douyin_evidence = classify_douyin(rel_path)

    decision = Decision(
        shot_type=shot_type,
        shot_conf=shot_conf,
        shot_evidence=shot_evidence,
        region=place.name if place else None,
        region_conf=region_conf,
        region_evidence=[*region_evidence, *area_evidence],
        province=place.province if place else "",
        city=place.city if place else "",
        area=area,
        activity=activity.name if activity else None,
        edition=edition if activity else None,
        activity_conf=activity_conf,
        activity_evidence=activity_evidence,
    )
    if is_douyin:
        decision.branch = "douyin"
        decision.branch_conf = douyin_conf
        decision.branch_evidence = douyin_evidence
    elif activity and activity_conf >= ACTIVITY_THRESHOLD:
        decision.branch = "activity"
        decision.branch_conf = activity_conf
        decision.branch_evidence = list(activity_evidence)
    else:
        decision.branch = "region"

    if decision.branch == "region" and decision.region is None:
        decision.status = "needs_review"
        outside = next((item for item in region_evidence if item.rule == "gps:outside"), None)
        decision.notes.append(
            f"地点没认出来：{outside.detail}，路径里也没出现清单上的地名"
            if outside else "地点没认出来：没有坐标，路径里也没出现清单上的地名"
        )
    elif shot_conf < AUTO_THRESHOLD:
        decision.status = "needs_review"
        decision.notes.append(f"拍摄类型只有 {shot_conf:.2f} 的把握")
    elif decision.branch == "activity" and activity_conf < AUTO_THRESHOLD:
        decision.status = "needs_review"
        decision.notes.append(f"活动只有 {activity_conf:.2f} 的把握")
    elif decision.branch == "region" and region_conf < AUTO_THRESHOLD:
        decision.status = "needs_review"
        decision.notes.append(f"地点只有 {region_conf:.2f} 的把握")
    elif decision.branch == "region" and place and place.areas and not decision.area:
        decision.status = "needs_review"
        names = "、".join(place.area_names())
        decision.notes.append(
            f"「{place.name}」在配置里分了园区区域（{names}），这条素材对不上是哪一块："
            f"把区域名写进路径，或者 review --set 钉一次"
        )
    else:
        decision.status = "auto"
    return decision


def settle_place(decision: Decision, config: Config) -> Decision:
    """人工改过地点之后补上省/市，并检查园区区域还缺不缺。

    省和市不是从素材里认出来的，是 places.toml 里那个地点的属性；人工把一条素材
    钉到「泡泡塘森林溪谷」以后，它自然就该进河南/郑州。这条规则放在 decide 之后、
    落位之前跑，否则人工钉的地点就少了上面两层。
    """
    if decision.branch != "region" or not decision.region:
        return decision
    place = None
    for candidate in config.places:
        if candidate.name == decision.region or decision.region in candidate.aliases:
            place = candidate
            break
    if place is None:
        return decision

    decision.province = place.province
    decision.city = place.city
    if place.areas and not decision.area:
        names = "、".join(place.area_names())
        note = (
            f"「{place.name}」在配置里分了园区区域（{names}），这条素材对不上是哪一块："
            f"把区域名写进路径，或者 review --set 钉一次"
        )
        if decision.status != "needs_review":
            decision.status = "needs_review"
        if note not in decision.notes:
            decision.notes.append(note)
    return decision


def explain(decision: Decision) -> str:
    """给人看的判定说明，``guicang explain`` 用它。"""
    lines = [f"结论：{decision.summary()}（{decision.status}）"]
    for label, items in (
        ("拍摄类型", decision.shot_evidence),
        ("区域", decision.region_evidence),
        ("活动", decision.activity_evidence),
    ):
        if not items:
            lines.append(f"  {label}：无证据")
            continue
        lines.append(f"  {label}：")
        for item in items:
            lines.append(f"    - [{item.rule}] {item.detail} → {item.weight:.2f}")
    return "\n".join(lines)
