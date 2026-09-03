"""RAG 索引器补测：语料加载规范化与建索引/增量同步链路（批次3，原零直测）。"""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from src.rag import indexer


def _write_corpus(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


HERO_BLOCKS = [
    {
        "block_id": "hero_caocao_skill",
        "hero": "曹操", "faction": "魏", "position": "攻击",
        "skill": "奸雄", "timing": ["出牌阶段"], "trigger_condition": ["受到伤害后"],
        "description": "可以获得造成伤害的牌", "settlement": "立即获得该牌",
        "is_current": "false", "as_of": "2026-01-01",
    },
    {
        "block_id": "hero_caocao_overview",
        "block_type": "overview", "hero": "曹操", "description": "魏武帝",
    },
]


# ---------------------------------------------------------------
# load_all_blocks：语料读取 + 版本元数据注入
# ---------------------------------------------------------------

def test_load_all_blocks_normalizes_and_injects_version_meta(tmp_path, caplog) -> None:
    _write_corpus(tmp_path / "武将RAG语料.json", HERO_BLOCKS)
    # 其余语料文件缺省：应告警跳过而非报错
    with caplog.at_level("WARNING", logger="src.rag.indexer"):
        blocks = _load_all_blocks_from(tmp_path)

    assert [b[0] for b in blocks] == ["hero_caocao_skill", "hero_caocao_overview"]
    skill_text, skill_meta = blocks[0][1], blocks[0][2]
    assert "【武将技能】曹操｜魏｜攻击" in skill_text
    assert "技能：奸雄" in skill_text
    assert "结算：立即获得该牌" in skill_text
    assert skill_meta["is_current"] == "false"
    assert skill_meta["as_of"] == "2026-01-01"
    overview_meta = blocks[1][2]
    assert overview_meta["is_current"] == "true"
    assert "【武将总览】魏武帝" in blocks[1][1]
    assert "缺少语料文件" in caplog.text


def _load_all_blocks_from(docs_dir):
    original = indexer.DOCS_DIR
    indexer.DOCS_DIR = docs_dir
    try:
        return indexer.load_all_blocks()
    finally:
        indexer.DOCS_DIR = original


def test_load_all_blocks_raises_on_block_count_mismatch(tmp_path, monkeypatch) -> None:
    _write_corpus(tmp_path / "f.json", [{}])
    monkeypatch.setattr(indexer, "DOCS_DIR", tmp_path)
    monkeypatch.setattr(indexer, "CORPUS_FILES", [("f.json", lambda data: [("x", "t", {})] * (len(data) + 1))])

    with pytest.raises(ValueError, match="规范化块数与源数据不一致"):
        indexer.load_all_blocks()


# ---------------------------------------------------------------
# _norm_*：各语料规范化纯函数（含 _norm_combo 设计点A 锚点）
# ---------------------------------------------------------------

def test_norm_combo_keeps_hero_pair_as_list_meta() -> None:
    """设计点A：combo 语料不贴单值 hero、贴 heroes 列表——避免 post-filter 按武将过滤时丢掉一侧。"""
    bid, text, meta = indexer._norm_combo([{
        "block_id": "combo_1_2", "hero_a": "曹操", "hero_b": "刘备",
        "highlight": "双核心", "mechanism": "互补", "song": "某歌", "bv": "BV1x", "source_md": "a.md",
    }])[0]

    assert bid == "combo_1_2"
    assert text.startswith("【组合】曹操 + 刘备")
    assert "亮点：双核心" in text and "互补" in text and "（某歌）" in text
    assert "hero" not in meta
    assert meta["heroes"] == ["曹操", "刘备"]
    assert meta["bv"] == "BV1x" and meta["source_md"] == "a.md"


def test_norm_combo_omits_optional_fields_when_absent() -> None:
    _, text, meta = indexer._norm_combo([{"block_id": "c", "hero_a": "甲", "hero_b": "乙"}])[0]

    assert text == "【组合】甲 + 乙"
    assert "bv" not in meta and "source_md" not in meta


def test_norm_guide_tags_single_hero_for_generation_recall() -> None:
    bid, text, meta = indexer._norm_guide([
        {"block_id": "g_1", "hero": "曹操", "section": "定位", "text": "肉盾"},
    ])[0]

    assert bid == "g_1"
    assert text.startswith("【武将攻略】曹操｜定位")
    assert meta["hero"] == "曹操"


@pytest.mark.parametrize("norm,block,kind,frag,meta_expect", [
    (indexer._norm_rule,
     {"block_id": "r", "title": "胜负", "content": "主将死亡即败"},
     "rule", "【规则】胜负", {}),
    (indexer._norm_term,
     {"block_id": "term_摸牌", "term": "摸牌", "definition": "从牌库顶获得牌"},
     "term", "【术语】摸牌", {"term": "摸牌"}),
    (indexer._norm_faq,
     {"block_id": "faq_001", "faq_no": "1", "ruling": "先锦囊后伤害", "source": "官方群"},
     "faq", "【FAQ裁定】先锦囊后伤害", {"faq_no": "1"}),
    (indexer._norm_card,
     {"block_id": "card_k_杀", "card_type": "基本牌", "card_amount": "x", "effect": "造成伤害", "effect_detail": "无"},
     "card", "【卡牌】杀", {"card": "杀", "card_type": "基本牌"}),
    (indexer._norm_cardpt,
     {"block_id": "pt_杀", "card": "杀", "count": "x", "suits": "♠♥", "points": "4,6", "attribute_judge": "注记"},
     "cardpt", "【点数花色】杀", {"card": "杀"}),
    (indexer._norm_equip,
     {"block_id": "eq_诸葛连弩", "card": "诸葛连弩", "equip_subtype": "武器", "effect": "出杀无限"},
     "equip", "【装备】诸葛连弩", {"card": "诸葛连弩"}),
    (indexer._norm_modify,
     {"block_id": "md_杀", "card_name": "杀", "card_type": "基本牌",
      "base_effect": "基础", "strengthen_effect": "加强", "weaken_effect": "削弱", "settlement_detail": "结算"},
     "modify", "【加强削弱】杀", {"card": "杀"}),
    (indexer._norm_classification,
     {"block_id": "cls_曹操", "hero": "曹操", "categories_text": "攻击/控制", "position": "攻击", "reason": "理由"},
     "classification", "【武将分类】曹操：攻击/控制", {"hero": "曹操"}),
    (indexer._norm_special,
     {"block_id": "sp_连锁", "category": "连锁", "name": "多米诺", "effect": "效果", "stackable": "可叠加"},
     "special", "【特殊机制】连锁：多米诺", {"category": "连锁", "name": "多米诺"}),
])
def test_norm_kinds_emit_expected_text_and_meta(norm, block, kind, frag, meta_expect) -> None:
    """各语料 kind 标签、文本前缀与关键元数据字段的结构契约。"""
    out = norm([block])

    assert len(out) == 1
    bid, text, meta = out[0]
    assert bid == block["block_id"]
    assert frag in text
    assert meta["kind"] == kind
    for key, value in meta_expect.items():
        assert meta[key] == value


# ---------------------------------------------------------------
# build_index：全量重建 / 增量同步 / 防护分支（sentence-transformers 与 chromadb 打桩）
# ---------------------------------------------------------------

class _FakeCollection:
    def __init__(self):
        self.added = []
        self.updated = []
        self.deleted_ids = []
        self.deleted_where_count = 0
        self.existing_ids = []
        self.count_value = 0

    def get(self, include=None):
        return {"ids": list(self.existing_ids)}

    def delete(self, ids=None, where=None):
        if where is not None:
            self.deleted_where_count += 1
            self.count_value = 0
        else:
            self.deleted_ids.extend(ids)
            self.count_value -= len(ids)

    def add(self, ids, documents, metadatas, embeddings):
        self.added.append((list(ids), list(documents), list(metadatas)))
        self.count_value += len(ids)

    def update(self, ids, documents, metadatas, embeddings):
        self.updated.append((list(ids), list(documents), list(metadatas)))

    def count(self):
        return self.count_value


class _FakeClient:
    def __init__(self, collection, existing_collections=(), fail_delete=False):
        self.collection = collection
        self.existing_collections = list(existing_collections)
        self.fail_delete = fail_delete
        self.delete_attempts = []
        self.persistent_paths = []

    def delete_collection(self, name):
        self.delete_attempts.append(name)
        if self.fail_delete:
            raise RuntimeError("simulated file lock")

    def list_collections(self):
        return [SimpleNamespace(name=n) for n in self.existing_collections]

    def get_or_create_collection(self, name, metadata=None):
        return self.collection


def _patch_heavy_deps(monkeypatch, client):
    class _FakeModel:
        def __init__(self, model_name, device=None):
            self.model_name = model_name

        def encode(self, docs, **kwargs):
            return np.ones((len(docs), 4), dtype=np.float32)

    monkeypatch.setitem(sys.modules, "sentence_transformers",
                        SimpleNamespace(SentenceTransformer=_FakeModel))
    monkeypatch.setitem(sys.modules, "chromadb",
                        SimpleNamespace(PersistentClient=lambda path: client.persistent_paths.append(path) or client))


def _patch_corpus(monkeypatch, tmp_path):
    _write_corpus(tmp_path / "武将RAG语料.json", HERO_BLOCKS)
    monkeypatch.setattr(indexer, "DOCS_DIR", tmp_path)
    monkeypatch.setattr(indexer.config, "CHROMA_DIR", tmp_path / "chroma")


def test_build_index_rebuild_writes_all_blocks(monkeypatch, tmp_path) -> None:
    _patch_corpus(monkeypatch, tmp_path)
    collection = _FakeCollection()
    client = _FakeClient(collection)
    _patch_heavy_deps(monkeypatch, client)

    total, coll_name = indexer.build_index(rebuild=True)

    assert (total, coll_name) == (2, "mjs_rag_v1")
    assert client.delete_attempts == ["mjs_rag_v1"]
    assert collection.deleted_where_count == 0  # 空集合无需清空
    assert len(collection.added) == 1  # 2 块单批写入
    ids, documents, metadatas = collection.added[0]
    assert ids == ["hero_caocao_skill", "hero_caocao_overview"]
    assert "【武将技能】曹操" in documents[0]
    assert metadatas[0]["is_current"] == "false"
    assert collection.count() == 2


def test_build_index_rebuild_clears_residual_vectors(monkeypatch, tmp_path) -> None:
    _patch_corpus(monkeypatch, tmp_path)
    collection = _FakeCollection()
    collection.count_value = 5  # 残留旧向量
    client = _FakeClient(collection)
    _patch_heavy_deps(monkeypatch, client)

    indexer.build_index(rebuild=True)

    assert collection.deleted_where_count == 1
    assert collection.count() == 2  # 清空后仅剩本次写入


def test_build_index_aborts_when_stale_collection_undeletable(monkeypatch, tmp_path) -> None:
    """删除旧集合失败且集合仍在：必须中止，不能在残留向量上重建（静默吞异常回归）。"""
    _patch_corpus(monkeypatch, tmp_path)
    client = _FakeClient(_FakeCollection(), existing_collections=["mjs_rag_v1"], fail_delete=True)
    _patch_heavy_deps(monkeypatch, client)

    with pytest.raises(RuntimeError, match="删除失败"):
        indexer.build_index(rebuild=True)


def test_build_index_proceeds_when_collection_absent(monkeypatch, tmp_path) -> None:
    _patch_corpus(monkeypatch, tmp_path)
    collection = _FakeCollection()
    client = _FakeClient(collection, existing_collections=[], fail_delete=True)
    _patch_heavy_deps(monkeypatch, client)

    total, _ = indexer.build_index(rebuild=True)

    assert total == 2  # 首建集合不存在属正常路径


def test_build_index_incremental_syncs_add_update_delete(monkeypatch, tmp_path) -> None:
    _patch_corpus(monkeypatch, tmp_path)
    collection = _FakeCollection()
    collection.existing_ids = ["hero_caocao_skill", "hero_stale"]
    collection.count_value = 2  # 库内已有 2 块（其中 1 块已过期）
    client = _FakeClient(collection)
    _patch_heavy_deps(monkeypatch, client)

    total, _ = indexer.build_index(rebuild=False)

    assert collection.deleted_ids == ["hero_stale"]
    assert collection.updated and collection.updated[0][0] == ["hero_caocao_skill"]
    assert collection.added and collection.added[0][0] == ["hero_caocao_overview"]
    assert total == 2


def test_build_index_rejects_duplicate_block_ids(monkeypatch, tmp_path) -> None:
    _write_corpus(tmp_path / "武将RAG语料.json", HERO_BLOCKS + [dict(HERO_BLOCKS[0])])
    monkeypatch.setattr(indexer, "DOCS_DIR", tmp_path)
    client = _FakeClient(_FakeCollection())
    _patch_heavy_deps(monkeypatch, client)

    with pytest.raises(ValueError, match="语料块 id 重复"):
        indexer.build_index(rebuild=True)


def test_build_index_without_corpus_returns_zero(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(indexer, "DOCS_DIR", tmp_path)
    client = _FakeClient(_FakeCollection())
    _patch_heavy_deps(monkeypatch, client)

    assert indexer.build_index(rebuild=True) == (0, "")
