# -*- coding: utf-8 -*-
"""数据快照层同步脚本（sync_rule_stats.py）
=========================================
维护《元规则整理-完整版.md》中的"数据快照段"（A 层）：从 data/*.json 生成期望统计，
与文档现状对比输出差异报告；人工确认后 --apply 原位替换表格内容（不改标题、不增删章节，
块 ID 不变）。半自动段（3.1/3.2/3.5 等历史 LLM 统计口径）生成候选值，须 --apply-candidates 才应用。

段分类：
- full 全自动段：0.1 卡牌体系、0.2 武将体系、3.5 限定技计数、5.2 数据类 FAQ 行（模板+数据插值）
- candidate 半自动段：3.1/3.2 时机频次、3.5 每种牌限1次/首次类/累计阈值（checkpoint 仅报告）

用法：
    python -m src.scripts.sync_rule_stats                 # 输出差异报告，有差异退出码 1
    python -m src.scripts.sync_rule_stats --only 0.1      # 只处理指定段
    python -m src.scripts.sync_rule_stats --apply         # 应用 full 段差异
    python -m src.scripts.sync_rule_stats --apply --apply-candidates   # 同时应用候选段
    python -m src.scripts.sync_rule_stats --json out.json # 差异报告写入 json
"""
import argparse
import io
import json
import os
import re
import sys
from collections import Counter, defaultdict

from src.config.env import PROJECT_ROOT as ROOT
DEFAULT_DOC = os.path.join(ROOT, 'docs', '元规则整理-完整版.md')
DEFAULT_CHANGELOG = os.path.join(ROOT, 'docs', 'changelog', '元规则changelog.md')

SECTION_NAMES = ('0.1', '0.2', '3.1', '3.2', '3.5', '5.2')
FULL_FAQ_ROWS = ('46', '47', '48', '49', '59', '60', '61', '62')
CHECK_FAQ_ROWS = ('58',)

# 历史 LLM 统计的人工顺序（同计数时保持文档原顺序，避免无意义 diff）
FACTION_ORDER = ('西汉', '曹魏', '秦', '蜀汉', '孙吴', '东汉', '西晋', '赵', '西楚', '燕', '魏', '韩', '楚', '齐', '黄巾', '张楚', '西周')
WEAPON_ORDER = ('龙舌弓', '惊羽弓', '方天画戟', '羽扇', '亮银枪', '丈八蛇矛', '青龙偃月刀', '开山斧',
                '干将莫邪', '鸣鸿刀', '轩辕剑', '诸葛连弩')
CATEGORY_LABELS = {'状态/标记': '状态标记'}
POSITION_ORDER = ('控制', '攻击', '辅助', '爆发', '防御', '治疗')
ACTION_ORDER = ('冲杀', '火杀', '雷杀', '闪避', '蟠桃', '怒气', '易')

HEADING_RE = re.compile(r'^(#{2,3})\s+(.*)$')
SEPARATOR_RE = re.compile(r'^\|\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|$')
TABLE_HEADER_FIRST_CELLS = {'类型', '数量', '内容', '项', '数据', '时机', '频次', '备注', '#', '限制', '含义 [推断]', '示例', '状态', '定义/要点', '来源', '动作', '定义/要点', '单位', '定义', '状态', '限制'}


# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------

def load_data(root=ROOT):
    """加载全部数据源；单个失败返回 None（diff 报告会提示缺源）。"""
    out = {}
    for rel in ('cards.json', 'heroes.json', 'card_points.json', 'equip_attrs.json',
                'card_annotations.json', 'special_cards.json'):
        path = os.path.join(root, 'data', rel)
        if not os.path.exists(path):
            out[rel.split('.')[0]] = None
            continue
        try:
            with open(path, encoding='utf-8') as f:
                out[rel.split('.')[0]] = json.load(f)
        except Exception:
            out[rel.split('.')[0]] = None
    return out


# ---------------------------------------------------------------------------
# 0.1 卡牌体系
# ---------------------------------------------------------------------------

