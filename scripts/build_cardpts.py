# -*- coding: utf-8 -*-
"""生成卡牌点数花色语料（从 xlsx 提取，补充 RAG 知识库）"""
import zipfile, re, io, sys, json, xml.etree.ElementTree as ET, collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
path = r'data\mjs卡牌点数.xlsx'
z = zipfile.ZipFile(path)
NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
shared = []
for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall(NS+'si'):
    shared.append(''.join(t.text or '' for t in si.iter(NS+'t')))
root = ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
rows = []
for row in root.iter(NS+'row'):
    cells = {}
    for c in row.findall(NS+'c'):
        col = re.match(r'([A-Z]+)', c.get('r')).group(1)
        t = c.get('t'); v = c.find(NS+'v')
        val = v.text if v is not None else ''
        if t == 's': val = shared[int(val)] if val != '' else ''
        cells[col] = val
    rows.append([cells.get('A',''), cells.get('B',''), cells.get('C','')])
data = [r for r in rows[1:] if r[0]]

card_pts = collections.defaultdict(collections.Counter)
card_suits = collections.defaultdict(collections.Counter)
for name, suit, pt in data:
    card_pts[name][pt] += 1
    card_suits[name][suit] += 1

# 属性判定
def attr_judge(name):
    if name == '火杀': return '火焰伤害（♥=火属性）'
    if name == '雷杀': return '雷电伤害（点数4且花色♦）'
    if name == '冲杀': return '普通伤害（♦非点数4）'
    if name == '闪避': return '响应杀（抵消杀）'
    if name == '蟠桃': return '回复体力'
    if name == '怒气': return '增益/回复（重伤时）'
    if name == '易': return '太极花色，可当作任意行动牌'
    if name == '八卦盾': return '判定：♣→回复1体力；♠→抵消此杀（强命杀下抵消无效；♣回复先生效）'
    if name == '霜冻': return '判定：♠/♦→本回合无法选择其他角色为目标（自己与无目标牌不受限）'
    if name == '久旱': return '判定：♥/♣→本回合跳过摸牌阶段（跳过则阶段内技能不触发）'
    if name == '天雷': return '判定：点数4→4点雷电伤害（无伤害来源）'
    if name == '地火': return '判定：点数3→3点火焰伤害（无伤害来源）'
    return ''

blocks = []
md = ['# 卡牌点数花色语料', '',
      '> 来源：mjs卡牌点数.xlsx（162 张牌全量：花色 ♥40/♣40/♠40/♦40/太极2，点数 1~8）。', '']
for name in sorted(card_pts):
    suits = dict(card_suits[name])
    pts = dict(sorted(card_pts[name].items(), key=lambda x: int(x[0])))
    total = sum(card_pts[name].values())
    judge = attr_judge(name)
    b = {
        'block_id': 'cardpts_' + name,
        'card': name, 'count': total,
        'suits': suits, 'points': pts,
        'attribute_judge': judge,
    }
    blocks.append(b)
    suit_str = '、'.join(f'{s}×{n}' for s, n in suits.items())
    pt_str = '、'.join(f'{p}点×{n}' for p, n in pts.items())
    md += [
        f'### {b["block_id"]}',
        f'【卡牌】{name} | 【数量】{total}',
        f'【花色】{suit_str}',
        f'【点数】{pt_str}',
        f'【属性判定】{judge if judge else "—"}',
        '',
    ]

out = r'data\rag_corpus'
with open(out + r'\卡牌点数花色语料.md', 'w', encoding='utf-8', newline='\n') as f:
    f.write('\n'.join(md))
with open(out + r'\卡牌点数花色语料.json', 'w', encoding='utf-8', newline='\n') as f:
    json.dump(blocks, f, ensure_ascii=False, indent=1)

# 关键规则验证输出
print('=== 规则验证 ===')
huosha = card_suits['火杀']; print('火杀花色(应全♥):', dict(huosha), '数量', sum(card_pts['火杀'].values()))
leisha = card_suits['雷杀']; print('雷杀花色(应全♦):', dict(leisha), '点数', dict(card_pts['雷杀']))
chongsha = card_suits['冲杀']; print('冲杀花色(应全♦):', dict(chongsha), '点数', dict(card_pts['冲杀']))
print('闪避:', dict(card_suits['闪避']), '蟠桃:', dict(card_suits['蟠桃']), '怒气:', dict(card_suits['怒气']))
print('易:', dict(card_suits['易']), dict(card_pts['易']))
print('块数:', len(blocks))
