# -*- coding: utf-8 -*-
"""ONNX 编码器对齐门禁（G1）：50 块 + 20 query 与 sentence-transformers 逐条对齐。

CI 自动跳过（importorskip torch；语料/ONNX 产物缺一即 skip）。
两趟工作流（D3：先测量后固化）：
  第一趟  MJS_PARITY_RECORD=1 python -m pytest tests/test_rag_onnx_parity.py -s
          → 只打印 min/分位数，不做阈值判定
  第二趟  按观测值 − 余量固化下方常量（不低于 0.999/0.99），正常判定；
          门槛只随观测值调整，不允许手改让灯变绿
"""
from __future__ import annotations

import json
import os

import numpy as np
import pytest

torch = pytest.importorskip("torch")  # noqa: F841 — ST 侧基线依赖
pytest.importorskip("onnxruntime")
pytest.importorskip("tokenizers")

from src.config.env import PROJECT_ROOT as ROOT  # noqa: E402
from src.rag import config  # noqa: E402

FP32_MIN_COS = 0.999
MIN_TOP30_OVERLAP = 29   # /30（fp32 观测 30/30，留 1 块余量）
# int8 仅为参考件（S7 已裁决：不达标、维持 fp32 打包）：观测 min 余弦 ≈0.952、
# top-30 min 27/30，低于打包标准（0.99 / 29）。这里保留宽松信息性下限，
# 只看守量化管道不整体崩坏（重导出后质量断崖），不作发版门禁。
INT8_INFO_MIN_COS = 0.90
INT8_INFO_TOP30_OVERLAP = 25
N_BLOCKS = 50
N_QUERIES = 20
TOP_N = 30
RECORD = os.environ.get("MJS_PARITY_RECORD") == "1"


def _require_assets():
    if not (config.ONNX_MODEL_DIR / "sha256.json").is_file():
        pytest.skip("ONNX 产物未导出（dev-only）：python -m src.scripts.export_embedding_onnx")
    if not (config.CORPUS_DIR / "武将RAG语料.json").is_file():
        pytest.skip("本地语料未同步（私有数据仓）：python -m src.scripts.pull_data pull")
    if not (ROOT / "data" / "rag_evals" / "rule_faq_eval.json").is_file():
        pytest.skip("评估集缺失：python -m src.scripts.eval_rule_faqs --generate")


@pytest.fixture(scope="module")
def corpus_and_queries():
    _require_assets()
    from src.rag.indexer import load_all_blocks

    blocks = load_all_blocks()
    step = max(1, len(blocks) // N_BLOCKS)
    texts = [b[1] for b in blocks[::step]][:N_BLOCKS]
    dataset = json.loads(
        (ROOT / "data" / "rag_evals" / "rule_faq_eval.json").read_text(encoding="utf-8"))
    questions = [it["question"] for it in dataset["items"][:N_QUERIES]
                 if it.get("question")]
    assert len(texts) == N_BLOCKS and len(questions) == N_QUERIES
    return texts, questions


@pytest.fixture(scope="module")
def embeddings(corpus_and_queries):
    """三套编码（st 基线 / onnx fp32 / onnx int8），module 级只算一次。"""
    from src.rag.embedder import OnnxEmbedder, STEmbedder

    texts, queries = corpus_and_queries
    st = STEmbedder()
    onnx_fp32 = OnnxEmbedder()
    onnx_int8 = OnnxEmbedder(model_file="model.int8.onnx")
    queries_prefixed = [config.EMBEDDING_QUERY_INSTRUCTION + q for q in queries]
    return {
        "blocks": {
            "st": st.encode(texts, normalize_embeddings=True),
            "fp32": onnx_fp32.encode(texts),
            "int8": onnx_int8.encode(texts),
        },
        "queries": {
            "st": st.encode(queries_prefixed, normalize_embeddings=True),
            "fp32": onnx_fp32.encode(queries_prefixed),
            "int8": onnx_int8.encode(queries_prefixed),
        },
    }


def _assert_cos(label, coss, floor):
    if RECORD:
        percentiles = np.percentile(coss, [0, 1, 50, 99])
        print(f"\n[{label}] n={len(coss)} min={percentiles[0]:.6f} "
              f"p1={percentiles[1]:.6f} p50={percentiles[2]:.6f} "
              f"p99={percentiles[3]:.6f}")
        return
    worst = float(np.min(coss))
    assert worst >= floor, f"{label} 最低余弦 {worst:.6f} < 门槛 {floor}"


def test_parity_fp32_cosine(embeddings):
    for part in ("blocks", "queries"):
        ref = embeddings[part]["st"]
        got = embeddings[part]["fp32"]
        cos = np.sum(ref * got, axis=1) / (
            np.linalg.norm(ref, axis=1) * np.linalg.norm(got, axis=1))
        _assert_cos(f"fp32/{part}", cos, FP32_MIN_COS)


def test_parity_int8_cosine(embeddings):
    for part in ("blocks", "queries"):
        ref = embeddings[part]["st"]
        got = embeddings[part]["int8"]
        cos = np.sum(ref * got, axis=1) / (
            np.linalg.norm(ref, axis=1) * np.linalg.norm(got, axis=1))
        _assert_cos(f"int8/{part}", cos, INT8_INFO_MIN_COS)


def test_normalized_norms_approx_one(embeddings):
    for part in ("blocks", "queries"):
        for backend in ("st", "fp32", "int8"):
            norms = np.linalg.norm(embeddings[part][backend], axis=1)
            assert np.allclose(norms, 1.0, atol=1e-3), \
                f"{backend}/{part} 归一化后模长偏离 1（max 偏差 {np.max(np.abs(norms - 1)):.2e}）"


def test_top30_overlap(embeddings):
    """每个 query 的块侧 top-30 重合：fp32 ≥ 29/30（门禁）；
    int8 ≥ 25/30（信息性，S7 已裁决维持 fp32 打包）。"""
    blocks = embeddings["blocks"]
    for backend, floor in (("fp32", MIN_TOP30_OVERLAP),
                           ("int8", INT8_INFO_TOP30_OVERLAP)):
        overlaps = []
        for i in range(embeddings["queries"]["st"].shape[0]):
            ref_top = np.argsort(-(blocks["st"] @ embeddings["queries"]["st"][i]))[:TOP_N]
            got_top = np.argsort(-(blocks[backend] @ embeddings["queries"][backend][i]))[:TOP_N]
            overlaps.append(len(set(ref_top.tolist()) & set(got_top.tolist())))
        if RECORD:
            print(f"\n[top30/{backend}] min={min(overlaps)} "
                  f"mean={np.mean(overlaps):.1f} / {TOP_N}")
            continue
        worst = min(overlaps)
        assert worst >= floor, \
            f"{backend} top-30 重合最小 {worst}/{TOP_N} < {floor}"
