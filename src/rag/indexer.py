# -*- coding: utf-8 -*-
"""索引器：读取 docs/*.json 语料 → 规范化文本块 → 本地 bge 向量化 → 写入 ChromaDB（项目内持久化）

带分阶段进度与耗时摘要，便于观察执行情况。
"""
import sys, json, time, logging
from pathlib import Path

from src.rag import config
from tqdm import tqdm

logger = logging.getLogger('rag.indexer')

sys.stdout.reconfigure(encoding='utf-8')

DOCS_DIR = config.CORPUS_DIR


# ---------------------------------------------------------------
# 各语料 json 的规范化函数：返回 list[(block_id, text, metadata)]
# ---------------------------------------------------------------

def _norm_hero(blocks):
    out = []
    for b in blocks:
        bid = b.get('block_id', '')
        kind = b.get('block_type', 'skill')
        hero = b.get('hero', '')
        meta = {'block_id': bid, 'kind': 'hero', 'hero': hero,
                'faction': b.get('faction', ''), 'position': b.get('position', ''),
                'gender': str(b.get('gender', '')), 'max_hp': str(b.get('max_hp', ''))}
        if kind == 'overview':
            text = f'【武将总览】{b.get("description", "")}'
        else:
            text = (f'【武将技能】{hero}｜{b.get("faction", "")}｜{b.get("position", "")}\n'
                    f'技能：{b.get("skill", "")}\n'
                    f'描述：{b.get("description", "")}\n'
                    f'结算：{b.get("settlement", "")}')
            meta['skill'] = b.get('skill', '')
        out.append((bid, text.strip(), meta))
    return out


def _norm_card(blocks):
    out = []
    for b in blocks:
        bid = b.get('block_id', '')
        name = bid.split('_', 2)[-1] if '_' in bid else ''
        text = (f'【卡牌】{name}（{b.get("card_type", "")}）x{b.get("card_amount", "")}\n'
                f'效果：{b.get("effect", "")}\n'
                f'细则：{b.get("effect_detail", "")}')
        out.append((bid, text.strip(), {'block_id': bid, 'kind': 'card', 'card': name,
                                        'card_type': b.get('card_type', '')}))
    return out


def _norm_rule(blocks):
    out = []
    for b in blocks:
        bid = b.get('block_id', '')
        text = f'【规则】{b.get("title", "")}\n{b.get("content", "")}'
        out.append((bid, text.strip(), {'block_id': bid, 'kind': 'rule'}))
    return out


def _norm_term(blocks):
    out = []
    for b in blocks:
        bid = b.get('block_id', '')
        text = f'【术语】{b.get("term", "")}\n{b.get("definition", "")}'
        out.append((bid, text.strip(), {'block_id': bid, 'kind': 'term', 'term': b.get('term', '')}))
    return out


def _norm_faq(blocks):
    out = []
    for b in blocks:
        bid = b.get('block_id', '')
        text = f'【FAQ裁定】{b.get("ruling", "")}\n来源：{b.get("source", "")}'
        out.append((bid, text.strip(), {'block_id': bid, 'kind': 'faq', 'faq_no': b.get('faq_no', '')}))
    return out


def _norm_special(blocks):
    out = []
    for b in blocks:
        bid = b.get('block_id', '')
        cat = b.get('category', '')
        name = b.get('name', '')
        parts = [f'【特殊机制】{cat}：{name}']
        for key in ('card_type', 'effect', 'function', 'description', 'stackable'):
            if b.get(key):
                parts.append(f'{key}：{b[key]}')
        out.append((bid, '\n'.join(parts).strip(),
                    {'block_id': bid, 'kind': 'special', 'category': cat, 'name': name}))
    return out


def _norm_cardpt(blocks):
    out = []
    for b in blocks:
        bid = b.get('block_id', '')
        text = (f'【点数花色】{b.get("card", "")} x{b.get("count", "")}\n'
                f'花色：{b.get("suits", "")}  点数：{b.get("points", "")}\n'
                f'{b.get("attribute_judge", "")}')
        out.append((bid, text.strip(), {'block_id': bid, 'kind': 'cardpt', 'card': b.get('card', '')}))
    return out


def _norm_equip(blocks):
    out = []
    for b in blocks:
        bid = b.get('block_id', '')
        text = (f'【装备】{b.get("card", "")}（{b.get("equip_subtype", "")}）\n'
                f'效果：{b.get("effect", "")}')
        out.append((bid, text.strip(), {'block_id': bid, 'kind': 'equip', 'card': b.get('card', '')}))
    return out


def _norm_modify(blocks):
    out = []
    for b in blocks:
        bid = b.get('block_id', '')
        text = (f'【加强削弱】{b.get("card_name", "")}（{b.get("card_type", "")}）\n'
                f'基础：{b.get("base_effect", "")}\n'
                f'加强：{b.get("strengthen_effect", "")}\n'
                f'削弱：{b.get("weaken_effect", "")}\n'
                f'结算：{b.get("settlement_detail", "")}')
        out.append((bid, text.strip(), {'block_id': bid, 'kind': 'modify', 'card': b.get('card_name', '')}))
    return out




def _norm_classification(blocks):
    out = []
    for b in blocks:
        bid = b.get('block_id', '')
        hero = b.get('hero', '')
        text = ('【武将分类】' + hero + '：' + b.get('categories_text', '') + '\n'
                '官方定位：' + b.get('position', '') + '\n' + b.get('reason', ''))
        out.append((bid, text.strip(),
                    {'block_id': bid, 'kind': 'classification', 'hero': hero}))
    return out
