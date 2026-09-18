"""OCR 未决错法的频次与人工确认答案记录。

供"白名单配置"界面消费：识别线程记录无法自动确认的错法（发现），
用户在界面上的人工点选记录为候选内的答案（收集）；两者汇入同一个
频次文件，辅助人工决定哪些错法值得补进用户层白名单。

写侧有两个线程（OcrWorker 工作线程 + GUI 确认点选），模块内用一把锁
串行化"读-改-原子替换"；任何 IO 失败仅告警降级，绝不影响识别主流程。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime

from src.config.env import PROJECT_ROOT

logger = logging.getLogger(__name__)

STATS_PATH = PROJECT_ROOT / "data" / "ocr_name_pending_stats.json"
_VERSION = 1
# 同一错法在该窗口内的重复识别不累计（同局 2 秒一拍的轮询不虚增计数）。
# 计数语义为「节流后的记录次数」，排序有意义、绝对值无意义。
_THROTTLE_SECONDS = 60
_LOCK = threading.Lock()


def _empty_document() -> dict:
    return {"version": _VERSION, "entries": {}}


def _load_document() -> dict:
    if not STATS_PATH.exists():
        return _empty_document()
    try:
        document = json.loads(STATS_PATH.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or not isinstance(document.get("entries"), dict):
            raise ValueError("根节点必须为含 entries 对象的对象")
        return document
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("OCR 错法频次文件加载失败，重建空文件: %s", exc)
        return _empty_document()


def _write_document(document: dict) -> None:
    STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATS_PATH.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(document, file, ensure_ascii=False, indent=1)
        file.write("\n")
    temporary.replace(STATS_PATH)


def _entry_for(document: dict, raw_name: str, candidates: list[str]) -> dict:
    entries = document["entries"]
    entry = entries.get(raw_name)
    if entry is None:
        entry = {
            "count": 0,
            "candidates": candidates,
            "scenes": [],
            "first_seen": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "is_truncation": bool(candidates) and len(raw_name) < min(len(c) for c in candidates),
            "confirmed": "",
            "confirmed_count": 0,
        }
        entries[raw_name] = entry
    entry["candidates"] = candidates or entry["candidates"]
    return entry


def record_pending(raw_name: str, candidates: list[str], scene: str) -> None:
    """记录一个无法自动确认的错法读数（识别线程调用）。"""
    raw = raw_name.strip()
    if not raw:
        return
    ordered_candidates = [str(c) for c in candidates if str(c)]
    try:
        with _LOCK:
            document = _load_document()
            now = time.time()
            entry = _entry_for(document, raw, ordered_candidates)
            if now - float(entry.get("last_seen_ts", 0.0)) < _THROTTLE_SECONDS:
                return  # 节流窗口内静默跳过，不写盘
            entry["count"] = int(entry.get("count", 0)) + 1
            entry["last_seen_ts"] = now
            entry["last_seen"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            if scene and scene not in entry["scenes"]:
                entry["scenes"] = [*entry["scenes"], scene]
            _write_document(document)
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("OCR 错法频次记录失败（忽略）: %s", exc)


def record_confirmation(raw_name: str, answer: str, candidates: list[str]) -> None:
    """记录用户在界面上对未决槽位的人工点选（GUI 线程调用）。

    答案必须在该槽候选集内（两个确认入口均有此约束，此处再防一层）；
    raw_name 与答案相同不构成错法对，不记录。
    """
    raw = raw_name.strip()
    answer = answer.strip()
    ordered_candidates = [str(c) for c in candidates if str(c)]
    if not raw or not answer or raw == answer or answer not in ordered_candidates:
        return
    try:
        with _LOCK:
            document = _load_document()
            entry = _entry_for(document, raw, ordered_candidates)
            entry["confirmed"] = answer
            entry["confirmed_count"] = int(entry.get("confirmed_count", 0)) + 1
            entry["last_confirmed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            _write_document(document)
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("OCR 错法确认记录失败（忽略）: %s", exc)


