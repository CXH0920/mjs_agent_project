# -*- coding: utf-8 -*-
"""规则文档快照读写公共层（audit_rule_doc / sync_rule_stats 共享）。

承载 .rule_doc_snapshot.json 的格式与读写（构建 / 写入 / 加载 / 计数）。
独立成模块的原因：写基线（sync.refresh_snapshot）与读基线（audit）都需要
这套逻辑，原先分居两个脚本并互相函数内延迟导入，形成运行期互引环
（scripts.sync_rule_stats <-> scripts.audit_rule_doc）；下沉至本模块后
两个脚本均只向下依赖，环消除（审计问题二）。

私有助手（norm / chapters）在此即为跨脚本 API，故去掉下划线前缀。
"""

import datetime
import hashlib
import json
import os
import re

from src.scripts import build_rule_corpus as brc
from src.scripts.rag_common import get_script_logger

logger = get_script_logger("snapshot_common")

from src.config.env import PROJECT_ROOT as ROOT

DEFAULT_SNAPSHOT = os.path.join(ROOT, '.rule_doc_snapshot.json')
SNAPSHOT_VERSION = 1


def doc_md5(path):
    with open(path, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()


def norm(content_lines):
    return '\n'.join(content_lines).strip('\n')


def chapters(doc_path):
    """扫描 ## 章标题，返回 [{'no': int, 'title': str}]。"""
    out = []
    with open(doc_path, encoding='utf-8') as f:
        for ln in f.read().splitlines():
            m = brc.HEADING_RE.match(ln)
            if m and len(m.group(1)) == 2:
                text = m.group(2).strip()
                no, _ = brc._parse_heading(text)
                if no is None:
                    continue
                title = re.sub(r'^\d+\.\s*', '', text)
                out.append({'no': no, 'title': title})
    return out


def load_snapshot(path=DEFAULT_SNAPSHOT):
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception as error:
        print(f'  ⚠️ 快照加载失败，按无快照处理（全部章节将视为新增）: {error}')
        logger.warning("快照 %s 加载失败，按无快照处理: %s", path, error)
        return None


def snapshot_counts(path=DEFAULT_SNAPSHOT):
    """供 maintain_rag.py 使用：返回 语料文件名 -> 快照期望块数；无快照返回 None。"""
    snap = load_snapshot(path)
    if not snap:
        return None
    c = snap.get('counts', {})
    return {
        '元规则RAG语料-章节块.json': c.get('sections'),
        '术语表.json': c.get('terms'),
        'FAQ裁定块.json': c.get('faqs'),
    }


def build_snapshot(doc_path, root):
    blocks, terms, faqs, dropped = brc.parse_rule_doc(doc_path)
    chapter_list = chapters(doc_path)
    chapter_blocks = [b['block_id'] for b in blocks if b['section'] is None]
    sections = {}
    for b in blocks:
        if b['section'] is not None:
            sections.setdefault(str(b['chapter']), []).append(b['block_id'])
    block_map = {}
    for b in blocks:
        block_map[b['block_id']] = {'title': b['title'], 'content': norm(b['content'])}
    with open(doc_path, encoding='utf-8') as f:
        text = f.read()
    return {
        'version': SNAPSHOT_VERSION,
        'updated_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'doc_md5': doc_md5(doc_path),
        'chapters': chapter_list,
        'chapter_blocks': chapter_blocks,
        'sections': sections,
        'faq_ids': ['faq_%03d' % q['faq_no'] for q in sorted(faqs, key=lambda x: x['faq_no'])],
        'term_ids': [t['block_id'] for t in terms],
        'blocks': block_map,
        'counts': {'sections': len(blocks), 'terms': len(terms), 'faqs': len(faqs),
                   'pending': text.count('[待确认')},
    }


def write_snapshot(snap, path):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
