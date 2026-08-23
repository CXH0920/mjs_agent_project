# -*- coding: utf-8 -*-
"""装备属性落地：生成装备属性语料 + 注入卡牌语料（数据源 data/equip_attrs.json，由 xlsx 迁移而来）"""

from src.scripts.rag_common import CORPUS, load_json, save_json, setup_stdout, project_path

setup_stdout()

_equips = load_json(project_path('data', 'equip_attrs.json'))
# 用 .get 容忍缺键（面板保存时 None 字段被 exclude_defaults 省略，键可能不存在）
EQUIP_ATTRS = {e['name']: (e['subtype'], e.get('attack_range'), e.get('distance_mod')) for e in _equips}

# ---- 1. 装备属性语料 ----
cards = load_json(project_path('data', 'cards.json'))
card_by_name = {c['name']: c for c in cards}

blocks = []
md = ['# 装备属性语料', '',
      '> 来源：data/equip_attrs.json（坐骑距离修正 + 武器攻击范围），效果取自 cards.json。', '']
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
with open(CORPUS / '装备属性语料.md', 'w', encoding='utf-8', newline='\n') as f:
    f.write('\n'.join(md))
save_json(CORPUS / '装备属性语料.json', blocks)
print('装备属性语料块:', len(blocks))

# ---- 2. 注入卡牌语料 ----
card_blocks = load_json(CORPUS / '卡牌RAG语料.json')
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
save_json(CORPUS / '卡牌RAG语料.json', card_blocks)
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
with open(CORPUS / '卡牌RAG语料.md', 'w', encoding='utf-8', newline='\n') as f:
    f.write('\n'.join(md2))
print('卡牌语料注入装备属性:', injected, '件')
