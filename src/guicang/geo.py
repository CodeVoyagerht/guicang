"""地理：把经纬度吸附到最近的地点，把路径里的地名认出来。

没有反向地理编码（要联网、要 SDK、要钱），也不需要——我去的地方就那么几个，
写进 places.toml 就够了。半径之外的坐标宁可返回「不知道」也不硬凑一个地点：
错的地点在树里比空着更难发现。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import Place

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """两点球面距离，单位公里。"""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, a)))


@dataclass(frozen=True)
class Hit:
    place: Place
    distance_km: float
    confidence: float
    rule: str


def confidence_for(distance_km: float, radius_km: float) -> float:
    """越靠圆心越确定。边缘仍然给 0.62，因为半径是人定的、GPS 漂移是几十米级。"""
    if radius_km <= 0:
        return 0.0
    ratio = min(1.0, max(0.0, distance_km / radius_km))
    return round(0.95 - 0.33 * ratio, 3)


def nearest_place(places: list[Place], lat: float | None, lon: float | None) -> Hit | None:
    """半径内最近的地点；都太远就返回 None。"""
    if lat is None or lon is None or not places:
        return None
    best: Hit | None = None
    for place in places:
        if place.lat is None or place.lon is None:
            continue
        distance = haversine_km(lat, lon, place.lat, place.lon)
        if distance > place.radius_km:
            continue
        hit = Hit(
            place=place,
            distance_km=round(distance, 3),
            confidence=confidence_for(distance, place.radius_km),
            rule="gps",
        )
        if best is None or hit.distance_km < best.distance_km:
            best = hit
    return best


def place_in_text(places: list[Place], text: str) -> Hit | None:
    """从相对路径里认地名。命中的名字越长越可靠——「奥体」比「郑州奥体中心」弱。"""
    if not text:
        return None
    best: Hit | None = None
    lowered = text.lower()
    for place in places:
        for alias in place.names():
            needle = alias.lower()
            if not needle or needle not in lowered:
                continue
            # 名字长度归一：2 个字给 0.72，8 个字封顶 0.88
            conf = min(0.88, 0.66 + 0.03 * len(alias))
            hit = Hit(place=place, distance_km=0.0, confidence=round(conf, 3), rule="path")
            if best is None or hit.confidence > best.confidence:
                best = hit
    return best


def suggest_places(places: list[Place], lat: float | None, lon: float | None) -> list[Hit]:
    """给待定素材做提示：报出半径外最近的三个点，让人一眼看出该不该加地点。"""
    if lat is None or lon is None:
        return []
    hits = []
    for place in places:
        if place.lat is None or place.lon is None:
            continue
        distance = haversine_km(lat, lon, place.lat, place.lon)
        hits.append(
            Hit(place=place, distance_km=round(distance, 3), confidence=0.0, rule="nearby")
        )
    hits.sort(key=lambda hit: hit.distance_km)
    return hits[:3]
