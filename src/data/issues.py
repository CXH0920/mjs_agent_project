"""
名将杀 Agent - 数据问题值对象

DataIssue / LoadReport：数据加载与关联校验共用的轻量值对象。
独立成模块的原因：json_repository、各仓储与 business 层都要构造 DataIssue，
若随 DataManager 基类放在 manager.py，底层工具将反向依赖基类模块形成循环
依赖（审计 F3，此前靠 manager 内延迟导入压制）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class DataIssue:
    """数据加载或关联校验中发现的一项问题。"""

    severity: str
    kind: str
    file_path: Path
    message: str
    record_index: int | None = None
    entity_key: object | None = None
    field_name: str | None = None
    # True=源自磁盘文件损坏（保存守卫据此拒绝覆盖）；False=外部输入/校验产物
    # （如爬虫坏记录、跨实体引用缺失），不触发守卫也不触发坏文件留底
    from_disk: bool = True


@dataclass
class LoadReport:
    """一次完整数据加载的结构化结果。"""

    issues: list[DataIssue] = field(default_factory=list)
