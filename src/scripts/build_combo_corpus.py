# -*- coding: utf-8 -*-
"""生成组合RAG语料：csv亮点块 + combos md深解块 + 手录导入块，按武将对去重合并 + 统计。

块结构（indexer _norm_combo 消费）：
  block_id = combo_{A}_{B}（武将名排序保证唯一；孟尝君+黄月英深解按曲拆 _1..4）
  hero_a / hero_b / highlight(csv亮点/手录) / mechanism(md深解) / bv / pub_date / source_md / related
设计点 A：组合块不贴 hero（_norm_combo 不设 hero 元数据）
设计点 C：强力组合多选展开、盘点按搭档拆、孟尝君深解按曲拆、csv一行一块
设计点 D：同武将对合并（csv亮点+md深解+手录），block_id 唯一
"""
import csv
import re
from pathlib import Path

from src.scripts.rag_common import CORPUS, load_json, save_json, setup_stdout, project_path

setup_stdout()

COMBOS = Path('data/raw_guides/jinxia/combos')

heroes_raw = load_json(project_path('data', 'heroes.json'))
_hl = heroes_raw['heroes'] if isinstance(heroes_raw, dict) and 'heroes' in heroes_raw else heroes_raw
NAMES = {h['name'] for h in _hl if isinstance(h, dict) and 'name' in h}
print('武将名表:', len(NAMES))

# 武将名修正（盘点 md 笔误）
NAME_FIX = {'春生君': '春申君', '王简': '王翦'}


def key(a, b):
    return tuple(sorted([a, b]))


def split_table(line):
    """返回表格行各列（去首尾空元素），非表格/分隔行/表头行返回 None。"""
    if not line.startswith('|'):
        return None
    parts = [p.strip() for p in line.split('|')]
    if parts and parts[0] == '':
        parts = parts[1:]
    if parts and parts[-1] == '':
        parts = parts[:-1]
    if not parts:
        return None
    if all(re.match(r'^[-:]+$', p) for p in parts):  # |---|---|
        return None
    joined = ''.join(parts)
    hdrs = ['配合逻辑', '机制', '效果', '档位', '轮次', '牌量', '维度', '内容',
            '可复制', '操作流程', '目标武将', '造出', '名称', '难度', '曲序', '来源']
    if all(len(p) <= 5 for p in parts) and any(h in joined for h in hdrs):
        return None
    return parts


# ============================================================
# 1. csv 亮点
# ============================================================
csv_data = {}  # key -> {highlight, bv, pub_date}
with open(COMBOS / 'bilibili_videos_weijiang.csv', encoding='utf-8') as f:
    for r in csv.DictReader(f):
        a = (r.get('武將1') or r.get('武将1') or '').strip()
        b = (r.get('武將2') or r.get('武将2') or '').strip()
        if a not in NAMES or b not in NAMES:
            continue
        k = key(a, b)
        csv_data[k] = {
            'highlight': (r.get('描述') or '').strip(),
            'bv': (r.get('BV号') or '').strip(),
            'pub_date': (r.get('发布时间') or '').strip(),
        }
print('csv 组合:', len(csv_data))

# 手录导入（combos_import.csv：实战配队确认纳入、无视频来源，列头与系列 csv 对齐）
import_data = {}  # key -> {highlight}
import_csv = COMBOS / 'combos_import.csv'
if import_csv.exists():
    with open(import_csv, encoding='utf-8') as f:
        for r in csv.DictReader(f):
            a = (r.get('武將1') or r.get('武将1') or '').strip()
            b = (r.get('武將2') or r.get('武将2') or '').strip()
            desc = (r.get('描述') or '').strip()
            if a not in NAMES or b not in NAMES or not desc:
                continue
            import_data[key(a, b)] = {'highlight': desc}
    print('手录导入组合:', len(import_data))

# ============================================================
# 2. combos md 深解
# ============================================================
md_data = {}  # key -> {mechanism, source_md}


