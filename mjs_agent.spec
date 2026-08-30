# -*- mode: python ; coding: utf-8 -*-
"""名将杀 Agent PyInstaller 规格（onedir，精简/完整双模式）。

构建：python release.py [--full] [--zip]
- 精简版（默认）：核心对战辅助 + OCR + AI(httpx)，exclude playwright 重链
- 完整版（MJS_FULL=1）：精简 + RAG 知识库维护页 + Playwright 抓取

关键设计详见 打包发版指南.md（踩坑 1-10）；改动相关代码切勿回退踩坑处理。
验证靠 release.py 打包 + 烟雾测试，OCR 模型路径/excludes 等可能需迭代调试。
"""

import fnmatch
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, copy_metadata

# 项目根（spec 文件所在目录，与 release.py 同级）
HERE = Path(SPECPATH).resolve()
BUILD_DEPS = HERE / "build_deps"  # CPU paddlepaddle 独立安装目录

# ── 双模式开关 ──
FULL = os.environ.get("MJS_FULL") == "1"

# CPU paddle 优先收集：让 collect_all("paddle") 命中 build_deps 的 CPU 版，
# 规避 myenv GPU 版带 CUDA/cuDNN 的体积膨胀（release.py 另设 PYTHONPATH 双保险）
if BUILD_DEPS.is_dir():
    sys.path.insert(0, str(BUILD_DEPS))


# ── _collect_dir：替代裸 Tree（PyInstaller 6.x 兼容，踩坑1）──
def _match_exclude(rel: Path, pattern: str) -> bool:
    """匹配过滤规则：扩展名(*.ext)、含/的目录段(seg)、纯名既匹配文件名也匹配路径任一段。"""
    if pattern.startswith("*."):
        return rel.suffix == pattern[1:] or rel.name.endswith(pattern[1:])
    if "/" in pattern:
        return any(part == pattern for part in rel.parts)
    return rel.name == pattern or pattern in rel.parts


def _collect_dir(src, dst, excludes=()):
    """walk src 生成 (src, dst) 二元组 datas。

    PyInstaller 6.x format_binaries_and_datas 直接 for src,dst 解包，不再
    isinstance(Tree) 分流；裸 Tree 元素是三元组会崩。本函数生成二元组，
    excludes 按 basename/扩展名/目录段过滤运行时产物。
    """
    src = Path(src)
    result = []
    for p in src.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(src)
        if any(_match_exclude(rel, ex) for ex in excludes):
            continue
        result.append((str(p), str(Path(dst) / rel.parent)))
    return result


# ── 收集依赖 ──
binaries = []
datas = []
hiddenimports = []

# CPU paddle + paddleocr + 推理 import 链依赖（pyclipper/shapely/skimage/rapidfuzz/
# imgaug/lmdb 在 paddleocr 内部 import，PyInstaller 静态分析漏收其 C 扩展，需显式 collect_all）
for pkg in ("paddle", "paddleocr", "pyclipper", "shapely", "skimage", "rapidfuzz", "imgaug", "lmdb"):
    b, d, h = collect_all(pkg)
    binaries += b
    datas += d
    hiddenimports += h

# -m 子进程入口模块（QProcess 用 sys.executable -m <module> 跑，PyInstaller 不跟踪
# -m 字符串，需显式声明；browser_generator 是 batch 的 lazy import，保险加上）
hiddenimports += [
    "src.scraper.ai_batch",
    "src.scraper.official",
    "src.scraper.incremental",
    "src.scraper.ai.browser_generator",
]
# 维护页 -m 脚本（src.scripts 包，PyInstaller 不跟踪 -m 字符串，需显式收集全部子模块）
hiddenimports += collect_submodules("src.scripts")

# Cython Utility/*.cpp（踩坑3）、imageio dist-info（踩坑4）、cnradical 数据（踩坑5）
datas += collect_data_files("Cython")
datas += copy_metadata("imageio")
datas += collect_data_files("cnradical")

# ── OCR 模型（~/.paddleocr/whl → paddleocr_models，离线用）──
# paddle_loader frozen 下复制 BUNDLE_ROOT/paddleocr_models 到 %TEMP%，det/rec/cls 指向它
ocr_home = Path.home() / ".paddleocr" / "whl"
for sub in ("det", "rec", "cls"):
    ch_dir = ocr_home / sub / "ch"
    if not ch_dir.is_dir():
        continue
    for p in ch_dir.rglob("*"):
        if p.is_file():
            # 扁平化到 paddleocr_models/{sub}/，det_model_dir 指向它
            # （paddle inference 在 model_dir 内 glob *.pdmodel 加载）
            datas.append((str(p), f"paddleocr_models/{sub}"))

# ── 静态资源 ──
# data：排除运行时产物（对应 .gitignore + 首启生成物）。
# rag_corpus 语料 / rag_evals 评估集是知识库维护页的运行资料（任务源/输出/状态判断都
# 依赖，见 task_defs.py），不排除；rag_index / rag_models 仍排除（向量检索依赖的
# transformers 已被 excludes，索引与模型缓存可由维护管道在开发机重建）
DATA_GLOBS = ("eval_*.json", "syn_*.json", "sample_*.json", "test_guide.json",
              "*.corrupt-*.json", "*_待复核.csv",
              # 示例占位数据（已被 prompt 内嵌 few-shot 替代，无代码引用）、
              # 带日期的一次性分析文档（源数据在 hero_classification.json）
              "example_*.json", "武将分类20*.md")
