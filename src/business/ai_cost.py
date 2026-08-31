"""AI 生成成本估算的业务层入口。

UI（生成工作流/后端选择对话框）经本模块估算成本，不直接依赖采集层；
估算规则（prompt_utils）变更时 UI 无感知。
"""

from __future__ import annotations

from src.config.env import get_api_config


def estimate_generation_cost(
    items: int,
    kind: str,
    model: str | None = None,
    use_rag: bool | None = None,
) -> dict:
    """按条目数与生成类型（guide/synergy）估算成本，返回 estimation dict。

    model 缺省取当前生效 API 档案的模型；use_rag 传入时计入 RAG 预算影响
    （后端选择对话框重算用），不传时按默认口径。
    """
    from src.scraper.ai.prompt_utils import estimate_cost, estimate_item_cost

    model = model or get_api_config()["model"]
    if kind == "guide":
        return estimate_cost(items, "guide", model)
    return estimate_item_cost(items, "synergy", model, use_rag=use_rag)
