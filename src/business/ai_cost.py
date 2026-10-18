"""AI 生成成本估算的业务层入口。

UI（生成工作流/后端选择对话框）经本模块估算成本，不直接依赖采集层；
估算规则（prompt_utils）变更时 UI 无感知。
"""

from __future__ import annotations

from src.config.profiles import get_api_config


def estimate_generation_cost(
    items: int,
    kind: str,
    model: str | None = None,
    use_rag: bool | None = None,
) -> dict:
    """按条目数与生成类型（guide/synergy）估算成本，返回 estimation dict。

    model 缺省取当前生效 API 档案的模型；use_rag 传入时计入 RAG 预算影响
    （后端选择对话框重算用），不传时按默认口径（RAG 语料注入，与对话框
    默认勾选一致）——不可把 None 直接透传，底层 `if use_rag` 会将其按
    False 结算成经典口径。未知 kind 显式报错，不静默降级。
    """
    from src.scraper.ai.prompt_utils import estimate_cost, estimate_item_cost

    model = model or get_api_config()["model"]
    if kind not in ("guide", "synergy"):
        raise ValueError(f"未知生成类型: {kind}")
    if use_rag is None:
        use_rag = True
    if kind == "guide":
        return estimate_cost(items, "guide", model, use_rag=use_rag)
    return estimate_item_cost(items, "synergy", model, use_rag=use_rag)
