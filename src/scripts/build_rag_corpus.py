# -*- coding: utf-8 -*-
"""生成武将 RAG 语料：总览块 + 技能块，含规则抽取的索引字段"""
import json, re

from src.scripts.rag_common import CORPUS, load_json, save_json, setup_stdout, project_path

setup_stdout()

heroes = load_json(project_path('data', 'heroes.json'))
cards = load_json(project_path('data', 'cards.json'))

# 保序列表（set 推导迭代顺序不稳定会导致 related 字段顺序每次运行不同）
card_names = [c['name'] for c in cards]

# 术语词典（优先匹配长词）
TERMS = ['出牌阶段', '摸牌阶段', '弃牌阶段', '回合开始', '回合结束', '每轮结束', '每回合限1次',
         '战法牌', '行动牌', '装备牌', '获得牌', '造成伤害', '受到伤害', '失去体力', '回复体力',
         '重伤', '濒死', '阵亡', '击杀', '卜卦', '判定', '牌堆', '手牌', '装备区', '卜卦区',
         '处理区', '弃牌堆', '摸牌堆', '弃置', '交给', '获得', '打出', '使用', '添加', '烧毁',
         '销毁', '展示', '翻面', '横置', '削弱', '增强', '元气', '连环', '封禁', '强命', '距离',
         '攻击范围', '花色', '点数', '牌名', '复制', '本轮', '每轮', '每个回合', '限1次',
         '额外', '立即', '当作', '随机', '任意', '有牌', '体力', '桃', '酒', '怒']
# 按长度降序，避免短词先命中
TERMS.sort(key=len, reverse=True)

# 时机正则
TIMING_PATTERNS = [
    (r'(?:你的|其|其他角色|一名其他角色|所有角色)?(回合开始|回合结束|每轮结束|每轮开始时?|出牌阶段(?:开始|结束)?|摸牌阶段(?:开始|结束)?|弃牌阶段(?:开始|结束)?)', 'phase'),
    (r'当(?:你|其|其他角色|一名其他角色|一名角色|有角色|所有角色)([^，。；]{1,18}?)(?:时|后|前)', 'trigger'),
    (r'(?:你|其|其他角色|一名其他角色)(?:成为|进入|失去|获得|造成|受到|打出|使用|弃置|交给|回复|发动|死亡|阵亡)([^，。；]{0,12}?)(?:后|时|前)', 'trigger2'),
]

def extract_timing(desc):
    found = []
    for pat, kind in TIMING_PATTERNS:
        for m in re.finditer(pat, desc):
            g = m.group(1) if m.lastindex else m.group(0)
            g = g.strip()
            if g and len(g) >= 2 and g not in found:
                found.append(g)
        if len(found) >= 4:
            break
    return found[:5]

# 固定句式触发条件（描述开头，按长度降序避免前缀误匹配）
FIXED_TRIGGER_PATTERNS = [
    '出牌阶段限1次', '出牌阶段各限1次', '出牌阶段开始时', '出牌阶段结束时',
    '每个角色的回合开始时', '摸牌阶段开始时', '摸牌阶段结束时', '弃牌阶段开始时', '弃牌阶段结束时',
    '回合开始时', '回合结束时', '每轮开始时', '每轮结束时',
    '每回合开始时', '每回合限1次', '每个回合限1次', '每轮限1次', '每局游戏限1次',
    '出杀/杀伤', '出杀', '出牌阶段', '闪避', '回合开始',
    '登场', '阵亡', '受伤', '杀伤', '应战', '限定',
]

# 固定句式正则（描述开头）：累计类 / 映射类 / 泛化"X时"句式
FIXED_TRIGGER_REGEXES = [
    (r'^你每(?:累计|获得|打出|损失|失去|弃置|使用|造成|受到)[^，。；]{2,16}', None),
    (r'^所有角色每累计[^，。；]{2,16}', None),
    (r'^你阵亡后', '阵亡'),
    (r'^每局游戏限\d+次', None),
    (r'^每打出\d+张[^，。；]{2,12}', None),
    (r'^其他角色(?:每回合|出牌阶段)限1次', None),
    (r'^每个角色出牌阶段限1次', None),
    (r'^每个回合首次[^，。；。]{2,12}', None),
    (r'^造成伤害后', None),
    (r'^[^，。；。]{4,20}?时', None),
]

