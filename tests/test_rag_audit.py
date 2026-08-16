# -*- coding: utf-8 -*-
"""rag_audit 误报修复回归测试。

覆盖：单字黑名单生效、已收录牌名碎片排除、技能名排除、
排除清单（遁甲天书/遁甲）、hero 字段拆分与泛指跳过、新牌名仍提示。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import rag_audit  # noqa: E402


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    _write(root / "data" / "heroes.json", [
        {"name": "乐广", "skills": [
            {"name": "清谈", "description": "与方天画戟相同，洗入更多的符水，比如符水牌。"},
        ]},
        {"name": "张华", "skills": [
            {"name": "奇门遁甲", "description": "奇门遁甲每回合自动发动。"},
            {"name": "化身", "description": "例如左慈发动遁甲天书获得此技能。"},
            {"name": "夺剑", "description": "若未成功销毁任意1张龙泉剑，获得玄铁剑。"},
        ]},
    ])
    _write(root / "data" / "hero_classification.json",
           {"hero_categories": {"乐广": ["爆发型"], "张华": ["爆发型"]}})
    _write(root / "data" / "cards.json", [{"name": "方天画戟"}, {"name": "玄武盾"}])
    _write(root / "data" / "special_cards.json", [
        {"category": "专属牌", "name": "龙泉剑", "hero": "赵云"},
        {"category": "概念", "name": "击杀", "hero": "白蹄乌、李信、杜预"},
        {"category": "概念", "name": "限定技", "hero": "众多武将"},
    ])
    return root


def test_single_char_blacklist_effective(root: Path) -> None:
    issues = "\n".join(rag_audit.audit_hero_coverage(root))
    assert "与方天画戟" not in issues
    assert "了更多的符" not in issues
    assert "比如符" not in issues


def test_known_name_fragment_excluded(root: Path) -> None:
    issues = "\n".join(rag_audit.audit_hero_coverage(root))
    assert "张龙泉剑" not in issues
    assert "与方天画戟" not in issues


def test_skill_name_excluded(root: Path) -> None:
    issues = "\n".join(rag_audit.audit_hero_coverage(root))
    assert "奇门遁甲" not in issues


def test_non_card_terms_excluded(root: Path) -> None:
    issues = "\n".join(rag_audit.audit_hero_coverage(root))
    assert "遁甲天书" not in issues
    assert "慈发动遁甲" not in issues
    assert "例如遁甲" not in issues


def test_unknown_hero_reported_and_generic_skipped(root: Path) -> None:
    issues = rag_audit.audit_hero_coverage(root)
    assert "special_cards 引用了未知武将: 白蹄乌" in issues
    assert not any("众多武将" in it for it in issues)


def test_new_unknown_card_still_reported(root: Path) -> None:
    issues = "\n".join(rag_audit.audit_hero_coverage(root))
    assert "玄铁剑" in issues


def test_orphan_category_keys_reported(root: Path) -> None:
    """分类表引用 heroes.json 中不存在的武将应被反向校验报出（#10）。"""
    issues = rag_audit.audit_hero_coverage(root)
    assert not any("分类表引用未知武将" in it for it in issues), "无孤儿键时不应误报"
    cls_path = root / "data" / "hero_classification.json"
    data = json.loads(cls_path.read_text(encoding="utf-8"))
    data["hero_categories"]["贾诩(限定)"] = ["爆发型"]
    data["hero_categories"]["赵姬妾→刘弗陵"] = ["辅助型"]
    cls_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    issues = rag_audit.audit_hero_coverage(root)
    text = "\n".join(issues)
    assert "分类表引用未知武将 2 个" in text
    assert "贾诩(限定)" in text
    assert "赵姬妾→刘弗陵" in text
