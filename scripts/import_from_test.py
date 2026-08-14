# -*- coding: utf-8 -*-
"""从 test_project 单向同步官方数据（heroes.json / cards.json）到本地 data/。

test_project 的 data/ 是权威源；本脚本先输出差异报告，再复制覆盖。
用法：
    python scripts/import_from_test.py            # 预览差异并确认后执行
    python scripts/import_from_test.py --dry-run  # 仅预览，不写文件
    python scripts/import_from_test.py --yes      # 跳过确认直接执行
"""
import os
ROOT = os.environ.get("RAG_PROJECT_DIR") or r"G:\py_savepoint\mjs_rag_project"
import io, sys, os, json, shutil, argparse, datetime


DEFAULT_TEST_ROOT = r'G:\py_savepoint\test_project'
DATA = os.path.join(ROOT, 'data')
FILES = ['heroes.json', 'cards.json']


def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def build_diff(test_heroes, local_heroes):
    """返回 (lines, changed)。"""
    lines = []
    changed = False
    tmap = {h.get('id'): h for h in test_heroes or []}
    lmap = {h.get('id'): h for h in local_heroes or []}
    added = sorted(set(tmap) - set(lmap))
    removed = sorted(set(lmap) - set(tmap))
    if added:
        changed = True
        lines.append('新增武将 %d 人:' % len(added))
        for i in added:
            lines.append('  + %s (id=%s)' % (tmap[i].get('name', ''), i))
    if removed:
        changed = True
        lines.append('删除武将 %d 人:' % len(removed))
        for i in removed:
            lines.append('  - %s (id=%s)' % (lmap[i].get('name', ''), i))
    for i in sorted(set(tmap) & set(lmap)):
        t, l = tmap[i], lmap[i]
        for k in ('name', 'faction', 'position', 'max_hp', 'max_hand', 'gender', 'difficulty'):
            if t.get(k) != l.get(k):
                changed = True
                lines.append('字段差异 %s (id=%s): %s: %r -> %r' % (t.get('name', ''), i, k, l.get(k), t.get(k)))
        tsk = {s.get('name'): s for s in t.get('skills', [])}
        lsk = {s.get('name'): s for s in l.get('skills', [])}
        for sn in sorted(set(tsk) | set(lsk)):
            if sn not in tsk:
                changed = True
                lines.append('删除技能 %s (%s): %s' % (t.get('name', ''), i, sn))
            elif sn not in lsk:
                changed = True
                lines.append('新增技能 %s (%s): %s' % (t.get('name', ''), i, sn))
            else:
                for k in ('description', 'settlement'):
                    if tsk[sn].get(k) != lsk[sn].get(k):
                        changed = True
                        lines.append('技能差异 %s (%s) [%s]: %s 字段不同' % (t.get('name', ''), i, sn, k))
    return lines, changed


def diff_cards(test_cards, local_cards):
    lines = []
    changed = False
    tmap = {c.get('name'): c for c in test_cards or []}
    lmap = {c.get('name'): c for c in local_cards or []}
    added = sorted(set(tmap) - set(lmap))
    removed = sorted(set(lmap) - set(tmap))
    if added:
        changed = True
        lines.append('新增卡牌 %d 张: %s' % (len(added), '、'.join(added)))
    if removed:
        changed = True
        lines.append('删除卡牌 %d 张: %s' % (len(removed), '、'.join(removed)))
    for name in sorted(set(tmap) & set(lmap)):
        if tmap[name].get('effect') != lmap[name].get('effect'):
            changed = True
            lines.append('卡牌效果差异: %s' % name)
    return lines, changed


def main():
    parser = argparse.ArgumentParser(description='同步 test_project 官方数据到本地 data/')
    parser.add_argument('--test-root', default=DEFAULT_TEST_ROOT, help='test_project 根目录')
    parser.add_argument('--dry-run', action='store_true', help='仅输出差异报告，不写文件')
    parser.add_argument('--yes', action='store_true', help='跳过确认直接执行')
    args = parser.parse_args()

    report = []
    any_changed = False
    for fname in FILES:
        test_path = os.path.join(args.test_root, 'data', fname)
        local_path = os.path.join(DATA, fname)
        if not os.path.exists(test_path):
            report.append('缺少源文件: %s' % test_path)
            continue
        test_data = load_json(test_path)
        local_data = load_json(local_path)
        if fname == 'heroes.json':
            lines, changed = build_diff(test_data, local_data)
        else:
            lines, changed = diff_cards(test_data, local_data)
        report.append('== %s ==' % fname)
        report.extend(lines if lines else ['（无差异）'])
        any_changed = any_changed or changed

    print('=' * 60)
    print('差异报告（test_project -> mjs_rag_project）')
    print('=' * 60)
    print('\n'.join(report))
    print('=' * 60)

    if args.dry_run:
        print('[--dry-run] 未写任何文件。')
        return

    if not any_changed:
        print('无差异，无需同步。')
        return

    if not args.yes:
        ans = input('确认以 test_project 为准覆盖本地 data 文件？(y/N): ').strip().lower()
        if ans not in ('y', 'yes'):
            print('已取消。')
            return

    for fname in FILES:
        test_path = os.path.join(args.test_root, 'data', fname)
        shutil.copyfile(test_path, os.path.join(DATA, fname))
        print('已同步 %s' % fname)

    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    report_dir = os.path.join(DATA, 'sync_report')
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(report_dir, 'import_%s.txt' % stamp)
    with open(report_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(report) + '\n')
    print('差异报告已归档: %s' % report_path)


if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    main()