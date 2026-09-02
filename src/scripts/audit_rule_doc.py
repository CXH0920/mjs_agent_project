# -*- coding: utf-8 -*-
"""《元规则整理-完整版》T0 文档机器校验（增量维护守门）。

校验项（详见 docs/元规则T0文档维护方案.md 第 6 节）：
1. 解析回声：build_rule_corpus 丢弃的 FAQ/术语表格行逐行报警（含行号）
2. 表格结构：同一表格列数一致（内容含裸 | 会导致列数异常）
3. 块 ID 唯一：section/faq/term 无重复
4. ID 稳定性：与快照对比，新增块只允许出现在章节末尾追加位（中部插入/重排/删除报警）
5. FAQ 编号：单调递增、无重复、无跳号（废弃条目划线保留编号）
6. 确认状态一致性：[待确认] 计数变化报告；划线 FAQ 仍在计数时提示
7. 交叉引用：「来源」列 卡牌N / 武将 X 对照 cards.json / heroes.json
8. 已定稿块指纹：原文本逐字保留且顺序不变才允许追加/插入（防 LLM 回归改写）；删除/改写 → 报警
9. 章节结构指纹：0~7 章标题与顺序不可变

用法：
    python -m src.scripts.audit_rule_doc                    # 校验并报告
    python -m src.scripts.audit_rule_doc --strict           # 有任一 ERROR/WARN 退出码 1
    python -m src.scripts.audit_rule_doc --update-snapshot  # 校验后刷新基线快照
    python -m src.scripts.audit_rule_doc --doc <path> --snapshot <path>  # 指定路径（测试用）
"""
import re
import io
import os
import sys
import json
import hashlib
import argparse
import datetime

from src.scripts import build_rule_corpus as brc

from src.config.env import PROJECT_ROOT as ROOT
DEFAULT_DOC = os.path.join(ROOT, 'docs', '元规则整理-完整版.md')
DEFAULT_SNAPSHOT = os.path.join(ROOT, '.rule_doc_snapshot.json')
SNAPSHOT_VERSION = 1

CARD_REF_RE = re.compile(r'卡牌\s*(\d+)')
HERO_REF_RE = re.compile(r'武将\s+([^，,、+（(/等]+)')
CHAPTER_RE = re.compile(r'^##\s+(\d+)\.\s*(.*)$')


def load_snapshot(path=DEFAULT_SNAPSHOT):
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception as error:
        print(f'  ⚠️ 快照加载失败，按无快照处理（全部章节将视为新增）: {error}')
        return None


def snapshot_counts(path=DEFAULT_SNAPSHOT):
    """供 maintain_rag.py 使用：返回 语料文件名 -> 快照期望块数；无快照返回 None。"""
    snap = load_snapshot(path)
    if not snap:
        return None
    c = snap.get('counts', {})
    return {
        '元规则RAG语料-章节块.json': c.get('sections'),
        '术语表.json': c.get('terms'),
        'FAQ裁定块.json': c.get('faqs'),
    }

