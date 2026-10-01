# -*- coding: utf-8 -*-
"""索引精化工作台的渲染词汇表（审计 G4 切片 4.3）。

字段标签/提示词、字段卡片状态、清单行状态的颜色文案常量。对话框与
RefinementListPane / RefinementEditorPane 共用，避免 pane 反向 import
对话框模块造成循环；对话框模块保留 `_NOT_APPLICABLE_HINT` 等历史名字
绑定（测试经 dialog_module 读取）。
"""

from __future__ import annotations

from src.ui.shared.style import MUTED_TEXT, PRIMARY, SUCCESS, TONE_INFO, TONE_NEUTRAL, TONE_SUCCESS

FIELD_LABELS = {
    "timing": "时机",
    "trigger_condition": "触发条件",
    "target": "影响对象",
    "special_rules": "结算边界",
}

FIELD_HINTS = {
    "timing": "每行一个时机锚点，如：出牌阶段、其他角色的回合结束时、常驻被动",
    "trigger_condition": "每行一个完整触发情形（时机+前提+次数写成一句话，前提交集用“且”，"
                         "多个触发器分多行），如：出牌阶段结束时，且你本回合未发动过技能；"
                         "常驻技能写：常驻生效",
    "target": "每行一个影响对象，如：一名其他角色、所有角色",
    "special_rules": "每行一条结算边界，只压缩结算说明原文，如：被封禁时首次达到条件仍算已达成",
}

NOT_APPLICABLE_HINT = "卡牌块无此字段"

# 字段卡片状态：空 / LLM 建议 / 已精化（磁盘已有内容）/ 人工修改
FIELD_STATE_LABELS = {"empty": "待填写", "llm": "LLM 建议", "saved": "已精化", "manual": "已修改"}
FIELD_STATE_TONES = {"empty": TONE_NEUTRAL, "llm": TONE_INFO, "saved": TONE_SUCCESS, "manual": TONE_SUCCESS}

# 清单行状态
ROW_STATE_TEXT = {"pending": "○ 未处理", "suggested": "◉ 已建议", "modified": "✎ 已修改",
                  "refined": "✓ 已精化", "generated": "○ 已生成"}
ROW_STATE_COLOR = {"pending": MUTED_TEXT, "suggested": PRIMARY, "modified": SUCCESS,
                   "refined": SUCCESS, "generated": MUTED_TEXT}
