# -*- coding: utf-8 -*-
"""RAG 人工维护覆盖度审计。

检查：
1. heroes.json 中未在 hero_classification.json 归类的武将（新武将需人工归类）；
2. special_cards.json 引用了不存在的武将；
3. 技能描述中出现的疑似专属牌名（启发式提取，仅作人工确认提示，不保证准确）。

返回问题清单（list[str]）；无问题时返回空列表。不影响语料构建，
由 maintain_rag.py 选择是否以 --strict-audit 视为失败。
"""
import os
import io, sys, os, json, re


# 疑似专属牌名的结尾字（收窄，避免把通用术语误判为牌名）
_SUFFIX = '剑戟弓鞭锤钩刃枪甲盾玺伞幡符印珠镜扇书车'
# 通用术语黑名单：候选词包含其中任意项时排除
_BLACKLIST = [
    '手牌', '摸牌', '弃牌', '出牌', '装备', '技能', '武将', '战法', '武器', '防具', '坐骑', '行动',
    '获得', '打出', '交给', '置于', '加入', '清除', '弃置', '翻出', '削弱', '增强', '卜卦',
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
    cat_map = cls.get('hero_categories', {}) or {}
    unclassified = [h['name'] for h in heroes if h['name'] not in cat_map]
    issues.append('未归类武将 %d 人（请补充 data/hero_classification.json）' % len(unclassified))
    for name in unclassified[:30]:
        issues.append('  未归类: %s' % name)

    for s in specials:
        sh = s.get('hero', '')
        if not sh:
            continue
        for _name in re.split(r'[\u3001,?]', sh):
            _name = re.split(r'[(\uff08]', _name, 1)[0].strip()
            if not _name or _name == '通用' or _name == '—' or _name.endswith('等'):
                continue
            if _name not in hero_names:
                issues.append('special_cards 引用了未知武将: %s' % _name)

    known = {c.get('name', '') for c in cards} | {s.get('name', '') for s in specials}
    cand = set()
    for h in heroes:
        for sk in h.get('skills', []):
            text = (sk.get('description', '') or '') + (sk.get('settlement', '') or '')
            for m in re.finditer(r'[\u4e00-\u9fff]{2,4}[' + _SUFFIX + r']', text):
                w = m.group(0)
                if w in known:
                    continue
                if any(b in w for b in _BLACKLIST if len(b) >= 2):
                    continue
                cand.add(w)
    if cand:
        issues.append('疑似专属牌未收录 %d 个（人工确认后补充 special_cards.json，仅列前 %d）'
                      % (len(cand), _MAX_SUSPECT_HINTS))
        for w in sorted(cand)[:_MAX_SUSPECT_HINTS]:
            issues.append('  疑似: %s' % w)
    return issues


if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    root = os.environ.get("RAG_PROJECT_DIR") or r"G:\py_savepoint\mjs_rag_project"
    for it in audit_hero_coverage(root):
        print('- ' + it)