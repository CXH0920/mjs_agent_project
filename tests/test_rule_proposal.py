# -*- coding: utf-8 -*-
"""提案工作流测试：合入器（apply_rule_proposal）+ 起草器（propose_rule_changes）。

覆盖：FAQ 新增编号接续、FAQ 修订、术语追加、行替换、新小节、驳回跳过、
LLM 输出解析与占位降级、提案编号递增。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import apply_rule_proposal as arp  # noqa: E402
import propose_rule_changes as prc  # noqa: E402


DOC = """# 元规则整理（完整版）

## 1. 术语表

### 1.1 牌的类型
| 类型 | 定义 | 具体牌/备注 |
|---|---|---|
| 行动牌 | 杀、闪避、蟠桃、怒气、易 | 已确认 |

### 1.2 动作定义
| 动作 | 定义/要点 | 来源 |
|---|---|---|
| 打出 | 主动从手牌使用 | 卡牌 8 |

## 4. 通用结算原则

### 4.1 打出杀 vs 使用杀（最重要区分）
1. 所有"打出杀"都是打出杀（主动/响应都算）（卡牌 8）

## 5. 常见裁定汇总（FAQ 语料）

### 5.2 武将类裁定（15 条）
| # | 裁定 | 来源 |
|---|---|---|
| 17 | 新获得的技能是全新技能 | 王元姬躬执纺绩 |
| 18 | 增益效果在下个出牌阶段开始时才生效 | 王元姬谦冲接下 |
""".rstrip("\n")


def test_faq_new_appends_with_next_number():
    proposal = {"items": [
        {"id": "P-01", "type": "faq_new", "target": "5.2", "status": "approved",
         "suggested_text": "酒与怒气连用计数独立", "source": "人工确认"}
    ]}
    new_text, applied, errors = arp.apply_proposal(DOC, proposal)
    assert applied == ["P-01"]
    assert errors == []
    assert "| 19 | 酒与怒气连用计数独立 | 人工确认 |" in new_text
    assert proposal["items"][0]["applied_faq_no"] == 19


def test_faq_revise_in_place():
    proposal = {"items": [
        {"id": "P-01", "type": "faq_revise", "target": "faq_017", "status": "revised",
         "suggested_text": "新获得的技能是全新技能（修订）", "source": "人工确认"}
    ]}
    new_text, applied, errors = arp.apply_proposal(DOC, proposal)
    assert applied == ["P-01"]
    assert "| 17 | 新获得的技能是全新技能（修订） | 人工确认 |" in new_text
    assert "王元姬躬执纺绩" not in new_text


def test_term_new_appends_to_table_end():
    proposal = {"items": [
        {"id": "P-01", "type": "term_new", "target": "1.1", "status": "approved",
         "suggested_text": "| 新牌型 | 定义 | 人工确认 |"}
    ]}
    new_text, applied, errors = arp.apply_proposal(DOC, proposal)
    assert applied == ["P-01"]
    assert "| 行动牌 | 杀、闪避、蟠桃、怒气、易 | 已确认 |\n| 新牌型 | 定义 | 人工确认 |" in new_text


def test_row_revise_replaces_exact_line():
    proposal = {"items": [
        {"id": "P-01", "type": "row_revise", "status": "approved",
         "old_text": "| 行动牌 | 杀、闪避、蟠桃、怒气、易 | 已确认 |",
         "suggested_text": "| 行动牌 | 杀、闪避、蟠桃、怒气、易、新 | 已确认 |"}
    ]}
    new_text, applied, errors = arp.apply_proposal(DOC, proposal)
    assert applied == ["P-01"]
    assert "| 行动牌 | 杀、闪避、蟠桃、怒气、易、新 | 已确认 |" in new_text


def test_section_new_appended_to_chapter_end():
    proposal = {"items": [
        {"id": "P-01", "type": "section_new", "target": "4", "status": "approved",
         "suggested_text": "### 4.11 新机制 X\n\n- 新机制描述"}
    ]}
    new_text, applied, errors = arp.apply_proposal(DOC, proposal)
    assert applied == ["P-01"]
    assert "### 4.11 新机制 X" in new_text
    # 插在 4.1 之后、## 5 之前
    assert new_text.index("### 4.11") < new_text.index("## 5.")


def test_rejected_and_pending_skipped():
    proposal = {"items": [
        {"id": "P-01", "type": "faq_new", "target": "5.2", "status": "rejected",
         "suggested_text": "不应合入"},
        {"id": "P-02", "type": "faq_new", "target": "5.2", "status": "pending",
         "suggested_text": "也不应合入"}
    ]}
    new_text, applied, errors = arp.apply_proposal(DOC, proposal)
    assert applied == []
    assert new_text == DOC


def test_unknown_type_reports_error():
    proposal = {"items": [
        {"id": "P-01", "type": "bogus", "status": "approved", "suggested_text": "x"}
    ]}
    _, applied, errors = arp.apply_proposal(DOC, proposal)
    assert applied == []
    assert any("未知类型" in e for e in errors)


def test_next_proposal_id_increments(tmp_path):
    (tmp_path / "CP-2026-08-16-01.json").write_text("{}", encoding="utf-8")
    (tmp_path / "CP-2026-08-16-02.json").write_text("{}", encoding="utf-8")
    pid = prc.next_proposal_id(tmp_path)
    assert pid == "CP-2026-08-16-03"


def test_generate_items_placeholder_without_llm():
    rows = [{"type": "新增", "file": "heroes.json", "object": 1, "name": "东方朔",
             "summary": "新增武将（2 个技能）", "mechanism": "疑似新机制"}]
    items = prc.generate_proposal_items(rows, DOC, None)
    assert items[0]["type"] == "none"
    assert items[0]["status"] == "pending"


class FakeGenerator:
    def _call_api(self, messages, temperature=0.7):
        return {"content": json.dumps({"items": [
            {"type": "faq_new", "target": "5.2", "suggested_text": "新裁定",
             "source": "人工确认", "basis": "b", "suggested_status": "待确认",
             "rationale": "r"}
        ]}, ensure_ascii=False)}


def test_generate_items_parses_llm_output():
    rows = [{"type": "新增", "file": "heroes.json", "object": 1, "name": "X",
             "summary": "s", "mechanism": "m"}]
    items = prc.generate_proposal_items(rows, DOC, FakeGenerator())
    assert items[0]["type"] == "faq_new"
    assert items[0]["target"] == "5.2"
    assert items[0]["status"] == "pending"