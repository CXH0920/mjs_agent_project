# -*- coding: utf-8 -*-
"""提案合入器（apply_rule_proposal.py）
====================================
读取人工确认后的结构化提案 JSON，执行合入《元规则整理-完整版.md》：
- faq_new   ：FAQ 追加到 5.1/5.2 表末尾，编号 = 现有最大 + 1（不回收）
- faq_revise：FAQ 行原位修订（裁定列替换）
- term_new  ：术语/动作/状态行追加到指定表末尾
- row_revise：任意表格行原位替换（old_text 精确匹配）
- section_new：新 ### 小节追加到指定章末尾

合入后自动链：audit_rule_doc.py --strict（失败中止且不写 changelog）→
maintain_rag.py --only 元规则 → changelog 追加 → 提案归档。

用法：
    python -m src.scripts.apply_rule_proposal --proposal docs/archive/proposals/CP-xxx.json
    python -m src.scripts.apply_rule_proposal --proposal xxx.json --doc docs/元规则整理-完整版.md
"""
import argparse
import io
import json
import os
import re
import subprocess
import sys

from src.business.rag.rule_doc_service import build_faq_revise_row
from src.config.env import PROJECT_ROOT as ROOT
from src.scripts.rag_common import HEADING_RE, SEPARATOR_RE, install_crash_logger, setup_stdout

DEFAULT_DOC = os.path.join(ROOT, 'docs', '元规则整理-完整版.md')
DEFAULT_CHANGELOG = os.path.join(ROOT, 'docs', 'changelog', '元规则changelog.md')

TABLE_HEADER_FIRST_CELLS = {'类型', '数量', '内容', '项', '数据', '时机', '频次', '备注', '#', '限制', '含义 [推断]', '示例', '状态', '定义/要点', '来源', '动作', '单位', '定义'}


def _is_table_header(ln):
    if not ln.startswith('|'):
        return False
    return ln.split('|')[1].strip() in TABLE_HEADER_FIRST_CELLS


def find_section(lines, heading_prefix):
    """返回 [start, end)，标题行含；找不到返回 None。"""
    start = None
    start_level = None
    for i, ln in enumerate(lines):
        m = HEADING_RE.match(ln)
        if m and m.group(2).strip().startswith(heading_prefix):
            start, start_level = i, len(m.group(1))
            break
    if start is None:
        return None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        m = HEADING_RE.match(lines[i])
        if m and len(m.group(1)) <= start_level:
            end = i
            break
    return start, end


def table_rows(lines, start, end):
    return [(i, lines[i]) for i in range(start, end)
            if lines[i].startswith('|') and not SEPARATOR_RE.match(lines[i]) and not _is_table_header(lines[i])]


def max_faq_no(lines):
    """文档中 FAQ 行最大编号（5.x 表内第一列为数字）。"""
    mx = 0
    for i, ln in enumerate(lines):
        if not ln.startswith('|'):
            continue
        m = re.match(r'^\|\s*(\d+)\s*\|', ln)
        if m:
            mx = max(mx, int(m.group(1)))
    return mx


# ---------------------------------------------------------------------------
# 合入动作
# ---------------------------------------------------------------------------

def _apply_faq_new(lines, item, errors):
    """FAQ 追加到 5.1/5.2 表末尾；target 形如 '5.1' / '5.2'。"""
    sec = find_section(lines, item.get('target', '5.2'))
    if not sec:
        errors.append('%s: 找不到章节 %s' % (item['id'], item['target']))
        return lines
    no = max_faq_no(lines) + 1
    text = (item.get('edited_text') or item.get('suggested_text') or '').strip()
    if not text:
        errors.append('%s: 缺少裁定文本' % item['id'])
        return lines
    source = item.get('source', '人工确认')
    new_line = '| %d | %s | %s |' % (no, text, source)
    # 在目标表数据行末尾插入（最后一个数据行之后）
    rows = table_rows(lines, *sec)
    if not rows:
        errors.append('%s: 章节 %s 无可追加的表格' % (item['id'], item['target']))
        return lines
    insert_at = rows[-1][0] + 1
    lines.insert(insert_at, new_line)
    item['applied_faq_no'] = no
    return lines