def doc_md5(path):
    with open(path, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()


def _norm(content_lines):
    return '\n'.join(content_lines).strip('\n')


def _chapters(doc_path):
    """扫描 ## 章标题，返回 [{'no': int, 'title': str}]。"""
    out = []
    with open(doc_path, encoding='utf-8') as f:
        for ln in f.read().splitlines():
            m = brc.HEADING_RE.match(ln)
            if m and len(m.group(1)) == 2:
                text = m.group(2).strip()
                no, _ = brc._parse_heading(text)
                if no is None:
                    continue
                title = re.sub(r'^\d+\.\s*', '', text)
                out.append({'no': no, 'title': title})
    return out


def _cross_ref_sets(root):
    """读取交叉引用数据源；加载失败返回 None（调用方跳过校验并出 WARN，
    而不是拿空集合把文档中全部引用逐条误报为未知）。"""
    cards = set()
    heroes = set()
    skills = set()
    try:
        with open(os.path.join(root, 'data', 'cards.json'), encoding='utf-8') as f:
            for c in json.load(f):
                try:
                    cards.add(int(c.get('id')))
                except (TypeError, ValueError):
                    pass
        with open(os.path.join(root, 'data', 'heroes.json'), encoding='utf-8') as f:
            for h in json.load(f):
                name = h.get('name', '')
                if name:
                    heroes.add(name)
                for sk in h.get('skills', []):
                    sk_name = sk.get('name', '')
                    if sk_name:
                        skills.add(sk_name)
    except Exception as error:
        print(f'  ⚠️ 数据源加载失败，跳过交叉引用校验: {error}')
        return None
    return cards, heroes, skills


def _is_subsequence(a, b):
    """a 是否为 b 的子序列（逐行、保序）；用于块指纹：原文本保留则允许追加/插入。"""
    it = iter(b)
    return all(any(x == y for y in it) for x in a)


def _longest_prefix(text, names):
    for n in sorted(names, key=len, reverse=True):
        if text.startswith(n):
            return n
    return None


def build_snapshot(doc_path, root):
    blocks, terms, faqs, dropped = brc.parse_rule_doc(doc_path)
    chapters = _chapters(doc_path)
    chapter_blocks = [b['block_id'] for b in blocks if b['section'] is None]
    sections = {}
    for b in blocks:
        if b['section'] is not None:
            sections.setdefault(str(b['chapter']), []).append(b['block_id'])
    block_map = {}
    for b in blocks:
        block_map[b['block_id']] = {'title': b['title'], 'content': _norm(b['content'])}
    with open(doc_path, encoding='utf-8') as f:
        text = f.read()
    return {
        'version': SNAPSHOT_VERSION,
        'updated_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'doc_md5': doc_md5(doc_path),
        'chapters': chapters,
        'chapter_blocks': chapter_blocks,
        'sections': sections,
        'faq_ids': ['faq_%03d' % q['faq_no'] for q in sorted(faqs, key=lambda x: x['faq_no'])],
        'term_ids': [t['block_id'] for t in terms],
        'blocks': block_map,
        'counts': {'sections': len(blocks), 'terms': len(terms), 'faqs': len(faqs),
                   'pending': text.count('[待确认')},
    }


def write_snapshot(snap, path):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)