def _sum_amount(card):
    try:
        return int(str(card.get('card_amount', '1')).strip() or '1')
    except (TypeError, ValueError):
        return 1


def gen_card_system_rows(cards):
    """生成 0.1 卡牌体系表三行（全自动）。"""
    by_type = defaultdict(list)
    for c in cards or []:
        by_type[c.get('card_type', '')].append(c)

    action = {c['name']: _sum_amount(c) for c in by_type.get('行动牌', [])}
    kill_parts = ['%s%d' % (n, action[n]) for n in ('冲杀', '火杀', '雷杀') if n in action]
    others = ['%s%d' % (n, action[n]) for n in ACTION_ORDER if n not in ('冲杀', '火杀', '雷杀') and n in action]
    action_content = ''
    if kill_parts:
        action_content = '杀（%s）' % '/'.join(kill_parts)
    if others:
        action_content = (action_content + '、' if action_content else '') + '、'.join(others)

    tactic = sorted(by_type.get('战法牌', []),
                    key=lambda c: int(c['id']) if str(c.get('id', '')).strip().isdigit() else 0)
    tactic_content = '、'.join('%s%d' % (c['name'], _sum_amount(c)) for c in tactic)
    equip_count = sum(_sum_amount(c) for c in by_type.get('装备牌', []))

    return [
        '| 行动牌 | %d | %s |' % (len(action), action_content),
        '| 战法牌 | %d | %s |' % (len(tactic), tactic_content),
        '| 装备牌 | %d | 武器/防具/盔/坐骑，各 1 张 |' % equip_count,
    ]


# ---------------------------------------------------------------------------
# 0.2 武将体系
# ---------------------------------------------------------------------------

def _dist_text(counter, order=None, fmt='%s(%d)'):
    def sort_key(kv):
        k, v = kv
        if order:
            return (-v, order.index(k) if k in order else 99, str(k))
        if isinstance(k, int):
            return (-v, -k, 0)
        return (-v, 0, str(k))
    items = sorted(counter.items(), key=sort_key)
    return '、'.join(fmt % kv for kv in items)


def gen_hero_stats_rows(heroes):
    """生成 0.2 武将体系表五行（全自动）。"""
    heroes = heroes or []
    total_skills = sum(len(h.get('skills', [])) for h in heroes)
    skill_dist = Counter(len(h.get('skills', [])) for h in heroes)
    skill_parts = ['%d 人 %d 技能' % (skill_dist[k], k) for k in sorted(skill_dist)]
    faction = Counter(h.get('faction', '') for h in heroes if h.get('faction'))
    position = Counter(h.get('position', '') for h in heroes if h.get('position'))
    hp = Counter(h.get('max_hp') for h in heroes if h.get('max_hp') is not None)
    hand = Counter(h.get('max_hand') for h in heroes if h.get('max_hand') is not None)
    return [
        '| 武将数 | %d（每武将 2~3 个技能，共 %d 个；%s） |' % (len(heroes), total_skills, ' / '.join(skill_parts)),
        '| 阵营 | %d 种：%s |' % (len(faction), _dist_text(faction, FACTION_ORDER, '%s%d')),
        '| 定位 | %d 种：%s |' % (len(position), _dist_text(position, POSITION_ORDER, '%s%d')),
        '| 体力（max_hp） | %s |' % _dist_text(hp, fmt='%d(%d)'),
        '| 手牌上限（max_hand） | %s |' % _dist_text(hand, fmt='%d(%d)'),
    ]


# ---------------------------------------------------------------------------
# 3.1 / 3.2 时机频次（半自动候选）
# ---------------------------------------------------------------------------

STAGE_TIMING_RULES = (
    ('出牌阶段', '出牌阶段'),
    ('回合开始时', '回合开始时'),
    ('回合结束时', '回合结束时'),
    ('摸牌阶段', '摸牌阶段'),
    ('弃牌阶段', '弃牌阶段'),
)

