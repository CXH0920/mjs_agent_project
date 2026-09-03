"""元规则 T0 文档机器校验（audit_rule_doc）规则单测（批次3，原零直测）。

各用例共用一份最小合法 T0 文档：先 build_snapshot 建基线，再对文档做
单一变异，断言对应校验规则的告警级别与关键词。
"""

from __future__ import annotations

import json

from src.scripts.audit_rule_doc import audit, build_snapshot, load_snapshot
from src.scripts.audit_rule_doc import write_snapshot

BASE_DOC = [
    "## 1. 对战流程",
    "",
    "### 1.1 回合流程",
    "每个回合依次经历准备、摸牌、出牌、弃牌阶段。",
    "回合结束后进入下一位武将的回合。",
    "",
    "### 1.2 术语表",
    "| # | 说明 | 来源 |",
    "| --- | --- | --- |",
    "| 摸牌 | 从牌库顶获得牌 | 规则书P5 |",
    "",
    "### 1.3 FAQ裁定",
    "| # | 裁定 | 来源 |",
    "| --- | --- | --- |",
    "| 1 | 伤害结算在锦囊之后 | 官方群2026-01 |",
    "| 2 | 装备替换不触发新装备效果 | 官方群2026-02 |",
]


def _write(path, lines):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _baseline(tmp_path, lines=BASE_DOC):
    """写文档并建立基线快照，返回 (doc, snap)。"""
    doc, snap = tmp_path / "doc.md", tmp_path / "snap.json"
    _write(doc, lines)
    write_snapshot(build_snapshot(doc, tmp_path), snap)
    return doc, snap


def _run(doc, snap, tmp_path, **kwargs):
    return audit(doc_path=doc, snapshot_path=snap, root=tmp_path, print_report=False, **kwargs)


def _has(issues, keyword):
    return any(keyword in i["msg"] for i in issues)


def test_clean_doc_against_baseline_has_no_errors(tmp_path) -> None:
    doc, snap = _baseline(tmp_path)

    issues = _run(doc, snap, tmp_path)

    assert [i for i in issues if i["level"] == "ERROR"] == []
    snap_data = load_snapshot(snap)
    assert snap_data["counts"] == {"sections": 4, "terms": 1, "faqs": 2, "pending": 0}
    assert snap_data["faq_ids"] == ["faq_001", "faq_002"]


def test_table_column_mismatch_detected(tmp_path) -> None:
    doc, snap = _baseline(tmp_path)
    lines = BASE_DOC[:5] + ["| 一列 | 两列 |", "| 只有一列 |"] + BASE_DOC[5:]
    _write(doc, lines)

    issues = _run(doc, snap, tmp_path)

    assert _has(issues, "表格列数异常")


def test_end_append_allowed_but_mid_insert_flagged(tmp_path) -> None:
    doc, snap = _baseline(tmp_path)
    appended = BASE_DOC + ["", "### 1.4 追加小节", "末尾追加的内容行。"]
    _write(doc, appended)

    assert [i for i in _run(doc, snap, tmp_path) if i["level"] == "ERROR"] == []

    # 把新小节插到 1.2 与 1.3 之间：前缀不再与快照一致
    mid = BASE_DOC[:10] + ["", "### 1.4 追加小节", "插入中段的内容行。"] + BASE_DOC[10:]
    _write(doc, mid)

    issues = _run(doc, snap, tmp_path)

    assert _has(issues, "中部插入/重排/删除")


def test_faq_deletion_gap_and_duplicate_detected(tmp_path) -> None:
    doc, snap = _baseline(tmp_path)

    deleted = [ln for ln in BASE_DOC if not ln.startswith("| 2 |")]
    _write(doc, deleted)
    assert _has(_run(doc, snap, tmp_path), "FAQ 块被删除")

    gapped = [ln.replace("| 2 |", "| 3 |") for ln in BASE_DOC]
    _write(doc, gapped)
    assert _has(_run(doc, snap, tmp_path), "FAQ 编号跳号/缺失")

    duplicated = [ln.replace("| 2 |", "| 1 |") for ln in BASE_DOC]
    _write(doc, duplicated)
    issues = _run(doc, snap, tmp_path)
    assert _has(issues, "FAQ 编号重复")
    assert _has(issues, "块 ID 重复")


def test_block_append_delete_and_rewrite_rules(tmp_path) -> None:
    doc, snap = _baseline(tmp_path)
    anchor = "每个回合依次经历准备、摸牌、出牌、弃牌阶段。"

    # 追加：原文本逐字保留 → 允许（INFO），不产生 ERROR
    appended = BASE_DOC[:4] + ["新增的补充说明行。"] + BASE_DOC[4:]
    _write(doc, appended)
    issues = _run(doc, snap, tmp_path)
    assert not [i for i in issues if i["level"] == "ERROR"]
    assert _has(issues, "块追加/插入")

    # 删除原行 → 块内容被删除
    removed = [ln for ln in BASE_DOC if ln != anchor]
    _write(doc, removed)
    assert _has(_run(doc, snap, tmp_path), "块内容被删除")

    # 原行改写（非追加）→ 疑似回归
    rewritten = [ln.replace(anchor, anchor + "，但是顺序被改写了。") for ln in BASE_DOC]
    _write(doc, rewritten)
    assert _has(_run(doc, snap, tmp_path), "块内容被改写")


def test_chapter_structure_change_detected(tmp_path) -> None:
    doc, snap = _baseline(tmp_path)
    _write(doc, [ln.replace("## 1. 对战流程", "## 1. 战斗流程") for ln in BASE_DOC])

    issues = _run(doc, snap, tmp_path)

    assert _has(issues, "章节结构变更")


def test_cross_reference_reports_unknown_card_and_hero(tmp_path) -> None:
    doc, snap = _baseline(tmp_path)
    _write(doc, BASE_DOC[:4] + ["来源：卡牌 77 或 武将 虚无客。"] + BASE_DOC[4:])
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "cards.json").write_text('[{"id": 1}]', encoding="utf-8")
    (data_dir / "heroes.json").write_text(
        json.dumps([{"name": "曹操", "skills": [{"name": "奸雄"}]}], ensure_ascii=False),
        encoding="utf-8",
    )

    issues = _run(doc, snap, tmp_path)

    assert _has(issues, "来源引用未知卡牌编号：77")
    assert _has(issues, "疑似引用未知武将")


def test_cross_ref_skipped_when_sources_missing(tmp_path) -> None:
    doc, snap = _baseline(tmp_path)
    _write(doc, BASE_DOC[:4] + ["来源：卡牌 77。"] + BASE_DOC[4:])

    issues = _run(doc, snap, tmp_path)

    # 数据源缺失：跳过交叉引用并出 WARN，而不是把全部引用误报为未知
    assert _has(issues, "交叉引用校验已跳过")
    assert not _has(issues, "未知卡牌编号")


def test_update_snapshot_flag_refreshes_file(tmp_path) -> None:
    doc, snap = _baseline(tmp_path)
    before = load_snapshot(snap)["doc_md5"]
    _write(doc, BASE_DOC[:4] + ["追加了新内容的行。"] + BASE_DOC[4:])

    _run(doc, snap, tmp_path, update_snapshot=True)

    after = load_snapshot(snap)
    assert after["doc_md5"] != before
    assert "追加" in after["blocks"]["rule_section_01_01"]["content"]