def _apply_faq_revise(lines, item, errors):
    """FAQ 行原位修订；target 形如 'faq_061'。"""
    m = re.match(r'faq_(\d+)', item.get('target', ''))
    if not m:
        errors.append('%s: target 需为 faq_编号' % item['id'])
        return lines
    no = int(m.group(1))
    text = (item.get('edited_text') or item.get('suggested_text') or '').strip()
    source = item.get('source') or ''
    for i, ln in enumerate(lines):
        mm = re.match(r'^\|\s*%d\s*\|' % no, ln)
        if mm:
            if source:
                lines[i] = '| %d | %s | %s |' % (no, text, source)
            else:
                lines[i] = build_faq_revise_row(ln, text)
            return lines
    errors.append('%s: 找不到 FAQ %d' % (item['id'], no))
    return lines


def _apply_term_new(lines, item, errors):
    """术语/动作/状态行追加到指定表末尾；target 形如 '1.1' / '1.2' / '1.3' / '0.3' / '0.4'。"""
    sec = find_section(lines, item.get('target', '1.1'))
    if not sec:
        errors.append('%s: 找不到章节 %s' % (item['id'], item['target']))
        return lines
    new_line = (item.get('edited_text') or item.get('suggested_text') or '').strip()
    if not new_line.startswith('|'):
        errors.append('%s: 术语行需为表格行（| 名称 | 定义 | 来源 |）' % item['id'])
        return lines
    rows = table_rows(lines, *sec)
    if not rows:
        errors.append('%s: 章节 %s 无可追加的表格' % (item['id'], item['target']))
        return lines
    lines.insert(rows[-1][0] + 1, new_line)
    return lines


def _apply_row_revise(lines, item, errors):
    """表格行原位替换；target 为块 ID 或章节前缀，old_text 精确匹配原行。"""
    old = item.get('old_text') or ''
    new = (item.get('edited_text') or item.get('suggested_text') or '').strip()
    if not old or not new.startswith('|'):
        errors.append('%s: row_revise 需要 old_text 与新行文本' % item['id'])
        return lines
    hit = 0
    for i, ln in enumerate(lines):
        if ln.strip() == old.strip():
            lines[i] = new
            hit += 1
    if hit == 0:
        errors.append('%s: 找不到原行（old_text 不匹配）' % item['id'])
    return lines


def _apply_section_new(lines, item, errors):
    """新 ### 小节追加到指定章末尾；target 形如 '4'，suggested_text 为小节全文。"""
    text = (item.get('edited_text') or item.get('suggested_text') or '').strip()
    if not text:
        errors.append('%s: 缺少小节内容' % item['id'])
        return lines
    # 定位章标题（## N.）
    ch_start = None
    for i, ln in enumerate(lines):
        m = HEADING_RE.match(ln)
        if m and len(m.group(1)) == 2 and re.match(r'^%s\.' % item.get('target', ''), m.group(2).strip()):
            ch_start = i
            break
    if ch_start is None:
        errors.append('%s: 找不到章 %s' % (item['id'], item['target']))
        return lines
    ch_end = len(lines)
    for i in range(ch_start + 1, len(lines)):
        m = HEADING_RE.match(lines[i])
        if m and len(m.group(1)) == 2:
            ch_end = i
            break
    # 在章末尾插入（下一个 ## 之前），若章尾是 --- 分隔线则插在分隔线之前
    insert_at = ch_end
    if insert_at > ch_start and lines[insert_at - 1].strip() == '---':
        insert_at -= 1
    body = text.splitlines()
    lines[insert_at:insert_at] = body
    return lines


APPLYERS = {
    'faq_new': _apply_faq_new,
    'faq_revise': _apply_faq_revise,
    'term_new': _apply_term_new,
    'row_revise': _apply_row_revise,
    'section_new': _apply_section_new,
}


def apply_proposal(doc_text, proposal):
    """执行合入，返回 (new_text, applied_ids, errors)。仅处理 status=approved/revised。"""
    lines = doc_text.splitlines()
    applied = []
    errors = []
    for item in proposal.get('items', []):
        status = item.get('status', 'pending')
        if status not in ('approved', 'revised'):
            continue  # pending/rejected 跳过
        applier = APPLYERS.get(item.get('type'))
        if applier is None:
            errors.append('%s: 未知类型 %s' % (item['id'], item.get('type')))
            continue
        before = list(lines)
        lines = applier(lines, item, errors)
        if lines == before and item['id'] not in [e.split(':')[0] for e in errors]:
            errors.append('%s: 未产生任何修改' % item['id'])
        else:
            applied.append(item['id'])
    return '\n'.join(lines), applied, errors


