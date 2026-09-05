# -*- coding: utf-8 -*-
"""sync_rule_stats 数据快照段同步测试。

覆盖：0.1/0.2 统计生成、5.2 数据类 FAQ 行、diff 检出过期数字、
apply 原位替换且标题不变、候选段未确认不可应用。
"""
from pathlib import Path

import pytest
from src.scripts import sync_rule_stats as srs  # noqa: E402


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


@pytest.fixture
def doc_text() -> str:
    return """# 元规则整理（完整版）

## 0. 游戏体系总览

### 0.1 卡牌体系
| 类型 | 数量 | 内容 |
|---|---|---|
| 行动牌 | 7 | 杀（冲杀14/火杀27/雷杀4）、闪避22、蟠桃12、怒气9、易2 |
| 战法牌 | 1 | 烽火狼烟3 |
| 装备牌 | 1 | 武器/防具/盔/坐骑，各 1 张 |

### 0.2 武将体系
| 项 | 数据 |
|---|---|
| 武将数 | 171（每武将 2~3 个技能，共 418 个；95 人 2 技能 / 76 人 3 技能） |
| 阵营 | 17 种：西汉30、曹魏17、秦17、蜀汉16、孙吴16、东汉13、西晋12、赵9、西楚8、燕7、魏6、韩5、楚5、齐5、黄巾3、张楚1、西周1 |
| 定位 | 6 种：控制59、攻击46、辅助34、爆发16、防御13、治疗3 |
| 体力（max_hp） | 4(89)、6(54)、5(19)、3(3)、9(2)、7(2)、2(1)、1(1) |
| 手牌上限（max_hand） | 4(57)、3(54)、5(52)、2(5)、6(2)、1(1) |

## 3. 时机系统

### 3.1 阶段类时机（按出现频次）
| 时机 | 频次 | 备注 |
|---|---|---|
| 出牌阶段 | 194 | 含开始时/结束时/限1次 |

### 3.2 触发类时机（高频）
| 时机 | 频次 | 备注 |
|---|---|---|
| 你打出牌时 | 13 | |

### 3.5 次数限制体系
| 限制 | 含义 [推断] | 示例 |
|---|---|---|
| 限定（本局限1次） | 技能描述以"限定，"开头，共31个技能（荀彧/曹丕等） | 数据统计 |
| 子效果限次 | 部分效果仅限1次：每种牌限1次(5处)、每名角色每回合限1次(1处)、首次类(17处)、累计阈值一次性(21处) | 数据统计 |

## 5. 常见裁定汇总（FAQ 语料）

### 5.2 武将类裁定（15 条）
| # | 裁定 | 来源 |
|---|---|---|
| 46 | 火杀全部为♥（27张），♥=火属性（点数表确认） | 点数表 |
| 49 | 牌堆构成：闪避♠22、蟠桃♣12、怒气♣9、冲杀♦14、火杀♥27、雷杀♦4、易太极2 | 点数表 |
| 60 | 武器攻击范围：龙舌弓5/惊羽弓5/方天画戟4/羽扇4/亮银枪3/丈八蛇矛3/青龙偃月刀3/开山斧3/干将莫邪2/鸣鸿刀2/轩辕剑2/诸葛连弩1 | 装备属性表 |
| 61 | 限定技=本局限1次，描述以"限定，"开头（31个技能）；部分效果仅限1次（每种牌限1次/首次类/累计阈值） | 数据统计 |
"""