# 人工精化触发条件（2026-08-12 审核通过）：key=(武将, 技能)，命中优先返回，不参与规则提取
# ⚠️ 官方更新 heroes.json 后：技能改名/删除会触发下方失效校验；同名技能描述变更需人工对照本表重新审核
TRIGGER_OVERRIDES = {
    ('刘禅', '乐不思蜀'): ['（无，持续效果）'],
    ('司马炎', '容纳谠正'): ['（无，持续效果）'],
    ('贾诩', '算无遗策'): ['（无，持续效果）'],
    ('司马相如', '自着犊鼻'): ['（主动发动，原文未注明时机）'],
    ('黄月英', '奇智佐谋'): ['（无，持续效果）'],
    ('朱亥', '市井国士'): ['（无，持续效果）'],
    ('朱亥', '不拘小礼'): ['（无，持续效果）'],
    ('侯嬴', '市井隐士'): ['（无，持续效果）', '回合结束时'],
    ('侯嬴', '修身洁行'): ['（无，持续效果）'],
    ('李信', '衍水追锋'): ['（主动发动，原文未注明时机）', '击杀'],
    ('尉缭', '天官兵谈'): ['（无，持续效果）', '其他角色卜卦后'],
    ('祝融夫人', '火神族裔'): ['（无，持续效果）'],
    ('袁术', '妄自尊大'): ['（无，持续效果）', '回合结束时'],
    ('袁术', '四世三公'): ['（无，持续效果）'],
    ('刘彻', '推恩令'): ['（无，持续效果）'],
    ('西门豹', '厚民薄库'): ['（主动发动，原文未注明时机）'],
    ('田单', '火牛阵'): ['（主动发动，原文未注明时机）', '回合结束时'],
    ('苏秦', '六国相印'): ['（无，持续效果）', '每回合开始时'],
    ('张仪', '众口铄金'): ['与你势力相同的所有角色每累计打出3张相同名称的牌时'],
    ('春申君', '无妄之灾'): ['（无，持续效果）'],
    ('周勃', '削平诸吕'): ['（主动发动，原文未注明时机）', '出牌阶段结束时'],
    ('扶苏', '山有扶苏'): ['（主动发动，原文未注明时机）', '因此获得牌的其他角色打出下一张♣牌时'],
    ('左慈', '掷杯戏曹'): ['（无，持续效果）'],
    ('刘备', '携民渡江'): ['（无，持续效果）'],
    ('刘备', '惟贤惟德'): ['出牌阶段'],
    ('张飞', '嗜酒如命'): ['（无，持续效果）'],
    ('典韦', '逐虎过涧'): ['（主动发动，原文未注明时机）'],
    ('张良', '运筹帷幄'): ['（无，持续效果）'],
    ('张良', '博浪椎秦'): ['（主动发动，原文未注明时机）'],
    ('关羽', '武圣义绝'): ['（无，持续效果）'],
    ('关羽', '万军取首'): ['（无，持续效果）', '你造成伤害时'],
    ('章邯', '赦徒授兵'): ['（主动发动，原文未注明时机）'],
    ('袁绍', '四世三公'): ['（无，持续效果）'],
    ('吕布', '无双飞将'): ['你获得战法牌时'],
    ('王戎', '卖李钻核'): ['其他角色打出获得过的你的牌，即将进入弃牌堆时'],
}