# 2a 强力组合.md —— 表格组合列 **A + B / C**
def parse_qiangli():
    t = (COMBOS / '强力组合.md').read_text(encoding='utf-8')
    cnt = 0
    for line in t.splitlines():
        parts = split_table(line)
        if not parts or '+' not in parts[0]:
            continue
        cell = parts[0].strip('* ')
        lf, rt = cell.split('+', 1)
        mech = parts[1] if len(parts) > 1 else ''
        eff = parts[2] if len(parts) > 2 else ''
        for rb in re.split(r'[/／]', rt):
            a, b = lf.strip(), rb.strip()
            if a in NAMES and b in NAMES:
                k = key(a, b)
                md_data.setdefault(k, {})
                md_data[k]['mechanism'] = f'机制：{mech}\n效果：{eff}'
                md_data[k]['source_md'] = '强力组合.md'
                cnt += 1
    print('强力组合.md 深解:', cnt)


# 2b 巴清搭配.md —— 主角巴清，表格 **武将** | 配合逻辑（含、多武将拆）
def parse_baqing():
    hero = '巴清'
    t = (COMBOS / '巴清搭配.md').read_text(encoding='utf-8')
    cnt = 0
    for line in t.splitlines():
        parts = split_table(line)
        if not parts:
            continue
        partner_cell = parts[0].strip('* ')
        logic = parts[1] if len(parts) > 1 else ''
        for p in re.split(r'[、,，]', partner_cell):
            p = NAME_FIX.get(p, p).strip()
            if p in NAMES:
                k = key(hero, p)
                md_data.setdefault(k, {})
                md_data[k]['mechanism'] = f'配合逻辑：{logic}'
                md_data[k]['source_md'] = '巴清搭配.md'
                cnt += 1
    print('巴清搭配.md 深解:', cnt)


# 2c 平阳公强势组合盘点.md —— 主角平阳公主，小标题武将 + 表格武将双路
def parse_pingyang():
    hero = '平阳公主'
    t = (COMBOS / '平阳公主强势组合盘点.md').read_text(encoding='utf-8')
    cur_title_hero = None
    cnt = 0
    for line in t.splitlines():
        m = re.match(r'#{2,4}\s+(?:[①-⑩\d]+[．.、]?\s*)?(.+?)\s*$', line)
        if m:
            cand = NAME_FIX.get(m.group(1).strip().strip('*★ '),
                                 m.group(1).strip().strip('*★ '))
            cur_title_hero = cand if cand in NAMES else None
            continue
        parts = split_table(line)
        if not parts:
            continue
        partners = []
        cell0 = NAME_FIX.get(parts[0].strip('* '), parts[0].strip('* '))
        if cell0 in NAMES:
            partners.append(cell0)
        if cur_title_hero:
            partners.append(cur_title_hero)
        logic = ' / '.join(p for p in (parts[1:] if cell0 in NAMES else parts) if p)
        for p in partners:
            k = key(hero, p)
            md_data.setdefault(k, {})
            prev = md_data[k].get('mechanism', '')
            new = f'配合逻辑：{logic}'
            md_data[k]['mechanism'] = (prev + '\n' + new).strip() if prev else new
            md_data[k]['source_md'] = '平阳公主盘点.md'
            cnt += 1
    print('平阳公主盘点.md 深解:', cnt)


parse_qiangli()
parse_baqing()
parse_pingyang()


# 2d 孟尝君+黄月英.md —— 按曲拆，独立 block_id 后缀
def parse_menghuang():
    t = (COMBOS / '孟尝君 + 黄月英.md').read_text(encoding='utf-8')
    cur = None
    buf = []
    songs = []
    for line in t.splitlines():
        m = re.match(r'#{2,4}\s+.*?(第.{1,3}曲[：:].+)', line)
        if m:
            if cur and buf:
                songs.append((cur, '\n'.join(buf).strip()))
            cur = m.group(1).strip()
            buf = []
        elif cur:
            if line.startswith('|') and split_table(line) is None:  # 跳过表头/分隔行
                continue
            buf.append(line)
    if cur and buf:
        songs.append((cur, '\n'.join(buf).strip()))
    print('孟尝君+黄月英 曲数:', len(songs))
    return songs


songs = parse_menghuang()

# ============================================================
# 3. 组装 blocks（按 block_id 合并/去重）
# ============================================================
blocks = {}  # block_id -> block


def base_bid(k):
    return f'combo_{k[0]}_{k[1]}'


# csv 亮点先入
for k, d in csv_data.items():
    bid = base_bid(k)
    blocks[bid] = {
        'block_id': bid, 'hero_a': k[0], 'hero_b': k[1],
        'highlight': d['highlight'], 'bv': d['bv'], 'pub_date': d['pub_date'],
        'mechanism': '', 'source_md': '', 'song': '',
        'related': [f'武将:{k[0]}', f'武将:{k[1]}'],
    }