TRIGGER_TIMING_RULES = (
    ('你打出牌时', ('你打出',)),
    ('你选择杀的目标时', ('选择', '目标')),
    ('你造成伤害时', ('造成伤害时',)),
    ('你即将受到伤害时', ('即将受到伤害',)),
    ('你成为其他角色打出牌的目标时', ('成为', '目标')),
    ('你获得（其他角色的）牌时', ('获得', '牌')),
    ('你失去最后的手牌时', ('失去最后的',)),
    ('有角色受到伤害时', ('受到伤害时',)),
    ('你失去体力时', ('失去体力时',)),
    ('你弃牌时', ('弃牌时',)),
    ('其他角色阵亡时', ('阵亡时',)),
    ('你的体力值首次变为1时', ('首次变为1',)),
    ('有牌被放入牌堆时', ('放入牌堆',)),
    ('你的手牌上限大于体力值时', ('手牌上限大于体力',)),
    ('你打出削弱牌时', ('削弱牌',)),
    ('击杀时', ('击杀',)),
)


def _skill_texts(heroes):
    for h in heroes or []:
        for sk in h.get('skills', []):
            desc = sk.get('description', '') or ''
            if desc:
                yield desc


def gen_timing_candidates(heroes, rules):
    """按技能级统计（一个技能描述命中全部关键词计 1 次），返回 {时机名: 频次}。"""
    texts = list(_skill_texts(heroes))
    return {name: sum(1 for t in texts if all(k in t for k in keywords)) for name, keywords in rules}


def gen_limit_checks(heroes):
    """限定技计数（全自动）；每种牌限1次/首次类/累计阈值为候选。"""
    texts = list(_skill_texts(heroes))
    return {
        'limited': sum(1 for t in texts if t.startswith('限定，')),
        'per_card': sum(1 for t in texts if '每种牌限1次' in t),
        'first_time': sum(1 for t in texts if '首次' in t),
        'cumulative': sum(1 for t in texts if '累计' in t),
    }


# ---------------------------------------------------------------------------
# 5.2 数据类 FAQ 行（模板 + 数据插值）
# ---------------------------------------------------------------------------

def _card_points_stats(cp):
    stats = defaultdict(lambda: {'suits': set(), 'points': set(), 'count': 0})
    for c in (cp or {}).get('cards', []) or []:
        name = c.get('name', '')
        if not name:
            continue
        stats[name]['suits'].add(str(c.get('suit', '')))
        stats[name]['points'].add(str(c.get('point', '')))
        stats[name]['count'] += int(str(c.get('count', '1')).strip() or '1')
    return stats