@pytest.fixture
def data() -> dict:
    return {
        "cards": [
            {"id": "4", "name": "易", "card_type": "行动牌", "card_amount": "2"},
            {"id": "8", "name": "冲杀", "card_type": "行动牌", "card_amount": "14"},
            {"id": "48", "name": "火杀", "card_type": "行动牌", "card_amount": "27"},
            {"id": "49", "name": "雷杀", "card_type": "行动牌", "card_amount": "4"},
            {"id": "50", "name": "怒气", "card_type": "行动牌", "card_amount": "9"},
            {"id": "57", "name": "蟠桃", "card_type": "行动牌", "card_amount": "12"},
            {"id": "58", "name": "闪避", "card_type": "行动牌", "card_amount": "22"},
            {"id": "1", "name": "烽火狼烟", "card_type": "战法牌", "card_amount": "3"},
            {"id": "22", "name": "轩辕剑", "card_type": "装备牌", "card_amount": "1"},
        ],
        "heroes": [
            {"name": "甲", "faction": "西汉", "position": "控制", "max_hp": 4, "max_hand": 4,
             "skills": [{"name": "s1", "description": "出牌阶段限一次。"}]},
            {"name": "乙", "faction": "曹魏", "position": "攻击", "max_hp": 6, "max_hand": 3,
             "skills": [{"name": "s2", "description": "回合开始时，摸1张牌。"},
                        {"name": "s3", "description": "限定，出牌阶段，你可以..."}]},
            {"name": "丙", "faction": "西汉", "position": "控制", "max_hp": 4, "max_hand": 4,
             "skills": [{"name": "s4", "description": "弃牌阶段结束时..."},
                        {"name": "s5", "description": "回合结束时..."},
                        {"name": "s6", "description": "每种牌限1次。"}]},
        ],
        "card_points": {"cards": [
            {"name": "火杀", "suit": "♥", "point": "1", "count": 27},
            {"name": "雷杀", "suit": "♦", "point": "4", "count": 4},
            {"name": "冲杀", "suit": "♦", "point": "1", "count": 14},
            {"name": "闪避", "suit": "♠", "point": "2", "count": 22},
            {"name": "蟠桃", "suit": "♣", "point": "1", "count": 12},
            {"name": "怒气", "suit": "♣", "point": "3", "count": 9},
            {"name": "易", "suit": "太极", "point": "1", "count": 1},
            {"name": "易", "suit": "太极", "point": "8", "count": 1},
        ]},
        "equip_attrs": [
            {"name": "赤兔", "subtype": "坐骑", "distance_mod": -1},
            {"name": "绝影", "subtype": "坐骑", "distance_mod": 1},
            {"name": "龙舌弓", "subtype": "武器", "attack_range": 5},
            {"name": "诸葛连弩", "subtype": "武器", "attack_range": 1},
        ],
        "card_annotations": {"annotations": [{"card_id": "1"}, {"card_id": "2"}]},
        "special_cards": [
            {"category": "专属牌", "name": "龙泉剑"},
            {"category": "状态/标记", "name": "流血"},
        ],
    }


def test_gen_card_system_rows(data):
    rows = srs.gen_card_system_rows(data["cards"])
    assert rows[0] == "| 行动牌 | 7 | 杀（冲杀14/火杀27/雷杀4）、闪避22、蟠桃12、怒气9、易2 |"
    assert rows[1].startswith("| 战法牌 | 1 | 烽火狼烟3 |")
    assert rows[2] == "| 装备牌 | 1 | 武器/防具/盔/坐骑，各 1 张 |"


def test_gen_hero_stats_rows_order(data):
    rows = srs.gen_hero_stats_rows(data["heroes"])
    assert rows[0] == "| 武将数 | 3（每武将 2~3 个技能，共 6 个；1 人 1 技能 / 1 人 2 技能 / 1 人 3 技能） |"
    assert "西汉2、曹魏1" in rows[1]
    # 同计数时数值降序（4 在 6 前按人数）
    assert rows[3] == "| 体力（max_hp） | 4(2)、6(1) |"
    assert rows[4] == "| 手牌上限（max_hand） | 4(2)、3(1) |"


def test_diff_detects_stale_numbers(doc_text, data):
    issues = srs.diff_sections(doc_text, data)
    kinds = {i["section"]: i["kind"] for i in issues}
    assert kinds.get("0.2") == "full"
    assert kinds.get("3.1") == "candidate"
    sec35 = [i for i in issues if i["section"] == "3.5"]
    assert any(i["kind"] == "full" for i in sec35)  # 限定技计数差异
    assert kinds.get("5.2") == "full"
    # 0.1 与数据一致，无差异
    assert not any(i["section"] == "0.1" for i in issues)


def test_apply_updates_in_place_keeps_headings(doc_text, data):
    new_text, applied = srs.apply_diffs(doc_text, data)
    assert applied
    assert "### 0.1 卡牌体系" in new_text
    assert "### 0.2 武将体系" in new_text
    assert "| 武将数 | 3（每武将 2~3 个技能，共 6 个；1 人 1 技能 / 1 人 2 技能 / 1 人 3 技能） |" in new_text
    # 候选段未确认不应用
    assert "| 出牌阶段 | 194 |" in new_text


