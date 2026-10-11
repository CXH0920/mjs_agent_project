# -*- coding: utf-8 -*-
"""评估历史追加（rag_eval_history.py）

eval_rule_faqs / eval_guide_quality 每次运行追加一条 JSONL 记录到
logs/rag/eval_history.jsonl（不入库），供周更对比与回归观察：
语料/编码器/索引任一变动后的 hit_rate 变化可追溯。

--no-record 是逃生开关；历史落盘失败只告警不拖垮评估（退出码契约不变）。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from src.config.env import PROJECT_ROOT as ROOT

logger = logging.getLogger(__name__)

HISTORY_PATH = ROOT / "logs" / "rag" / "eval_history.jsonl"


def dir_mb(path) -> float:
    """目录体积（MB，一位小数）；不可读返回 0.0。"""
    try:
        return round(sum(p.stat().st_size for p in Path(path).rglob("*")
                         if p.is_file()) / 1048576, 1)
    except OSError:
        return 0.0


def _embedding_meta() -> dict:
    """当前编码器后端与模型指纹：backend 来自 config，sha256 取 ONNX 侧车
    （侧车由 export_embedding_onnx 写出；st 档或未导出时为 None）。"""
    from src.rag import config

    meta = {"backend": getattr(config, "EMBEDDING_BACKEND", "st"), "model_sha256": None}
    sidecar = ROOT / "data" / "rag_models" / "onnx" / "sha256.json"
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        entry = data.get("model.onnx")
        sha = entry.get("sha256") if isinstance(entry, dict) else entry
        if isinstance(sha, str) and sha:
            meta["model_sha256"] = sha[:12]
    except (OSError, ValueError):
        pass
    return meta


def append_eval_history(script: str, metrics: dict, record: bool = True) -> None:
    """追加一条评估记录（record=False 对应 --no-record）。"""
    if not record:
        return
    try:
        entry = {"ts": datetime.now().isoformat(timespec="seconds"),
                 "script": script, **metrics, **_embedding_meta()}
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with HISTORY_PATH.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as error:
        logger.warning("eval 历史追加失败（不影响评估结果）: %s", error)
