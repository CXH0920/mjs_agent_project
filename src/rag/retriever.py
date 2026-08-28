# -*- coding: utf-8 -*-
"""检索器：混合检索 = 元数据硬过滤 + 向量相似度 + 关键词兜底"""
import sys, time, logging

from src.rag import config
from src.rag.indexer import load_all_blocks

logger = logging.getLogger(__name__)

sys.stdout.reconfigure(encoding='utf-8')

# 规则高频状态/术语词（关键词兜底用）
KEYWORDS = [
    '封禁', '强命', '贯穿', '连环', '流血', '中毒', '重伤', '濒死', '阵亡', '登场',
    '击杀', '杀伤', '应战', '受伤', '卜卦', '出杀次数', '手牌上限', '装备上限', '体力上限',
    '转化', '削弱', '增强', '结盟', '余音', '元气', '销毁', '烧毁', '弃置', '交给',
    '获得', '打出', '添加', '复制', '摸牌阶段', '出牌阶段', '弃牌阶段', '回合开始', '回合结束',
    '距离', '攻击范围', '专属牌', '专属战法牌', '传国玉玺', '藤甲', '诸葛连弩', '八卦盾',
    '玄武盾', '银狮盔', '云锦袍', '凤羽盔', '亮银枪', '方天画戟', '青龙偃月刀', '开山斧',
    '武圣义绝', '无双飞将', '背水一战', '诈降焚舟', '乐不思蜀', '空城计', '借尸还魂',
]


