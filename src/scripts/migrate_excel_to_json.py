# -*- coding: utf-8 -*-
"""一次性迁移：data/mjs卡牌点数.xlsx → card_points.json / equip_attrs.json / special_cards.json 回填。

背景：xlsx 是人工登记的唯一权威源，但程序此前只读 sheet1，且 sheet2/sheet3 无任何代码消费。
本脚本把 xlsx 三个 sheet 全部落地为 JSON 源数据，此后 JSON 为唯一维护源，xlsx 归档 data/archive/。

用法（幂等，可作"从 Excel 重新导入"应急通道）：
    python -m src.scripts.migrate_excel_to_json                    # 迁移全部
    python -m src.scripts.migrate_excel_to_json --only points equips  # 只迁移指定部分（UI 导入按钮用）
"""
import argparse
import io
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from src.config.env import PROJECT_ROOT as ROOT
DATA = os.path.join(ROOT, "data")
# 归档后的 xlsx 作为重新导入的来源；仍存在于原位置时优先使用原位置
XLSX = os.path.join(DATA, "mjs卡牌点数.xlsx")
if not os.path.exists(XLSX):
    XLSX = os.path.join(DATA, "archive", "mjs卡牌点数.xlsx")

from src.data.json_repository import atomic_write_json  # noqa: E402

# 卜卦判定规则：原硬编码于 build_cardpts.py attr_judge()，现抽为数据（12 条）
JUDGE_RULES = [
    ("火杀", "火焰伤害（♥=火属性）"),
    ("雷杀", "雷电伤害（点数4且花色♦）"),
    ("冲杀", "普通伤害（♦非点数4）"),
    ("闪避", "响应杀（抵消杀）"),
    ("蟠桃", "回复体力"),
    ("怒气", "增益/回复（重伤时）"),
    ("易", "太极花色，可当作任意行动牌"),
    ("八卦盾", "判定：♣→回复1体力；♠→抵消此杀（强命杀下抵消无效；♣回复先生效）"),
    ("霜冻", "判定：♠/♦→本回合无法选择其他角色为目标（自己与无目标牌不受限）"),
    ("久旱", "判定：♥/♣→本回合跳过摸牌阶段（跳过则阶段内技能不触发）"),
    ("天雷", "判定：点数4→4点雷电伤害（无伤害来源）"),
    ("地火", "判定：点数3→3点火焰伤害（无伤害来源）"),
]

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _read_sheet(sheetfile):
    """读取 xlsx 工作表为 {列名: 文本} 字典列表（保留行序）。"""
    with zipfile.ZipFile(XLSX) as z:
        shared = []
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(_NS + "si"):
            shared.append("".join(t.text or "" for t in si.iter(_NS + "t")))
        rows = []
        for row in ET.fromstring(z.read(sheetfile)).iter(_NS + "row"):
            cells = {}
            for c in row.findall(_NS + "c"):
                col = re.match(r"([A-Z]+)", c.get("r")).group(1)
                t = c.get("t")
                v = c.find(_NS + "v")
                val = v.text if v is not None else ""
                if t == "s":
                    val = shared[int(val)] if val != "" else ""
                cells[col] = val
            if cells:
                rows.append(cells)
    return rows


def _atomic_write(path, payload):
    """原子写（mkstemp + fsync + replace，失败清理临时文件）。"""
    atomic_write_json(path, payload, indent=2)


def migrate_card_points():
    """sheet1（162 张牌明细）→ data/card_points.json（按 牌名/花色/点数 聚合计数）。

    xlsx sheet1 逐张记录（同名同花同点可有多行），聚合为唯一组合 + count。
    """
    rows = _read_sheet("xl/worksheets/sheet1.xml")
    problems = []
    suit_counter = {}
    combo = {}
    for r in rows[1:]:  # 跳过表头
        name = r.get("A", "").strip()
        if not name:
            continue
        suit = r.get("B", "").strip()
        point = r.get("C", "").strip()
        suit_counter[suit] = suit_counter.get(suit, 0) + 1
        if suit not in ("♥", "♣", "♠", "♦", "太极"):
            problems.append(f"异常花色: {name}={suit!r}")
        if point not in tuple(str(i) for i in range(1, 9)):
            problems.append(f"异常点数: {name}={point!r}")
        combo[(name, suit, point)] = combo.get((name, suit, point), 0) + 1
    cards = [{"name": n, "suit": s, "point": p, "count": c}
             for (n, s, p), c in sorted(combo.items())]
    total = sum(c["count"] for c in cards)
    if total != 162:
        problems.append(f"牌数 {total} != 期望 162")
    _atomic_write(os.path.join(DATA, "card_points.json"),
                  {"cards": cards, "judge_rules": [{"name": n, "rule": r} for n, r in JUDGE_RULES]})
    print(f"[card_points.json] 唯一组合 {len(cards)} 个（共 {total} 张），花色分布 {dict(sorted(suit_counter.items()))}")
    for p in problems:
        print("  ! " + p)


