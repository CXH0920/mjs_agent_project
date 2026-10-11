# -*- coding: utf-8 -*-
"""embedder 抽象层测试：后端选择 / 缺件熔断 / 输出形状与 dtype。

onnxruntime、tokenizers、sentence_transformers 全部以假模块注入，
本文件在任何环境可跑（CI 无 torch/onnxruntime 依赖）。
"""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import numpy as np
import pytest
from src.rag import embedder


@pytest.fixture(autouse=True)
def _reset_singleton():
    embedder._SINGLETON.clear()
    yield
    embedder._SINGLETON.clear()


# ---------------------------------------------------------------
# 测试脚手架：三件套目录 + 假推理后端
# ---------------------------------------------------------------

def _make_onnx_dir(tmp_path, input_names=None, *, tamper=None):
    """写出可过自校验的 onnx 目录；tamper 指定破坏方式。"""
    d = tmp_path / "onnx"
    d.mkdir()
    (d / "model.onnx").write_bytes(b"fake-onnx-weights")
    (d / "tokenizer.json").write_text("{}", encoding="utf-8")
    if tamper == "drop_model":
        (d / "model.onnx").unlink()
    sidecar = {
        "model.onnx": {"sha256": embedder._sha256_of(d / "model.onnx")
                       if tamper != "drop_model" else "1" * 64},
        "tokenizer.json": {"sha256": embedder._sha256_of(d / "tokenizer.json")},
        "input_names": input_names or ["input_ids", "token_type_ids", "attention_mask"],
        "source_weights_sha256": "ab" * 32,
        "exported_at": "2026-10-11T00:00:00",
        "torch_version": "2.12.0",
    }
    if tamper == "wrong_hash":
        sidecar["model.onnx"]["sha256"] = "0" * 64
    if tamper == "drop_sidecar":
        return d
    (d / "sha256.json").write_text(json.dumps(sidecar), encoding="utf-8")
    return d


class _FakeSession:
    seen_feeds: list = []

    def __init__(self, model_path, options=None, providers=None):
        self.options = options

    def run(self, output_names, feeds):
        _FakeSession.seen_feeds.append(feeds)
        batch, seq = feeds["input_ids"].shape
        return [np.ones((batch, seq, 8), dtype=np.float32)]

    def get_inputs(self):
        return [SimpleNamespace(name=n)
                for n in ("input_ids", "attention_mask", "token_type_ids")]


class _FakeEncoding:
    def __init__(self, ids):
        self.ids = ids
        self.attention_mask = [1] * len(ids)
        self.type_ids = [0] * len(ids)


class _FakeTokenizer:
    def __init__(self):
        self.truncation = None

    @classmethod
    def from_file(cls, path):
        return cls()

    def enable_truncation(self, max_length):
        self.truncation = max_length

    def enable_padding(self, **kwargs):
        pass

    def encode_batch(self, texts):
        # id 序列长度 = 2*字符数（保证批内不等长，验证右 padding 路径）
        encodings = [_FakeEncoding([7] * (2 * len(t))) for t in texts]
        width = max(len(e.ids) for e in encodings)
        for e in encodings:  # 右 padding 到等长
            pad = width - len(e.ids)
            e.ids = e.ids + [0] * pad
            e.attention_mask = e.attention_mask + [0] * pad
            e.type_ids = e.type_ids + [0] * pad
        return encodings


@pytest.fixture
def fake_backends(monkeypatch):
    _FakeSession.seen_feeds = []
    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(
        SessionOptions=lambda: SimpleNamespace(intra_op_num_threads=0),
        InferenceSession=_FakeSession))
    monkeypatch.setitem(sys.modules, "tokenizers", SimpleNamespace(
        Tokenizer=_FakeTokenizer))


# ---------------------------------------------------------------
# 缺件熔断
# ---------------------------------------------------------------

def test_missing_sidecar_raises_with_fix_hint(tmp_path):
    d = _make_onnx_dir(tmp_path, tamper="drop_sidecar")
    with pytest.raises(RuntimeError, match="export_embedding_onnx"):
        embedder.OnnxEmbedder(d)


def test_missing_model_file_raises(tmp_path):
    d = _make_onnx_dir(tmp_path, tamper="drop_model")
    with pytest.raises(RuntimeError, match="缺失 model.onnx"):
        embedder.OnnxEmbedder(d)


