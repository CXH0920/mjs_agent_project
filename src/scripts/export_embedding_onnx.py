# -*- coding: utf-8 -*-
"""bge 编码器 ONNX 导出（export_embedding_onnx.py，dev-only：依赖 torch/onnx）

从 config._find_local_model() 定位的同一份源权重导出 ONNX 三件套到
data/rag_models/onnx/（model.onnx / tokenizer.json / sha256.json 侧车），
运行期 OnnxEmbedder 只认侧车哈希——产物与索引同源由此保证。

用法：
    python -m src.scripts.export_embedding_onnx            # fp32 导出 + 写侧车（幂等）
    python -m src.scripts.export_embedding_onnx --int8     # 追加 model.int8.onnx
    python -m src.scripts.export_embedding_onnx --check    # 仅校验（doctor / 发版前置）

产物不入库（.gitignore data/rag_models/）；一条命令重建链：
    export_embedding_onnx → pytest tests/test_rag_onnx_parity.py → python -m src.rag.indexer
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from src.config.env import PROJECT_ROOT as ROOT

OUT_DIR = ROOT / "data" / "rag_models" / "onnx"
# opset 18：torch 2.12 dynamo 导出的原生版本。降到 17 需经 version converter，
# 实测会破坏图内广播语义（Add 节点运行期崩）；onnxruntime 1.23 完整支持 18
OPSET = 18
MAX_SEQ_LEN = 512
# 与 BERT forward 位置参数顺序一致；实际喂入顺序以侧车 input_names 为准
INPUT_NAMES = ["input_ids", "attention_mask", "token_type_ids"]


def _sha256_of(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_sidecar() -> dict:
    try:
        return json.loads((OUT_DIR / "sha256.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _up_to_date(name: str, sidecar: dict) -> bool:
    """幂等：目标存在且哈希与现有侧车记录一致。"""
    path = OUT_DIR / name
    if not path.is_file():
        return False
    entry = sidecar.get(name)
    expect = entry.get("sha256") if isinstance(entry, dict) else entry
    return isinstance(expect, str) and _sha256_of(path) == expect


def _resolve_source_model() -> Path:
    from src.rag import config

    model_dir = config._find_local_model()
    if model_dir is None:
        raise SystemExit(
            "未找到本地 bge-small-zh-v1.5 源权重。请先在开发环境跑一次"
            " RAG 检索（sentence-transformers 会经 modelscope/HF 缓存下载），"
            "或设置 RAG_MODEL_DIR 指向已有模型目录")
    return Path(model_dir)


def _st_do_lower_case(source_dir: Path) -> bool:
    """对齐 ST 运行时分词：sentence_bert_config.json 的 do_lower_case 优先
    （bge-small-zh 该文件为 true，而 tokenizer_config.json 为 false——
    实测 ST 侧 'FAQ'→'faq' 入词表、源 tokenizer.json 则出 [UNK]）。"""
    import json

    for name, default in (("sentence_bert_config.json", None),
                          ("tokenizer_config.json", False)):
        path = source_dir / name
        if path.is_file():
            try:
                value = json.loads(path.read_text(encoding="utf-8")).get(
                    "do_lower_case", default)
                if value is not None:
                    return bool(value)
            except (OSError, ValueError):
                continue
    return False


def export_fp32(source_dir: Path) -> Path:
    # onnx 用于把 dynamo 导出的外部权重内联回单文件，必须在 torch.onnx.export
    # 之前确认可用——否则会留下无侧车的半成品（外部引用形态 model.onnx +
    # model.onnx.data）且每次重跑重复挂；onnx 无任何包传递依赖它（dev-only，
    # 见 environment.yml 注释），缺装在这里给出一句话指引
    try:
        import onnx  # noqa: F401
    except ImportError as error:
        raise SystemExit(
            "缺 onnx 包（权重内联需要）：conda run -n myenv pip install onnx==1.23.2"
        ) from error
    import torch
    from transformers import AutoModel, AutoTokenizer

    out_path = OUT_DIR / "model.onnx"
    # strip_accents=False：transformers 在 do_lower_case=True 时会把 strip_accents
    # 默认置 True，多出 NFD+剥变音符（'≠' 被拆成 '='）——与 ST 运行时行为不符
    tokenizer = AutoTokenizer.from_pretrained(
        str(source_dir), do_lower_case=_st_do_lower_case(source_dir),
        strip_accents=False)
    net = AutoModel.from_pretrained(str(source_dir))
    net.eval()
    encoded = tokenizer(
        ["导出校准文本甲", "为这个句子生成表示以用于检索相关文章：第二条更长的导出校准样本文本"],
        return_tensors="pt", padding=True, truncation=True, max_length=MAX_SEQ_LEN)
    args = tuple(encoded[name] for name in INPUT_NAMES)
    dynamic_axes = {name: {0: "batch", 1: "seq"} for name in INPUT_NAMES}
    dynamic_axes["last_hidden_state"] = {0: "batch", 1: "seq"}
    # 校准样本须为不等长的多条：单条样本会让 dynamo 把 batch/seq 维错误
    # 专门化（运行期 Add 节点 32×128 广播崩溃，实测）
    print(f"[导出] fp32 → {out_path}（opset {OPSET}，动态 batch/seq）")
    with torch.no_grad():
        torch.onnx.export(
            net, args, str(out_path),
            opset_version=OPSET,
            input_names=INPUT_NAMES,
            output_names=["last_hidden_state"],
            dynamic_axes=dynamic_axes,
        )
    # torch 2.x dynamo 导出会把权重存到 model.onnx.data 外部文件——内联回
    # 单文件，保证三件套契约（侧车校验/打包收集/冷备份都只看 model.onnx）
    import onnx

    graph = onnx.load(str(out_path))
    onnx.save_model(graph, str(out_path), save_as_external_data=False)
    out_path.with_name(out_path.name + ".data").unlink(missing_ok=True)
    # tokenizer.json 按与 ST 运行时一致的配置重新序列化（normalizer 含
    # lowercase 等），而不是直接复制源文件——保证分词与 st 档逐 token 一致
    tokenizer.backend_tokenizer.save(str(OUT_DIR / "tokenizer.json"))
    return out_path


def export_int8() -> Path:
    try:
        import onnx  # noqa: F401 — quantize_dynamic 内部依赖 onnx，前置暴露
        from onnxruntime.quantization import QuantType, quantize_dynamic
    except ImportError as error:
        raise SystemExit(
            "缺 onnx 包（int8 量化需要）：conda run -n myenv pip install onnx==1.23.2"
        ) from error

    out_path = OUT_DIR / "model.int8.onnx"
    print(f"[量化] int8 → {out_path}（quantize_dynamic）")
    quantize_dynamic(str(OUT_DIR / "model.onnx"), str(out_path),
                     weight_type=QuantType.QInt8)
    return out_path


def write_sidecar(source_dir: Path, include_int8: bool) -> None:
    import torch

    sidecar = {
        "model.onnx": {"sha256": _sha256_of(OUT_DIR / "model.onnx"),
                       "bytes": (OUT_DIR / "model.onnx").stat().st_size},
        "tokenizer.json": {"sha256": _sha256_of(OUT_DIR / "tokenizer.json")},
        "input_names": INPUT_NAMES,
        "source_weights_sha256": _sha256_of(source_dir / "model.safetensors")
        if (source_dir / "model.safetensors").is_file() else None,
        "source_model_dir": str(source_dir),
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "torch_version": torch.__version__,
        "onnx_opset": OPSET,
    }
    if include_int8 and (OUT_DIR / "model.int8.onnx").is_file():
        sidecar["model.int8.onnx"] = {
            "sha256": _sha256_of(OUT_DIR / "model.int8.onnx"),
            "bytes": (OUT_DIR / "model.int8.onnx").stat().st_size,
        }
    (OUT_DIR / "sha256.json").write_text(
        json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[侧车] sha256.json 已更新（{', '.join(k for k in sidecar if k.endswith('.onnx'))}）")


def check() -> list[str]:
    """校验三件套（+可选 int8）哈希一致；返回问题清单（空 = 通过）。"""
    from src.rag.embedder import OnnxEmbedder

    problems: list[str] = []
    sidecar = _load_sidecar()
    if not sidecar:
        return [f"缺少 {OUT_DIR / 'sha256.json'}（未导出过）"]
    try:
        OnnxEmbedder(OUT_DIR)  # 构造即校验 model.onnx / tokenizer.json
    except RuntimeError as error:
        problems.append(str(error))
    if "model.int8.onnx" in sidecar and not _up_to_date("model.int8.onnx", sidecar):
        problems.append("model.int8.onnx 缺失或哈希与侧车不符")
    return problems


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001 — 无控制台场景不致命
            pass
    parser = argparse.ArgumentParser(description="bge 编码器 ONNX 导出（fp32/int8 + 侧车）")
    parser.add_argument("--int8", action="store_true", help="追加 int8 动态量化产物")
    parser.add_argument("--check", action="store_true", help="仅校验就位状态，不导出")
    args = parser.parse_args(argv)

    if args.check:
        problems = check()
        if problems:
            print("ONNX 编码器未就位：", file=sys.stderr)
            for line in problems:
                print(f"  - {line}", file=sys.stderr)
            return 1
        print("ONNX 编码器三件套已就位且哈希匹配")
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sidecar = _load_sidecar()
    source_dir = _resolve_source_model()
    print(f"[源权重] {source_dir}")

    if _up_to_date("model.onnx", sidecar) and _up_to_date("tokenizer.json", sidecar):
        print("[跳过] fp32 产物已就位且哈希匹配（幂等）")
    else:
        export_fp32(source_dir)

    if args.int8:
        if _up_to_date("model.int8.onnx", sidecar) and _up_to_date("model.onnx", sidecar):
            print("[跳过] int8 产物已就位且哈希匹配（幂等）")
        else:
            export_int8()

    write_sidecar(source_dir, include_int8=args.int8
                  or (OUT_DIR / "model.int8.onnx").is_file())
    problems = check()
    for line in problems:
        print(f"  ⚠️ {line}", file=sys.stderr)
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