class Retriever:
    def __init__(self):
        self._client = None
        self._collection = None
        self._model = None
        self._blocks = None          # 内存块索引（关键词兜底用）
        self._id2meta = None
        self._id2text = None         # block_id -> 文本（避免线性遍历）
        self._hero_index = None      # 武将/牌名 -> 块列表（hero_blocks 倒排）
        self._keyword_index = None   # KEYWORDS 词 -> 含词块 id 列表（惰性构建）
        self._load_t = 0.0

    # ---- 懒加载 ----
    @property
    def collection(self):
        if self._collection is None:
            import chromadb
            client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
            self._collection = client.get_collection('mjs_rag_v1')
        return self._collection

    @property
    def model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            print(f'[模型] 加载 {config.EMBEDDING_MODEL} ...', flush=True)
            t0 = time.time()
            self._model = SentenceTransformer(config.EMBEDDING_MODEL, device='cpu')
            logger.info('模型就绪，耗时 %.1fs', time.time()-t0)
            print(f'      ✅ 模型就绪，耗时 {time.time()-t0:.1f}s', flush=True)
        return self._model

    @property
    def blocks(self):
        if self._blocks is None:
            print('[语料] 加载内存块索引（关键词兜底）...', flush=True)
            t0 = time.time()
            self._blocks = load_all_blocks()
            self._id2meta = {bid: meta for bid, _, meta in self._blocks}
            self._id2text = {bid: text for bid, text, _ in self._blocks}
            hero_index = {}
            for block in self._blocks:
                meta = block[2]
                for key in ('hero', 'name'):
                    name = meta.get(key)
                    if name:
                        hero_index.setdefault(name, []).append(block)
            self._hero_index = hero_index
            self._load_t = time.time() - t0
            logger.info('语料加载 %d 块，耗时 %.1fs', len(self._blocks), self._load_t)
            print(f'      ✅ 语料 {len(self._blocks)} 块，耗时 {self._load_t:.1f}s', flush=True)
        return self._blocks

    # ---- 查询 ----
    def _vector_search(self, query, where=None, n=30):
        q = self.model.encode(
            config.EMBEDDING_QUERY_INSTRUCTION + query,
            normalize_embeddings=True)
        res = self.collection.query(query_embeddings=[q.tolist()],
                                    n_results=n, where=where,
                                    include=['metadatas', 'documents', 'distances'])
        out = []
        if res and res['ids'] and res['ids'][0]:
            for bid, doc, dist, meta in zip(res['ids'][0], res['documents'][0],
                                            res['distances'][0], res['metadatas'][0]):
                out.append({'block_id': bid, 'text': doc, 'metadata': meta,
                            'score': float(1.0 - dist), 'source': 'vector'})
        return out

    def _build_keyword_index(self):
        """KEYWORDS 静态词 → 含该词的块 id 倒排（惰性构建一次，#24）。"""
        if self._keyword_index is None:
            index = {term: [] for term in KEYWORDS}
            for bid, text, _meta in self.blocks:
                for term in KEYWORDS:
                    if term in text:
                        index[term].append(bid)
            self._keyword_index = index
        return self._keyword_index

    def _keyword_hits(self, query):
        """在全部块文本中做子串命中，返回 {block_id: 命中加分}。

        静态 KEYWORDS 走倒排索引；查询相关的名称（数量少）保持线性扫描，
        避免每次查询全量遍历所有块（#24）。
        """
        q_lower = query.lower()
        terms = [t for t in KEYWORDS if t in q_lower]
        names = set()
        for bid, _, meta in self.blocks:
            for k in ('hero', 'skill', 'card', 'name', 'term'):
                v = meta.get(k)
                if v and len(v) >= 2 and v in q_lower:
                    names.add(v)
        hits = {}
        if terms:
            index = self._build_keyword_index()
            for term in terms:
                for bid in index.get(term, ()):
                    hits[bid] = hits.get(bid, 0) + 1
        if names:
            for bid, text, _meta in self.blocks:
                hit = sum(1 for name in names if name in text)
                if hit:
                    hits[bid] = hits.get(bid, 0) + hit
        if not hits:
            return hits
        return {bid: count * config.KEYWORD_BONUS for bid, count in hits.items()}

    def search(self, query, heroes=None, top_k=None):
        """混合检索：向量 + 关键词经 RRF 排名融合，再按类型配额取 top_k。
        heroes: 指定武将名列表（元数据硬过滤，保证召回）。"""
        top_k = top_k or config.TOP_K
        where = None
        if heroes:
            where = {'hero': {'$in': list(heroes)}}
        vec = [v for v in self._vector_search(query, where=where, n=max(top_k * 2, 30))
               if v['score'] >= config.MIN_VECTOR_SCORE]
        kw = self._keyword_hits(query)

        # RRF：向量与关键词各自按分数降序排名，融合分 = 1/(RRF_K + rank)
        merged = {}
        for r, item in enumerate(sorted(vec, key=lambda x: x['score'], reverse=True), start=1):
            merged[item['block_id']] = dict(item, rrf=1.0 / (config.RRF_K + r), source='vector')
        for r, (bid, bonus) in enumerate(sorted(kw.items(), key=lambda x: x[1], reverse=True), start=1):
            item = merged.get(bid)
            if item is None:
                item = {'block_id': bid, 'text': self._text_of(bid),
                        'metadata': self._meta_of(bid), 'score': bonus, 'source': 'keyword'}
                merged[bid] = item
            item['rrf'] = item.get('rrf', 0.0) + 1.0 / (config.RRF_K + r)
            if item['source'] == 'vector':
                item['source'] = 'vector+kw'

        # 纯关键词块数量上限（RRF 单边分天然靠后，仅防数量失控）
        kw_only = sorted((it for it in merged.values() if it['source'] == 'keyword'),
                         key=lambda x: x['rrf'], reverse=True)
        for it in kw_only[config.MAX_KEYWORD_ONLY:]:
            del merged[it['block_id']]

        ranked = sorted(merged.values(), key=lambda x: x['rrf'], reverse=True)
        return self._apply_kind_quota(ranked, top_k)

    def _apply_kind_quota(self, items, top_k):
        """按语料类型配额挑选：每类最多 KIND_MAX 个，总数不超过 top_k。"""
        counts = {}
        out = []
        for it in items:
            kind = it.get('metadata', {}).get('kind', 'hero')
            if counts.get(kind, 0) >= config.KIND_MAX.get(kind, 1):
                continue
            counts[kind] = counts.get(kind, 0) + 1
            out.append(it)
            if len(out) >= top_k:
                break
        return out

    def hero_blocks(self, hero):
        """返回某武将的全部语料块（攻略/相性用，保证人物召回完整）。"""
        self.blocks  # 确保懒加载（调用方可能先于 search 调用本方法）
        return [{'block_id': bid, 'text': text, 'metadata': meta, 'source': 'hero'}
                for bid, text, meta in (self._hero_index.get(hero) or [])]

    def _text_of(self, bid):
        self.blocks  # 确保懒加载
        return (self._id2text or {}).get(bid, '')

    def _meta_of(self, bid):
        self.blocks  # 确保懒加载
        return (self._id2meta or {}).get(bid, {})


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description='检索测试（不调用 LLM）')
    p.add_argument('--query', required=True)
    p.add_argument('--hero', action='append', default=None)
    p.add_argument('--top-k', type=int, default=config.TOP_K)
    args = p.parse_args()
    t0 = time.time()
    print(f'[检索] {args.query}  过滤: {args.hero}   top_k={args.top_k}')
    r = Retriever()
    res = r.search(args.query, heroes=args.hero, top_k=args.top_k)
    print(f'[结果] 召回 {len(res)} 块，耗时 {time.time()-t0:.1f}s')
    print('-' * 60)
    for it in res:
        print(f'[{it["score"]:.3f}] {it["block_id"]} ({it["source"]})')
        print('   ', it['text'][:120].replace('\n', ' / '))
    print('-' * 60)
    print(f'[完成] 总耗时 {time.time()-t0:.1f}s')