CORPUS_FILES = [
    ('武将RAG语料.json', _norm_hero),
    ('卡牌RAG语料.json', _norm_card),
    ('元规则RAG语料-章节块.json', _norm_rule),
    ('术语表.json', _norm_term),
    ('FAQ裁定块.json', _norm_faq),
    ('特殊机制语料.json', _norm_special),
    ('卡牌点数花色语料.json', _norm_cardpt),
    ('装备属性语料.json', _norm_equip),
        ('加强削弱语料.json', _norm_modify),
    ('武将分类语料.json', _norm_classification),
]


def load_all_blocks():
    """读取全部语料 json，返回 list[(block_id, text, metadata)]。"""
    all_blocks = []
    for fname, norm in CORPUS_FILES:
        path = DOCS_DIR / fname
        if not path.exists():
            print(f'  ⚠️ 缺少 {fname}，跳过')
            continue
        data = json.loads(path.read_text(encoding='utf-8'))
        blocks = norm(data)
        all_blocks.extend(blocks)
    return all_blocks


def build_index(rebuild=True):
    """全量/增量写入 ChromaDB。返回 (总块数, 集合名)。"""
    t0 = time.time()
    print('=' * 60)
    print('构建 RAG 向量索引')
    print('=' * 60)

    # 1/4 加载语料
    t1 = time.time()
    print(f'[1/4] 加载语料 ...')
    all_blocks = load_all_blocks()
    if not all_blocks:
        print('❌ 无语料可索引'); return 0, ''
    print(f'      ✅ 加载 {len(all_blocks)} 块，耗时 {time.time()-t1:.1f}s')

    # 语料块 id 唯一性校验（重复会导致 Chroma 覆盖/写入失败）
    seen_ids = set()
    dup = set()
    for bid, _, _ in all_blocks:
        if bid in seen_ids:
            dup.add(bid)
        seen_ids.add(bid)
    if dup:
        raise ValueError(f'语料块 id 重复：{sorted(dup)[:10]}')

    # 2/4 加载模型
    t2 = time.time()
    print(f'[2/4] 加载向量模型：{config.EMBEDDING_MODEL}')
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(config.EMBEDDING_MODEL, device='cpu')
    print(f'      ✅ 模型就绪，耗时 {time.time()-t2:.1f}s')
    logger.info('模型就绪，耗时 %.1fs', time.time()-t2)

    # 3/4 向量化
    t3 = time.time()
    print(f'[3/4] 向量化 {len(all_blocks)} 块（batch=32）...')
    docs = [b[1] for b in all_blocks]
    embs = model.encode(docs, batch_size=32, normalize_embeddings=True,
                        show_progress_bar=True)
    print(f'      ✅ 向量化完成，耗时 {time.time()-t3:.1f}s')

    # 4/4 写入 Chroma
    t4 = time.time()
    print(f'[4/4] 写入向量库 → {config.CHROMA_DIR}')
    import chromadb
    client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
    coll_name = 'mjs_rag_v1'
    if rebuild:
        try:
            client.delete_collection(coll_name)
        except Exception:
            pass
    coll = client.get_or_create_collection(
        coll_name, metadata={'hnsw:space': 'cosine', 'description': '名将杀 RAG 语料'})
    ids = [b[0] for b in all_blocks]
    metas = [b[2] for b in all_blocks]
    BATCH = 200
    if not rebuild:
        # 增量：集合同步（新增 add / 交集 update / 过期 delete），保留 collection 结构与 HNSW 索引
        existing = set(coll.get(include=[])['ids'])
        new_ids = set(ids)
        stale = sorted(existing - new_ids)
        if stale:
            coll.delete(ids=stale)
            print(f'      [删除过期块 {len(stale)} 个]')
        to_add = [i for i, b in enumerate(ids) if b not in existing]
        if to_add:
            for i in tqdm(range(0, len(to_add), BATCH), desc='新增', unit='批', file=sys.stdout):
                idx = to_add[i:i+BATCH]
                coll.add(ids=[ids[j] for j in idx], documents=[docs[j] for j in idx],
                         metadatas=[metas[j] for j in idx], embeddings=[embs[j].tolist() for j in idx])
            print(f'      [新增 {len(to_add)} 块]')
        to_update = [i for i, b in enumerate(ids) if b in existing]
        if to_update:
            for i in tqdm(range(0, len(to_update), BATCH), desc='更新', unit='批', file=sys.stdout):
                idx = to_update[i:i+BATCH]
                coll.update(ids=[ids[j] for j in idx], documents=[docs[j] for j in idx],
                            metadatas=[metas[j] for j in idx], embeddings=[embs[j].tolist() for j in idx])
            print(f'      [更新 {len(to_update)} 块]')
    else:
        if coll.count() > 0:
            coll.delete(where={})
        for i in tqdm(range(0, len(ids), BATCH), desc='写入', unit='批', file=sys.stdout):
            coll.add(ids=ids[i:i+BATCH], documents=docs[i:i+BATCH],
                     metadatas=metas[i:i+BATCH], embeddings=embs[i:i+BATCH].tolist())

    total = time.time() - t0
    print('-' * 60)
    print(f'✅ 索引构建完成：集合 {coll_name}，{coll.count()} 块，总耗时 {total:.1f}s')
    logger.info('索引构建完成：%s %d 块，总耗时 %.1fs', coll_name, coll.count(), total)
    print(f'   向量库位置：{config.CHROMA_DIR}')
    print('=' * 60)
    return coll.count(), coll_name


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description='构建 RAG 向量索引')
    p.add_argument('--no-rebuild', action='store_true', help='增量同步（默认全量重建）')
    args = p.parse_args()
    config.setup_logging()
    try:
        n, name = build_index(rebuild=not args.no_rebuild)
    except Exception as e:
        logger.error('索引构建失败：%s', e)
        sys.exit(1)
    if n == 0:
        sys.exit(1)