# md 深解合并（同 bid 追加 mechanism；csv 无则新建）
md_only = 0
for k, d in md_data.items():
    bid = base_bid(k)
    if bid in blocks:
        blocks[bid]['mechanism'] = d['mechanism']
        blocks[bid]['source_md'] = d['source_md']
    else:
        blocks[bid] = {
            'block_id': bid, 'hero_a': k[0], 'hero_b': k[1],
            'highlight': '', 'bv': '', 'pub_date': '',
            'mechanism': d['mechanism'], 'source_md': d['source_md'], 'song': '',
            'related': [f'武将:{k[0]}', f'武将:{k[1]}'],
        }
        md_only += 1

# 手录导入合并（无视频；同 bid 已有块则亮点并句，否则新建）
import_merged = 0
for k, d in import_data.items():
    bid = base_bid(k)
    if bid in blocks:
        existing = blocks[bid]['highlight']
        if d['highlight'] and d['highlight'] not in existing:
            blocks[bid]['highlight'] = f'{existing}；{d["highlight"]}' if existing else d['highlight']
        import_merged += 1
    else:
        blocks[bid] = {
            'block_id': bid, 'hero_a': k[0], 'hero_b': k[1],
            'highlight': d['highlight'], 'bv': '', 'pub_date': '',
            'mechanism': '', 'source_md': '', 'song': '',
            'related': [f'武将:{k[0]}', f'武将:{k[1]}'],
        }
print('手录导入块:', len(import_data), '（并入既有块:', import_merged, '）')

# 孟尝君+黄月英按曲拆（独立后缀 bid，不与 csv 亮点块合并）
mk = key('孟尝君', '黄月英')
for i, (song, txt) in enumerate(songs, 1):
    bid = f'combo_{mk[0]}_{mk[1]}_{i}'
    blocks[bid] = {
        'block_id': bid, 'hero_a': mk[0], 'hero_b': mk[1],
        'highlight': '', 'bv': '', 'pub_date': '',
        'mechanism': txt, 'source_md': '孟尝君+黄月英.md', 'song': song,
        'related': [f'武将:{mk[0]}', f'武将:{mk[1]}'],
    }

# ============================================================
# 4. 统计 + 输出
# ============================================================
merged = sum(1 for b in blocks.values() if b['highlight'] and b['mechanism'])
csv_only = sum(1 for b in blocks.values() if b['highlight'] and not b['mechanism'])
import_only = len(import_data) - import_merged
print(f'总块数: {len(blocks)}  | 合并(csv+md): {merged}  | csv独有: {csv_only}  | md独有: {md_only}  | 手录并入: {import_merged}  | 手录独有: {import_only}')

# block_id 唯一性校验
ids = [b['block_id'] for b in blocks.values()]
assert len(ids) == len(set(ids)), f'block_id 重复: {set([x for x in ids if ids.count(x)>1])}'

# 输出 json
blocks_list = list(blocks.values())
save_json(CORPUS / '组合RAG语料.json', blocks_list)

# 输出 md（人读版）
md = ['# 组合RAG语料', '',
      f'> 来源：raw_guides/combos（系列csv {len(csv_data)} + 4 md + 手录导入 {len(import_data)}）。设计点A不贴hero，D按武将对合并。',
      f'> 总块 {len(blocks_list)}（合并 {merged} / csv独有 {csv_only} / md独有 {md_only} / 手录独有 {import_only}）。', '']
for b in sorted(blocks_list, key=lambda x: x['block_id']):
    md.append(f'### {b["block_id"]}')
    md.append(f'【武将】{b["hero_a"]} + {b["hero_b"]}')
    if b['highlight']:
        md.append(f'【亮点】{b["highlight"]}')
    if b['mechanism']:
        md.append(f'【深解】{b["mechanism"]}')
    if b['bv']:
        md.append(f'【BV】{b["bv"]}  【发布】{b["pub_date"]}')
    if b['source_md']:
        md.append(f'【来源】{b["source_md"]}')
    md.append('')
with open(CORPUS / '组合RAG语料.md', 'w', encoding='utf-8', newline='\n') as f:
    f.write('\n'.join(md))

print('已输出: 组合RAG语料.json + .md')
