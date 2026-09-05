# -*- coding: utf-8 -*-
"""生成卡牌 RAG 语料：49 块，含规则抽取索引字段"""
import re

from src.scripts.rag_common import CORPUS, load_json, project_path, save_json, setup_stdout

setup_stdout()

cards = load_json(project_path('data', 'cards.json'))
# 保序列表（set 推导迭代顺序不稳定会导致 related 字段顺序每次运行不同）
card_names = [c['name'] for c in cards]

TERMS = ['出牌阶段', '摸牌阶段', '弃牌阶段', '回合开始', '回合结束', '战法牌', '行动牌', '装备牌',
         '获得牌', '造成伤害', '受到伤害', '失去体力', '回复体力', '重伤', '濒死', '阵亡', '击杀',
         '卜卦', '判定', '牌堆', '手牌', '装备区', '卜卦区', '处理区', '弃牌堆', '摸牌堆', '弃置',
         '交给', '获得', '打出', '使用', '添加', '烧毁', '销毁', '展示', '削弱', '增强', '连环',
         '封禁', '强命', '距离', '攻击范围', '花色', '点数', '牌名', '复制', '本轮', '每轮',
         '每个回合', '限1次', '额外', '立即', '当作', '随机', '体力', '属性伤害', '火焰伤害', '雷电伤害']
TERMS.sort(key=len, reverse=True)

RULE_MAP = [
    ('获得牌', '元规则:时机-获得牌后'), ('回合开始', '元规则:时机-回合开始时'),
    ('回合结束', '元规则:时机-回合结束时'), ('出牌阶段', '元规则:时机-出牌阶段'),
    ('摸牌阶段', '元规则:时机-摸牌阶段'), ('弃牌阶段', '元规则:时机-弃牌阶段'),
    ('重伤', '元规则:重伤流程'), ('阵亡', '元规则:阵亡结算'), ('卜卦', '元规则:卜卦机制'),
    ('装备', '元规则:装备规则'), ('属性伤害', '元规则:伤害流程'), ('火焰伤害', '元规则:伤害类型'),
    ('雷电伤害', '元规则:伤害类型'), ('连环', '元规则:连环状态'), ('强命', '元规则:强命'),
    ('封禁', '元规则:封禁贯穿'), ('限1次', '元规则:次数限制'), ('战法牌', '卡牌:战法牌'),
    ('行动牌', '卡牌:行动牌'), ('装备牌', '卡牌:装备牌'), ('出杀次数', '元规则:出杀次数'),
    ('距离', '元规则:距离攻击范围'), ('攻击范围', '元规则:距离攻击范围'),
]

def extract_timing(text):
    found = []
    for m in re.finditer(r'(?:当|成为|在|若|打出后|使用后)([^，。；]{1,18}?)(?:时|后|前)', text):
        g = m.group(1).strip()
        if g and len(g) >= 2 and g not in found:
            found.append(g)
    for m in re.finditer(r'(回合开始|回合结束|出牌阶段|摸牌阶段|弃牌阶段)', text):
        if m.group(1) not in found:
            found.append(m.group(1))
    return found[:5]

def extract_trigger(text):
    conds = []
    for m in re.finditer(r'(?:当|在|若|成为)([^，。；]{2,30}?)(?:时|后|前)', text):
        conds.append(m.group(1).strip())
    return conds[:3]

def extract_keywords(text):
    return [t for t in TERMS if t in text]

def extract_related(text):
    rel = []
    for kw, ref in RULE_MAP:
        if kw in text and ref not in rel:
            rel.append(ref)
    for cn in card_names:
        if len(cn) >= 2 and cn in text and cn != '杀':
            r = f'卡牌:{cn}'
            if r not in rel:
                rel.append(r)
    return rel[:8]

blocks = []
md_lines = ['# 卡牌 RAG 语料', '',
            '> 来源：cards.json（49 张卡）。索引字段由规则抽取生成，可后续精化。', '']
for c in cards:
    desc = c['card_desc'].strip()
    detail = c['card_detail'].strip()
    text = desc + ' ' + detail
    b = {
        'block_id': f'card_{c["id"]}_{c["name"]}',
        'card_type': c['card_type'], 'card_amount': c['card_amount'],
        'timing': extract_timing(text), 'trigger_condition': extract_trigger(text),
        'keywords': extract_keywords(text), 'related': extract_related(text),
        'effect': desc, 'effect_detail': detail,
    }
    blocks.append(b)
    md_lines += [
        f'### {b["block_id"]}',
        f'【类型】{b["card_type"]} | 【数量】{b["card_amount"]}',
        f'【效果】{b["effect"]}',
        f'【效果说明】{b["effect_detail"].replace(chr(10), " / ")}',
        f'【时机】{" / ".join(b["timing"]) if b["timing"] else "（待精化）"}',
        f'【触发条件】{" / ".join(b["trigger_condition"]) if b["trigger_condition"] else "（待精化）"}',
        f'【关键词】{" | ".join(b["keywords"]) if b["keywords"] else "（待精化）"}',
        f'【关联】{" / ".join(b["related"]) if b["related"] else "（待精化）"}',
        '',
    ]

# 保留旧语料中由 build_equip_attr.py 注入的装备字段（防止单独重建卡牌语料时丢失）
_EQUIP_FIELDS = ('equip_subtype', 'attack_range', 'distance_mod', 'distance_mod_type')
_old_card_blocks = load_json(CORPUS / '卡牌RAG语料.json', required=False) or []
_old_by_id = {b.get('block_id'): b for b in _old_card_blocks if isinstance(b, dict)}
for _b in blocks:
    _prev = _old_by_id.get(_b.get('block_id'))
    if _prev:
        for _k in _EQUIP_FIELDS:
            if _prev.get(_k) is not None:
                _b[_k] = _prev[_k]

from src.scripts import rag_curated

_merged = rag_curated.merge_curated(blocks, CORPUS / '卡牌RAG语料.json')
if _merged:
    print('已保留 curated 精化块:', _merged)
with open(CORPUS / '卡牌RAG语料.md', 'w', encoding='utf-8', newline='\n') as f:
    f.write('\n'.join(md_lines))
save_json(CORPUS / '卡牌RAG语料.json', blocks)
print('卡牌块数:', len(blocks))
print('有时机:', sum(1 for b in blocks if b['timing']), ' 有关联:', sum(1 for b in blocks if b['related']), ' 有关键词:', sum(1 for b in blocks if b['keywords']))
