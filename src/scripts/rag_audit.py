# -*- coding: utf-8 -*-
"""RAG 人工维护覆盖度审计。

检查：
1. heroes.json 中未在 hero_classification.json 归类的武将（新武将需人工归类）；
2. special_cards.json 引用了不存在的武将；
3. 技能描述中出现的疑似牌名/道具名（启发式提取 + 黑名单/已知名称/排除清单过滤，仅作人工确认提示，不保证准确）；
4. card_points.json（原 xlsx sheet1）花色/点数/张数合法性；
5. equip_attrs.json（原 xlsx sheet2）件数/字段合法性；
6. 专属牌/战法牌结算详情回填完整性（死士为非实体牌标记，豁免）。

返回问题清单（list[str]）；无问题时返回空列表。不影响语料构建，
由 maintain_rag.py 选择是否以 --strict-audit 视为失败。
"""
import io, sys, os, json, re

from src.config.env import PROJECT_ROOT as ROOT
from src.business.rag.audit_service import (  # noqa: E402
    collect_card_points,
    collect_equip_attrs,
    collect_missing_settlements,
    collect_orphan_category_keys,
    collect_unclassified,
    collect_unknown_heroes,
)


# 疑似专属牌名的结尾字（收窄，避免把通用术语误判为牌名）
_SUFFIX = '剑戟弓鞭锤钩刃枪甲盾玺伞幡符印珠镜扇书车'
# 通用术语黑名单：候选词包含其中任意项时排除
_BLACKLIST = [
    '手牌', '摸牌', '弃牌', '出牌', '装备', '技能', '武将', '战法', '武器', '防具', '坐骑', '行动',
    '获得', '打出', '交给', '置于', '加入', '清除', '弃置', '翻出', '削弱', '增强', '卜卦', '例如', '比如', '分为',
    '区域', '目标', '角色', '回合', '阶段', '次数', '点数', '花色', '名称', '类型', '效果', '状态',
    '结算', '距离', '体力', '上限', '失去', '减少', '增加', '额外', '任意', '所有', '其他', '一名',
    '自动', '原本', '相同', '对应', '等量', '以下', '进入', '离开', '复制', '展示', '查看', '选择',
    '指定', '视为', '当作', '恢复', '流失', '濒死', '死亡', '登场', '替换', '销毁', '整理', '交换',
    '移动', '改变', '当前', '每轮', '每次', '每回合', '结束', '开始', '之后', '之前', '同时', '立即',
    '直到', '否则', '如果', '若', '则', '由', '被', '让', '令', '使', '以', '从', '向', '把', '将',
    '对', '在', '于', '和', '与', '中', '上', '下', '内', '外', '个', '张', '的', '了', '是', '有',
    '不', '无', '此', '该', '其', '你', '我', '他', '她', '它', '们',
]
_MAX_SUSPECT_HINTS = 30
# 已确认非牌名的技能效果/道具名（人工维护；若未来成为正式牌则移出）
_NON_CARD_TERMS = {'遁甲天书', '遁甲'}  # '遁甲' 为遁甲天书简称（左慈技能效果）


def audit_hero_coverage(root):
    """返回人工补充清单；空列表表示无问题。"""
    issues = []
    data_dir = os.path.join(root, 'data')
    try:
        with open(os.path.join(data_dir, 'heroes.json'), encoding='utf-8') as f:
            heroes = json.load(f)
        with open(os.path.join(data_dir, 'hero_classification.json'), encoding='utf-8') as f:
            cls = json.load(f)
        with open(os.path.join(data_dir, 'special_cards.json'), encoding='utf-8') as f:
            specials = json.load(f)
        with open(os.path.join(data_dir, 'cards.json'), encoding='utf-8') as f:
            cards = json.load(f)
    except Exception as e:
        return ['audit 数据读取失败: %s' % e]

    hero_names = {h['name'] for h in heroes}
    unclassified = collect_unclassified(hero_names, cls)
    issues.append('未归类武将 %d 人（请补充 data/hero_classification.json）' % len(unclassified))
    for name in unclassified[:30]:
        issues.append('  未归类: %s' % name)

    # 反向校验：分类表引用了 heroes.json 中不存在的武将（#10）
    orphan = collect_orphan_category_keys(hero_names, cls)
    if orphan:
        issues.append('分类表引用未知武将 %d 个（请清理 data/hero_classification.json 多余键）：%s'
                      % (len(orphan), '、'.join(orphan[:8])))
        for name in orphan[:30]:
            issues.append('  多余键: %s' % name)

    for _name in collect_unknown_heroes(specials, hero_names):
        issues.append('special_cards 引用了未知武将: %s' % _name)

    known = ({c.get('name', '') for c in cards} | {s.get('name', '') for s in specials}
             | {sk.get('name', '') for h in heroes for sk in h.get('skills', [])}
             | _NON_CARD_TERMS)
    cand = set()
    for h in heroes:
        for sk in h.get('skills', []):
            text = (sk.get('description', '') or '') + (sk.get('settlement', '') or '')
            # 标记已知名称覆盖区间，避免从已知名称内部切出碎片（如“遁甲天书”→“甲天书”）
            covered = [False] * len(text)
            for k in known:
                if len(k) < 2:
                    continue
                _pos = text.find(k)
                while _pos != -1:
                    for _i in range(_pos, _pos + len(k)):
                        covered[_i] = True
                    _pos = text.find(k, _pos + 1)
            # 重叠匹配：避免“获得玄铁剑”被黑名单整词过滤后漏掉内部新词
            for m in re.finditer(r'(?=([\u4e00-\u9fff]{2,4}[' + _SUFFIX + r']))', text):
                w = m.group(1)
                if w in known:
                    continue
                if all(covered[m.start() + _i] for _i in range(len(w))):
                    continue
                if any(k in w for k in known):
                    continue
                if any(b in w for b in _BLACKLIST):
                    continue
                cand.add(w)
    if cand:
        issues.append('疑似牌名/道具名未收录 %d 个（非专属牌，人工确认后补充 special_cards.json 或排除清单，仅列前 %d）'
                      % (len(cand), _MAX_SUSPECT_HINTS))
        for w in sorted(cand)[:_MAX_SUSPECT_HINTS]:
            issues.append('  疑似: %s' % w)

    # 4. 卡牌点数源校验（data/card_points.json，原 xlsx sheet1 + 判定规则）
    try:
        with open(os.path.join(data_dir, 'card_points.json'), encoding='utf-8') as f:
            payload = json.load(f)
        for it in collect_card_points(payload):
            issues.append(it['message'])
    except Exception as e:
        issues.append('data/card_points.json 读取失败: %s' % e)

    # 5. 装备属性源校验（data/equip_attrs.json，原 xlsx sheet2）
    try:
        with open(os.path.join(data_dir, 'equip_attrs.json'), encoding='utf-8') as f:
            equips = json.load(f)
        for it in collect_equip_attrs(equips):
            issues.append(it['message'])
    except Exception as e:
        issues.append('data/equip_attrs.json 读取失败: %s' % e)

    # 6. 专属牌/战法牌结算详情回填校验（死士为非实体牌标记，xlsx 无对应结算，豁免）
    missing_settle = sorted(
        s.get('name', '') for s in collect_missing_settlements(specials)
    )
    if missing_settle:
        issues.append('专属牌/战法牌缺结算详情 %d 个（人工确认后补充 special_cards.json）：%s'
                      % (len(missing_settle), '、'.join(missing_settle[:8])))
    return issues


if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    root = ROOT
    for it in audit_hero_coverage(root):
        print('- ' + it)