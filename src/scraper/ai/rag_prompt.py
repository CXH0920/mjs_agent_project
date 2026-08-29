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
        from src.rag.retriever import KEYWORDS
        hero_name = hero.get("name", "")
        if not hero_name:
            return ""
        retriever = _get_retriever()
        blocks = retriever.hero_blocks(hero_name)
        seen = {b["block_id"] for b in blocks}
        # 构建丰富查询：武将名 + 技能名 + 机制词，提升跨类检索相关性
        skills = []
        mech_terms = []
        for skill in (hero.get("skills") or [])[:4]:
            skill_name = str(skill.get("name", ""))
            if skill_name:
                skills.append(skill_name)
            description = str(skill.get("description", ""))
            for keyword in KEYWORDS:
                if keyword in description and keyword not in mech_terms:
                    mech_terms.append(keyword)
        query = " ".join(filter(None, [hero_name, *skills, *mech_terms[:20]]))
        # 跨类检索：不带 heroes 过滤，召回卡牌/装备/规则/FAQ；post-filter 按武将归属过滤
        # combo 块无 hero 单值但有 heroes 列表，按列表过滤掉不含目标武将的噪声组合
        extra = []
        seen_ids = set(seen)
        for b in retriever.search(query, top_k=rag_config.TOP_K):
            bid = b["block_id"]
            if bid in seen_ids:
                continue
            meta = b.get("metadata", {})
            meta_hero = meta.get("hero")
            heroes = meta.get("heroes", [])
            if (meta_hero or heroes) and meta_hero != hero_name and hero_name not in heroes:
                continue
            seen_ids.add(bid)
            extra.append(b)
        if not blocks and not extra:
            return ""
        budget = max_chars or rag_config.RAG_PROMPT_CHARS
        return _format_rag_chunks(blocks, extra, budget)
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
        # 双 query 融合：武将名找基础信息，技能名+机制词找联动效果，各取半数 top_k 去重合并
        target_names = {name_a, name_b}
        half_k = max(1, rag_config.TOP_K // 2)
        queries = [
            f"{name_a} {name_b}",
            " ".join(filter(None, [*skills, *mech_terms[:15]])),
        ]
        extra = []
        seen_ids = set(seen)
        for q in queries:
            if not q:
                continue
            for b in retriever.search(q, top_k=half_k):
                bid = b["block_id"]
                if bid in seen_ids:
                    continue
                meta = b.get("metadata", {})
                meta_hero = meta.get("hero")
                heroes = meta.get("heroes", [])
                # combo 块按 heroes 列表过滤, hero 块按 hero 过滤, 无武将归属块保留
                if (meta_hero or heroes) and meta_hero not in target_names and not any(h in target_names for h in heroes):
                    continue
                seen_ids.add(bid)
                extra.append(b)
        if not blocks and not extra:
            return ""
        budget = max_chars or rag_config.RAG_SYNERGY_PROMPT_CHARS
        return _format_rag_chunks(blocks, extra, budget)
    except Exception as e:
        _mark_degraded(type(e).__name__)
        logger.warning("相性 RAG 语料注入失败（本次降级为无 RAG）: %s", type(e).__name__)
        return ""


def _format_rag_chunks(core_blocks: list[dict], extra_blocks: list[dict],
                       budget: int, core_ratio: float = 0.7) -> str:
    """按 kind 分两段：官方规则语料(硬依据) + 社区实战参考(combo/guide)。
    官方/社区独立预算池(core_ratio 给官方，剩余给社区，官方未用滚给社区)；
    社区池内 combo 优先于 guide（组合信息对相性更直接，避免长攻略挤掉组合块）。
    整块丢弃不截断。"""
    COMMUNITY = {'combo', 'guide'}

    def split(blocks: list[dict]) -> tuple[list[dict], list[dict]]:
        off, comm = [], []
        for chunk in blocks:
            k = chunk.get('metadata', {}).get('kind', '')
            (comm if k in COMMUNITY else off).append(chunk)
        return off, comm

    off_core, comm_core = split(core_blocks)
    off_extra, comm_extra = split(extra_blocks)

    off_budget = int(budget * core_ratio)
    comm_budget = budget - off_budget
    official: list[str] = []
    community: list[str] = []

    def fill(blocks: list[dict], limit: int, target: list[str], used: list[int]) -> None:
        for chunk in blocks:
            remaining = limit - used[0]
            if remaining <= 0:
                break
            block = f"[{chunk['block_id']}] {chunk['text']}"
            warn = (chunk.get('metadata') or {}).get('staleness_reason')
            if warn:
                # 时间轴判定该块涉及的技能已被官方调整，提示生成侧勿当硬依据
                block = f"⚠️ 过时风险：{warn}\n{block}"
            if len(block) > remaining:
                continue  # 整块丢弃，避免截断在结算句中间造成残缺规则
            target.append(block)
            used[0] += len(block)

    off_used = [0]
    fill(off_core, off_budget, official, off_used)
    fill(off_extra, off_budget, official, off_used)  # 官方池内 core 优先、extra 滚动
    comm_pool = comm_budget + max(0, off_budget - off_used[0])  # 官方未用滚给社区
    comm_used = [0]
    fill(comm_extra, comm_pool, community, comm_used)  # combo 优先
    fill(comm_core, comm_pool, community, comm_used)  # guide 补充

    lines = ["## RAG 官方规则语料（请严格依据以下语料块作答）"]
    lines.extend(official)
    if community:
        lines.append("## 社区实战参考（玩家实战 combo/攻略思路，可参考但非官方规则，勿当硬依据）")
        lines.extend(community)
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
