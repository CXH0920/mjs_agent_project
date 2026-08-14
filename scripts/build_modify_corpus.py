# -*- coding: utf-8 -*-
"""生成加强削弱语料 + 统计"""
import os
ROOT = os.environ.get("RAG_PROJECT_DIR") or r"G:\py_savepoint\mjs_rag_project"
import json, io, sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
with open(os.path.join(ROOT, 'data', 'cards.json'), encoding='utf-8') as f:
    cards = json.load(f)
with open(os.path.join(ROOT, 'data', 'card_annotations.json'), encoding='utf-8') as f:
    ann = json.load(f)

cards_by_id = {c['id']: c for c in cards}
anns = ann['annotations']
print('annotations 条数:', len(anns))

missing = [a['card_id'] for a in anns if a['card_id'] not in cards_by_id]
print('card_id 未匹配到 cards.json:', missing if missing else '无')

only_strengthen = [a['card_id'] for a in anns if a['fields'].get('strengthen_effect') and not a['fields'].get('weaken_effect')]
only_weaken = [a['card_id'] for a in anns if a['fields'].get('weaken_effect') and not a['fields'].get('strengthen_effect')]
both = [a['card_id'] for a in anns if a['fields'].get('strengthen_effect') and a['fields'].get('weaken_effect')]
print('仅加强:', only_strengthen or '无', ' 仅削弱:', only_weaken or '无', ' 双有:', len(both))
# 多 content 条数
multi = [a['card_id'] for a in anns if max(len(a['fields'].get('strengthen_effect', [])), len(a['fields'].get('weaken_effect', []))) > 1]
print('单字段多content:', multi or '无')

# 生成语料
blocks = []
md = ['# 加强削弱语料', '',
      '> 来源：card_annotations.json（schema v1，经 card_id 关联 cards.json）。',
      '> 结算详情取自 card_annotations.json 的 settlement_rules（2026-08-12 补充，49/49 覆盖）。', '']
for a in sorted(anns, key=lambda x: int(x['card_id']) if x['card_id'].isdigit() else 999):
    cid = a['card_id']
    card = cards_by_id.get(cid)
    cname = card['name'] if card else '未知卡牌'
    ctype = card['card_type'] if card else ''
    base = card['card_desc'] if card else ''
    def take(lst):
        return lst[0]['content'].strip() if lst and lst[0].get('content') else ''
    def take_settle(lst):
        return lst[0].get('settlement_rules', '').strip() if lst and lst[0].get('settlement_rules') else ''
    strong = take(a['fields'].get('strengthen_effect') or [])
    weak = take(a['fields'].get('weaken_effect') or [])
    strong_settle = take_settle(a['fields'].get('strengthen_effect') or [])
    weak_settle = take_settle(a['fields'].get('weaken_effect') or [])
    detail_parts = []
    if strong_settle:
        detail_parts.append('【加强结算】' + strong_settle)
    if weak_settle:
        detail_parts.append('【削弱结算】' + weak_settle)
    detail = '\n'.join(detail_parts)
    b = {
        'block_id': f'modify_{cid}_{cname}',
        'card_id': cid, 'card_name': cname, 'card_type': ctype,
        'base_effect': base, 'strengthen_effect': strong, 'weaken_effect': weak,
        'settlement_detail': detail,
        'related': [f'卡牌:{cname}'],
    }
    blocks.append(b)
    md += [
        f'### {b["block_id"]}',
        f'【卡牌】{cname} | 【类型】{ctype}',
        f'【原效果】{base}',
        f'【加强效果】{strong if strong else "（无）"}',
        f'【削弱效果】{weak if weak else "（无）"}',
        f'【结算详情】{detail if detail else "（无）"}',
        f'【关联】{" / ".join(b["related"])}',
        '',
    ]
out = os.path.join(ROOT, 'docs')
with open(out + r'\加强削弱语料.md', 'w', encoding='utf-8', newline='\n') as f:
    f.write('\n'.join(md))
with open(out + r'\加强削弱语料.json', 'w', encoding='utf-8', newline='\n') as f:
    json.dump(blocks, f, ensure_ascii=False, indent=1)
print('语料块数:', len(blocks))
print('覆盖卡牌数(去重):', len(set(a['card_id'] for a in anns)))
