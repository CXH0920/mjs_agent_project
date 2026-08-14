"""RAG 攻略语料注入：检索规则语料并格式化为 prompt 区块。

供 build_guide_prompt 追加使用；任何异常一律降级为空串，
保证 API/浏览器两条生成链路不受 RAG 故障影响。
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_retriever_instance = None


def _rag_enabled() -> bool:
    """RAG 增强开关：环境变量 RAG_ENABLED（--no-rag 覆盖）优先，其次 config.env。"""
    env_flag = os.environ.get("RAG_ENABLED", "").strip().lower()
    if env_flag in ("0", "false", "no", "off"):
        return False
    try:
        from src.rag import config as rag_config
        return bool(rag_config.RAG_ENABLED)
    except Exception:
        return False


def _get_retriever():
    """进程内复用 Retriever 单例：语料/向量索引/模型只加载一次。"""
    global _retriever_instance
    if _retriever_instance is None:
        from src.rag.retriever import Retriever
        _retriever_instance = Retriever()
    return _retriever_instance


def reset_retriever() -> None:
    """清空 Retriever 单例（测试用）。"""
    global _retriever_instance
    _retriever_instance = None


def build_rag_context(hero: dict, max_chars: int | None = None) -> str:
    """检索并格式化 RAG 官方规则语料区块；失败时返回空串（降级）。"""
    if not _rag_enabled():
        return ""
    try:
        from src.rag import config as rag_config
        hero_name = hero.get("name", "")
        if not hero_name:
            return ""
        retriever = _get_retriever()
        blocks = retriever.hero_blocks(hero_name)
        seen = {b["block_id"] for b in blocks}
        extra = [
            b for b in retriever.search(hero_name, heroes=[hero_name], top_k=rag_config.TOP_K)
            if b["block_id"] not in seen
        ]
        chunks = blocks + extra
        if not chunks:
            return ""
        budget = max_chars or rag_config.RAG_PROMPT_CHARS
        lines = ["## RAG 官方规则语料（请严格依据以下语料块作答）"]
        used = 0
        for chunk in chunks:
            block = f"[{chunk['block_id']}] {chunk['text']}"
            remaining = budget - used
            if remaining <= 0:
                break
            if len(block) > remaining:
                lines.append(block[:remaining])
                used += remaining
                break
            lines.append(block)
            used += len(block)
        return "\n\n".join(lines)
    except Exception as e:
        logger.warning("RAG 语料注入失败（本次降级为无 RAG）: %s", type(e).__name__)
        return ""