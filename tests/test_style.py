"""GLOBAL_STYLE 页签 QSS 等价守护。

资料库二级页签（librarySectionTabs）与页面内分区页签（sectionTabs）的
4 条 QSS 规则在 GLOBAL_STYLE 内逐字符重复（仅 objectName 不同，视觉一致
是有意设计）。style.py 无渲染测试且改动频繁——本文件锁死：

- 两个 objectName 的规则集逐字符一致（只改一处会导致等价性断裂）
- 下划线选中态的关键属性在位（padding/选中底色/下划线）
"""

from __future__ import annotations

import re

from src.ui.shared.style import GLOBAL_STYLE, PRIMARY, PRIMARY_SOFT


def _extract_tab_rules(qss: str, object_name: str) -> str:
    """抽出该 objectName 的 4 条页签规则，selector 归一化后拼接返回。"""
    pattern = (
        r"QTabWidget#" + object_name
        + r"(?:::pane| QTabBar::tab(?::hover:!selected|:selected)?) \{.*?\}"
    )
    blocks = re.findall(pattern, qss, re.DOTALL)
    assert len(blocks) == 4, f"{object_name} 应有完整 4 条页签规则，实际 {len(blocks)}"
    return "\n".join(blocks).replace(f"#{object_name}", "#TABS")


def test_section_tabs_rules_are_identical() -> None:
    assert _extract_tab_rules(GLOBAL_STYLE, "librarySectionTabs") == _extract_tab_rules(
        GLOBAL_STYLE, "sectionTabs"
    )


def test_section_tabs_underline_selected_properties() -> None:
    rules = _extract_tab_rules(GLOBAL_STYLE, "sectionTabs")
    assert "padding: 7px 14px" in rules
    assert f"background-color: {PRIMARY_SOFT};" in rules
    assert f"border-bottom: 2px solid {PRIMARY};" in rules
