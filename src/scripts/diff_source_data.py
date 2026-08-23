# -*- coding: utf-8 -*-
"""数据源变更清单生成（diff_source_data.py）

对比新旧 data/*.json，输出"类型 | 文件 | 对象 | 名称 | 变更摘要 | 是否新机制"清单，
供《元规则T0文档维护方案》第 3 节"变更分流"使用。

- 默认旧基线：data/backups/ 中最新的 <文件名>-*.json 备份；也可用 --old 指定文件或目录。
- 新机制判定为启发式：命中完整版术语表/机制关键词时仅标"疑似需人工确认"，不自动判定。

用法：
    python -m src.scripts.diff_source_data
    python -m src.scripts.diff_source_data --old data/backups/heroes-20260730-233916-573897.json
    python -m src.scripts.diff_source_data --data heroes,cards
    python -m src.scripts.diff_source_data --out docs/changelog/变更清单-2026-08-15.md
"""
import io
import os
import sys
import json
import glob
import argparse

from src.config.env import PROJECT_ROOT as ROOT
DATA_DIR = os.path.join(ROOT, 'data')
BACKUP_DIR = os.path.join(DATA_DIR, 'backups')
DOC = os.path.join(ROOT, 'docs', '元规则整理-完整版.md')

# 机制关键词（启发式；与完整版术语表并集使用）
MECH_KEYWORDS = [
    '时机', '触发', '状态', '区域', '资源', '牌型', '判定', '首次', '每轮', '回合开始时',
    '出牌阶段', '上限', '移除', '标记', '印记', '点数', '花色', '转化', '复制', '交换',
    '获得', '失去', '继承', '附加', '锁定', '限定', '濒死', '阵亡', '亡语', '卜卦',
    '烧毁', '销毁', '弃置', '装备', '距离', '伤害', '回复', '摸牌', '弃牌', '重铸',
    '洗牌', '查看', '放置', '移出', '结算', '同时', '插入', '响应', '识破', '增伤',
    '减伤', '叠加', '计数', '连用', '专属', '战法', '削弱', '增强', '资源', '印记',
]

FIELD_LABELS = {
    'max_hp': '体力', 'max_hand': '手牌上限', 'faction': '阵营', 'position': '定位',
    'title': '称号', 'gender': '性别', 'last_updated': '更新时间',
    'card_desc': '描述', 'card_detail': '详情', 'card_amount': '张数', 'card_type': '类型',
    'attack_range': '攻击范围', 'distance_mod': '距离修正', 'subtype': '细分', 'note': '备注',
    'suit': '花色', 'point': '点数', 'effect': '效果', 'settlement': '结算', 'hero': '归属武将',
}


def load_lexicon():
    """完整版术语名 + 内置机制关键词。"""
    words = set(MECH_KEYWORDS)
    try:
        from src.scripts import build_rule_corpus as brc
        _, terms, _, _ = brc.parse_rule_doc(DOC)
        words.update(t['term'] for t in terms)
    except Exception:
        pass
    return words


def is_mechanism_like(text, words):
    if not text:
        return False
    for w in words:
        if w and w in text:
            return True
    return False


