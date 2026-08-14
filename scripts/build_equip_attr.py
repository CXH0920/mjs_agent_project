# -*- coding: utf-8 -*-
"""装备属性落地：生成装备属性语料 + 注入卡牌语料"""
import json, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

EQUIP_ATTRS = {
    '赤兔': ('坐骑', None, -1), '盗骊': ('坐骑', None, -1), '白蹄乌': ('坐骑', None, -1), '乌骓': ('坐骑', None, -1),
    '飒露紫': ('坐骑', None, 1), '爪黄飞电': ('坐骑', None, 1), '绝影': ('坐骑', None, 1), '的卢': ('坐骑', None, 1),
    '银狮盔': ('防具', None, None), '凤羽盔': ('防具', None, None), '玄武盾': ('防具', None, None),
    '八卦盾': ('防具', None, None), '云锦袍': ('防具', None, None), '藤甲': ('防具', None, None),
    '亮银枪': ('武器', 3, None), '诸葛连弩': ('武器', 1, None), '羽扇': ('武器', 4, None),
    '丈八蛇矛': ('武器', 3, None), '青龙偃月刀': ('武器', 3, None), '方天画戟': ('武器', 4, None),
    '干将莫邪': ('武器', 2, None), '龙舌弓': ('武器', 5, None), '惊羽弓': ('武器', 5, None),
    '鸣鸿刀': ('武器', 2, None), '开山斧': ('武器', 3, None), '轩辕剑': ('武器', 2, None),
}

out = r'data\rag_corpus'

# ---- 1. 装备属性语料 ----
with open(r'data\cards.json', encoding='utf-8') as f:
    cards = json.load(f)
card_by_name = {c['name']: c for c in cards}

blocks = []
md = ['# 装备属性语料', '',
      '> 来源：装备属性表（坐骑距离修正 + 武器攻击范围），效果取自 cards.json。', '']
for name, (st, rng, dist) in sorted(EQUIP_ATTRS.items(), key=lambda x: (x[1][0], x[0])):
    card = card_by_name.get(name, {})
    effect = card.get('card_desc', '')
    b = {
        'block_id': 'equipattr_' + name,
        'card': name, 'equip_subtype': st,
        'attack_range': rng, 'distance_mod': dist,
        'distance_mod_type': '攻击距离' if dist == -1 else ('防御距离' if dist == 1 else None),
        'effect': effect, 'related': ['卡牌:' + name],
    }
    blocks.append(b)
    rng_str = str(rng) if rng else '—'
    dist_str = (('距离' + str(dist)) if dist else '—')
    md += [
        f'### {b["block_id"]}',
        f'【装备】{name} | 【细分】{st} | 【攻击范围】{rng_str} | 【距离修正】{dist_str}',
        f'【效果】{effect}',
        f'【关联】{" / ".join(b["related"])}',
        '',
    ]
# 距离计算规则块
rule_block = {
    'block_id': 'equipattr_规则_距离计算',
    'card': '距离计算规则', 'equip_subtype': '系统规则',
    'attack_range': None, 'distance_mod': None, 'distance_mod_type': None,
    'effect': '见内容', 'related': [],
    'content': (
        '1. 基础攻击范围=1（无特殊技能/武器/马匹加持，只能打到距离1的玩家）\n'
        '2. 多件武器攻击范围取最大值\n'
        '3. 距离分攻击距离/防御距离，同类修正各自相加：\n'
        '   - 距离-1马 → 攻击距离修正（你到目标距离-1，等效能打更远）\n'
        '   - 距离+1马 → 防御距离修正（他人到你距离+1）\n'
        '   - 例：两匹-1马 + 一匹+1马 → 攻击距离-2、防御距离+1\n'
        '4. 坐骑明细：距离-1=赤兔/盗骊/白蹄乌/乌骓；距离+1=飒露紫/爪黄飞电/绝影/的卢'
    ),
}
blocks.append(rule_block)
md += [
    f'### {rule_block["block_id"]}',
    f'【规则】{rule_block["content"]}',
    '',
]
with open(out + r'\装备属性语料.md', 'w', encoding='utf-8', newline='\n') as f:
    f.write('\n'.join(md))
with open(out + r'\装备属性语料.json', 'w', encoding='utf-8', newline='\n') as f:
    json.dump(blocks, f, ensure_ascii=False, indent=1)
print('装备属性语料块:', len(blocks))

# ---- 2. 注入卡牌语料 ----
with open(out + r'\卡牌RAG语料.json', encoding='utf-8') as f:
    card_blocks = json.load(f)
injected = 0
for b in card_blocks:
    name = b['block_id'].split('_', 2)[2] if '_' in b['block_id'] else ''
    if b['card_type'] == '装备牌' and name in EQUIP_ATTRS:
        st, rng, dist = EQUIP_ATTRS[name]
        b['equip_subtype'] = st
        b['attack_range'] = rng
        b['distance_mod'] = dist
        b['distance_mod_type'] = '攻击距离' if dist == -1 else ('防御距离' if dist == 1 else None)
        injected += 1
with open(out + r'\卡牌RAG语料.json', 'w', encoding='utf-8', newline='\n') as f:
    json.dump(card_blocks, f, ensure_ascii=False, indent=1)
# 重新生成卡牌 md（含新字段）
md2 = ['# 卡牌 RAG 语料', '', '> 来源：cards.json（49 张卡）+ 装备属性表。索引字段由规则抽取生成，可后续精化。', '']
for b in card_blocks:
    md2.append(f'### {b["block_id"]}')
    line = f'【类型】{b["card_type"]} | 【数量】{b["card_amount"]}'
    if b.get('equip_subtype'):
        rng = b.get('attack_range'); dist = b.get('distance_mod')
        line += f' | 【装备细分】{b["equip_subtype"]} | 【攻击范围】{rng if rng else "—"} | 【距离修正】{("距离" + str(dist)) if dist else "—"}'
    md2.append(line)
    md2.append(f'【效果】{b["effect"]}')
    md2.append(f'【效果说明】{b["effect_detail"].replace(chr(10), " / ")}')
    md2.append(f'【时机】{" / ".join(b["timing"]) if b["timing"] else "（待精化）"}')
    md2.append(f'【触发条件】{" / ".join(b["trigger_condition"]) if b["trigger_condition"] else "（待精化）"}')
    md2.append(f'【关键词】{" | ".join(b["keywords"]) if b["keywords"] else "（待精化）"}')
    md2.append(f'【关联】{" / ".join(b["related"]) if b["related"] else "（待精化）"}')
    md2.append('')
with open(out + r'\卡牌RAG语料.md', 'w', encoding='utf-8', newline='\n') as f:
    f.write('\n'.join(md2))
print('卡牌语料注入装备属性:', injected, '件')
