# -*- coding: utf-8 -*-
"""FAQ 裁定回归评估（eval_rule_faqs.py）
=====================================
用 RAG 检索评估 FAQ 可命中率：每条评估题（问句 + 期望 faq 块 ID）走向量检索（FAQ 类型硬过滤），
断言期望块出现在 top-k 内，输出通过率报告。零 LLM 成本，可进 CI / 周更步骤 7。

评估集：data/rag_evals/rule_faq_eval.json
  {"version": "2026-08-16", "k": 5, "items": [{"question": "...", "expected": "faq_001"}]}

--generate 模式：从 data/rag_corpus/FAQ裁定块.json 生成初始评估集（问句 = 裁定文本 + ？，
人工校对一次后作为基线；后续 T0 变更追加新题）。

用法：
    python scripts/eval_rule_faqs.py --generate          # 生成/重建评估集
    python scripts/eval_rule_faqs.py                     # 运行评估（需已建索引）
    python scripts/eval_rule_faqs.py --top-k 5
"""
import argparse
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DEFAULT_DATASET = os.path.join(ROOT, 'data', 'rag_evals', 'rule_faq_eval.json')
FAQ_CORPUS = os.path.join(ROOT, 'data', 'rag_corpus', 'FAQ裁定块.json')


def generate_dataset(faq_path=FAQ_CORPUS, dataset_path=DEFAULT_DATASET, version=None):
    """从 FAQ 语料生成初始评估集；问句 = 裁定文本 + ？。"""
    with open(faq_path, encoding='utf-8') as f:
        faqs = json.load(f)
    version = version or __import__('datetime').date.today().isoformat()
    items = []
    for q in sorted(faqs, key=lambda x: x.get('faq_no', 0)):
        ruling = (q.get('ruling') or '').strip()
        bid = q.get('block_id') or 'faq_%03d' % q.get('faq_no', 0)
        if ruling:
            items.append({'question': ruling + '？', 'expected': bid})
    dataset = {'version': version, 'k': 5, 'items': items}
    os.makedirs(os.path.dirname(dataset_path), exist_ok=True)
    with open(dataset_path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)
    return dataset


def load_dataset(dataset_path=DEFAULT_DATASET):
    with open(dataset_path, encoding='utf-8') as f:
        return json.load(f)


def run_eval(dataset, retriever, top_k=None):
    """执行评估；返回 {total, passed, hit_rate, failed: [{question, expected, top}]}。
    retriever 需提供 _vector_search(query, where, n) 与 model 属性。"""
    top_k = top_k or dataset.get('k', 5)
    items = dataset.get('items', [])
    passed = 0
    failed = []
    for it in items:
        question = it.get('question', '')
        expected = it.get('expected', '')
        if not question or not expected:
            continue
        try:
            res = retriever._vector_search(question, where={'kind': 'faq'}, n=max(top_k * 3, 30))
        except Exception as exc:
            failed.append({'question': question, 'expected': expected, 'error': str(exc)})
            continue
        top_ids = [r['block_id'] for r in res[:top_k]]
        if expected in top_ids:
            passed += 1
        else:
            failed.append({'question': question, 'expected': expected, 'top': top_ids})
    total = len(items)
    return {
        'total': total,
        'passed': passed,
        'hit_rate': round(passed / total, 4) if total else 0.0,
        'top_k': top_k,
        'failed': failed,
    }


def main():
    parser = argparse.ArgumentParser(description='FAQ 裁定回归评估')
    parser.add_argument('--generate', action='store_true', help='生成/重建评估集后退出')
    parser.add_argument('--dataset', default=DEFAULT_DATASET, help='评估集路径')
    parser.add_argument('--top-k', type=int, default=None, help='命中窗口（默认取评估集 k）')
    parser.add_argument('--limit', type=int, default=None, help='只评估前 N 条（调试用）')
    args = parser.parse_args()

    if args.generate:
        ds = generate_dataset(dataset_path=args.dataset)
        print('已生成评估集：%s（%d 条，version=%s）' % (args.dataset, len(ds['items']), ds['version']))
        sys.exit(0)

    dataset = load_dataset(args.dataset)
    from src.rag.retriever import Retriever
    retriever = Retriever()
    items = dataset['items']
    if args.limit:
        dataset = dict(dataset, items=items[:args.limit])
    report = run_eval(dataset, retriever, top_k=args.top_k)
    print('=' * 60)
    print('FAQ 裁定回归评估（top-k=%d，共 %d 题）' % (report['top_k'], report['total']))
    print('通过：%d / %d，命中率：%.1f%%' % (report['passed'], report['total'], report['hit_rate'] * 100))
    if report['failed']:
        print('-' * 60)
        print('未命中 %d 题（示例）：' % len(report['failed']))
        for it in report['failed'][:10]:
            print('  ? %s' % it['question'][:60])
            print('    期望 %s；实际 top：%s' % (it['expected'], it.get('top', it.get('error'))))
    print('=' * 60)
    sys.exit(0 if report['passed'] == report['total'] else 1)


if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    main()