def extract_trigger_cond(hero, skill, desc):
    # 人工精化映射表优先（命中则不再走规则提取）
    key = (hero, skill)
    if key in TRIGGER_OVERRIDES:
        return TRIGGER_OVERRIDES[key][:3]
    conds = []
    # 当...时 场景
    for m in re.finditer(r'当([^，。；]{2,22}?)(?:时|后|前)', desc):
        conds.append('当' + m.group(1))
    # 若...则 条件
    for m in re.finditer(r'若([^，。；]{2,40}?)，', desc):
        conds.append('若' + m.group(1))
    # 固定句式（描述开头）：累计/映射/泛化"X时"正则 -> 固定词
    head = desc.strip()
    for pat, mapped in FIXED_TRIGGER_REGEXES:
        m = re.match(pat, head)
        if m:
            conds.append(mapped if mapped else m.group(0))
            break
    else:
        for t in FIXED_TRIGGER_PATTERNS:
            if head.startswith(t):
                conds.append(t)
                break
    return conds[:3]

def extract_target(desc):
    tg = []
    for m in re.finditer(r'(一名其他角色|其他角色|一名角色|任意名?角色|所有角色|有牌的角色|自己|你|目标角色|其)', desc):
        if m.group(1) not in tg:
            tg.append(m.group(1))
        if len(tg) >= 3:
            break
    return tg

def extract_keywords(text):
    kws = []
    for t in TERMS:
        if t in text and t not in kws:
            kws.append(t)
    return kws

# 元规则概念映射（关键词 → 元规则引用）
RULE_MAP = [
    ('获得牌', '元规则:时机-获得牌后'), ('回合开始', '元规则:时机-回合开始时'),
    ('回合结束', '元规则:时机-回合结束时'), ('出牌阶段', '元规则:时机-出牌阶段'),
    ('摸牌阶段', '元规则:时机-摸牌阶段'), ('弃牌阶段', '元规则:时机-弃牌阶段'),
    ('造成伤害', '元规则:伤害流程'), ('受到伤害', '元规则:伤害流程'), ('重伤', '元规则:重伤流程'),
    ('阵亡', '元规则:阵亡结算'), ('卜卦', '元规则:卜卦机制'), ('装备', '元规则:装备规则'),
    ('战法牌', '卡牌:战法牌'), ('行动牌', '卡牌:行动牌'), ('削弱', '概念:削弱增强'),
    ('元气', '概念:元气'), ('限1次', '元规则:次数限制'), ('失去体力', '元规则:体力结算'),
]

def extract_related(text):
    rel = []
    for kw, ref in RULE_MAP:
        if kw in text and ref not in rel:
            rel.append(ref)
    for cn in card_names:
        if len(cn) >= 2 and cn in text:
            r = f'卡牌:{cn}'
            if r not in rel:
                rel.append(r)
    return rel[:8]

def skill_block(h, s):
    desc = s['description'].strip()
    settle = s.get('settlement', '').strip()
    text = desc + ' ' + settle
    timing = extract_timing(desc)
    cond = extract_trigger_cond(h['name'], s['name'], desc)
    tgt = extract_target(desc)
    kws = extract_keywords(text)
    rel = extract_related(text)
    return {
        # 稳定 id：以技能名而非数组序号，技能调序/增删不改变精化块定位（#58）
        'block_id': f'hero_{h["id"]}_skill_{s["name"]}',
        'hero': h['name'], 'faction': h['faction'], 'position': h['position'],
        'max_hp': h['max_hp'], 'max_hand': h['max_hand'],
        'skill': s['name'],
        'timing': timing, 'trigger_condition': cond, 'target': tgt,
        'keywords': kws, 'related': rel,
        'description': desc, 'settlement': settle,
    }

# 生成
blocks = []
overview_total = sum(1 for h in heroes if h.get('skills', []))  # 总览块仅生成于有技能的武将
md_lines = ['# 武将 RAG 语料', '',
            '> 来源：heroes.json（%d 武将 / %d 技能 + %d 总览块）。索引字段由规则抽取生成，'
            % (len(heroes), sum(len(h.get('skills', [])) for h in heroes), overview_total),
            '> 时机/触发条件/关联可后续用大模型精化。', '']