DATA_NAMES = ("edge_profile", "char_info_cache.json", "backups", "archive",
              "rag_index", "rag_models",
              "announcements.json", "baike_snapshot.json", "武将推荐指数状态.json",
              "eval_review.md", "_skills_dump.txt", "_syn_review.md")


def _data_excluded(rel: Path) -> bool:
    name = rel.name
    if any(fnmatch.fnmatch(name, g) for g in DATA_GLOBS):
        return True
    return any(_match_exclude(rel, ex) for ex in DATA_NAMES)


for p in (HERE / "data").rglob("*"):
    if p.is_file():
        rel = p.relative_to(HERE / "data")
        if _data_excluded(rel):
            continue
        datas.append((str(p), f"data/{rel.parent}"))

# 元规则母本：元规则/术语/FAQ 语料任务的源（build_rule_corpus 读 PROJECT_ROOT/docs/，
# 首启由 _ensure_clean_runtime 部署到 exe 同级），docs 主体不入包，此文件单独收集
_meta_rule_doc = HERE / "docs" / "元规则整理-完整版.md"
if _meta_rule_doc.is_file():
    datas.append((str(_meta_rule_doc), "docs"))

# config：排除用户层敏感/个人配置（ocr_rois.json 用户 ROI、api_profiles.json 含真实
# API Key 的多供应商档案，运行时从 PROJECT_ROOT/config 读、缺失时返回空档案），
# 保留静态 default/faction_colors/model_pricing
datas += _collect_dir(HERE / "config", "config", excludes=["ocr_rois.json", "api_profiles.json"])
# templates / images
datas += _collect_dir(HERE / "templates", "templates")
datas += _collect_dir(HERE / "images", "images")
# 根级静态文件
datas += [(str(HERE / "mjs.ico"), ".")]
datas += [(str(HERE / "config.env.example"), ".")]
# src/data 静态基线（character_feature_repository 读 BUNDLE_ROOT/src/data，踩坑5）
datas += [(str(HERE / "src" / "data" / "char_info_cache.json"), "src/data")]
datas += [(str(HERE / "src" / "data" / "wubi86.txt"), "src/data")]
# docs/prompts（AI 生成 Prompt 文件，api_generator/browser_generator 读 BUNDLE_ROOT/docs/prompts）
if (HERE / "docs" / "prompts").is_dir():
    datas += _collect_dir(HERE / "docs" / "prompts", "docs/prompts")

# ── 完整版额外：playwright + RAG 检索（踩坑15）──
if FULL:
    b, d, h = collect_all("playwright")
    binaries += b
    datas += d
    hiddenimports += h
    # chromadb 按文件系统全量收集：检索路径上 telemetry/api.rust 等子模块是运行时
    # 动态 import，静态分析不可见（实测 eval 检索报 No module named
    # 'chromadb.api.rust' / 'chromadb.telemetry.product.posthog'），必须 collect_all 兜住
    b, d, h = collect_all("chromadb")
    binaries += b
    datas += d
    hiddenimports += h
    # chroma 向量索引：检索数据，与 bge 模型配套（索引即用该模型编码）；
    # 运行期随资料部署复制到可写根（chroma 打开 sqlite 需写权限，不能留在只读 _internal）
    datas += _collect_dir(HERE / "data" / "rag_index", "data/rag_index")
    # bge-small-zh 模型（HF snapshots 结构）：只收 safetensors 权重，
    # pytorch_model.bin 为冗余副本（sentence_transformers 优先加载 safetensors）
    datas += _collect_dir(HERE / "data" / "rag_models" / "modelscope",
                          "data/rag_models/modelscope", excludes=["pytorch_model.bin"])

# ── excludes ──
excludes = [
    # paddleocr.ppstructure 依赖，OCR 仅 det+rec 不用（踩坑9，~430MB）。
    # 注意 torch/transformers/tokenizers 同时是 sentence_transformers（RAG 检索）的
    # 推理底座：精简版排除 → RAG 降级为经典模式；完整版在下方移除这三项启用 RAG（踩坑15）。
    # onnxruntime 仅 chromadb 默认 embedding 用（检索显式传 query_embeddings），恒排除
    "torch", "torchvision", "transformers", "tokenizers", "onnxruntime", "onnx",
    # PySide6 WebEngine/QML（markdown 用 QTextBrowser，零 WebEngine，~150MB）
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets",
    "PySide6.QtQuick3D", "PySide6.QtQuickTest", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    # 测试/开发工具
    "pytest", "_pytest", "pluggy", "ruff", "IPython",
]
if not FULL:
    excludes += ["playwright"]
else:
    for _rag_dep in ("torch", "transformers", "tokenizers"):
        excludes.remove(_rag_dep)

# ── Analysis ──
a = Analysis(
    [str(HERE / "src" / "main.py")],
    pathex=[str(HERE), str(BUILD_DEPS)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="mjs_agent",
    console=False,  # windowed（无控制台，踩坑6/7）
    upx=False,  # paddle .pyd 压缩易致加载失败（踩坑9）
    icon=str(HERE / "mjs.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="mjs_agent",
    strip=False,
    upx=False,
)

# .full_build 标记由 release.py build() 在 PyInstaller 完成后写入（spec 末尾
# touch 会被 PyInstaller 构建覆盖 dist，时机不可靠），运行期 is_full_build() 读它。
