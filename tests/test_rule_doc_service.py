# -*- coding: utf-8 -*-
"""元规则维护服务测试：audit 解析、sync 差异解析、提案/疑难、FAQ 评估集。"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from src.business.rag import rule_doc_service as rds  # noqa: E402
import eval_rule_faqs as erf  # noqa: E402


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_parse_audit_output():
    text = """[ERROR] L1 解析丢弃
[WARN] 数据段一致性：2 处全自动差异
[INFO] 数据段候选：1 处
汇总：ERROR 1 / WARN 1 / INFO 1"""
    issues = rds.parse_audit_output(text)
    counts = rds.audit_issue_counts(issues)
    assert counts == {"ERROR": 1, "WARN": 1, "INFO": 1}
    assert any(i["level"] == "SUMMARY" for i in issues)


def test_parse_sync_diff(tmp_path):
    payload = [{"section": "0.2", "line_no": 29, "kind": "full",
                "old": "| 武将数 | 171 |", "new": "| 武将数 | 172 |", "message": "m"}]
    path = tmp_path / "diff.json"
    _write(path, payload)
    diffs = rds.parse_sync_diff(path)
    assert diffs[0]["section"] == "0.2"
    assert diffs[0]["kind"] == "full"


def test_list_proposals(tmp_path):
    d = tmp_path / "docs" / "archive" / "proposals"
    _write(d / "CP-2026-08-16-01.json",
           {"proposal_id": "CP-2026-08-16-01", "created_at": "2026-08-16",
            "items": [{"id": "P-01", "status": "approved"}, {"id": "P-02", "status": "rejected"}]})
    props = rds.list_proposals(tmp_path)
    assert len(props) == 1
    assert props[0]["approved"] == 1
    assert props[0]["rejected"] == 1


def test_confirmed_diff_path(tmp_path):
    assert rds.confirmed_diff_path(tmp_path) == tmp_path / "scripts" / ".sync_confirmed_diffs.json"


def _write_proposal(tmp_path) -> Path:
    path = tmp_path / "docs" / "archive" / "proposals" / "CP-2026-08-16-01.json"
    _write(path, {
        "proposal_id": "CP-2026-08-16-01",
        "items": [
            {"id": "P-01", "type": "faq_new", "target": "5.2", "suggested_text": "原文本",
             "status": "pending", "edited_text": None},
        ],
    })
    return path


def test_update_proposal_item_statuses(tmp_path):
    path = _write_proposal(tmp_path)
    rds.update_proposal_item(tmp_path, str(path), "P-01", "approved")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["items"][0]["status"] == "approved"
    # revised + edited_text
    rds.update_proposal_item(tmp_path, str(path), "P-01", "revised", "修改后文本")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["items"][0]["status"] == "revised"
    assert data["items"][0]["edited_text"] == "修改后文本"
    # rejected
    rds.update_proposal_item(tmp_path, str(path), "P-01", "rejected")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["items"][0]["status"] == "rejected"


def test_update_proposal_item_rejects_invalid(tmp_path):
    path = _write_proposal(tmp_path)
    before = path.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        rds.update_proposal_item(tmp_path, str(path), "P-01", "approved-typo")
    with pytest.raises(ValueError):
        rds.update_proposal_item(tmp_path, str(path), "P-99", "approved")
    assert path.read_text(encoding="utf-8") == before  # 拒绝时文件不变


def test_update_proposal_item_no_tmp_residue(tmp_path):
    path = _write_proposal(tmp_path)
    rds.update_proposal_item(tmp_path, str(path), "P-01", "approved")
    assert not path.with_name(path.name + ".tmp").exists()


def test_add_pending_and_to_proposal(tmp_path):
    rds.add_pending(tmp_path, "组合结算盲点", "张华+主父偃", "实战")
    items = rds.load_pending(tmp_path)
    assert items[0]["description"] == "组合结算盲点"
    path = rds.pending_to_proposal(tmp_path, items[0]["id"])
    proposal = json.loads(Path(path).read_text(encoding="utf-8"))
    assert proposal["items"][0]["type"] == "faq_new"
    assert proposal["items"][0]["suggested_text"] == "组合结算盲点"
    assert rds.load_pending(tmp_path)[0]["status"] == "proposed"


def test_parse_doc_chapter7(tmp_path):
    doc = tmp_path / "元规则整理-完整版.md"
    _write(doc, """## 7. 待确认与疑难登记

