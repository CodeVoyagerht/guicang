"""人工钉住的判定：``.guicang/manual.json``。

自动判定错的时候（在能见度很差的傍晚拍的一段航拍，没有 GPS、没写 SRT），
机器再怎么猜也不该硬猜。这里让人只写一句「这条是航拍，归老君山」，
之后每次 rescan 都认它，且优先级高于一切自动证据。

为什么单独存一份文件而不塞进 SQLite：人要不要手改、要不要看 git 的 diff，
决定了它该是纯文本。SQLite 里再存一份就多了一个真相源，rescan 一覆盖就打架。
"""

from __future__ import annotations

import json
from pathlib import Path

from .classify import ACTIVITY_THRESHOLD
from .config import CONFIG_DIR
from .model import Decision, Evidence

MANUAL_FILE = "manual.json"

SHOT_KEY = "shot_type"
REGION_KEY = "region"
AREA_KEY = "area"
ACTIVITY_KEY = "activity"
EDITION_KEY = "edition"
_KEYS = (SHOT_KEY, REGION_KEY, AREA_KEY, ACTIVITY_KEY, EDITION_KEY)

MANUAL_WEIGHT = 1.0


def manual_path(root: Path | str) -> Path:
    return Path(root).expanduser().resolve() / CONFIG_DIR / MANUAL_FILE


def load_manual(root: Path | str) -> dict[str, dict]:
    """读人工判定。文件没有、写坏了都当成空表——不该因为这个扫不了库。"""
    path = manual_path(root)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    entries = payload.get("paths") if isinstance(payload, dict) else None
    if not isinstance(entries, dict):
        return {}
    clean: dict[str, dict] = {}
    for rel, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        kept = {key: str(entry[key]).strip() for key in _KEYS
                if isinstance(entry.get(key), str) and str(entry[key]).strip()}
        if kept:
            clean[str(rel)] = kept
    return clean


def save_manual(root: Path | str, entries: dict[str, dict]) -> Path:
    path = manual_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "_说明": "人工钉住的判定，优先级最高。key 是相对素材根目录的路径（正斜杠）。"
                 "可用字段：shot_type / region / activity / edition。删掉某条即恢复自动判定。",
        "paths": {rel: {key: str(entry[key]) for key in _KEYS if entry.get(key)}
                  for rel, entry in sorted(entries.items())},
    }
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def set_manual(root: Path | str, rel: str, **fields) -> dict[str, dict]:
    entries = load_manual(root)
    entry = dict(entries.get(rel) or {})
    for key in _KEYS:
        value = fields.get(key)
        if value is None:
            continue
        value = str(value).strip()
        if value:
            entry[key] = value
        else:
            entry.pop(key, None)
    if entry:
        entries[rel] = entry
    else:
        entries.pop(rel, None)
    save_manual(root, entries)
    return entries


def clear_manual(root: Path | str, rel: str) -> dict[str, dict]:
    entries = load_manual(root)
    entries.pop(rel, None)
    save_manual(root, entries)
    return entries


def apply(decision: Decision, entry: dict | None, when=None) -> Decision:
    """把人工判定盖到自动结果上。改动就地生效，返回同一个 Decision。"""
    if not entry:
        return decision

    if entry.get(SHOT_KEY):
        decision.shot_type = entry[SHOT_KEY]
        decision.shot_conf = MANUAL_WEIGHT
        decision.shot_evidence = [Evidence("manual", f"人工指定拍摄类型：{entry[SHOT_KEY]}", MANUAL_WEIGHT)]

    if entry.get(REGION_KEY):
        decision.region = entry[REGION_KEY]
        decision.region_conf = MANUAL_WEIGHT
        decision.region_evidence = [Evidence("manual", f"人工指定区域：{entry[REGION_KEY]}", MANUAL_WEIGHT)]

    if entry.get(AREA_KEY):
        decision.area = entry[AREA_KEY]
        decision.region_evidence = [
            *decision.region_evidence,
            Evidence("manual", f"人工指定园区区域：{entry[AREA_KEY]}", MANUAL_WEIGHT),
        ]

    if entry.get(ACTIVITY_KEY):
        decision.activity = entry[ACTIVITY_KEY]
        decision.activity_conf = MANUAL_WEIGHT
        edition = entry.get(EDITION_KEY) or decision.edition
        if not edition and when is not None:
            edition = f"{when.year}年"
        decision.edition = edition
        decision.activity_evidence = [
            Evidence("manual", f"人工指定活动：{entry[ACTIVITY_KEY]}", MANUAL_WEIGHT)
        ]
    elif entry.get(EDITION_KEY) and decision.activity:
        decision.edition = entry[EDITION_KEY]
        decision.activity_conf = MANUAL_WEIGHT
        decision.activity_evidence = list(decision.activity_evidence) + [
            Evidence("manual", f"人工指定届次：{entry[EDITION_KEY]}", MANUAL_WEIGHT)
        ]

    _refresh_branch_and_status(decision)
    return decision


def _refresh_branch_and_status(decision: Decision) -> None:
    from .model import AUTO_THRESHOLD

    if decision.activity and decision.activity_conf >= ACTIVITY_THRESHOLD:
        decision.branch = "activity"
    else:
        decision.branch = "region"

    missing = []
    if not decision.region:
        missing.append("区域未识别")
    elif decision.region_conf < AUTO_THRESHOLD:
        missing.append("区域没把握")
    if decision.shot_conf < AUTO_THRESHOLD:
        missing.append("拍摄类型没把握")
    if decision.activity and decision.activity_conf < ACTIVITY_THRESHOLD:
        missing.append("活动没把握")
    decision.status = "needs_review" if missing else "auto"
    if missing:
        decision.notes = list(getattr(decision, "notes", []) or []) + missing
