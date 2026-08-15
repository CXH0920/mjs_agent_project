"""RAG 攻略语料注入：检索规则语料并格式化为 prompt 区块。

供 build_guide_prompt 追加使用；任何异常一律降级为空串，
保证 API/浏览器两条生成链路不受 RAG 故障影响。
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_retriever_instance = None
# 运行时降级原因（RAG 被选择但检索/注入异常时记录，生成循环消费一次）
degraded_reason: str | None = None


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
        return _format_rag_chunks(chunks, budget)
    except Exception as e:
        _mark_degraded(type(e).__name__)
        logger.warning("RAG 语料注入失败（本次降级为无 RAG）: %s", type(e).__name__)
        return ""


def build_synergy_rag_context(hero_a: dict, hero_b: dict, max_chars: int | None = None) -> str:
    """检索双方武将语料块 + 相关跨类语料块并格式化为 prompt 区块；失败时返回空串（降级）。

    跨类检索只保留无 hero 元数据或属于目标武将的块，避免其他武将语料混入 prompt；
    查询以机制词优先（KEYWORDS），避免泛分析词把无关武将块排到前面。
    """
    if not _rag_enabled():
        return ""
    try:
        from src.rag import config as rag_config
        from src.rag.retriever import KEYWORDS
        name_a = hero_a.get("name", "")
        name_b = hero_b.get("name", "")
        if not name_a or not name_b:
            return ""
        retriever = _get_retriever()
        blocks = retriever.hero_blocks(name_a) + retriever.hero_blocks(name_b)
        seen = {b["block_id"] for b in blocks}
        skills = []
        mech_terms = []
        for hero in (hero_a, hero_b):
            for skill in (hero.get("skills") or [])[:4]:
                skill_name = str(skill.get("name", ""))
                if skill_name:
                    skills.append(skill_name)
                description = str(skill.get("description", ""))
                for keyword in KEYWORDS:
                    if keyword in description and keyword not in mech_terms:
                        mech_terms.append(keyword)
        query = " ".join(filter(None, [name_a, name_b, *skills, *mech_terms[:20]]))
        target_names = {name_a, name_b}
        extra = [
            b for b in retriever.search(query, top_k=rag_config.TOP_K)
            if b["block_id"] not in seen
            and (not b.get("metadata", {}).get("hero") or b["metadata"]["hero"] in target_names)
        ]
        chunks = blocks + extra
        if not chunks:
            return ""
        budget = max_chars or rag_config.RAG_SYNERGY_PROMPT_CHARS
        return _format_rag_chunks(chunks, budget)
    except Exception as e:
        _mark_degraded(type(e).__name__)
        logger.warning("相性 RAG 语料注入失败（本次降级为无 RAG）: %s", type(e).__name__)
        return ""


def _format_rag_chunks(chunks: list[dict], budget: int) -> str:
    """按 [block_id] 前缀 + 字符预算格式化语料块，标题与攻略/相性共用。"""
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


def _mark_degraded(reason: str) -> None:
    """记录本次进程的 RAG 降级原因（供生成循环输出一次可见提示）。"""
    global degraded_reason
    degraded_reason = reason


def take_degraded_reason() -> str | None:
    """取出并清空降级原因；生成循环每个任务输出一次提示。"""
    global degraded_reason
    reason = degraded_reason
    degraded_reason = None
    return reason