### 7.1 疑难登记
| 登记日期 | 疑难描述 | 涉及技能/卡牌 | 来源 | 状态 |
|---|---|---|---|---|
| 2026-08-16 | 组合盲点 | 张华 | 实战 | 进行中 |
""")
    rows = rds.parse_doc_chapter7(doc)
    assert rows[0]["description"] == "组合盲点"
    assert rows[0]["status"] == "进行中"


def _write_doc(tmp_path) -> Path:
    doc = tmp_path / "元规则整理-完整版.md"
    _write(doc, """### 5.2 武将类裁定（15 条）
| # | 裁定 | 来源 |
|---|---|---|
| 60 | 武器攻击范围：龙舌弓5/惊羽弓5 | 装备属性表 |
| 61 | 限定技=本局限1次，描述以"限定，"开头（31个技能） | 数据统计 |
| 62 | 特殊机制（专属牌22/战法牌20） | 数据统计 |
""")
    return doc


def test_doc_target_line_faq(tmp_path):
    doc = _write_doc(tmp_path)
    item = {"type": "faq_revise", "target": "faq_61", "suggested_text": "x"}
    assert rds.doc_target_line(doc, item).startswith("| 61 |")


def test_doc_target_line_row_revise(tmp_path):
    doc = _write_doc(tmp_path)
    old = "| 61 | 限定技=本局限1次，描述以\"限定，\"开头（31个技能） | 数据统计 |"
    item = {"type": "row_revise", "target": "5.2", "old_text": old}
    assert rds.doc_target_line(doc, item) == old


def test_doc_target_line_not_found(tmp_path):
    doc = _write_doc(tmp_path)
    assert rds.doc_target_line(doc, {"type": "faq_revise", "target": "faq_999"}) is None
    assert rds.doc_target_line(tmp_path / "不存在.md", {"type": "faq_revise", "target": "faq_61"}) is None


def test_doc_section_context(tmp_path):
    doc = _write_doc(tmp_path)
    ctx = rds.doc_section_context(doc, "faq_61")
    assert ctx is not None
    assert "| 60 |" in ctx and "| 62 |" in ctx  # 目标行 ± 上下文
    ctx_sec = rds.doc_section_context(doc, "5.2")
    assert "### 5.2" in ctx_sec
    assert rds.doc_section_context(doc, "9.9") is None


def test_doc_line_at(tmp_path):
    doc = _write_doc(tmp_path)
    assert rds.doc_line_at(doc, 3).startswith("| 60 |")
    assert rds.doc_line_at(doc, 999) is None
    assert rds.doc_line_at(tmp_path / "不存在.md", 0) is None


def test_doc_context_around(tmp_path):
    doc = _write_doc(tmp_path)
    ctx = rds.doc_context_around(doc, 3)
    assert "| 60 |" in ctx and "| 62 |" in ctx
    assert rds.doc_context_around(doc, 0).startswith("### 5.2")  # 越界自动裁剪
    assert rds.doc_context_around(doc, 999) is None


class FakeRetriever:
    def _vector_search(self, query, where=None, n=30):
        if "只有打出的杀" in query:
            return [{"block_id": "faq_001"}, {"block_id": "faq_999"}]
        return [{"block_id": "faq_002"}, {"block_id": "faq_999"}]


def test_eval_generate_and_run(tmp_path):
    faq_path = tmp_path / "FAQ裁定块.json"
    _write(faq_path, [
        {"block_id": "faq_001", "faq_no": 1, "ruling": "只有打出的杀有目标才执行伤害"},
        {"block_id": "faq_002", "faq_no": 2, "ruling": "响应效果的杀不造成伤害"},
    ])
    ds_path = tmp_path / "rule_faq_eval.json"
    ds = erf.generate_dataset(faq_path, ds_path, version="t")
    assert len(ds["items"]) == 2
    report = erf.run_eval(ds, FakeRetriever(), top_k=2)
    assert report["passed"] == 2
    assert report["hit_rate"] == 1.0