def load_json(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def newest_backup(stem):
    pat = os.path.join(BACKUP_DIR, stem + '-*.json')
    hits = sorted(glob.glob(pat))
    return hits[-1] if hits else None


def as_list(d):
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        # 常见容器：annotations / cards / hero_categories
        for k in ('annotations', 'cards'):
            if isinstance(d.get(k), list):
                return d[k]
        return list(d.values())
    return []


def key_of(item, key):
    v = item.get(key)
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return str(v)


def diff_heroes(old, new, words):
    rows = []
    om = {key_of(x, 'id'): x for x in as_list(old) if key_of(x, 'id') is not None}
    nm = {key_of(x, 'id'): x for x in as_list(new) if key_of(x, 'id') is not None}
    for k in sorted(set(nm) - set(om)):
        h = nm[k]
        rows.append(('新增', 'heroes.json', k, h.get('name', ''),
                     '新增武将（%d 个技能）' % len(h.get('skills', [])),
                     '疑似新机制（新武将，机制待人工核对）' if h.get('skills') else '否'))
    for k in sorted(set(om) - set(nm)):
        h = om[k]
        rows.append(('下架', 'heroes.json', k, h.get('name', ''), '从数据中消失', '否（交叉引用校验会报警）'))
    for k in sorted(set(om) & set(nm)):
        o, n = om[k], nm[k]
        if o == n:
            continue
        if o.get('name') != n.get('name'):
            rows.append(('改名', 'heroes.json', k, '%s → %s' % (o.get('name'), n.get('name')),
                         '武将改名', '否（需同步检查引用）'))
            continue
        parts = []
        mech_hit = False
        # 技能对比
        os_map = {s.get('name'): s for s in o.get('skills', [])}
        ns_map = {s.get('name'): s for s in n.get('skills', [])}
        for sn in sorted(set(ns_map) - set(os_map)):
            parts.append('新增技能:%s' % sn)
            mech_hit = mech_hit or is_mechanism_like(str(ns_map[sn]), words)
        for sn in sorted(set(os_map) - set(ns_map)):
            parts.append('删除技能:%s' % sn)
        for sn in sorted(set(os_map) & set(ns_map)):
            if os_map[sn] != ns_map[sn]:
                parts.append('技能:%s 描述/结算变更' % sn)
                mech_hit = mech_hit or is_mechanism_like(str(ns_map[sn]), words)
        # 普通字段
        for f in ('max_hp', 'max_hand', 'faction', 'position', 'title', 'gender', 'last_updated'):
            if o.get(f) != n.get(f):
                parts.append('%s:%s→%s' % (FIELD_LABELS.get(f, f), o.get(f), n.get(f)))
        if not parts:
            continue
        rows.append(('修改', 'heroes.json', k, n.get('name', ''), '；'.join(parts),
                     '疑似新机制（词表命中，需人工确认）' if mech_hit else '未命中（建议人工核对）'))
    return rows


def diff_generic(fname, old, new, key_field, fields, words):
    rows = []
    om = {key_of(x, key_field): x for x in as_list(old) if key_of(x, key_field) is not None}
    nm = {key_of(x, key_field): x for x in as_list(new) if key_of(x, key_field) is not None}
    for k in sorted(set(nm) - set(om)):
        it = nm[k]
        text = ' '.join(str(it.get(f, '')) for f in fields)
        rows.append(('新增', fname, k, it.get('name', ''),
                     '新增条目', '疑似新机制（需人工确认）' if is_mechanism_like(text, words) else '否'))
    for k in sorted(set(om) - set(nm)):
        it = om[k]
        rows.append(('下架', fname, k, it.get('name', ''), '从数据中消失', '否'))
    for k in sorted(set(om) & set(nm)):
        o, n = om[k], nm[k]
        if o == n:
            continue
        parts = []
        mech = False
        for f in fields:
            if o.get(f) != n.get(f):
                parts.append('%s:%s→%s' % (FIELD_LABELS.get(f, f), o.get(f), n.get(f)))
                mech = mech or is_mechanism_like(str(n.get(f, '')), words)
        if parts:
            rows.append(('修改', fname, k, n.get('name', ''),
                         '；'.join(parts),
                         '疑似新机制（需人工确认）' if mech else '未命中（建议人工核对）'))
    return rows


def diff_cards(old, new, words):
    return diff_generic('cards.json', old, new, 'id',
                        ['name', 'card_desc', 'card_detail', 'card_amount', 'card_type'], words)


def diff_card_annotations(old, new, words):
    rows = []
    om = {x.get('card_id'): x for x in as_list(old)}
    nm = {x.get('card_id'): x for x in as_list(new)}
    for k in sorted(set(nm) - set(om)):
        rows.append(('新增', 'card_annotations.json', k, '卡牌%s' % k, '新增加强/削弱标注', '否'))
    for k in sorted(set(om) - set(nm)):
        rows.append(('下架', 'card_annotations.json', k, '卡牌%s' % k, '标注消失', '否'))
    for k in sorted(set(om) & set(nm)):
        if om[k] != nm[k]:
            o, n = om[k], nm[k]
            parts = []
            mech = False
            for kind in ('strengthen_effect', 'weaken_effect'):
                ol = o.get('fields', {}).get(kind, []) or []
                nl = n.get('fields', {}).get(kind, []) or []
                if len(ol) != len(nl):
                    parts.append('%s 条数变化' % kind)
                for i in range(min(len(ol), len(nl))):
                    if ol[i] != nl[i]:
                        parts.append('%s 内容/结算变更' % kind)
                        mech = mech or is_mechanism_like(str(nl[i]), words)
            if parts:
                rows.append(('修改', 'card_annotations.json', k, '卡牌%s' % k, '；'.join(parts),
                             '疑似新机制（需人工确认）' if mech else '未命中（建议人工核对）'))
    return rows


def diff_card_points(old, new, words):
    rows = []
    o_cards = {x.get('name'): x for x in as_list(old.get('cards', []))}
    n_cards = {x.get('name'): x for x in as_list(new.get('cards', []))}
    for k in sorted(set(n_cards) - set(o_cards)):
        rows.append(('新增', 'card_points.json', k, k, '新增点数/花色条目', '否'))
    for k in sorted(set(o_cards) - set(n_cards)):
        rows.append(('下架', 'card_points.json', k, k, '点数条目消失', '否'))
    for k in sorted(set(o_cards) & set(n_cards)):
        if o_cards[k] != n_cards[k]:
            rows.append(('修改', 'card_points.json', k, k,
                         '花色/点数变更（%s→%s）' % (o_cards[k], n_cards[k]),
                         '否（如新判定牌需补 judge_rules）'))
    if old.get('judge_rules') != new.get('judge_rules'):
        rows.append(('修改', 'card_points.json', 'judge_rules', '卜卦判定规则',
                     '判定规则变更', '疑似新机制（需人工确认）' if is_mechanism_like(str(new.get('judge_rules')), words) else '未命中'))
    return rows


def diff_hero_classification(old, new):
    rows = []
    o_cat = old.get('hero_categories', {}) or {}
    n_cat = new.get('hero_categories', {}) or {}
    for k in sorted(set(n_cat) - set(o_cat)):
        rows.append(('新增', 'hero_classification.json', k, k, '新增武将分类', '否'))
    for k in sorted(set(o_cat) - set(n_cat)):
        rows.append(('下架', 'hero_classification.json', k, k, '分类消失', '否'))
    for k in sorted(set(o_cat) & set(n_cat)):
        if o_cat[k] != n_cat[k]:
            rows.append(('修改', 'hero_classification.json', k, k, '分类变更', '否'))
    return rows


HANDLERS = {
    'heroes': ('heroes.json', diff_heroes),
    'cards': ('cards.json', diff_cards),
    'card_annotations': ('card_annotations.json', diff_card_annotations),
    'card_points': ('card_points.json', diff_card_points),
    'equip_attrs': ('equip_attrs.json',
                    lambda o, n, w: diff_generic('equip_attrs.json', o, n, 'name',
                                                 ['attack_range', 'distance_mod', 'subtype', 'note'], w)),
    'special_cards': ('special_cards.json',
                      lambda o, n, w: diff_generic('special_cards.json', o, n, 'name',
                                                   ['suit', 'point', 'effect', 'settlement', 'hero',
                                                    'card_type', 'category'], w)),
    'hero_classification': ('hero_classification.json', lambda o, n, w: diff_hero_classification(o, n)),
}


def find_old(fname, old_arg):
    if os.path.isdir(old_arg):
        return newest_backup(os.path.splitext(fname)[0])
    if os.path.isfile(old_arg):
        return old_arg
    return None


def main():
    parser = argparse.ArgumentParser(description='数据源变更清单生成')
    parser.add_argument('--old', default=BACKUP_DIR, help='旧基线文件或备份目录（默认 data/backups）')
    parser.add_argument('--data', default=','.join(HANDLERS), help='要对比的文件，逗号分隔')
    parser.add_argument('--out', default=None, help='输出 markdown 路径（默认仅控制台）')
    args = parser.parse_args()

    words = load_lexicon()
    rows = []
    for name in args.data.split(','):
        name = name.strip()
        if name not in HANDLERS:
            print('跳过未知数据源：%s' % name)
            continue
        fname, handler = HANDLERS[name]
        new = load_json(os.path.join(DATA_DIR, fname))
        if new is None:
            print('跳过 %s：新文件读取失败' % fname)
            continue
        old_path = find_old(fname, args.old)
        if not old_path:
            print('跳过 %s：无旧基线（backups 目录无 %s-*.json）' % (fname, os.path.splitext(fname)[0]))
            continue
        old = load_json(old_path)
        if old is None:
            print('跳过 %s：旧基线读取失败 %s' % (fname, old_path))
            continue
        if old == new:
            continue
        rows.extend(handler(old, new, words))

    if not rows:
        print('未发现变更。')
        return
    lines = ['| 类型 | 文件 | 对象 | 名称 | 变更摘要 | 是否新机制 |', '|---|---|---|---|---|---|']
    for r in rows:
        lines.append('| %s | %s | %s | %s | %s | %s |' % tuple(str(x) for x in r))
    body = '\n'.join(lines)
    print(body)
    if args.out:
        out = args.out
        if os.path.isdir(out):
            import datetime
            out = os.path.join(out, '变更清单-%s.md' % datetime.date.today().isoformat())
        with open(out, 'w', encoding='utf-8', newline='\n') as f:
            f.write('# 数据源变更清单\n\n> 生成：%s（diff_source_data.py）；新机制标记为启发式疑似，须人工确认。\n\n%s\n'
                    % (__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S'), body))
        print('\n已写入：%s' % out)


if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    main()