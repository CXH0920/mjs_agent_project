# -*- coding: utf-8 -*-
"""RAG 攻略知识库配置（test_project 内嵌版）。

语料与向量索引随项目入库（data/rag_corpus、data/rag_index）；
嵌入模型默认共享 mjs_rag_project 的 modelscope 缓存（config.env RAG_MODEL_DIR 可覆盖）。
"""
from __future__ import annotations

import os
from pathlib import Path

from src.config.env import PROJECT_ROOT, parse_env_file

ROOT = PROJECT_ROOT
CORPUS_DIR = ROOT / "data" / "rag_corpus"
RAG_INDEX_DIR = ROOT / "data" / "rag_index"
CHROMA_DIR = RAG_INDEX_DIR / "chroma"
MODEL_CACHE = ROOT / "data" / "rag_models" / "models"
MODELSCOPE_DIR = ROOT / "data" / "rag_models" / "modelscope"
LOG_FILE = ROOT / "logs" / "rag.log"

# 注意：不再在 import 时创建目录（#49）——目录在使用点确保：
# CHROMA_DIR 由 chromadb.PersistentClient 自建；LOG_FILE 由 setup_logging 创建；
# CORPUS_DIR 由 scripts/build_*.py 创建；MODEL_CACHE 由模型加载器创建。

# 必须在 import sentence_transformers / transformers 之前设置
os.environ.setdefault("HF_HOME", str(MODEL_CACHE))
os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", str(MODEL_CACHE / "sentence_transformers"))
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")  # 国内镜像

_ENV = parse_env_file()

# ---------------------------------------------------------------
# RAG 开关与路径（优先级：环境变量 > config.env > 默认值）
# ---------------------------------------------------------------
RAG_ENABLED = str(os.environ.get("RAG_ENABLED") or _ENV.get("RAG_ENABLED", "true")).lower() in ("true", "1", "yes")
# 默认留空：不硬编码机器绝对路径（#50）；未配置时 _find_local_model 回退项目内缓存/HF 在线下载
RAG_MODEL_DIR = os.environ.get("RAG_MODEL_DIR") or _ENV.get("RAG_MODEL_DIR") or ""
RAG_PROJECT_DIR = os.environ.get("RAG_PROJECT_DIR") or _ENV.get("RAG_PROJECT_DIR") or ""

def _to_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key) or _ENV.get(key) or default)
    except (ValueError, TypeError):
        return default

TOP_K = _to_int("RAG_TOP_K", 12)
RAG_PROMPT_CHARS = _to_int("RAG_PROMPT_CHARS", 6000)
RAG_BROWSER_PROMPT_CHARS = _to_int("RAG_BROWSER_PROMPT_CHARS", 3000)
RAG_SYNERGY_PROMPT_CHARS = _to_int("RAG_SYNERGY_PROMPT_CHARS", 6000)

# ---------------------------------------------------------------
# Embedding：本地 bge-small-zh-v1.5
# 优先加载项目内 modelscope 缓存，其次共享 RAG_MODEL_DIR，最后 HF 在线下载
# ---------------------------------------------------------------
def _find_local_model():
    candidates = []
    if MODELSCOPE_DIR.exists():
        candidates.append(MODELSCOPE_DIR)
    if RAG_MODEL_DIR and Path(RAG_MODEL_DIR).exists():
        candidates.append(Path(RAG_MODEL_DIR))
    for base in candidates:
        for p in base.rglob("config.json"):
            if "bge-small-zh" in p.as_posix():
                return p.parent
    return None

_LOCAL_MODEL = _find_local_model()
EMBEDDING_MODEL = str(_LOCAL_MODEL) if _LOCAL_MODEL else os.environ.get(
    "MJS_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
EMBEDDING_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："

# ---------------------------------------------------------------
# 检索参数
# ---------------------------------------------------------------
VECTOR_WEIGHT = 1.0     # 向量相似度权重
KEYWORD_BONUS = 0.15    # 关键词命中的加分（绝对分）
RRF_K = 60              # RRF 常数
MIN_VECTOR_SCORE = 0.25  # 向量相似度下限（1-cosine distance）
MAX_KEYWORD_ONLY = 3    # 纯关键词命中块数量上限
KIND_MAX = {            # 类型配额：每类语料块最多进入最终结果的条数
    "hero": 6, "card": 2, "rule": 2, "faq": 2, "term": 1,
    "special": 1, "cardpt": 1, "equip": 1, "modify": 1, "classification": 1,
}

# ---------------------------------------------------------------
# Prompt 预算 / 日志
# ---------------------------------------------------------------
MAX_PROMPT_CHARS = 12000  # 注入 Prompt 的语料块总字符预算


def setup_logging(level: int = 10):
    """初始化 rag 日志：info 写入 logs/rag.log，控制台仅 warning 及以上。"""
    import logging
    logger = logging.getLogger("rag")
    if getattr(logger, "_configured", False):
        return logger
    logger.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
        fh.setLevel(level)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        pass
    sh = logging.StreamHandler()
    sh.setLevel(logging.WARNING)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    logger._configured = True
    return logger