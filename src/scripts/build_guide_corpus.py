# -*- coding: utf-8 -*-
"""生成武将攻略RAG语料：guides 45篇 md → per武将攻略块（按 ## 章节拆，hero=武将名）。

块结构（indexer _norm_guide 消费）：
  block_id = guide_{hero}_{i}（按 ## 章节序号）
  hero / section(章节标题) / text(章节正文) / related
设计点 A：攻略块贴 hero=武将名（_norm_guide 设 hero 元数据，保证生成该武将攻略时必召回）
设计点 C：按 ## 章节拆，控制单块大小避免超 prompt 预算被整块丢弃
"""
import re
from pathlib import Path

from src.data.hero_timeline import CORPUS_BASE_DATE, load_timeline, stamp_guide_block
from src.scripts.rag_common import CORPUS, install_crash_logger, load_json, project_path, save_json, setup_stdout

setup_stdout()
install_crash_logger("build_guide_corpus")

GUIDES = Path('data/raw_guides/jinxia/guides')

heroes_raw = load_json(project_path('data', 'heroes.json'))
_hl = heroes_raw['heroes'] if isinstance(heroes_raw, dict) and 'heroes' in heroes_raw else heroes_raw
NAMES = {h['name'] for h in _hl if isinstance(h, dict) and 'name' in h}
print('武将名表:', len(NAMES))

# 武将名修正（guide 文件名笔误）
NAME_FIX = {'春生君': '春申君', '王简': '王翦'}

MAX_BLOCK_CHARS = 600  # 单块上限：超则按 ### 再拆，避免注入时整块丢弃


def split_sections(t):
    """按 ## 拆块；若某块超 MAX_BLOCK_CHARS 再按 ### 拆。返回 [(title, text), ...]"""
    cur_title = None
    buf = []
    raw = []
    for line in t.splitlines():
        if line.startswith('## ') and not line.startswith('### '):
            if cur_title is not None and buf:
                raw.append((cur_title, '\n'.join(buf).strip()))
            cur_title = line.lstrip('#').strip()
            buf = []
        elif cur_title is not None:
            buf.append(line)
    if cur_title is not None and buf:
        raw.append((cur_title, '\n'.join(buf).strip()))

    out = []
    for title, txt in raw:
        if not txt:
            continue
        if len(txt) <= MAX_BLOCK_CHARS:
            out.append((title, txt))
            continue
        # 超长块按 ### 再拆
        sub_cur = None
        sub_buf = []
        pre_buf = []  # 第一个 ### 之前的内容
        seen_h3 = False
        sub_raw = []
        for line in txt.splitlines():
            if line.startswith('### '):
                if sub_cur is not None:
                    sub_raw.append((sub_cur, '\n'.join(sub_buf).strip()))
                sub_cur = line.lstrip('#').strip()
                sub_buf = []
                seen_h3 = True
            elif seen_h3:
                sub_buf.append(line)
            else:
                pre_buf.append(line)
        if not seen_h3:
            if len(txt) <= MAX_BLOCK_CHARS:
                out.append((title, txt))
            else:
                # 按句号/换行累积拆，保持句子完整
                sents = re.split(r'(?<=[。！？\n])', txt)
                cur, cl = [], 0
                for s in sents:
                    if cl + len(s) > MAX_BLOCK_CHARS and cur:
                        out.append((title, ''.join(cur).strip()))
                        cur, cl = [], 0
                    cur.append(s)
                    cl += len(s)
                if cur:
                    out.append((title, ''.join(cur).strip()))
            continue
        if sub_cur is not None:  # 收尾最后一个 ### 块
            sub_raw.append((sub_cur, '\n'.join(sub_buf).strip()))
        if (txt_pre := '\n'.join(pre_buf).strip()):
            out.append((title, txt_pre))
        for st, sv in sub_raw:
            if sv:
                out.append((f'{title} · {st}', sv))
    # 后处理：超长块统一按句号拆（覆盖无 ### 的超长块与 ### 子块本身超长）
    final = []
    for ftitle, ftxt in out:
        if len(ftxt) <= MAX_BLOCK_CHARS:
            final.append((ftitle, ftxt))
            continue
        sents = re.split(r'(?<=[。！？\n])', ftxt)
        cur, cl = [], 0
        for s in sents:
            while len(s) > MAX_BLOCK_CHARS:  # 单句超长硬拆
                final.append((ftitle, s[:MAX_BLOCK_CHARS]))
                s = s[MAX_BLOCK_CHARS:]
            if cl + len(s) > MAX_BLOCK_CHARS and cur:
                final.append((ftitle, ''.join(cur).strip()))
                cur, cl = [], 0
            if s:
                cur.append(s)
                cl += len(s)
        if cur:
            final.append((ftitle, ''.join(cur).strip()))
    return final


blocks = []
skipped = []
# 旧语料的 (as_of, content_md5)：攻略文本未变则保留原语料时间，变了重置基线
_old_blocks = load_json(CORPUS / '武将攻略RAG语料.json', required=False) or []
_prev_meta = {b.get('block_id'): (b.get('as_of'), b.get('content_md5')) for b in _old_blocks}
timeline = load_timeline()
for md in sorted(GUIDES.glob('*.md')):
    hero = NAME_FIX.get(md.stem, md.stem)
    if hero not in NAMES:
        skipped.append(md.stem)
        continue
    t = md.read_text(encoding='utf-8')
    sections = split_sections(t)
    if not sections:  # 无 ## 章节，整篇一块
        sections = [('全文', t.strip())]
    for i, (title, txt) in enumerate(sections, 1):
        bid = f'guide_{hero}_{i}'
        block = {
            'block_id': bid, 'hero': hero, 'section': title,
            'text': txt, 'related': [f'武将:{hero}'],
        }
        prev_as_of, prev_md5 = _prev_meta.get(bid, (None, None))
        blocks.append(stamp_guide_block(block, prev_as_of=prev_as_of,
                                        prev_md5=prev_md5, timeline=timeline))

print(f'guides 处理: {len(blocks)} 块  | 跳过(文件名非武将): {skipped}')
stale = [b for b in blocks if b.get('is_current') == 'false']
hints = [b for b in blocks if b.get('staleness_hint')]
print(f"版本戳: as_of 基线 {CORPUS_BASE_DATE} | 过时块 {len(stale)}（检索默认排除）| 漂移提示 {len(hints)}")
for b in stale:
    print(f"  ⚠️ 过时: {b['block_id']} — {b['staleness_reason']}")

# block_id 唯一性
ids = [b['block_id'] for b in blocks]
assert len(ids) == len(set(ids)), f'block_id 重复: {set(x for x in ids if ids.count(x) > 1)}'

# 块大小分布
sizes = [len(b['text']) for b in blocks]
print(f'块大小: min={min(sizes)} max={max(sizes)} avg={sum(sizes)//len(sizes)}')

save_json(CORPUS / '武将攻略RAG语料.json', blocks)

# 输出 md（人读版）
md_out = ['# 武将攻略RAG语料', '',
          '> 来源：raw_guides/guides。设计点A贴hero=武将名，C按##章节拆。',
          f'> 总块 {len(blocks)}。', '']
for b in blocks:
    md_out.append(f'### {b["block_id"]}')
    md_out.append(f'【武将】{b["hero"]}  【章节】{b["section"]}  【字数】{len(b["text"])}')
    md_out.append(f'【内容】{b["text"][:150]}...')
    md_out.append('')
with open(CORPUS / '武将攻略RAG语料.md', 'w', encoding='utf-8', newline='\n') as f:
    f.write('\n'.join(md_out))

print('已输出: 武将攻略RAG语料.json + .md')
