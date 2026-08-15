# -*- coding: utf-8 -*-
"""语料精化(curated)合并：重跑 build 脚本时保留人工/LLM 精化的索引字段。

供 build_card_corpus.py / build_rag_corpus.py 在写文件前调用；
旧语料中同 block_id 的 curated 会覆盖新生成块的顶层索引字段并保留 curated。
"""

import json
import os

INDEX_FIELDS = ("timing", "trigger_condition", "keywords", "related")


def merge_curated(blocks, old_json_path):
    """将旧语料中的 curated 合并到新生成块。返回合并的块数。"""
    if not os.path.exists(old_json_path):
        return 0
    try:
        with open(old_json_path, encoding='utf-8') as f:
            old_data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(old_data, list):
        return 0
    old = {b.get('block_id'): b for b in old_data if isinstance(b, dict)}
    merged = 0
    for b in blocks:
        curated = old.get(b.get('block_id'), {}).get('curated')
        if not isinstance(curated, dict):
            continue
        for f in INDEX_FIELDS:
            value = curated.get(f)
            if isinstance(value, list):
                b[f] = list(value)
        b['curated'] = curated
        merged += 1
    return merged