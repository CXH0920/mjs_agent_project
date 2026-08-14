# -*- coding: utf-8 -*-
"""生成特殊机制语料：专属牌 / 专属战法牌 / 特殊机制(牌区·状态·概念)"""
import json, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 一、专属牌（牌名, 类型, 效果, 相关武将）
def _load_special_items():
    """从 data/special_cards.json 读取特殊机制数据（单一维护源）。"""
    with open(r'data\special_cards.json', encoding='utf-8') as f:
        _items = json.load(f)

    def _group(cat):
        return [it for it in _items if it.get('category') == cat]

    special_cards = [(it['name'], it.get('card_type', ''), it.get('effect', ''), it.get('hero', ''))
                     for it in _group('专属牌')]
    special_war = [(it['name'], it.get('effect', ''), it.get('hero', ''))
                   for it in _group('专属战法牌')]
    special_zones = [(it['name'], it.get('function', ''), it.get('hero', ''))
                     for it in _group('特殊牌区')]
    states = [(it['name'], it.get('effect', ''), it.get('stackable', ''), it.get('hero', ''))
              for it in _group('状态/标记')]
    concepts = [(it['name'], it.get('description', ''), it.get('hero', ''))
               for it in _group('概念')]
    return special_cards, special_war, special_zones, states, concepts


SPECIAL_CARDS, SPECIAL_WAR, SPECIAL_ZONES, STATES, CONCEPTS = _load_special_items()
out = r'data\rag_corpus'
blocks = []
md = ['# 特殊机制语料', '',
      '> 来源：人工整理（专属牌/专属战法牌/特殊牌区/状态标记/概念）。',
      '> 完整结算详情以《mjs卡牌点数.xlsx》【专属牌】sheet 为准（2026-08-08 补充，经人工确认：义兵以xlsx为准、玉钩以heroes.json"≥"为准、怒气=基础行动牌、酒=专属牌（效果相同、连用计数独立））。', '']

def add(cat, block_id, lines, b):
    global md
    blocks.append(b)
    md.append(f'### {block_id}')
    md += lines
    md.append('')

for name, ctype, effect, hero in SPECIAL_CARDS:
    bid = 'special_card_' + name
    b = {'block_id': bid, 'category': '专属牌', 'name': name, 'card_type': ctype,
         'effect': effect, 'hero': hero, 'related': [f'武将:{hero}'] if hero != '—' else []}
    add('card', bid, [f'【类别】专属牌 | 【类型】{ctype}',
                      f'【效果】{effect}', f'【相关武将】{hero}'], b)

for name, effect, hero in SPECIAL_WAR:
    bid = 'special_war_' + name
    b = {'block_id': bid, 'category': '专属战法牌', 'name': name,
         'effect': effect, 'hero': hero, 'related': [f'武将:{hero}']}
    add('war', bid, [f'【类别】专属战法牌 | 【所属武将】{hero}',
                     f'【效果】{effect}'], b)

for name, func, hero in SPECIAL_ZONES:
    bid = 'special_zone_' + name
    b = {'block_id': bid, 'category': '特殊牌区', 'name': name,
         'function': func, 'hero': hero, 'related': [f'武将:{hero}'] if hero != '通用' else []}
    add('zone', bid, [f'【类别】特殊牌区 | 【相关武将】{hero}',
                      f'【功能】{func}'], b)

for name, effect, stack, hero in STATES:
    bid = 'special_state_' + name
    b = {'block_id': bid, 'category': '状态/标记', 'name': name,
         'effect': effect, 'stackable': stack, 'hero': hero, 'related': []}
    add('state', bid, [f'【类别】状态/标记 | 【可叠加】{stack}',
                       f'【效果】{effect}', f'【相关武将】{hero}'], b)

for name, desc, hero in CONCEPTS:
    bid = 'special_concept_' + name
    b = {'block_id': bid, 'category': '概念', 'name': name,
         'description': desc, 'hero': hero, 'related': []}
    add('concept', bid, [f'【类别】概念 | 【相关武将】{hero}',
                         f'【说明】{desc}'], b)

with open(out + r'\特殊机制语料.md', 'w', encoding='utf-8', newline='\n') as f:
    f.write('\n'.join(md))
with open(out + r'\特殊机制语料.json', 'w', encoding='utf-8', newline='\n') as f:
    json.dump(blocks, f, ensure_ascii=False, indent=1)
print('块数:', len(blocks))
import collections
print(dict(collections.Counter(b['category'] for b in blocks)))