def test_apply_candidates_requires_flag(doc_text, data):
    _, applied = srs.apply_diffs(doc_text, data)
    assert not any(i["section"] in ("3.1", "3.2") for i in applied)
    new_text2, applied2 = srs.apply_diffs(doc_text, data, apply_candidates=True)
    assert any(i["section"] == "3.1" for i in applied2)
    assert "| 出牌阶段 | 2 |" in new_text2


def test_faq_rows_generated_from_data(data):
    rows = srs.gen_faq_rows(data)
    assert rows["46"] == "| 46 | 火杀全部为♥（27张），♥=火属性（点数表确认） | 点数表 |"
    assert rows["49"] == "| 49 | 牌堆构成：闪避♠22、蟠桃♣12、怒气♣9、冲杀♦14、火杀♥27、雷杀♦4、易太极2 | 点数表 |"
    assert "（1个技能）" in rows["61"]
    assert "状态标记1" in rows["62"]


# ---------------------------------------------------------------------------
# apply_confirmed（B2 确认清单逐行替换）
# ---------------------------------------------------------------------------

def test_apply_confirmed_all_ok(doc_text):
    line_no = doc_text.splitlines().index("| 武将数 | 171（每武将 2~3 个技能，共 418 个；95 人 2 技能 / 76 人 3 技能） |")
    confirmed = [{
        "section": "0.2", "line_no": line_no,
        "old": doc_text.splitlines()[line_no],
        "new": "| 武将数 | 172（每武将 2~3 个技能，共 421 个；95 人 2 技能 / 77 人 3 技能） |",
        "message": "m",
    }]
    new_text, applied, errors = srs.apply_confirmed(confirmed, doc_text)
    assert errors == []
    assert len(applied) == 1
    assert "| 武将数 | 172（每武将 2~3 个技能" in new_text
    assert "### 0.2 武将体系" in new_text  # 标题不变


def test_apply_confirmed_old_mismatch_rejects_all(doc_text):
    line_no = doc_text.splitlines().index("| 武将数 | 171（每武将 2~3 个技能，共 418 个；95 人 2 技能 / 76 人 3 技能） |")
    confirmed = [{
        "section": "0.2", "line_no": line_no,
        "old": "| 武将数 | 999（已被其他途径修改） |",
        "new": "| 武将数 | 172 |",
        "message": "m",
    }]
    new_text, applied, errors = srs.apply_confirmed(confirmed, doc_text)
    assert applied == []
    assert len(errors) == 1
    assert "不一致" in errors[0]
    assert new_text == doc_text  # 文档零改动


def test_apply_confirmed_line_out_of_range(doc_text):
    confirmed = [{"section": "0.2", "line_no": 9999, "old": None, "new": "x", "message": "m"}]
    new_text, applied, errors = srs.apply_confirmed(confirmed, doc_text)
    assert applied == []
    assert "越界" in errors[0]
    assert new_text == doc_text


def test_apply_confirmed_bad_row_shape_rejected(doc_text):
    line_no = doc_text.splitlines().index("| 武将数 | 171（每武将 2~3 个技能，共 418 个；95 人 2 技能 / 76 人 3 技能） |")
    # 非表格行（缺 | 起止）
    confirmed = [{
        "section": "0.2", "line_no": line_no,
        "old": doc_text.splitlines()[line_no],
        "new": "武将数 172",
        "message": "m",
    }]
    new_text, applied, errors = srs.apply_confirmed(confirmed, doc_text)
    assert applied == []
    assert "不是完整表格行" in errors[0]
    assert new_text == doc_text
    # 列数不一致（多一列且完整表格行）
    confirmed[0]["new"] = "| 武将数 | 172 | 多余列 |"
    _, applied, errors = srs.apply_confirmed(confirmed, doc_text)
    assert applied == []
    assert "列数与原文不一致" in errors[0]


def test_apply_confirmed_mixed_rejects_all(doc_text):
    lines = doc_text.splitlines()
    ok_line = lines.index("| 武将数 | 171（每武将 2~3 个技能，共 418 个；95 人 2 技能 / 76 人 3 技能） |")
    confirmed = [
        {"section": "0.2", "line_no": ok_line, "old": lines[ok_line],
         "new": "| 武将数 | 172 |", "message": "m"},
        {"section": "3.1", "line_no": 9999, "old": None, "new": "x", "message": "m"},
    ]
    new_text, applied, errors = srs.apply_confirmed(confirmed, doc_text)
    # 有一条失败 → 整批拒绝，成功行也不写
    assert applied == []
    assert len(errors) == 1
    assert new_text == doc_text