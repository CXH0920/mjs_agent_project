# -*- coding: utf-8 -*-
"""变更提案起草器（propose_rule_changes.py）
========================================
输入：diff_source_data.py 的数据变更清单（自动收集或 --changes-json 传入）
处理：调用 LLM（DeepSeek，复用 src/business/rag/refinement_service.build_generator）
     为每条变更生成结构化提案（类型/目标位置/建议文本/依据/建议确认状态）
输出：docs/archive/proposals/CP-YYYY-MM-DD-NN.json + 同名单 md（沿用提案单模板格式）

LLM 只产提案、不直接改定稿；人工在提案 JSON 中把 status 改为 approved/revised/rejected 后，
由 scripts/apply_rule_proposal.py 合入。无 API Key 时降级为占位提案（人工填写）。

用法：
    python scripts/propose_rule_changes.py                     # 自动对比 data/backups 并起草
    python scripts/propose_rule_changes.py --changes-json c.json
    python scripts/propose_rule_changes.py --no-llm            # 不调用 LLM，生成占位提案
    python scripts/propose_rule_changes.py --out-dir docs/archive/proposals
"""
import argparse
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DEFAULT_DOC = os.path.join(ROOT, 'docs', '元规则整理-完整版.md')
DEFAULT_PROPOSAL_DIR = os.path.join(ROOT, 'docs', 'archive', 'proposals')

SYSTEM_PROMPT = (
    "你是名将杀（三国杀类）游戏的规则维护助手。《元规则整理-完整版.md》是规则知识库母本："
    "FAQ 按编号 1~79 存于第 5 章；术语/动作/状态定义存于 1.1/1.2/1.3；通用结算原则存于第 4 章。"
    "根据【数据变更清单】与【文档全文】，判断每条变更是否影响文档语义（新机制/新裁定/新术语/既有规则被推翻），"
    "输出 JSON 对象：{\"items\": [...]}，items 为数组，每项与变更清单一一对应：\n"
    '{"id":"P-01","type":"faq_new|faq_revise|term_new|row_revise|section_new|none",'
    '"target":"5.2 或 faq_061 或 1.1 或 4（type=none 时填 -）",'
    '"suggested_text":"建议文本：FAQ 为裁定正文；term_new 为完整表格行 | 名称 | 定义 | 来源 |；row_revise 为完整新行；section_new 为小节全文（含 ### 标题）",'
    '"source":"来源（卡牌 N / 武将 X / 人工确认 / 公告）",'
    '"basis":"依据（引用变更清单）","suggested_status":"待确认",'
    '"rationale":"一句话说明是否需动 T0 及原因"}\n'
    "纯数值/措辞变更且不影响语义时 type 用 none。只输出 JSON，不要解释。"
)


def collect_diff_rows(old_arg=None):
    """复用 diff_source_data 收集变更行，返回 [{type,file,object,name,summary,mechanism}]。"""
    import diff_source_data as dsd
    words = dsd.load_lexicon()
    rows = []
    for name in dsd.HANDLERS:
        fname, handler = dsd.HANDLERS[name]
        new = dsd.load_json(os.path.join(dsd.DATA_DIR, fname))
        if new is None:
            continue
        old_path = dsd.find_old(fname, old_arg or dsd.BACKUP_DIR)
        if not old_path:
            continue
        old = dsd.load_json(old_path)
        if old is None or old == new:
            continue
        rows.extend(handler(old, new, words))
    return [
        {'type': r[0], 'file': r[1], 'object': r[2], 'name': r[3], 'summary': r[4], 'mechanism': r[5]}
        for r in rows
    ]


def next_proposal_id(out_dir):
    """CP-YYYY-MM-DD-NN：当日已有则序号 +1。"""
    today = __import__('datetime').date.today().isoformat()
    prefix = 'CP-%s-' % today
    mx = 0
    if os.path.isdir(out_dir):
        for fn in os.listdir(out_dir):
            m = re.match(re.escape(prefix) + r'(\d+)\.json$', fn)
            if m:
                mx = max(mx, int(m.group(1)))
    return '%s%02d' % (prefix, mx + 1)


