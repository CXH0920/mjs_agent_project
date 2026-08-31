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