def gen_faq_rows(data):
    """生成 5.2 数据类 FAQ 行（整行模板，数字全部由数据插值）。"""
    cp = data.get('card_points')
    stats = _card_points_stats(cp)
    fire = stats.get('火杀', {}).get('count', 0)
    thunder = stats.get('雷杀', {}).get('count', 0)
    normal = stats.get('冲杀', {}).get('count', 0)
    yi_st = stats.get('易', {})
    yi_suit = '太极' if '太极' in yi_st.get('suits', set()) else (''.join(sorted(yi_st.get('suits', set()))) or '?')
    yi_points = '、'.join(sorted(yi_st.get('points', set()), key=lambda p: int(p) if p.isdigit() else 99))

    rows = {
        '46': '| 46 | 火杀全部为♥（%d张），♥=火属性（点数表确认） | 点数表 |' % fire,
        '47': '| 47 | 雷杀全部为♦4（%d张）；普通杀为♦非4（%d张）；点数4且非♥即雷 | 点数表 |' % (thunder, normal),
        '48': '| 48 | 易=%s花色（%d张，点数%s）；基础牌堆中太极花色仅用于易（专属牌中另有太极牌，如传国玉玺、猛兽牌象、张良/陈平专属战法牌） | 点数表 |' % (yi_suit, yi_st.get('count', 0), yi_points),
    }
    deck = []
    for name, note in (('闪避', '♠'), ('蟠桃', '♣'), ('怒气', '♣'), ('冲杀', '♦'), ('火杀', '♥'), ('雷杀', '♦'), ('易', '太极')):
        st = stats.get(name)
        if st and st['count']:
            deck.append('%s%s%d' % (name, note, st['count']))
    rows['49'] = '| 49 | 牌堆构成：%s | 点数表 |' % '、'.join(deck)

    ann = data.get('card_annotations') or {}
    rows['58'] = '| 58 | 加强/削弱效果经 card_annotations.json 关联卡牌，%d张全覆盖；结算详情取自 settlement_rules（2026-08-12 补充） | 数据表 |' % len(ann.get('annotations', []) or [])

    equip = data.get('equip_attrs') or []
    minus = [e['name'] for e in equip if e.get('distance_mod') == -1]
    plus = [e['name'] for e in equip if e.get('distance_mod') == 1]
    rows['59'] = '| 59 | 坐骑距离修正：距离-1（攻击）＝%s；距离+1（防御）＝%s | 装备属性表 |' % ('/'.join(minus), '/'.join(plus))
    weapons = [e for e in equip if e.get('subtype') == '武器' and e.get('attack_range') is not None]
    weapons.sort(key=lambda e: (-int(e['attack_range']),
                                WEAPON_ORDER.index(e['name']) if e['name'] in WEAPON_ORDER else 99,
                                e['name']))
    rows['60'] = '| 60 | 武器攻击范围：%s | 装备属性表 |' % '/'.join('%s%d' % (e['name'], int(e['attack_range'])) for e in weapons)

    heroes = data.get('heroes') or []
    rows['61'] = '| 61 | 限定技=本局限1次，描述以"限定，"开头（%d个技能）；部分效果仅限1次（每种牌限1次/首次类/累计阈值） | 数据统计 |' % gen_limit_checks(heroes)['limited']

    sc = data.get('special_cards') or []
    cat = Counter(CATEGORY_LABELS.get(c.get('category', ''), c.get('category', '')) for c in sc)
    parts = ['%s%d' % (k, cat[k]) for k in ('专属牌', '专属战法牌', '特殊牌区', '状态标记', '概念') if cat.get(k)]
    rows['62'] = '| 62 | 特殊机制（%s，共%d块）详见《特殊机制语料》 | 人工整理 |' % ('/'.join(parts), sum(cat.values()))
    return rows


# ---------------------------------------------------------------------------
# 文档定位与差异
# ---------------------------------------------------------------------------

def find_section(lines, heading_prefix):
    """返回标题行（含）到下一个同级/上级标题前的行区间 [start, end)；找不到返回 None。"""
    start = None
    start_level = None
    for i, ln in enumerate(lines):
        m = HEADING_RE.match(ln)
        if m and m.group(2).strip().startswith(heading_prefix):
            start = i
            start_level = len(m.group(1))
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


def _is_table_header(ln):
    if not ln.startswith('|'):
        return False
    first = ln.split('|')[1].strip()
    return first in TABLE_HEADER_FIRST_CELLS


def table_rows_in(lines, start, end):
    """返回区间内数据表格行 [(行号, 文本)]（跳过表头与分隔行）。"""
    rows = []
    for i in range(start, end):
        ln = lines[i]
        if not ln.startswith('|') or SEPARATOR_RE.match(ln) or _is_table_header(ln):
            continue
        rows.append((i, ln))
    return rows


def _match_rows(table, expected, section, message, kind, issues):
    for (ln, old), new in zip(table, expected):
        if old != new:
            issues.append({'section': section, 'line_no': ln, 'old': old, 'new': new,
                           'kind': kind, 'message': message})
    if len(table) != len(expected):
        issues.append({'section': section, 'line_no': table[0][0] if table else 0,
                       'old': None, 'new': None, 'kind': kind,
                       'message': '%s 表行数不一致（文档 %d 行 vs 数据 %d 行，数据源变化）'
                                  % (section, len(table), len(expected))})


