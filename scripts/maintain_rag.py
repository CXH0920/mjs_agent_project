# -*- coding: utf-8 -*-
"""
RAG 语料维护调度主脚本（maintain_rag.py）
==========================================
用途：修改完 T0 级权威文档（data/ 源数据 + docs/元规则整理-完整版.md）后，
      一键按依赖顺序重跑相关 build 脚本，维护 docs/ 下全部 RAG 语料。

用法：
    python scripts/maintain_rag.py            # 增量：只重跑源文件有变更的语料
    python scripts/maintain_rag.py --force    # 强制重跑全部语料
    python scripts/maintain_rag.py --check    # 只检测变更，不执行
    python scripts/maintain_rag.py --only 武将 # 只跑名称包含"武将"的任务
    python scripts/maintain_rag.py --keep-going # 单个失败后继续执行其余任务

依赖：仅 Python 标准库；需在项目根目录运行（与 data/ docs/ scripts/ 同级）。
"""
import sys, os, json, hashlib, subprocess, argparse, time

import rag_audit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根
STATE_FILE = os.path.join(ROOT, 'scripts', '.rag_state.json')
SCRIPTS_DIR = os.path.join(ROOT, 'scripts')
DOCS_DIR = os.path.join(ROOT, 'data', 'rag_corpus')


