"""无 RAG 路径的核心规则摘要加载器。

读取 data/rag_corpus/核心规则摘要.md，返回带标题的 prompt 区块；
任何异常返回空串，不阻断生成（与 RAG 降级策略一致）。
"""
from __future__ import annotations

import logging
from pathlib import Path

from src.config.env import BUNDLE_ROOT

logger = logging.getLogger(__name__)

_CORE_RULES_FILE = BUNDLE_ROOT / "data" / "rag_corpus" / "核心规则摘要.md"


def load_core_rules() -> str:
    """加载核心规则摘要；缺失或异常返回空串（降级）。"""
    try:
        if not _CORE_RULES_FILE.exists():
            return ""
        text = _CORE_RULES_FILE.read_text(encoding="utf-8").strip()
        if not text:
            return ""
        return f"## 基础规则参考\n{text}"
    except Exception as e:
        logger.warning("核心规则摘要加载失败（降级为空）: %s", type(e).__name__)
        return ""