def diff_sections(doc_text, data, only=None):
    """对比文档与数据，返回差异项列表（full/candidate/checkpoint）。"""
    lines = doc_text.splitlines()
    issues = []

    if only is None or only == '0.1':
        sec = find_section(lines, '0.1')
        if sec:
            _match_rows(table_rows_in(lines, *sec), gen_card_system_rows(data.get('cards')),
                        '0.1', '卡牌体系统计与 cards.json 不一致', 'full', issues)
    if only is None or only == '0.2':
        sec = find_section(lines, '0.2')
        if sec:
            _match_rows(table_rows_in(lines, *sec), gen_hero_stats_rows(data.get('heroes')),
                        '0.2', '武将体系统计与 heroes.json 不一致', 'full', issues)

    if only is None or only in ('3.1', '3.2'):
        stage = gen_timing_candidates(data.get('heroes'), STAGE_TIMING_RULES)
        trigger = gen_timing_candidates(data.get('heroes'), TRIGGER_TIMING_RULES)
        for sec_name, counts in (('3.1', stage), ('3.2', trigger)):
            sec = find_section(lines, sec_name)
            if not sec:
                continue
            for ln, old in table_rows_in(lines, *sec):
                first = old.split('|')[1].strip()
                if first in counts:
                    m = re.match(r'^(\|\s*%s\s*\|\s*)\d+(\s*\|.*)$' % re.escape(first), old)
                    if m:
                        expected = '%s%d%s' % (m.group(1), counts[first], m.group(2))
                        if expected != old:
                            issues.append({'section': sec_name, 'line_no': ln, 'old': old, 'new': expected,
                                           'kind': 'candidate',
                                           'message': '时机频次为半自动候选值（正则按技能级统计），需人工确认'})

    if only is None or only == '3.5':
        checks = gen_limit_checks(data.get('heroes'))
        sec = find_section(lines, '3.5')
        if sec:
            for ln, old in table_rows_in(lines, *sec):
                if '限定' in old and '共' in old and '个技能' in old:
                    expected = re.sub(r'共\d+个技能', '共%d个技能' % checks['limited'], old)
                    if expected != old:
                        issues.append({'section': '3.5', 'line_no': ln, 'old': old, 'new': expected,
                                       'kind': 'full', 'message': '限定技计数与 heroes.json 不一致'})
                elif '每种牌限1次' in old:
                    m = re.search(r'每种牌限1次\((\d+)处\)', old)
                    if m and int(m.group(1)) != checks['per_card']:
                        issues.append({'section': '3.5', 'line_no': ln, 'old': old, 'new': None,
                                       'kind': 'checkpoint',
                                       'message': '每种牌限1次处数：文档 %s vs 数据 %d（候选，需人工确认）'
                                                  % (m.group(1), checks['per_card'])})
                elif '首次类' in old and '累计阈值' in old:
                    m1 = re.search(r'首次类\((\d+)处\)', old)
                    m2 = re.search(r'累计阈值一次性\((\d+)处\)', old)
                    if m1 and int(m1.group(1)) != checks['first_time']:
                        issues.append({'section': '3.5', 'line_no': ln, 'old': old, 'new': None,
                                       'kind': 'checkpoint',
                                       'message': '首次类处数：文档 %s vs 数据 %d（候选，需人工确认）'
                                                  % (m1.group(1), checks['first_time'])})
                    if m2 and int(m2.group(1)) != checks['cumulative']:
                        issues.append({'section': '3.5', 'line_no': ln, 'old': old, 'new': None,
                                       'kind': 'checkpoint',
                                       'message': '累计阈值处数：文档 %s vs 数据 %d（候选，需人工确认）'
                                                  % (m2.group(1), checks['cumulative'])})

    if only is None or only == '5.2':
        faq_rows = gen_faq_rows(data)
        sec = find_section(lines, '5.2')
        if sec:
            for ln, old in table_rows_in(lines, *sec):
                first = old.split('|')[1].strip()
                if first in faq_rows and old != faq_rows[first]:
                    kind = 'full' if first in FULL_FAQ_ROWS else 'checkpoint'
                    issues.append({'section': '5.2', 'line_no': ln, 'old': old, 'new': faq_rows[first],
                                   'kind': kind,
                                   'message': 'FAQ #%s 数据类条目与数据源不一致' % first})
    return issues