def audit(doc_path=DEFAULT_DOC, snapshot_path=DEFAULT_SNAPSHOT, root=None,
          update_snapshot=False, print_report=True):
    """执行全部校验，返回 issues 列表；strict 的退出码语义由调用方在返回值上自行实现。"""
    root = root or ROOT
    issues = []
    blocks, terms, faqs, dropped = brc.parse_rule_doc(doc_path)
    snap = load_snapshot(snapshot_path)
    with open(doc_path, encoding='utf-8') as f:
        lines = f.read().splitlines()
    text = '\n'.join(lines)

    # ---- 1. 解析回声 ----
    for d in dropped:
        issues.append({'level': 'ERROR', 'msg': 'L%d [%s] 解析丢弃：%s 原文：%s'
                       % (d['line_no'], d['kind'], d['reason'], d['text'][:80])})

    # ---- 2. 表格结构（列数一致） ----
    for b in blocks:
        run = []
        for off, row in enumerate(b['content']):
            if row.startswith('|'):
                run.append((b['start_line'] + 1 + off, row))
            elif run:
                _check_table_run(run, issues)
                run = []
        if run:
            _check_table_run(run, issues)

    # ---- 3. 块 ID 唯一 ----
    seen = {}
    for b in blocks:
        seen.setdefault(b['block_id'], []).append('L%d %s' % (b['start_line'], b['title']))
    for f in faqs:
        seen.setdefault(f['block_id'], []).append('L%d' % f['line_no'])
    for t in terms:
        seen.setdefault(t['block_id'], []).append('L%d' % t['line_no'])
    for bid, locs in seen.items():
        if len(locs) > 1:
            issues.append({'level': 'ERROR', 'msg': '块 ID 重复：%s 出现 %d 次（%s）'
                           % (bid, len(locs), ' / '.join(locs))})

    # ---- 4. 章节结构指纹 ----
    chapters = _chapters(doc_path)
    if snap:
        if snap.get('chapters') != chapters:
            issues.append({'level': 'ERROR',
                           'msg': '章节结构变更：快照=%s 当前=%s'
                           % ([c['title'] for c in snap.get('chapters', [])],
                              [c['title'] for c in chapters])})

    # ---- 5. ID 稳定性（需快照） ----
    if snap:
        _check_id_stability(snap, blocks, faqs, terms, issues)

    # ---- 6. FAQ 编号 ----
    nums = sorted(f['faq_no'] for f in faqs)
    if nums:
        expect = list(range(1, nums[-1] + 1))
        if nums != expect:
            missing = sorted(set(expect) - set(nums))
            issues.append({'level': 'ERROR', 'msg': 'FAQ 编号跳号/缺失：%s（当前最大 %d）'
                           % (missing[:10], nums[-1])})
    if len(set(nums)) != len(nums):
        dup = sorted({n for n in nums if nums.count(n) > 1})
        issues.append({'level': 'ERROR', 'msg': 'FAQ 编号重复：%s' % dup})

    # ---- 7. 确认状态一致性 ----
    pending = text.count('[待确认')
    if snap and snap.get('counts', {}).get('pending', pending) != pending:
        issues.append({'level': 'INFO', 'msg': '[待确认] 计数变化：快照 %d → 当前 %d（若为新增提案请走确认流程）'
                       % (snap['counts']['pending'], pending)})
    for f in faqs:
        if '~~' in f['ruling']:
            issues.append({'level': 'WARN', 'msg': 'FAQ %d 已划线但仍计入 FAQ（划线保留编号，废弃请确认是否仍进语料）'
                           % f['faq_no']})

    # ---- 8. 交叉引用 ----
    cross_ref = _cross_ref_sets(root)
    if cross_ref is None:
        issues.append({'level': 'WARN', 'msg': '数据源加载失败，交叉引用校验已跳过'})
    else:
        cards, heroes, skills = cross_ref
        for ln_no, ln in enumerate(lines, 1):
            for m in CARD_REF_RE.finditer(ln):
                cid = int(m.group(1))
                if cid not in cards:
                    issues.append({'level': 'ERROR', 'msg': 'L%d 来源引用未知卡牌编号：%d' % (ln_no, cid)})
            for m in HERO_REF_RE.finditer(ln):
                chunk = m.group(1).strip()
                if not chunk or chunk[0].isdigit() or '|' in chunk:
                    continue
                if _longest_prefix(chunk, heroes) is None and _longest_prefix(chunk, skills) is None:
                    issues.append({'level': 'WARN', 'msg': 'L%d 来源疑似引用未知武将/技能：%s' % (ln_no, chunk)})

    # ---- 9. 已定稿块指纹（防回归，允许末尾追加） ----
    if snap:
        cur_map = {b['block_id']: {'title': b['title'], 'content': _norm(b['content'])}
                   for b in blocks}
        for bid, old in snap.get('blocks', {}).items():
            cur = cur_map.get(bid)
            if cur is None:
                continue  # 已由 ID 稳定性报告
            if cur['title'] != old.get('title'):
                issues.append({'level': 'ERROR', 'msg': '块标题变更（块身份改变）：%s' % bid})
            old_c, new_c = old.get('content', ''), cur['content']
            if new_c == old_c:
                continue
            old_nb = [ln for ln in old_c.split('\n') if ln.strip()]
            new_nb = [ln for ln in new_c.split('\n') if ln.strip()]
            if _is_subsequence(old_nb, new_nb):
                issues.append({'level': 'INFO', 'msg': '块追加/插入（原文本保留，允许）：%s（+%d 行）'
                               % (bid, len(new_nb) - len(old_nb))})
            elif _is_subsequence(new_nb, old_nb):
                issues.append({'level': 'ERROR', 'msg': '块内容被删除：%s' % bid})
            else:
                issues.append({'level': 'ERROR', 'msg': '块内容被改写（非追加，疑似回归）：%s' % bid})

    # ---- 10. 数据段一致性（sync_rule_stats，A 层数据快照） ----
    try:
        from src.scripts import sync_rule_stats as srs
        with open(doc_path, encoding='utf-8') as f:
            doc_text = f.read()
        diffs = srs.diff_sections(doc_text, srs.load_data(root))
        full = [d for d in diffs if d['kind'] == 'full']
        cand = [d for d in diffs if d['kind'] == 'candidate']
        chk = [d for d in diffs if d['kind'] == 'checkpoint']
        if full:
            issues.append({'level': 'WARN', 'msg': '数据段一致性：%d 处全自动差异（段：%s）；请运行 src/scripts/sync_rule_stats.py 确认后 --apply'
                           % (len(full), '、'.join(sorted({d['section'] for d in full})))})
        if cand:
            issues.append({'level': 'INFO', 'msg': '数据段候选：%d 处半自动候选差异（段：%s），需人工确认后 --apply-candidates'
                           % (len(cand), '、'.join(sorted({d['section'] for d in cand})))})
        if chk:
            issues.append({'level': 'INFO', 'msg': '数据段校验点：%d 处数字不一致（段：%s），需人工核对'
                           % (len(chk), '、'.join(sorted({d['section'] for d in chk})))})
    except Exception as exc:
        issues.append({'level': 'WARN', 'msg': '数据段一致性校验失败：%s' % exc})

    # ---- 汇总 ----
    if update_snapshot:
        snap_new = build_snapshot(doc_path, root)
        write_snapshot(snap_new, snapshot_path)
    if print_report:
        _print_report(issues, snap, update_snapshot)
    return issues


