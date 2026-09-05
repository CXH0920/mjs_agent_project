# -*- coding: utf-8 -*-
"""生成卡牌点数花色语料（从 data/card_points.json 读取，补充 RAG 知识库）"""
import collections

from src.scripts.rag_common import CORPUS, load_json, project_path, save_json, setup_stdout

setup_stdout()
payload = load_json(project_path('data', 'card_points.json'))
rows = payload['cards']
judge_map = {r['name']: r['rule'] for r in payload.get('judge_rules', [])}

card_pts = collections.defaultdict(collections.Counter)
card_suits = collections.defaultdict(collections.Counter)
for item in rows:
    name, suit, pt = item['name'], item['suit'], item['point']
    n = item.get('count', 1)
    card_pts[name][pt] += n
    card_suits[name][suit] += n

# 属性判定：从 card_points.json 的 judge_rules 取（原硬编码 attr_judge 已迁移为数据）
def attr_judge(name):
    return judge_map.get(name, '')

blocks = []
md = ['# 卡牌点数花色语料', '',
      '> 来源：data/card_points.json（162 张牌全量：花色 ♥40/♣40/♠40/♦40/太极2，点数 1~8）。', '']
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

with open(CORPUS / '卡牌点数花色语料.md', 'w', encoding='utf-8', newline='\n') as f:
    f.write('\n'.join(md))
save_json(CORPUS / '卡牌点数花色语料.json', blocks)

# 关键规则验证输出
print('=== 规则验证 ===')
huosha = card_suits['火杀']; print('火杀花色(应全♥):', dict(huosha), '数量', sum(card_pts['火杀'].values()))
leisha = card_suits['雷杀']; print('雷杀花色(应全♦):', dict(leisha), '点数', dict(card_pts['雷杀']))
chongsha = card_suits['冲杀']; print('冲杀花色(应全♦):', dict(chongsha), '点数', dict(card_pts['冲杀']))
print('闪避:', dict(card_suits['闪避']), '蟠桃:', dict(card_suits['蟠桃']), '怒气:', dict(card_suits['怒气']))
print('易:', dict(card_suits['易']), dict(card_pts['易']))
print('块数:', len(blocks))
