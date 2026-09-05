# -*- coding: utf-8 -*-
"""元规则切块 + 术语表 + FAQ 块（稳定块 ID 版）

- 章节块 ID：rule_section_<章>_<节>（章级块 rule_section_<章>），按标题编号解析；
- FAQ 块 ID：faq_%03d，绑定裁定编号，单调递增不回收（废弃条目划线保留编号）；
- 术语块 ID：term_<名称>，天然稳定。

本模块同时提供 parse_rule_doc()，供 src/scripts/audit_rule_doc.py 复用解析逻辑做"解析回声"校验。
"""
import re

from src.scripts.rag_common import CORPUS, HEADING_RE, SEPARATOR_RE, project_path, save_json, setup_stdout

TERM_BLOCK_KEYWORDS = ('术语表', '牌的类型', '动作定义', '状态定义', '区域清单', '资源')
TERM_BLACKLIST_PREFIX = ('类型', '动作', '状态', '区域', '资源', '花色体系', '——')
FAQ_BLOCK_KEYWORD = '裁定'

NUMBER_RE = re.compile(r'^(\d+)(?:\.(\d+))?\s*')
FAQ_ROW_RE = re.compile(r'^\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|$')
TERM_ROW_RE = re.compile(r'^\|\s*([^|]+?)\s*\|\s*(.+?)\s*\|\s*([^|]*?)\s*\|$')
HEADER_CELL_RE = re.compile(r'^\|\s*([^|]+?)\s*\|')


def _is_separator(line):
    return bool(SEPARATOR_RE.match(line))


def _first_cell(line):
    m = HEADER_CELL_RE.match(line)
    return m.group(1).strip() if m else ''


def _parse_heading(text):
    """从标题文本解析 (章节号, 节号)；无编号返回 (None, None)。"""
    m = NUMBER_RE.match(text)
    if not m:
        return None, None
    return int(m.group(1)), (int(m.group(2)) if m.group(2) else None)


def parse_rule_doc(src):
    """解析《元规则整理-完整版》，返回 (blocks, terms, faqs, dropped)。

    blocks: list[dict]  章节块，含 type/title/content/block_id/chapter/section/start_line
    terms:  list[dict]  术语，含 block_id/term/definition/source/line_no
    faqs:   list[dict]  FAQ，含 block_id/faq_no/ruling/source/line_no
    dropped: list[dict] 解析器丢弃的疑似行（解析回声），含 line_no/kind/text/reason
    """
    with open(src, encoding='utf-8') as f:
        lines = f.read().splitlines()

    blocks = []
    terms = []
    faqs = []
    dropped = []
    cur = None          # (title, content, start_line, depth, chap, sec)
    chapter_no = None
    fallback_sec = {}

    def flush():
        nonlocal cur
        if cur:
            title, content, start_line, depth, chap, sec = cur
            while content and HEADING_RE.match(content[0]):
                content.pop(0)
            if content:
                block_id = 'rule_section_%02d' % chap if depth == 2 else 'rule_section_%02d_%02d' % (chap, sec)
                blocks.append({
                    'type': 'section', 'title': title, 'content': content,
                    'block_id': block_id, 'chapter': chap, 'section': sec,
                    'start_line': start_line,
                })
        cur = None

    for idx, ln in enumerate(lines, 1):
        m = HEADING_RE.match(ln)
        if m:
            flush()
            depth = len(m.group(1))
            text = m.group(2).strip()
            chap, sec = _parse_heading(text)
            if chap is None:
                if depth == 2:
                    chap = (chapter_no + 1) if chapter_no is not None else 0
                    sec = None
                    dropped.append({'line_no': idx, 'kind': 'unnumbered_heading',
                                    'text': ln, 'reason': '章标题无编号，回退为递增序号'})
                else:
                    chap = chapter_no if chapter_no is not None else 0
                    fallback_sec[chap] = fallback_sec.get(chap, 0) + 1
                    sec = fallback_sec[chap]
                    dropped.append({'line_no': idx, 'kind': 'unnumbered_heading',
                                    'text': ln, 'reason': '小节标题无编号，回退为章节内递增序号'})
            if depth == 2:
                chapter_no = chap
            cur = [ln.strip(), [], idx, depth, chap, sec]
        elif cur is not None:
            cur[1].append(ln)
    flush()

    # ---- 术语表 ----
    for b in blocks:
        if any(k in b['title'] for k in TERM_BLOCK_KEYWORDS):
            base = b['start_line'] + 1
            for off, row in enumerate(b['content']):
                line_no = base + off
                if not row.startswith('|'):
                    continue
                if _is_separator(row):
                    continue
                cell = _first_cell(row)
                if not cell or cell == '#' or cell.startswith(TERM_BLACKLIST_PREFIX):
                    continue  # 表头/已知排除行，属预期丢弃
                m = TERM_ROW_RE.match(row)
                if not m:
                    dropped.append({'line_no': line_no, 'kind': 'term_row_unparsed',
                                    'text': row, 'reason': '术语表行格式无法解析（列数/裸|异常）'})
                    continue
                name, desc, src2 = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
                if (name and '-' not in name
                        and not name.startswith(TERM_BLACKLIST_PREFIX)):
                    terms.append({'block_id': 'term_' + name, 'term': name,
                                  'definition': desc, 'source': src2, 'line_no': line_no})
                else:
                    dropped.append({'line_no': line_no, 'kind': 'term_filtered',
                                    'text': row, 'reason': '术语名不满足过滤规则（含-等）'})

    # ---- FAQ 块 ----
    for b in blocks:
        if FAQ_BLOCK_KEYWORD in b['title']:
            base = b['start_line'] + 1
            for off, row in enumerate(b['content']):
                line_no = base + off
                if not row.startswith('|'):
                    continue
                if _is_separator(row):
                    continue
                cell = _first_cell(row)
                if not cell or cell == '#':
                    continue  # 表头
                m = FAQ_ROW_RE.match(row)
                if not m:
                    dropped.append({'line_no': line_no, 'kind': 'faq_row_unparsed',
                                    'text': row, 'reason': 'FAQ 行格式无法解析（编号/列数/裸|异常）'})
                    continue
                faqs.append({'block_id': 'faq_%03d' % int(m.group(1)), 'faq_no': int(m.group(1)),
                             'ruling': m.group(2).strip(), 'source': m.group(3).strip(),
                             'line_no': line_no})

    # 术语去重（保留先出现者）
    seen = set()
    terms_u = []
    for t in terms:
        if t['term'] not in seen:
            seen.add(t['term'])
            terms_u.append(t)
    terms = terms_u
    return blocks, terms, faqs, dropped


