# -*- coding: utf-8 -*-
"""编码器抽象：ONNX（默认）/ sentence-transformers（回滚档）统一 encode 契约。

encode() 必须返回 np.ndarray (B, dim)：indexer 依赖切片 .tolist()、
retriever 依赖 q.tolist()，这是 §2.2 的兼容性不变量。

onnxruntime / tokenizers 在加载点函数内惰性导入——CI 环境无这两项依赖
时本模块仍可导入（对齐测试 importorskip torch 同理）。
"""
import hashlib
import json
import logging
import threading
import time
from pathlib import Path

import numpy as np
from src.rag import config

logger = logging.getLogger(__name__)

FIX_HINT = "python -m src.scripts.export_embedding_onnx"

_SINGLETON: dict = {}
_LOCK = threading.Lock()

MAX_SEQ_LEN = 512


def _sha256_of(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class OnnxEmbedder:
    """onnxruntime 推理 + tokenizers 分词，CLS 池化 + L2 归一化。

    构造即校验三件套（model.onnx / tokenizer.json / sha256.json 侧车）：
    缺件或哈希不符直接熔断——避免带病运行产出与既有索引不同源的向量。
    """

    def __init__(self, model_dir=None, model_file="model.onnx"):
        self._dir = Path(model_dir or config.ONNX_MODEL_DIR)
        self._model_file = model_file
        self._verify_files()
        self._session = None
        self._tok = None
        self._input_names = None

    def _verify_files(self):
        sidecar_path = self._dir / "sha256.json"
        try:
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise RuntimeError(
                f"ONNX 编码器侧车缺失或不可读：{sidecar_path}（{error}）；"
                f"请运行 {FIX_HINT} 重建") from error
        problems = []
        for name in ("model.onnx", "tokenizer.json"):
            entry = sidecar.get(name)
            expect = entry.get("sha256") if isinstance(entry, dict) else entry
            path = self._dir / name
            if not path.is_file():
                problems.append(f"缺失 {name}")
            elif not isinstance(expect, str) or _sha256_of(path) != expect:
                problems.append(f"{name} 哈希与侧车不符（文件损坏或版本错配）")
        # 变体（如 model.int8.onnx）：侧车未登记或哈希不符同样熔断
        if self._model_file != "model.onnx":
            entry = sidecar.get(self._model_file)
            expect = entry.get("sha256") if isinstance(entry, dict) else entry
            path = self._dir / self._model_file
            if entry is None:
                raise RuntimeError(
                    f"侧车未登记 {self._model_file}；请运行 {FIX_HINT} --int8 导出")
            if not path.is_file() or not isinstance(expect, str) \
                    or _sha256_of(path) != expect:
                problems.append(f"{self._model_file} 缺失或哈希与侧车不符")
        if problems:
            raise RuntimeError(
                f"ONNX 编码器自校验失败：{'；'.join(problems)}；请重新运行 {FIX_HINT}")
        names = sidecar.get("input_names")
        self._expected_input_names = (
            [str(n) for n in names] if isinstance(names, list) else None)

    def _load(self):
        if self._session is not None:
            return
        # 惰性导入：CI / 精简环境缺 onnxruntime、tokenizers 时本模块仍可导入
        import onnxruntime as ort
        from tokenizers import Tokenizer

        t0 = time.time()
        options = ort.SessionOptions()
        options.intra_op_num_threads = config.RAG_EMBED_THREADS
        self._session = ort.InferenceSession(
            str(self._dir / self._model_file), options,
            providers=["CPUExecutionProvider"])
        # 喂入的输入名以侧车记录的导出时 graph input names 为准（兜底现查），
        # 避免 token_type_ids 有/无在运行期才报错
        graph_names = [item.name for item in self._session.get_inputs()]
        self._input_names = self._expected_input_names or graph_names
        self._tok = Tokenizer.from_file(str(self._dir / "tokenizer.json"))
        self._tok.enable_truncation(max_length=MAX_SEQ_LEN)
        # 右 padding：CLS 恒在 0 位，attention_mask 保证 pad 位不参与池化语义
        self._tok.enable_padding(length=None, pad_id=0, pad_type_id=0,
                                  pad_token="[PAD]")
        logger.info("ONNX 编码器就绪（输入 %s），耗时 %.1fs",
                    self._input_names, time.time() - t0)

    def encode(self, texts, batch_size=32, normalize_embeddings=True,
               show_progress_bar=False):
        self._load()
        # 与 ST 契约一致：单字符串 → (D,)，列表 → (B, D)（retriever 的
        # q.tolist() 依赖单条时一维，多一层嵌套 Chroma 会拒收）
        single = isinstance(texts, str)
        if single:
            texts = [texts]
        texts = list(texts)
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        chunks = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start:start + batch_size]
            encoded = self._tok.encode_batch(batch)
            feeds = {
                "input_ids": np.array([e.ids for e in encoded], dtype=np.int64),
                "attention_mask": np.array(
                    [e.attention_mask for e in encoded], dtype=np.int64),
            }
            if "token_type_ids" in self._input_names:
                feeds["token_type_ids"] = np.array(
                    [e.type_ids for e in encoded], dtype=np.int64)
            hidden = self._session.run(None, feeds)[0]
            emb = hidden[:, 0]  # CLS 池化（右 padding 下恒在 0 位）
            if normalize_embeddings:
                norms = np.linalg.norm(emb, axis=1, keepdims=True)
                emb = emb / np.maximum(norms, 1e-12)
            chunks.append(emb.astype(np.float32))
        out = np.concatenate(chunks, axis=0)
        return out[0] if single else out


class STEmbedder:
    """sentence-transformers 回滚档（开发环境有 torch 时经
    RAG_EMBEDDING_BACKEND=st 启用），encode 契约与 OnnxEmbedder 一致。"""

    def __init__(self):
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(config.EMBEDDING_MODEL, device="cpu")

    def encode(self, texts, batch_size=32, normalize_embeddings=True,
               show_progress_bar=False):
        out = self._model.encode(texts, batch_size=batch_size,
                                 normalize_embeddings=normalize_embeddings,
                                 show_progress_bar=show_progress_bar)
        return np.asarray(out)


def get_embedder():
    """后端选择 + 单例缓存：RAG_EMBEDDING_BACKEND 环境变量（默认 onnx）。"""
    backend = config.EMBEDDING_BACKEND
    with _LOCK:
        if backend not in _SINGLETON:
            if backend == "onnx":
                _SINGLETON[backend] = OnnxEmbedder()
            elif backend == "st":
                _SINGLETON[backend] = STEmbedder()
            else:
                raise RuntimeError(
                    f"未知 RAG_EMBEDDING_BACKEND: {backend!r}（可选 onnx / st）")
        return _SINGLETON[backend]
