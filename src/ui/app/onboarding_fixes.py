# -*- coding: utf-8 -*-
"""doctor 修复文案 → 首启向导动作的语境映射（spec_onboarding §3.3）。

doctor 的 fix 是开发者口吻（pip/conda/python -m），打包版用户看到即违反
M5 与反例条款。映射规则：
- 可一键执行的（frozen 支持 exe -m 分发）→ 中文动作名 + ScriptRunner 跑模块；
- frozen 下无意义的安装类命令 → 不渲染修复按钮，原文仅作详情（反馈给开发者）；
- 开发态保留原文按钮。
"""

from __future__ import annotations

import re

# fix 原文片段 → (中文动作名, -m 模块名)
_ONE_CLICK_FIXES = (
    ("src.scripts.fetch_recheck_models", "预取模型", "src.scripts.fetch_recheck_models"),
    ("src.rag.indexer", "重建索引", "src.rag.indexer"),
    ("export_embedding_onnx", "导出编码器", "src.scripts.export_embedding_onnx"),
)

# 开发者命令/安装类痕迹（pip/conda/environment.yml 安装、python -m 运维；
# "安装/重装"类 fix 在打包版依赖随包，同样无用户可执行动作）
_DEV_COMMAND = re.compile(r"python -m|pip |conda |environment\.yml|安装|重装")

# engine_loader 运行时 hint（v4 模型缺失异常文案），frozen 下替换为用户可动作的提示
_RUNTIME_HINT = "；请先运行 python -m src.scripts.fetch_recheck_models"
_RUNTIME_HINT_FROZEN = "（模型未预取，可在本向导「① 运行环境」一键预取）"


def fix_action(fix: str, frozen: bool) -> tuple[str, str, str]:
    """返回 (按钮文案, -m 模块名, tooltip)；按钮文案为空表示不渲染按钮。"""
    for marker, label, module in _ONE_CLICK_FIXES:
        if marker in fix:
            return label, module, f"{label}（自动执行，完成后可重新检查）"
    if frozen and _DEV_COMMAND.search(fix):
        return "", "", fix  # 无用户可执行动作：原文降级为详情，供反馈开发者
    return ("查看修复建议" if len(fix) > 8 else fix), "", fix


def sanitize_runtime_detail(text: str, frozen: bool) -> str:
    """frozen 语境抹去运行时文案里的开发者命令（engine_loader 的 v4 预取 hint）。"""
    if not frozen:
        return text
    return text.replace(_RUNTIME_HINT, _RUNTIME_HINT_FROZEN)
