"""无 RAG 路径的核心规则摘要加载器。

读取 data/rag_corpus/核心规则摘要.md，返回带标题的 prompt 区块；
任何异常返回空串，不阻断生成（与 RAG 降级策略一致）。
"""
from __future__ import annotations

import logging
import re

from src.config.env import BUNDLE_ROOT

logger = logging.getLogger(__name__)

_CORE_RULES_FILE = BUNDLE_ROOT / "data" / "rag_corpus" / "核心规则摘要.md"
_CARD_SYSTEM_BODY = re.compile(r"## 卡牌体系\n(.*?)(?=\n## |\Z)", re.DOTALL)


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


def load_card_system() -> str:
    """加载核心规则摘要的"卡牌体系"段（RAG 未召回卡牌块时的防串味兜底）。

    只取卡牌体系段（行动/战法/装备/专属牌名清单），比全文 core_rules 精准省 token；
    缺失或异常返回空串，不阻断生成。
    """
    try:
        if not _CORE_RULES_FILE.exists():
            return ""
        text = _CORE_RULES_FILE.read_text(encoding="utf-8").strip()
        m = _CARD_SYSTEM_BODY.search(text)
        if not m:
            return ""
        return f"## 卡牌体系参考\n{m.group(1).strip()}"
    except Exception as e:
        logger.warning("卡牌体系段加载失败（降级为空）: %s", type(e).__name__)
        return ""
