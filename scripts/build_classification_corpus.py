# -*- coding: utf-8 -*-
"""生成武将分类语料：从 data/hero_classification.json 提取机制分类 + 克制链，生成 RAG 语料块。

输入：data/hero_classification.json（人工维护）、data/heroes.json（官方数据）
输出：docs/武将分类语料.json / .md
未归类武将不会生成块，并打印待补充清单。
"""
import os
ROOT = os.environ.get("RAG_PROJECT_DIR") or r"G:\py_savepoint\mjs_rag_project"
import io, sys, os, json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

DATA = os.path.join(ROOT, 'data')
DOCS = os.path.join(ROOT, 'docs')

with open(os.path.join(DATA, 'hero_classification.json'), encoding='utf-8') as f:
    cls = json.load(f)
with open(os.path.join(DATA, 'heroes.json'), encoding='utf-8') as f:
    heroes = json.load(f)

cat_by_name = {c['name']: c for c in cls['categories']}
hero_map = {h['name']: h for h in heroes}
chain = cls.get('counter_chain', {})


def chain_text(cat_name):
    parts = []
    for k, v in chain.items():
        if k == cat_name:
            parts.append('克制：' + v)
        elif cat_name in v:
            parts.append('被克制：' + v)
    return '；'.join(parts)


blocks = []
md = ['# 武将分类语料', '',
      '> 来源：data/hero_classification.json（人工维护的机制分类与克制链）。', '']
unclassified = []
for h in heroes:
    name = h['name']
    cats = cls['hero_categories'].get(name)
    if not cats:
        unclassified.append(name)
        continue
    cat_defs = []
    for c in cats:
        cat = cat_by_name.get(c)
        if cat:
            cat_defs.append(c + '：' + cat['core_features'])
    ct = chain_text(cats[0])
    reason = '；'.join(cat_defs)
    if ct:
        reason = reason + '；' + ct
    bid = 'classification_' + name
    b = {
        'block_id': bid,
        'hero': name,
        'position': h.get('position', ''),
        'categories': cats,
        'categories_text': '/'.join(cats),
        'reason': reason,
    }
    blocks.append(b)
    md.append('### ' + bid)
    md.append('【武将分类】' + name + '：' + b['categories_text'])
    md.append('官方定位：' + b['position'])
    md.append(b['reason'])
    md.append('')

if unclassified:
    print('未归类武将（请补充 data/hero_classification.json）: %d' % len(unclassified))
    for n in unclassified:
        print('  - ' + n)

with open(os.path.join(DOCS, '武将分类语料.json'), 'w', encoding='utf-8', newline='\n') as f:
    json.dump(blocks, f, ensure_ascii=False, indent=1)
with open(os.path.join(DOCS, '武将分类语料.md'), 'w', encoding='utf-8', newline='\n') as f:
    f.write('\n'.join(md))
print('块数:', len(blocks))