def _check_table_run(run, issues):
    expect = run[0][1].count('|') - 1
    for ln_no, row in run[1:]:
        n = row.count('|') - 1
        if n != expect:
            issues.append({'level': 'ERROR', 'msg': 'L%d 表格列数异常（该表 %d 列，此行 %d 列，可能含裸 |）：%s'
                           % (ln_no, expect, n, row[:60])})


def _check_id_stability(snap, blocks, faqs, terms, issues):
    # 章级块
    old_ch = snap.get('chapter_blocks', [])
    new_ch = [b['block_id'] for b in blocks if b['section'] is None]
    if old_ch != new_ch:
        issues.append({'level': 'ERROR', 'msg': '章级块变化：快照=%s 当前=%s' % (old_ch, new_ch)})
    # 各章小节
    cur_sec = {}
    for b in blocks:
        if b['section'] is not None:
            cur_sec.setdefault(str(b['chapter']), []).append(b['block_id'])
    old_sec = snap.get('sections', {})
    for ch in sorted(set(old_sec) | set(cur_sec)):
        old_ids = old_sec.get(ch, [])
        new_ids = cur_sec.get(ch, [])
        if new_ids[:len(old_ids)] != old_ids:
            issues.append({'level': 'ERROR', 'msg': '章节 %s 小节出现中部插入/重排/删除：快照前序=%s 当前前序=%s'
                           % (ch, old_ids, new_ids[:len(old_ids)])})
    # FAQ
    old_faq = snap.get('faq_ids', [])
    new_faq = sorted(f['block_id'] for f in faqs)
    removed = [x for x in old_faq if x not in new_faq]
    for x in removed:
        issues.append({'level': 'ERROR', 'msg': 'FAQ 块被删除：%s（废弃应划线保留编号）' % x})
    if old_faq:
        mx = max(int(x.split('_')[1]) for x in old_faq)
        for x in new_faq:
            n = int(x.split('_')[1])
            if n <= mx and x not in old_faq:
                issues.append({'level': 'ERROR', 'msg': 'FAQ 编号回收/跳号：%s（新编号必须 > 当前最大 %d）' % (x, mx)})
    # 术语
    old_terms = set(snap.get('term_ids', []))
    new_term_ids = [t['block_id'] for t in terms]
    for x in old_terms - set(new_term_ids):
        issues.append({'level': 'ERROR', 'msg': '术语块被删除：%s' % x})


def _print_report(issues, snap, updated):
    n_err = sum(1 for i in issues if i['level'] == 'ERROR')
    n_warn = sum(1 for i in issues if i['level'] == 'WARN')
    n_info = sum(1 for i in issues if i['level'] == 'INFO')
    print('=' * 64)
    print('元规则文档校验（audit_rule_doc）')
    print('=' * 64)
    if snap is None:
        print('[提示] 未找到快照，稳定性/指纹/章节结构等基线校验已跳过；首次请用 --update-snapshot 建立基线。')
    for i in issues:
        print('  [%s] %s' % (i['level'], i['msg']))
    print('-' * 64)
    print('汇总：ERROR %d / WARN %d / INFO %d' % (n_err, n_warn, n_info))
    if updated:
        print('快照已更新。')


def main():
    parser = argparse.ArgumentParser(description='元规则 T0 文档机器校验')
    parser.add_argument('--strict', action='store_true', help='有任一 ERROR/WARN 时退出码 1')
    parser.add_argument('--update-snapshot', action='store_true', help='校验后刷新基线快照')
    parser.add_argument('--doc', default=DEFAULT_DOC, help='文档路径（默认 docs/元规则整理-完整版.md）')
    parser.add_argument('--snapshot', default=DEFAULT_SNAPSHOT, help='快照路径（默认 scripts/.rule_doc_snapshot.json）')
    args = parser.parse_args()

    issues = audit(doc_path=args.doc, snapshot_path=args.snapshot,
                   update_snapshot=args.update_snapshot)
    if args.strict and any(i['level'] in ('ERROR', 'WARN') for i in issues):
        sys.exit(1)


if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    main()