# ---------------------------------------------------------------------------
# 应用差异
# ---------------------------------------------------------------------------

def apply_diffs(doc_text, data, only=None, apply_candidates=False):
    """应用差异，返回 (new_text, applied)。候选段仅当 apply_candidates=True 时应用。"""
    issues = diff_sections(doc_text, data, only)
    lines = doc_text.splitlines()
    replacements = {}
    applied = []
    for it in issues:
        if it['new'] is None:
            continue  # checkpoint 仅报告
        if it['kind'] == 'candidate' and not apply_candidates:
            continue
        replacements[it['line_no']] = it['new']
        applied.append(it)
    for ln, new in sorted(replacements.items(), reverse=True):
        lines[ln] = new
    return '\n'.join(lines), applied


def apply_confirmed(confirmed, doc_text):
    """按确认清单逐行替换；返回 (new_text, applied, errors)。

    预检全部行后再写：任一 hard error（行号越界 / 当前行 != old / 新值含竖线）
    → 整批拒绝（返回原文本 + errors），文档零副作用。
    每项：{"section", "line_no"(0 基), "old", "new", "message"}。
    """
    lines = doc_text.splitlines()
    applied = []
    errors = []
    for it in confirmed:
        line_no = it.get('line_no')
        new = it.get('new')
        section = it.get('section', '?')
        if not isinstance(line_no, int) or line_no < 0 or line_no >= len(lines):
            errors.append('%s: 行号 %s 越界（文档共 %d 行）' % (section, line_no, len(lines)))
            continue
        old = it.get('old')
        if old is not None and lines[line_no] != old:
            errors.append('%s: 第 %d 行与确认时不一致（文档可能已被修改）：%s'
                          % (section, line_no + 1, lines[line_no][:40]))
            continue
        # 确认值 = 完整表格行：以 | 起止，且列数与 old 一致（防结构破坏）
        if not isinstance(new, str) or not new.strip():
            errors.append('%s: 第 %d 行确认值为空' % (section, line_no + 1))
            continue
        if not (new.startswith('|') and new.endswith('|')):
            errors.append('%s: 第 %d 行确认值不是完整表格行（需以 | 开头和结尾）' % (section, line_no + 1))
            continue
        if old is not None and len(new.split('|')) != len(old.split('|')):
            errors.append('%s: 第 %d 行确认值列数与原文不一致（%d vs %d）'
                          % (section, line_no + 1, len(new.split('|')), len(old.split('|'))))
            continue
        applied.append(it)
    if errors:
        return doc_text, [], errors
    for it in applied:
        lines[it['line_no']] = it['new']
    return '\n'.join(lines), applied, []


def append_changelog(applied, changelog_path=DEFAULT_CHANGELOG):
    """在 changelog 的变更记录表头后插入一行（只追加不删除）。"""
    if not applied:
        return False
    today = __import__('datetime').date.today().isoformat()
    sections = sorted({a['section'] for a in applied})
    summary = '；'.join('%s: %s' % (a['section'], a['message'][:40]) for a in applied[:5])
    if len(applied) > 5:
        summary += ' 等 %d 处' % len(applied)
    line = '| %s | %s | 数据同步 | %s | 数据源自动生成 | 待确认 |' % (today, '/'.join(sections), summary)
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
    return True


def refresh_snapshot(doc_path):
    """应用后刷新 .rule_doc_snapshot.json，使数据段更新成为新基线。"""
    from src.scripts.audit_rule_doc import build_snapshot, write_snapshot, DEFAULT_SNAPSHOT
    write_snapshot(build_snapshot(doc_path, ROOT), DEFAULT_SNAPSHOT)
    return True