# ---------------------------------------------------------------------------
# 任务表：name | build脚本 | 依赖的T0源文件 | 生成的json校验文件 | 期望块数
# ---------------------------------------------------------------------------
TASKS = [
    {
        'name': '武将语料',
        'script': 'build_rag_corpus.py',
        'sources': ['data/heroes.json', 'data/cards.json'],
        'outputs': [('武将RAG语料.json', 593)],
    },
    {
        'name': '卡牌语料',
        'script': 'build_card_corpus.py',
        'sources': ['data/cards.json'],
        'outputs': [('卡牌RAG语料.json', 49)],
    },
    {
        'name': '点数花色语料',
        'script': 'build_cardpts.py',
        'sources': ['data/card_points.json'],
        'outputs': [('卡牌点数花色语料.json', 49)],
    },
    {
        'name': '装备属性语料',
        'script': 'build_equip_attr.py',
        'sources': ['data/cards.json', 'data/equip_attrs.json', 'data/rag_corpus/卡牌RAG语料.json'],
        'outputs': [('装备属性语料.json', 27)],
    },
    {
        'name': '加强削弱语料',
        'script': 'build_modify_corpus.py',
        'sources': ['data/cards.json', 'data/card_annotations.json'],
        'outputs': [('加强削弱语料.json', 49)],
    },
    {
        'name': '元规则/术语/FAQ',
        'script': 'build_rule_corpus.py',
        'sources': ['docs/元规则整理-完整版.md'],
        'outputs': [('元规则RAG语料-章节块.json', 37), ('术语表.json', 46), ('FAQ裁定块.json', 79)],
    },
    {
        'name': '特殊机制语料',
        'script': 'build_special_corpus.py',
        'sources': ['data/special_cards.json'],   # 单一维护源：人工维护的专属牌/战法牌/状态等（含 xlsx 迁移的花色点数/结算）
        'outputs': [('特殊机制语料.json', 83)],
    },
    {
        'name': '武将分类语料',
        'script': 'build_classification_corpus.py',
        'sources': ['data/hero_classification.json', 'data/heroes.json'],
        'outputs': [('武将分类语料.json', None)],
    },
]

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def file_fingerprint(path):
    """返回 (md5, size, mtime) 作为文件变更指纹。"""
    full = os.path.join(ROOT, path)
    if not os.path.exists(full):
        return None
    with open(full, 'rb') as f:
        digest = hashlib.md5(f.read()).hexdigest()
    st = os.stat(full)
    return {'md5': digest, 'size': st.st_size, 'mtime': st.st_mtime}


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_state(state):
    with open(STATE_FILE, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def task_changed(task, state):
    """判断任务是否需要执行：任一依赖源或脚本自身发生变化。"""
    check_paths = list(task['sources']) + ['scripts/' + task['script']]
    for p in check_paths:
        if p.startswith('scripts/'):
            full = os.path.join(ROOT, p)
        else:
            full = os.path.join(ROOT, p)
        fp = file_fingerprint(p)
        old = state.get('files', {}).get(p)
        if fp != old:
            return True, p
    return False, None


def run_script(script_name, timeout=180):
    """运行 build 脚本，返回 (ok, output)。"""
    script = os.path.join(SCRIPTS_DIR, script_name)
    try:
        proc = subprocess.run(
            [sys.executable, script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout,
        )
        out = (proc.stdout or '') + (proc.stderr or '')
        return proc.returncode == 0, out.strip()
    except subprocess.TimeoutExpired:
        return False, f'[超时] {script_name} 超过 {timeout}s'
    except Exception as e:
        return False, f'[异常] {e}'


def verify_outputs(task):
    """校验生成的 json 块数，返回 (ok, 详情列表)。"""
    results = []
    all_ok = True
    for fname, expected in task['outputs']:
        path = os.path.join(DOCS_DIR, fname)
        if not os.path.exists(path):
            all_ok = False
            results.append(f'缺少文件 {fname}')
            continue
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            n = len(data)
            if expected is None:
                results.append(f'{fname}: {n} 块（动态数量）')
                continue
            status = 'OK' if n == expected else f'不符(实际{n}/期望{expected})'
            if n != expected:
                all_ok = False
            results.append(f'{fname}: {n} 块 {status}')
        except Exception as e:
            all_ok = False
            results.append(f'{fname}: 解析失败 {e}')
    return all_ok, results


def summarize_counts():
    """打印当前各语料 json 的块数概览。"""
    print('\n当前语料块数概览：')
    for task in TASKS:
        for fname, expected in task['outputs']:
            path = os.path.join(DOCS_DIR, fname)
            if os.path.exists(path):
                try:
                    with open(path, encoding='utf-8') as f:
                        n = len(json.load(f))
                    mark = '✅' if n == expected else '⚠️'
                    print(f'  {mark} {fname}: {n} 块')
                except Exception:
                    print(f'  ❌ {fname}: 解析失败')
            else:
                print(f'  ❌ {fname}: 缺失')


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='RAG 语料维护调度脚本')
    parser.add_argument('--force', action='store_true', help='强制重跑全部任务')
    parser.add_argument('--check', action='store_true', help='只检测变更，不执行')
    parser.add_argument('--only', metavar='关键词', help='只运行名称包含关键词的任务')
    parser.add_argument('--keep-going', action='store_true', help='单个任务失败后继续执行')
    parser.add_argument('--build-index', action='store_true', help='语料更新后重建向量索引（默认仅提示）')

    parser.add_argument('--strict-audit', action='store_true',
                        help='审计未覆盖项时视为失败')
    args = parser.parse_args()

    issues = rag_audit.audit_hero_coverage(ROOT)
    if issues:
        print("=" * 60)
        print('[audit] 人工补充清单（--strict-audit 时视为失败）:')
        for it in issues:
            print("  - " + it)
        print("=" * 60)
        if args.strict_audit:
            sys.exit(1)

    state = load_state()
    state.setdefault('files', {})
    now = time.strftime('%Y-%m-%d %H:%M:%S')

    print('=' * 64)
    print(f'RAG 语料维护调度  开始时间：{now}')
    print('=' * 64)

    if args.force:
        print('[模式] 强制重跑全部任务')
    elif args.check:
        print('[模式] 只检测变更（不执行）')
    else:
        print('[模式] 增量更新（仅重跑有变更的任务）')

    plan = []
    succeeded = []
    for task in TASKS:
        if args.only and args.only not in task['name']:
            continue
        changed, reason = task_changed(task, state)
        if args.force or changed:
            plan.append((task, reason))
        else:
            print(f'[跳过] {task["name"]}：无变更')

    if not plan:
        print('\n没有需要执行的任务。')
        if not args.check:
            print('提示：若源文件未变但语料疑似过期，可加 --force 强制重跑。')
        summarize_counts()
        return

    print(f'\n计划执行 {len(plan)} 个任务：' + '、'.join(t['name'] for t, _ in plan))
    for task, reason in plan:
        print(f'  - {task["name"]}  <- {task["script"]}' + (f'  （变更源：{reason}）' if reason else ''))

    if args.check:
        print('\n[--check] 检测完成，未执行任何脚本。')
        summarize_counts()
        return

    failed = []
    for task, reason in plan:
        print('\n' + '-' * 64)
        print(f'[执行] {task["name"]}  <-  {task["script"]}')
        if reason:
            print(f'  （变更源：{reason}）')
        ok, output = run_script(task['script'])
        if output:
            # 只显示前 15 行，避免刷屏
            lines = output.splitlines()
            shown = lines[:15]
            for ln in shown:
                print('  | ' + ln)
            if len(lines) > 15:
                print(f'  | ...（共 {len(lines)} 行输出，已截断）')
        if not ok:
            failed.append(task['name'])
            print(f'  ❌ 执行失败：{task["script"]}')
            if not args.keep_going:
                print('终止后续任务（可加 --keep-going 继续）。')
                break
        else:
            v_ok, details = verify_outputs(task)
            for d in details:
                print('  校验: ' + d)
            if v_ok:
                succeeded.append(task['name'])
                print('  ✅ 生成与校验通过')
            else:
                failed.append(task['name'])
                print('  ⚠️ 块数校验未通过')

    # 更新状态文件：无论成败都记录当前指纹（成功的任务记录；失败的也记录以便下次重试）
    for task, _ in plan:
        if task['name'] in failed and not args.force:
            continue
        for p in task['sources'] + ['scripts/' + task['script']]:
            fp = file_fingerprint(p)
            if fp is not None:
                state['files'][p] = fp
    state['last_run'] = now
    save_state(state)

    # 语料更新后的索引联动：--build-index 显式重建；否则仅提示
    if not failed and succeeded:
        if args.build_index:
            print('\n[索引] 语料已更新，重建向量索引 ...')
            try:
                proc = subprocess.run(
                    [sys.executable, '-m', 'src.rag.indexer'], cwd=ROOT,
                    capture_output=True, text=True, encoding='utf-8',
                    errors='replace', timeout=600)
                out = ((proc.stdout or '') + (proc.stderr or '')).strip()
                if proc.returncode == 0:
                    print('  ✅ 索引重建完成')
                else:
                    print('  ❌ 索引重建失败（可稍后手动执行 python -m src.rag.indexer）')
                if out:
                    for ln in out.splitlines()[:15]:
                        print('  | ' + ln)
            except Exception as e:
                print(f'  ❌ 索引重建异常：{e}')
        else:
            print('\n提示：语料已更新，可运行 python -m src.rag.indexer 重建向量索引')

    print('\n' + '=' * 64)
    if failed:
        print(f'完成，但有失败任务：{"、".join(failed)}')
    else:
        print('全部任务执行成功 ✅')
    summarize_counts()
    print('=' * 64)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