def migrate_equip_attrs():
    """sheet2（26 件装备）→ data/equip_attrs.json（结构化 + 原文 note）。"""
    rows = _read_sheet("xl/worksheets/sheet2.xml")
    equips = []
    problems = []
    for r in rows[1:]:
        name = r.get("A", "").strip()
        if not name:
            continue
        note = r.get("C", "").strip()
        m = re.search(r"范围(\d+)", note)
        attack_range = int(m.group(1)) if m else None
        distance_mod = -1 if "距离-1" in note else (1 if "距离+1" in note else None)
        if "距离" in note and distance_mod is None:
            problems.append(f"未识别距离修正: {name}={note!r}")
        equips.append({"name": name, "subtype": r.get("B", "").strip(),
                       "attack_range": attack_range, "distance_mod": distance_mod, "note": note})
    if len(equips) != 26:
        problems.append(f"装备数 {len(equips)} != 期望 26")
    _atomic_write(os.path.join(DATA, "equip_attrs.json"), equips)
    print(f"[equip_attrs.json] 件数 {len(equips)}")
    for p in problems:
        print("  ! " + p)


def migrate_special_cards():
    """sheet3（42 条专属牌/战法牌结算）→ 回填 special_cards.json 并补入顺手牵羊。

    不回写已有人工润色字段（effect/hero 等），仅追加 xlsx 独有字段
    suit/point/attack_range/settlement；「死士」为非实体牌标记，xlsx 无对应行，豁免保留。
    """
    rows = _read_sheet("xl/worksheets/sheet3.xml")
    x3 = {r.get("A", "").strip(): r for r in rows[1:] if r.get("A", "").strip()}
    path = os.path.join(DATA, "special_cards.json")
    with open(path, encoding="utf-8") as f:
        specials = json.load(f)
    problems = []
    backed = 0
    missing = []
    for it in specials:
        if it.get("category") not in ("专属牌", "专属战法牌"):
            continue
        row = x3.get(it["name"])
        if row is None:
            missing.append(it["name"])
            continue
        for key, col in (("suit", "F"), ("point", "G"), ("attack_range", "D"), ("settlement", "I")):
            val = row.get(col, "").strip()
            if val:
                it[key] = val
        backed += 1
    if missing:
        problems.append("xlsx 无对应行（保留原条目）: " + "、".join(missing))
    if "顺手牵羊" not in {it["name"] for it in specials if it.get("category") == "专属战法牌"}:
        row = x3.get("顺手牵羊")
        if row is None:
            problems.append("xlsx 中找不到「顺手牵羊」，未补入")
        else:
            specials.append({
                "category": "专属战法牌",
                "name": "顺手牵羊",
                "effect": row.get("H", "").strip(),
                "hero": "孟尝君",  # 鸡鸣狗盗可转化顺手牵羊（heroes.json）
                "suit": row.get("F", "").strip(),
                "point": row.get("G", "").strip(),
                "settlement": row.get("I", "").strip(),
            })
            print("[special_cards.json] 补入顺手牵羊（专属战法牌 ♠5，归属孟尝君）")
    # 排除清单核对：xlsx 独有/缺失条目（死士为有意豁免）
    json_names = {it["name"] for it in specials if it.get("category") in ("专属牌", "专属战法牌")}
    only_xlsx = sorted(set(x3) - json_names)
    only_json = sorted(json_names - set(x3))
    if only_xlsx:
        print("  ! xlsx 有而 json 无: " + "、".join(only_xlsx))
    if only_json:
        print("  豁免说明（json 有而 xlsx 无）: " + "、".join(only_json))
    _atomic_write(path, specials)
    print(f"[special_cards.json] 回填 {backed} 条（专属牌/专属战法牌共 {len(json_names)} 个名称）")
    for p in problems:
        print("  ! " + p)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="从 mjs卡牌点数.xlsx（含归档）重新导入 JSON 源数据")
    parser.add_argument("--only", nargs="+", choices=["points", "equips", "special"],
                        help="只迁移指定部分：points=卡牌点数, equips=装备属性, special=专属牌回填")
    args = parser.parse_args()
    parts = args.only or ["points", "equips", "special"]
    if not os.path.exists(XLSX):
        sys.exit(f"未找到 {XLSX}，请确认 xlsx 未删除。")
    if "points" in parts:
        migrate_card_points()
    if "equips" in parts:
        migrate_equip_attrs()
    if "special" in parts:
        migrate_special_cards()
    print("迁移完成。")