def main():
    setup_stdout()
    src = project_path('docs', '元规则整理-完整版.md')
    blocks, terms, faqs, dropped = parse_rule_doc(src)

    md_sections = ['# 元规则 RAG 语料（章节块）', '', '> 来源：《元规则整理-完整版》按小节切块。', '']
    for b in blocks:
        md_sections.append('### %s %s' % (b['block_id'], b['title']))
        md_sections.append('\n'.join(b['content']))
        md_sections.append('')
    with open(CORPUS / '元规则RAG语料-章节块.md', 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(md_sections))
    save_json(CORPUS / '元规则RAG语料-章节块.json', blocks)

    md_terms = ['# 术语表', '', '> 从《元规则整理-完整版》提取，每条一个术语块。', '']
    for t in terms:
        md_terms += ['### ' + t['block_id'], '【术语】' + t['term'],
                     '【定义】' + t['definition'], '【来源】' + t['source'], '']
    with open(CORPUS / '术语表.md', 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(md_terms))
    save_json(CORPUS / '术语表.json', terms)

    md_faqs = ['# FAQ 裁定块', '', '> %d 条裁定，每条一个块。' % len(faqs), '']
    for q in sorted(faqs, key=lambda x: x['faq_no']):
        md_faqs += ['### ' + q['block_id'], '【裁定】' + q['ruling'],
                    '【来源】' + q['source'], '']
    with open(CORPUS / 'FAQ裁定块.md', 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(md_faqs))
    save_json(CORPUS / 'FAQ裁定块.json', faqs)

    print('章节块:', len(blocks))
    print('术语条数:', len(terms))
    print('FAQ 条数:', len(faqs))
    if dropped:
        print('解析丢弃行: %d 条（建议先运行 src/scripts/audit_rule_doc.py 查看明细）' % len(dropped))
        for d in dropped[:10]:
            print('  L%d [%s] %s' % (d['line_no'], d['kind'], d['reason']))


if __name__ == '__main__':
    main()