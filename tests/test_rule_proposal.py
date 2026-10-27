# -*- coding: utf-8 -*-
"""提案工作流测试：合入器（apply_rule_proposal）+ 起草器（propose_rule_changes）。

覆盖：FAQ 新增编号接续、FAQ 修订、术语追加、行替换、新小节、驳回跳过、
LLM 输出解析与占位降级、提案编号递增；main 自动链的 -m 子进程形态与
母本原子写（scripts→src/scripts 整合后路径式子进程调用已失效）。
"""
import json
from types import SimpleNamespace

import pytest
from src.scripts import apply_rule_proposal as arp  # noqa: E402
from src.scripts import propose_rule_changes as prc  # noqa: E402

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
    from datetime import date
    today = date.today().isoformat()
    (tmp_path / f"CP-{today}-01.json").write_text("{}", encoding="utf-8")
    (tmp_path / f"CP-{today}-02.json").write_text("{}", encoding="utf-8")
    pid = prc.next_proposal_id(tmp_path)
    assert pid == f"CP-{today}-03"


def test_generate_items_placeholder_without_llm():
    rows = [{"type": "新增", "file": "heroes.json", "object": 1, "name": "东方朔",
             "summary": "新增武将（2 个技能）", "mechanism": "疑似新机制"}]
    items = prc.generate_proposal_items(rows, DOC, None)
    assert items[0]["type"] == "none"
    assert items[0]["status"] == "pending"


class FakeGenerator:
    def complete(self, messages, temperature=0.7):
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


# ---------------------------------------------------------------------------
# main 自动链：子进程形态（P1-1）与母本原子写（P1-2）
# ---------------------------------------------------------------------------

def _approved_proposal(tmp_path):
    proposal = tmp_path / "CP-test.json"
    proposal.write_text(json.dumps({"proposal_id": "CP-test", "items": [
        {"id": "P-01", "type": "faq_new", "target": "5.2", "status": "approved",
         "suggested_text": "自动链测试条目", "source": "人工确认"}
    ]}, ensure_ascii=False), encoding="utf-8")
    return proposal


def test_auto_chain_subprocess_uses_dash_m(monkeypatch, tmp_path):
    """自动链子进程必须为 `-m src.scripts.*` 模块形态。

    根级 scripts/ 已随 scripts→src/scripts 整合（1586377）移除，路径式调用
    会静默执行不存在的脚本（returncode=2）→ audit 永远"失败"→ 合入被回滚。
    """
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(arp.subprocess, "run", fake_run)
    ok, _ = arp.run_audit_strict(str(tmp_path / "doc.md"))
    assert ok is True
    assert captured["cmd"][1:3] == ["-m", "src.scripts.audit_rule_doc"]

    ok, _ = arp.run_maintain_rules()
    assert ok is True
    assert captured["cmd"][1:3] == ["-m", "src.scripts.maintain_rag"]


def test_run_audit_strict_spawns_real_module(tmp_path):
    """audit 子进程端到端必须真实调起模块并跑到报告输出，而非执行不存在的脚本。

    负向断言只认 "can't open file"：No such file or directory 会出现在
    CI 无数据源时交叉引用降级的合法告警文本里（data/*.json 经私有仓同步，
    CI 不存在），不能作"脚本不存在"的判据。
    """
    doc = tmp_path / "mini.md"
    doc.write_text("# 最小文档\n\n## 1. 测试\n\n内容\n", encoding="utf-8")
    _ok, out = arp.run_audit_strict(str(doc))
    assert "can't open file" not in out
    assert "元规则文档校验" in out  # audit 报告固定标题：子进程真实跑到了报告输出


def _spy_atomic_write(monkeypatch):
    """包装 atomic_write_text 记录调用路径（修复前该函数不存在于 arp，落回 no-op
    使 spy 断言失败复现，同时不阻断裸写路径的行为断言）。"""
    calls = []
    real = getattr(arp, "atomic_write_text", None)

    def spy(path, content):
        if real is not None:
            real(path, content)
        calls.append(str(path))

    monkeypatch.setattr(arp, "atomic_write_text", spy, raising=False)
    return calls


def test_main_merge_writes_doc_via_atomic_write(monkeypatch, tmp_path):
    """main 写回母本必须走 atomic_write_text（母本写保护约束，见 sync_rule_stats）。"""
    doc = tmp_path / "doc.md"
    doc.write_text(DOC, encoding="utf-8")
    monkeypatch.setattr(arp.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(arp, "append_changelog", lambda *a, **k: True)
    monkeypatch.setattr(arp, "archive_proposal", lambda *a: None)
    calls = _spy_atomic_write(monkeypatch)
    monkeypatch.setattr("sys.argv", ["apply_rule_proposal", "--proposal",
                                     str(_approved_proposal(tmp_path)),
                                     "--doc", str(doc), "--skip-maintain"])
    arp.main()

    assert "| 19 | 自动链测试条目 | 人工确认 |" in doc.read_text(encoding="utf-8")
    assert str(doc) in calls


def test_main_rolls_back_doc_atomically_when_audit_fails(monkeypatch, tmp_path):
    """audit 失败必须回滚母本原文，且回滚同样走原子写。"""
    doc = tmp_path / "doc.md"
    doc.write_text(DOC, encoding="utf-8")
    monkeypatch.setattr(arp, "run_audit_strict", lambda _p: (False, "模拟校验失败"))
    monkeypatch.setattr(arp, "append_changelog", lambda *a, **k: True)
    monkeypatch.setattr(arp, "archive_proposal", lambda *a: None)
    calls = _spy_atomic_write(monkeypatch)
    monkeypatch.setattr("sys.argv", ["apply_rule_proposal", "--proposal",
                                     str(_approved_proposal(tmp_path)),
                                     "--doc", str(doc), "--skip-maintain"])
    with pytest.raises(SystemExit) as exc:
        arp.main()

    assert exc.value.code == 1
    assert doc.read_text(encoding="utf-8") == DOC
    assert str(doc) in calls


def test_append_changelog_writes_atomically_and_appends(monkeypatch, tmp_path):
    """changelog 追加只增不删且必须原子写（对齐 sync_rule_stats.append_changelog）。"""
    changelog = tmp_path / "changelog.md"
    changelog.write_text(
        "| 日期 | 段 | 动作 | 摘要 | 来源 | 状态 |\n|---|---|---|---|---|---|\n",
        encoding="utf-8")
    calls = _spy_atomic_write(monkeypatch)

    merged = arp.append_changelog({"proposal_id": "CP-test"}, ["P-01"], [],
                                  changelog_path=str(changelog))

    assert merged is not False  # applied 非空即走过追加路径（返回值本身无人消费）
    text = changelog.read_text(encoding="utf-8")
    assert "| 日期 |" in text and "CP-test" in text and "P-01" in text
    assert calls == [str(changelog)]