# ---------------------------------------------------------------------------
# 自动链
# ---------------------------------------------------------------------------

def run_audit_strict(doc_path):
    """合入后机器校验；失败返回 False。"""
    script = os.path.join(ROOT, 'scripts', 'audit_rule_doc.py')
    proc = subprocess.run([sys.executable, script, '--strict', '--doc', doc_path],
                          cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace')
    return proc.returncode == 0, proc.stdout + proc.stderr


def run_maintain_rules():
    """重跑元规则/术语/FAQ 语料。"""
    script = os.path.join(ROOT, 'scripts', 'maintain_rag.py')
    proc = subprocess.run([sys.executable, script, '--only', '元规则'],
                          cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace')
    return proc.returncode == 0, proc.stdout + proc.stderr


def append_changelog(proposal, applied, errors, changelog_path=DEFAULT_CHANGELOG):
    """changelog 追加一行（只追加）。"""
    today = __import__('datetime').date.today().isoformat()
    summary = '；'.join('%s(%s)' % (a, proposal['proposal_id']) for a in applied[:5])
    line = '| %s | %s | 提案合入 | %s | %s | 已确认 |' % (today, '/'.join(applied[:5]) or '-', summary,
                                                          '；'.join(errors[:2]) if errors else 'audit 通过')
    lines = []
    if os.path.exists(changelog_path):
        with open(changelog_path, encoding='utf-8') as f:
            lines = f.read().splitlines()
    insert_at = None
    for i, ln in enumerate(lines):
        if ln.startswith('|---|---|'):
            insert_at = i + 1
            break
    if insert_at is None:
        lines.append(line)
    else:
        lines.insert(insert_at, line)
    with open(changelog_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')


def archive_proposal(proposal_path):
    """提案文件追加 .merged 标记（不删除原文件）。"""
    if os.path.exists(proposal_path):
        archive = proposal_path + '.merged'
        with open(proposal_path, encoding='utf-8') as f:
            data = json.load(f)
        data['merged_at'] = __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with open(archive, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    setup_stdout()
    install_crash_logger("apply_rule_proposal")
    parser = argparse.ArgumentParser(description='提案合入器')
    parser.add_argument('--proposal', required=True, help='已确认提案 JSON 路径')
    parser.add_argument('--doc', default=DEFAULT_DOC, help='文档路径')
    parser.add_argument('--skip-maintain', action='store_true', help='跳过 maintain_rag 重跑（测试用）')
    parser.add_argument('--skip-audit', action='store_true', help='跳过 audit --strict（测试用）')
    args = parser.parse_args()

    with open(args.proposal, encoding='utf-8') as f:
        proposal = json.load(f)
    with open(args.doc, encoding='utf-8') as f:
        doc_text = f.read()

    new_text, applied, errors = apply_proposal(doc_text, proposal)
    if errors:
        print('合入错误：')
        for e in errors:
            print('  -', e)
        print('未写回文档，请修正提案后重试。')
        sys.exit(1)
    if not applied:
        print('无可合入的提案项（需 status=approved/revised）。')
        sys.exit(0)

    with open(args.doc, 'w', encoding='utf-8', newline='\n') as f:
        f.write(new_text)

    ok, out = run_audit_strict(args.doc)
    if not ok and not args.skip_audit:
        print('audit --strict 未通过，文档已回滚。')
        print(out[-2000:])
        with open(args.doc, 'w', encoding='utf-8', newline='\n') as f:
            f.write(doc_text)
        sys.exit(1)

    if not args.skip_maintain:
        mok, mout = run_maintain_rules()
        if not mok:
            print('maintain_rag --only 元规则 失败：')
            print(mout[-2000:])
            sys.exit(1)

    append_changelog(proposal, applied, errors)
    archive_proposal(args.proposal)
    print('已合入 %s 项：%s' % (len(applied), '、'.join(applied)))
    if errors:
        print('提示（不影响合入）：')
        for e in errors:
            print('  -', e)


if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    main()