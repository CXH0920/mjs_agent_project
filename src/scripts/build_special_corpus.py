# -*- coding: utf-8 -*-
"""生成特殊机制语料：专属牌 / 专属战法牌 / 特殊机制(牌区·状态·概念)"""

from src.scripts.rag_common import CORPUS, load_json, save_json, setup_stdout, project_path

setup_stdout()


def _load_special_items():
    """从 data/special_cards.json 读取特殊机制数据（唯一人工维护源）。"""
    return load_json(project_path('data', 'special_cards.json'))


def face_lines(it):
    """牌面事实行：花色/点数/攻击范围/结算详情（有值才输出）。"""
    lines = []
    pair = (it.get('suit', '') or '') + (it.get('point', '') or '')
    if pair:
        lines.append(f'【牌面】{pair}')
    if it.get('attack_range'):
        lines.append(f'【攻击范围】{it["attack_range"]}')
    if it.get('settlement'):
        lines.append(f'【结算详情】{it["settlement"]}')
    return lines


def build_special_corpus(items):
    """生成特殊机制语料（md + json），返回块列表。"""
    blocks = []
    md = ['# 特殊机制语料', '',
          '> 来源：data/special_cards.json（唯一人工维护源；专属牌/专属战法牌的花色点数与结算详情由原 xlsx 迁移回填）。',
          '> 历史人工确认：义兵以xlsx为准、玉钩以heroes.json"≥"为准、怒气=基础行动牌、酒=专属牌（效果相同、连用计数独立）。', '']

    def add(cat, block_id, lines, b):
        blocks.append(b)
        md.append(f'### {block_id}')
        md.extend(lines)
        md.append('')

    for it in items:
        cat = it['category']
        name = it['name']
        hero = it.get('hero', '')
        if cat == '专属牌':
            ctype = it.get('card_type', '')
            bid = 'special_card_' + name
            b = {'block_id': bid, 'category': cat, 'name': name, 'card_type': ctype,
                 'effect': it.get('effect', ''), 'hero': hero,
                 'related': [f'武将:{hero}'] if hero != '—' else []}
            for k in ('suit', 'point', 'attack_range', 'settlement'):
                if it.get(k):
                    b[k] = it[k]
            add(cat, bid, [f'【类别】专属牌 | 【类型】{ctype}',
                           f'【效果】{it.get("effect", "")}'] + face_lines(it) + [f'【相关武将】{hero}'], b)
        elif cat == '专属战法牌':
            bid = 'special_war_' + name
            b = {'block_id': bid, 'category': cat, 'name': name,
                 'effect': it.get('effect', ''), 'hero': hero, 'related': [f'武将:{hero}']}
            for k in ('suit', 'point', 'settlement'):
                if it.get(k):
                    b[k] = it[k]
            add(cat, bid, [f'【类别】专属战法牌 | 【所属武将】{hero}',
                           f'【效果】{it.get("effect", "")}'] + face_lines(it), b)
        elif cat == '特殊牌区':
            bid = 'special_zone_' + name
            b = {'block_id': bid, 'category': cat, 'name': name,
                 'function': it.get('function', ''), 'hero': hero,
                 'related': [f'武将:{hero}'] if hero != '通用' else []}
            add(cat, bid, [f'【类别】特殊牌区 | 【相关武将】{hero}',
                           f'【功能】{it.get("function", "")}'], b)
        elif cat == '状态/标记':
            stack = it.get('stackable', '')
            bid = 'special_state_' + name
            b = {'block_id': bid, 'category': cat, 'name': name,
                 'effect': it.get('effect', ''), 'stackable': stack, 'hero': hero, 'related': []}
            add(cat, bid, [f'【类别】状态/标记 | 【可叠加】{stack}',
                           f'【效果】{it.get("effect", "")}', f'【相关武将】{hero}'], b)
        else:  # 概念
            bid = 'special_concept_' + name
            b = {'block_id': bid, 'category': cat, 'name': name,
                 'description': it.get('description', ''), 'hero': hero, 'related': []}
            add(cat, bid, [f'【类别】概念 | 【相关武将】{hero}',
                           f'【说明】{it.get("description", "")}'], b)

    with open(CORPUS / '特殊机制语料.md', 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(md))
    save_json(CORPUS / '特殊机制语料.json', blocks)
    return blocks


def main():
    items = _load_special_items()
    blocks = build_special_corpus(items)
    print('块数:', len(blocks))
    import collections
    print(dict(collections.Counter(b['category'] for b in blocks)))


if __name__ == '__main__':
    main()