def test_hash_mismatch_raises(tmp_path):
    d = _make_onnx_dir(tmp_path, tamper="wrong_hash")
    with pytest.raises(RuntimeError, match="哈希与侧车不符"):
        embedder.OnnxEmbedder(d)


# ---------------------------------------------------------------
# encode 输出契约（§2.2：np.ndarray、形状、dtype、归一化）
# ---------------------------------------------------------------

def test_encode_returns_normalized_ndarray(tmp_path, monkeypatch, fake_backends):
    d = _make_onnx_dir(tmp_path)
    monkeypatch.setattr(embedder.config, "ONNX_MODEL_DIR", d)
    emb = embedder.OnnxEmbedder(d)

    out = emb.encode(["甲甲", "乙"], batch_size=1)

    assert isinstance(out, np.ndarray)
    assert out.shape == (2, 8)
    assert out.dtype == np.float32
    assert np.allclose(np.linalg.norm(out, axis=1), 1.0, atol=1e-5)


def test_encode_single_string_and_empty_list(tmp_path, monkeypatch, fake_backends):
    d = _make_onnx_dir(tmp_path)
    emb = embedder.OnnxEmbedder(d)

    # 单字符串 → 一维（与 ST 契约一致，retriever 的 q.tolist() 依赖此形状）
    single = emb.encode("文本")
    assert isinstance(single, np.ndarray)
    assert single.shape == (8,)
    assert emb.encode([]).shape == (0, 0)


def test_encode_respects_input_names_from_sidecar(tmp_path, monkeypatch, fake_backends):
    """输入名以侧车为准：无 token_type_ids 的图不喂该键，避免运行期报错。"""
    d = _make_onnx_dir(tmp_path, input_names=["input_ids", "attention_mask"])
    emb = embedder.OnnxEmbedder(d)

    emb.encode(["文本"])

    assert list(_FakeSession.seen_feeds[-1]) == ["input_ids", "attention_mask"]


def test_encode_feeds_token_type_ids_when_declared(tmp_path, monkeypatch, fake_backends):
    d = _make_onnx_dir(tmp_path)  # 默认三输入（bge 真实形态）
    emb = embedder.OnnxEmbedder(d)

    emb.encode(["文本"])

    assert "token_type_ids" in _FakeSession.seen_feeds[-1]


def test_encode_unnormalized_keeps_raw_cls(tmp_path, monkeypatch, fake_backends):
    d = _make_onnx_dir(tmp_path)
    emb = embedder.OnnxEmbedder(d)

    out = emb.encode(["文本"], normalize_embeddings=False)

    assert np.allclose(np.linalg.norm(out, axis=1), np.sqrt(8), atol=1e-5)


# ---------------------------------------------------------------
# get_embedder：后端选择 + 单例
# ---------------------------------------------------------------

def test_get_embedder_unknown_backend_raises(monkeypatch):
    monkeypatch.setattr(embedder.config, "EMBEDDING_BACKEND", "tgi")
    with pytest.raises(RuntimeError, match="RAG_EMBEDDING_BACKEND"):
        embedder.get_embedder()


def test_get_embedder_onnx_returns_singleton(tmp_path, monkeypatch, fake_backends):
    d = _make_onnx_dir(tmp_path)
    monkeypatch.setattr(embedder.config, "EMBEDDING_BACKEND", "onnx")
    monkeypatch.setattr(embedder.config, "ONNX_MODEL_DIR", d)

    first = embedder.get_embedder()
    assert embedder.get_embedder() is first
    assert isinstance(first, embedder.OnnxEmbedder)


def test_get_embedder_st_backend_uses_sentence_transformers(monkeypatch):
    created = []

    class _FakeST:
        def __init__(self, model_name, device=None):
            created.append(model_name)

        def encode(self, texts, **kwargs):
            return np.ones((len(texts), 4), dtype=np.float32)

    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(
        SentenceTransformer=_FakeST))
    monkeypatch.setattr(embedder.config, "EMBEDDING_BACKEND", "st")
    monkeypatch.setattr(embedder.config, "EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")

    emb = embedder.get_embedder()
    out = emb.encode(["a", "b", "c"])

    assert isinstance(emb, embedder.STEmbedder)
    assert created == ["BAAI/bge-small-zh-v1.5"]
    assert out.shape == (3, 4)