def generate_proposal_items(rows, doc_text, generator):
    """逐批调用 LLM 生成提案项；无 generator 时返回占位项。"""
    if not rows:
        return []
    if generator is None:
        return [
            {'id': 'P-%02d' % (i + 1), 'type': 'none', 'target': '-',
             'suggested_text': '', 'source': '', 'basis': '%s：%s' % (r['name'], r['summary']),
             'suggested_status': '待确认', 'rationale': '未配置 LLM，占位提案，需人工填写',
             'status': 'pending', 'edited_text': None}
            for i, r in enumerate(rows)
        ]
    payload = json.dumps(rows, ensure_ascii=False)
    messages = [
        {'role': 'system', 'content': SYSTEM_PROMPT},
        {'role': 'user', 'content': '【数据变更清单】\n%s\n\n【文档全文】\n%s' % (payload, doc_text[:12000])},
    ]
    try:
        response = generator.complete(messages, temperature=0.2)
    except Exception as exc:
        print('LLM 调用异常，降级为占位提案：%s' % exc)
        return generate_proposal_items(rows, doc_text, None)
    if not response:
        print('LLM 无响应，降级为占位提案。')
        return generate_proposal_items(rows, doc_text, None)
    from src.scraper.ai.json_extract import extract_json
    try:
        data = extract_json(response.get('content', ''))
    except (ValueError, TypeError):
        data = None
    items_data = data.get('items') if isinstance(data, dict) else None
    if not isinstance(items_data, list):
        print('LLM 输出缺 items 数组，降级为占位提案。')
        return generate_proposal_items(rows, doc_text, None)
    items = []
    for i, raw in enumerate(items_data[:len(rows)]):
        if not isinstance(raw, dict):
            continue
        items.append({
            'id': 'P-%02d' % (i + 1),
            'type': str(raw.get('type', 'none')),
            'target': str(raw.get('target', '-')),
            'suggested_text': str(raw.get('suggested_text', '')),
            'source': str(raw.get('source', '')),
            'basis': str(raw.get('basis', '')),
            'suggested_status': str(raw.get('suggested_status', '待确认')),
            'rationale': str(raw.get('rationale', '')),
            'status': 'pending',
            'edited_text': None,
        })
    return items


def render_md(proposal, rows):
    lines = ['# 元规则变更提案单', '',
             '> 编号：%s' % proposal['proposal_id'],
             '> 生成：%s（propose_rule_changes.py）' % proposal['created_at'],
             '> 生成方式：数据变更清单 + LLM 起草；未经验证前全部视为草案。',
             '', '## 0. 数据变更清单', '',
             '| 类型 | 文件 | 对象 | 名称 | 变更摘要 | 是否新机制 |', '|---|---|---|---|---|---|']
    for r in rows:
        lines.append('| %s | %s | %s | %s | %s | %s |' % tuple(str(r[k]) for k in
                     ('type', 'file', 'object', 'name', 'summary', 'mechanism')))
    lines += ['', '## 1. 提案条目', '']
    for item in proposal['items']:
        lines += ['### %s ｜[%s] %s' % (item['id'], item['type'], item['rationale']),
                  '- **目标位置**：`%s`' % item['target'],
                  '- **建议文本**：%s' % (item['suggested_text'] or '（空，需人工填写）'),
                  '- **依据**：%s' % item['basis'],
                  '- **建议确认状态**：%s' % item['suggested_status'],
                  '- **确认结果**：□ 通过 / □ 驳回 / □ 改写', '']
    lines += ['', '## 2. 审阅总览（人工填写）', '',
              '| 提案号 | 结果 | 备注 |', '|---|---|---|']
    for item in proposal['items']:
        lines.append('| %s | | |' % item['id'])
    lines += ['', '## 3. 合入（人工确认后运行）', '',
              '```', 'python scripts/apply_rule_proposal.py --proposal docs/archive/proposals/%s.json' % proposal['proposal_id'],
              '```', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description='变更提案起草器')
    parser.add_argument('--changes-json', default=None, help='变更清单 JSON（[{type,file,object,name,summary,mechanism}]）')
    parser.add_argument('--no-llm', action='store_true', help='不调用 LLM，生成占位提案')
    parser.add_argument('--out-dir', default=DEFAULT_PROPOSAL_DIR, help='提案输出目录')
    parser.add_argument('--doc', default=DEFAULT_DOC, help='文档路径')
    args = parser.parse_args()

    rows = []
    if args.changes_json:
        with open(args.changes_json, encoding='utf-8') as f:
            rows = json.load(f)
    else:
        rows = collect_diff_rows()
        if not rows:
            print('未发现数据变更（data/backups 无旧基线或数据未变化）。')
            sys.exit(0)

    with open(args.doc, encoding='utf-8') as f:
        doc_text = f.read()

    generator = None
    if not args.no_llm:
        try:
            from src.business.rag.refinement_service import build_generator
            generator = build_generator()
        except Exception as exc:
            print('LLM 通道初始化失败，降级为占位提案：%s' % exc)

    items = generate_proposal_items(rows, doc_text, generator)
    proposal_id = next_proposal_id(args.out_dir)
    proposal = {
        'proposal_id': proposal_id,
        'created_at': __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'source': 'diff_source_data + LLM',
        'items': items,
    }
    os.makedirs(args.out_dir, exist_ok=True)
    json_path = os.path.join(args.out_dir, proposal_id + '.json')
    md_path = os.path.join(args.out_dir, proposal_id + '.md')
    with open(json_path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(proposal, f, ensure_ascii=False, indent=2)
    with open(md_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(render_md(proposal, rows))
    print('已生成提案：%s' % json_path)
    print('条目数：%d（type=none 表示无需动 T0）' % len(items))


if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    main()