# TRIGGER_OVERRIDES 失效校验（官方改技能名/删除后提醒清理）
_hero_skill_keys = {(h['name'], s['name']) for h in heroes for s in h.get('skills', [])}
for _k in TRIGGER_OVERRIDES:
    if _k not in _hero_skill_keys:
        print(f'⚠️ TRIGGER_OVERRIDES 失效条目: {_k[0]}/{_k[1]} 不在 heroes.json（技能改名或删除？）')

hero_counter = 0  # 有技能武将数 = 实际生成的总览块数
for h in heroes:
    sk = h.get('skills', [])
    if not sk:
        continue
    hero_counter += 1
    sk_names = ' / '.join(s['name'] for s in sk)
    blocks.append({
        'block_id': f'hero_{h["id"]}_overview',
        'block_type': 'overview',
        'hero': h['name'], 'faction': h['faction'], 'position': h['position'],
        'gender': h.get('gender', ''),
        'max_hp': h['max_hp'], 'max_hand': h['max_hand'],
        'skills': [s['name'] for s in sk],
        'description': f'{h["name"]}：{h["faction"]}，定位{h["position"]}，{h["max_hp"]}体力，手牌上限{h["max_hand"]}，技能：{sk_names}。',
        'related': [f'技能:{s["name"]}' for s in sk],
    })
    md_lines.append(f'## 武将 {h["id"]} {h["name"]}')
    md_lines.append(f'### hero_{h["id"]}_overview 武将总览')
    md_lines.append(f'【武将】{h["name"]} | {h["faction"]} | {h["position"]} | {h["max_hp"]}体力 | 手牌上限{h["max_hand"]}')
    md_lines.append(f'【技能】{sk_names}')
    md_lines.append('')
    for s in sk:
        b = skill_block(h, s)
        blocks.append(b)
        md_lines += [
            f'### {b["block_id"]} {b["skill"]}',
            f'【武将】{b["hero"]} | {b["faction"]} | {b["position"]} | {b["max_hp"]}体力',
            f'【技能】{b["skill"]}',
            f'【时机】{" / ".join(b["timing"]) if b["timing"] else "（待精化）"}',
            f'【触发条件】{" / ".join(b["trigger_condition"]) if b["trigger_condition"] else "（待精化）"}',
            f'【目标】{" / ".join(b["target"]) if b["target"] else "（待精化）"}',
            f'【关键词】{" | ".join(b["keywords"]) if b["keywords"] else "（待精化）"}',
            f'【描述】{b["description"]}',
            f'【结算说明】{b["settlement"] if b["settlement"] else "（无）"}',
            f'【关联】{" / ".join(b["related"]) if b["related"] else "（待精化）"}',
            '',
        ]

md_path = CORPUS / '武将RAG语料.md'
json_path = CORPUS / '武将RAG语料.json'
with open(md_path, 'w', encoding='utf-8', newline='\n') as f:
    f.write('\n'.join(md_lines))
from src.scripts import rag_curated
_merged = rag_curated.merge_curated(blocks, json_path)
if _merged:
    print('已保留 curated 精化块:', _merged)
save_json(json_path, blocks)

# 统计摘要
import collections
skill_blocks = [b for b in blocks if b.get('block_type') != 'overview']
with_timing = sum(1 for b in skill_blocks if b['timing'])
with_cond = sum(1 for b in skill_blocks if b['trigger_condition'])
with_target = sum(1 for b in skill_blocks if b['target'])
with_kw = sum(1 for b in skill_blocks if b['keywords'])
with_rel = sum(1 for b in skill_blocks if b['related'])
print(f'武将数: {len(heroes)}  总览块数: {hero_counter}  技能块数: {len(skill_blocks)}  总块数: {len(blocks)}')
print(f'抽取覆盖率  时机:{with_timing}  触发条件:{with_cond}  目标:{with_target}  关键词:{with_kw}  关联:{with_rel}')
print('MD:', md_path)
print('JSON:', json_path)
