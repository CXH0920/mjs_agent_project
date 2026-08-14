# -*- coding: utf-8 -*-
"""元规则切块 + 术语表 + FAQ 块（修复版）"""
import os
ROOT = os.environ.get("RAG_PROJECT_DIR") or r"G:\py_savepoint\mjs_rag_project"
import re, io, sys, json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

src = os.path.join(ROOT, 'docs', '元规则整理-完整版.md')
with open(src, encoding='utf-8') as f:
    lines = f.read().splitlines()

# ---- 1. 章节块：按 ## 或 ### 标题切 ----
blocks = []
cur = None
def flush():
    global cur
    if cur:
        title, content = cur
        while content and re.match(r'^#{2,3} ', content[0]):
            content.pop(0)
        if content:
            blocks.append({'type': 'section', 'title': title, 'content': '\n'.join(content).strip()})
    cur = None

for ln in lines:
    if re.match(r'^#{2,3} ', ln):
        flush()
        cur = [ln.strip(), []]
    elif cur is not None:
        cur[1].append(ln)
flush()

for i, b in enumerate(blocks, 1):
    b['block_id'] = f'rule_section_{i:02d}'

# ---- 2. 术语表 ----
terms = []
for b in blocks:
    if any(k in b['title'] for k in ['术语表', '牌的类型', '动作定义', '状态定义', '区域清单', '资源']):
        for row in b['content'].splitlines():
            m = re.match(r'^\|\s*([^|]+?)\s*\|\s*(.+?)\s*\|\s*([^|]*?)\s*\|$', row)
            if m:
                name, desc, src2 = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
                if (name and len(name) >= 2 and '-' not in name
                        and not name.startswith(('类型', '动作', '状态', '区域', '资源', '花色体系', '——'))):
                    terms.append({'block_id': 'term_' + name, 'term': name, 'definition': desc, 'source': src2})
seen = set(); terms_u = []
for t in terms:
    if t['term'] not in seen:
        seen.add(t['term']); terms_u.append(t)
terms = terms_u

# ---- 3. FAQ 块 ----
faqs = []
for b in blocks:
    if '裁定' in b['title']:
        for row in b['content'].splitlines():
            m = re.match(r'^\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|$', row)
            if m:
                faqs.append({'block_id': 'faq_%02d' % int(m.group(1)), 'faq_no': int(m.group(1)),
                             'ruling': m.group(2).strip(), 'source': m.group(3).strip()})

out_dir = os.path.join(ROOT, 'docs')
md_sections = ['# 元规则 RAG 语料（章节块）', '', '> 来源：《元规则整理-完整版》按小节切块。', '']
for b in blocks:
    md_sections.append('### %s %s' % (b['block_id'], b['title']))
    md_sections.append(b['content'])
    md_sections.append('')
with open(out_dir + r'\元规则RAG语料-章节块.md', 'w', encoding='utf-8', newline='\n') as f:
    f.write('\n'.join(md_sections))
with open(out_dir + r'\元规则RAG语料-章节块.json', 'w', encoding='utf-8', newline='\n') as f:
    json.dump(blocks, f, ensure_ascii=False, indent=1)

md_terms = ['# 术语表', '', '> 从《元规则整理-完整版》提取，每条一个术语块。', '']
for t in terms:
    md_terms += ['### ' + t['block_id'], '【术语】' + t['term'], '【定义】' + t['definition'], '【来源】' + t['source'], '']
with open(out_dir + r'\术语表.md', 'w', encoding='utf-8', newline='\n') as f:
    f.write('\n'.join(md_terms))
with open(out_dir + r'\术语表.json', 'w', encoding='utf-8', newline='\n') as f:
    json.dump(terms, f, ensure_ascii=False, indent=1)

md_faqs = ['# FAQ 裁定块', '', '> %d 条裁定，每条一个块。' % len(faqs), '']
for q in sorted(faqs, key=lambda x: x['faq_no']):
    md_faqs += ['### ' + q['block_id'], '【裁定】' + q['ruling'], '【来源】' + q['source'], '']
with open(out_dir + r'\FAQ裁定块.md', 'w', encoding='utf-8', newline='\n') as f:
    f.write('\n'.join(md_faqs))
with open(out_dir + r'\FAQ裁定块.json', 'w', encoding='utf-8', newline='\n') as f:
    json.dump(faqs, f, ensure_ascii=False, indent=1)

print('章节块:', len(blocks))
print('术语条数:', len(terms))
print('FAQ 条数:', len(faqs))
