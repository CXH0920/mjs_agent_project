# -*- coding: utf-8 -*-
"""audit_summary 数据源加载失败的语义回归。

heroes.json 读失败 ≠ 没有武将：不得拿空集合把全部已归类武将误报为
orphan（分类表引用未知武将），而应跳过武将相关校验并明确提示。
"""
import json
from pathlib import Path

from src.business.rag.audit_service import audit_summary


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _make_root(root: Path, heroes_payload: object) -> Path:
    _write(root / "data" / "heroes.json", heroes_payload)
    # 分类表已归类两名武将：若空集合参与校验，二者都会被误报为 orphan
    _write(root / "data" / "hero_classification.json",
           {"hero_categories": {"曹操": ["控制"], "刘备": ["辅助"]}})
    _write(root / "data" / "special_cards.json", [])
    return root


def test_heroes_unreadable_skips_hero_checks_instead_of_false_orphans(tmp_path: Path) -> None:
    root = _make_root(tmp_path / "proj", [])
    (root / "data" / "heroes.json").write_text("这不是合法 JSON {{{", encoding="utf-8")
    issues = audit_summary(root)

    kinds = [issue.kind for issue in issues]
    assert "heroes_source_unavailable" in kinds  # 明确提示数据源不可用
    assert "orphan_category_key" not in kinds    # 不再全量误报
    assert "unclassified_hero" not in kinds


def test_missing_heroes_file_same_semantics(tmp_path: Path) -> None:
    root = _make_root(tmp_path / "proj", [])
    (root / "data" / "heroes.json").unlink()
    issues = audit_summary(root)

    kinds = [issue.kind for issue in issues]
    assert "heroes_source_unavailable" in kinds
    assert "orphan_category_key" not in kinds


def test_valid_heroes_keeps_normal_checks(tmp_path: Path) -> None:
    root = _make_root(tmp_path / "proj", [{"name": "曹操"}, {"name": "刘备"}])
    # 曹操已归类、刘备未归类
    _write(root / "data" / "hero_classification.json",
           {"hero_categories": {"曹操": ["控制"]}})
    issues = audit_summary(root)

    # 数据正常时不出现数据源告警；未归类校验正常工作
    assert all(issue.kind != "heroes_source_unavailable" for issue in issues)
    unclassified = next(i for i in issues if i.kind == "unclassified_hero")
    assert set(unclassified.target) == {"刘备"}


def test_collect_stale_card_curated_warns_when_changes_missing(tmp_path: Path, caplog) -> None:
    """card_changes.json 缺失 ≠ 没有超期卡牌：检查能力失效必须显式可见"""
    import logging

    from src.business.rag.audit_service import collect_stale_card_curated

    with caplog.at_level(logging.WARNING):
        assert collect_stale_card_curated(tmp_path) == []

    assert any("card_changes.json 不存在" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# TD-A5-001 回归：结构不合规数据降级为提示条目，审计自身不再穿透崩溃
# ---------------------------------------------------------------------------


def test_collect_card_points_bad_rows_degrade_instead_of_crash() -> None:
    """cards 含非对象行/计数非数值：计 bad_row 提示，总张数校验继续。"""
    from src.business.rag.audit_service import collect_card_points

    issues = collect_card_points({
        "cards": [
            "x",  # 非对象行
            {"name": "闪", "suit": "黑桃", "point": "A", "count": "oops"},  # 计数非数值
        ]
    })
    kinds = [item["kind"] for item in issues]
    assert "bad_row" in kinds
    assert "total" in kinds  # 计数按 1 兜底参与合计，件数校验不被坏行中断


def test_collect_equip_attrs_bad_rows_degrade_instead_of_crash() -> None:
    """equips 含非对象行：bad_row 提示，正常行的细分类型校验照常。"""
    from src.business.rag.audit_service import collect_equip_attrs

    issues = collect_equip_attrs(["x", {"name": "测试", "subtype": "异常类型", "distance_mod": "+1"}])
    kinds = [item["kind"] for item in issues]
    assert "bad_row" in kinds
    assert "bad_subtype" in kinds


def test_collect_unclassified_ignores_nameless_hero_entries() -> None:
    """heroes.json 漏 name 的条目（None）不参与排序，未归类校验正常返回。"""
    from src.business.rag.audit_service import collect_unclassified

    assert collect_unclassified(
        {None, "刘备", "曹操"}, {"hero_categories": {}}
    ) == ["刘备", "曹操"]


def test_audit_summary_bad_rows_surface_as_issues(tmp_path: Path) -> None:
    """坏行经 audit_summary 透出为可跳转提示条目（映射链不静默丢弃）。"""
    root = _make_root(tmp_path / "proj", [])
    _write(root / "data" / "card_points.json", {"cards": ["x"]})
    _write(root / "data" / "equip_attrs.json", ["x"])
    issues = audit_summary(root)

    kinds = [issue.kind for issue in issues]
    assert "card_points_bad_row" in kinds
    assert "equip_attrs_bad_row" in kinds
    assert all(issue.target_tab for issue in issues if issue.kind.endswith("bad_row"))


def test_audit_summary_wrong_root_structure_reported(tmp_path: Path) -> None:
    """heroes/specials 根结构不符（可解析但非数组）按数据源不可读提示。"""
    root = _make_root(tmp_path / "proj", [])
    _write(root / "data" / "heroes.json", {"heroes": []})
    _write(root / "data" / "special_cards.json", {"items": []})
    issues = audit_summary(root)

    kinds = [issue.kind for issue in issues]
    assert "heroes_source_unavailable" in kinds
    assert "specials_unreadable" in kinds
    assert "orphan_category_key" not in kinds  # 根结构异常同"读失败"语义：跳过武将校验


def test_audit_summary_self_failure_degrades_to_single_issue(
    tmp_path: Path, monkeypatch, caplog,
) -> None:
    """审计自身缺陷（未预见的异常）降级为单条 audit_self_failed，不向上抛。"""
    import logging

    def _boom(root: Path) -> list[str]:
        raise RuntimeError("审计自身炸了")

    monkeypatch.setattr(
        "src.business.rag.audit_service.collect_timeline_risk_messages", _boom
    )
    with caplog.at_level(logging.ERROR):
        issues = audit_summary(_make_root(tmp_path / "proj", []))

    assert [issue.kind for issue in issues] == ["audit_self_failed"]
    assert "审计自身失败" in issues[0].message
    assert any("审计自身失败" in r.message for r in caplog.records)