# ---------------------------------------------------------------------------
# 报告与 CLI
# ---------------------------------------------------------------------------

def build_report(issues):
    lines = ['# 元规则数据段差异报告', '',
             '> 生成：%s（sync_rule_stats.py）' % __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
             '> full=全自动可应用；candidate=半自动候选需 --apply-candidates；checkpoint=仅数字校验提示。',
             '', '| 段 | 行号 | 类型 | 差异摘要 |', '|---|---|---|---|']
    for it in issues:
        summary = it['message']
        if it['old'] is not None and it['new'] is not None:
            summary += '：`%s` → `%s`' % (it['old'].strip()[:50], it['new'].strip()[:50])
        lines.append('| %s | %s | %s | %s |' % (it['section'], it['line_no'] + 1, it['kind'], summary))
    lines.append('')
    if not issues:
        lines.append('未发现差异。')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description='元规则数据快照段同步')
    parser.add_argument('--only', choices=SECTION_NAMES, default=None, help='只处理指定段')
    parser.add_argument('--apply', action='store_true', help='应用差异（默认仅报告）')
    parser.add_argument('--apply-candidates', action='store_true', help='同时应用候选段差异')
    parser.add_argument('--apply-json', default=None, help='应用确认清单（逐行替换；退出码 0=成功/1=预检失败/2=前置失败）')
    parser.add_argument('--doc', default=DEFAULT_DOC, help='文档路径')
    parser.add_argument('--json', default=None, help='差异报告输出 json 路径')
    args = parser.parse_args()

    with open(args.doc, encoding='utf-8') as f:
        doc_text = f.read()
    data = load_data()
    issues = diff_sections(doc_text, data, args.only)
    report = build_report(issues)

    if args.apply:
        new_text, applied = apply_diffs(doc_text, data, args.only, args.apply_candidates)
        if applied:
            with open(args.doc, 'w', encoding='utf-8', newline='\n') as f:
                f.write(new_text)
            append_changelog(applied)
            if os.path.abspath(args.doc) == os.path.abspath(DEFAULT_DOC):
                refresh_snapshot(args.doc)
            else:
                print('[提示] 非默认文档，跳过快照刷新。')
            print('已应用 %d 处差异（%s）' % (len(applied), '、'.join(sorted({a['section'] for a in applied}))))
        else:
            print('无可应用的差异（候选段需 --apply-candidates）。')
        print(report)
        sys.exit(1 if issues else 0)
    elif args.apply_json:
        try:
            with open(args.apply_json, encoding='utf-8') as f:
                confirmed = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            print('无法读取确认清单 %s：%s' % (args.apply_json, exc))
            sys.exit(2)
        if not isinstance(confirmed, list):
            print('确认清单格式错误：应为数组')
            sys.exit(2)
        new_text, applied, errors = apply_confirmed(confirmed, doc_text)
        if errors:
            print('应用失败 %d 处，未修改文档：' % len(errors))
            for e in errors:
                print('  -', e)
            sys.exit(1)
        with open(args.doc, 'w', encoding='utf-8', newline='\n') as f:
            f.write(new_text)
        for a in applied:
            a.setdefault('message', '')
        append_changelog(applied)
        if os.path.abspath(args.doc) == os.path.abspath(DEFAULT_DOC):
            refresh_snapshot(args.doc)
        else:
            print('[提示] 非默认文档，跳过快照刷新。')
        print('已应用 %d 处确认差异（%s）' % (len(applied), '、'.join(sorted({a['section'] for a in applied}))))
        sys.exit(0)
    else:
        print(report)
        if args.json:
            with open(args.json, 'w', encoding='utf-8', newline='\n') as f:
                json.dump(issues, f, ensure_ascii=False, indent=2)
        sys.exit(1 if issues else 0)


if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    main()