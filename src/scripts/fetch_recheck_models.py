# -*- coding: utf-8 -*-
"""B1 复核引擎（PP-OCRv4 ONNX）模型预取（fetch_recheck_models.py）

rapidocr 3.9.2 wheel 只物理内置 v6 三件套；v4 复核引擎所需的 det/rec/字典
三文件由本脚本从 RapidOCR 官方 modelscope 分发预取到 rapidocr 包的 models/
目录，与 v6 同目录同机制，仓库零模型字节。运行时（engine_loader）显式指向
这些文件，缺文件熔断、绝不在线下载（离线纪律见设计文档 §十四）。

幂等：已存在且 SHA256 匹配则跳过；不匹配（下载损坏/上游漂移）视为异常并
用哈希校验后的下载件覆盖修复。

用法：
    python -m src.scripts.fetch_recheck_models            # 预取/修复（需一次网络）
    python -m src.scripts.fetch_recheck_models --check    # 仅校验就位状态（打包前置）
"""
import argparse
import hashlib
import importlib.util
import sys
import urllib.request
from pathlib import Path

# det/rec 条目与 rapidocr 3.9.2 default_models.yaml 登记的 URL+SHA256 逐一一致；
# 字典 URL 取 rapidocr/ch_ppocr_rec/main.py 的 DEFAULT_DICT_URL（yaml 未登记
# 哈希，此处钉 .tmp_test 语料验证资产的实测值），来源登记见 data/SOURCES.md
V4_MODEL_FILES = [
    (
        "ch_PP-OCRv4_det_mobile.onnx",
        "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv4/det/ch_PP-OCRv4_det_mobile.onnx",
        "d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9",
    ),
    (
        "ch_PP-OCRv4_rec_mobile.onnx",
        "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv4/rec/ch_PP-OCRv4_rec_mobile.onnx",
        "48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b",
    ),
    (
        "ppocr_keys_v1.txt",
        "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v2.0.7/paddle/PP-OCRv4/rec/ch_PP-OCRv4_rec_infer/ppocr_keys_v1.txt",
        # 与 .tmp_test 语料验证资产的唯一差异是末行无换行符（6623 行内容逐行一致），行为等价
        "28b2362ad4ab2dc38769aa72feb535e3a9ddb3fd2a7585a05920e6393b1dc7f7",
    ),
]

_CHUNK = 1024 * 1024


def _models_dir() -> Path:
    """rapidocr 包内置模型目录（v4 预取目标，与 v6 同址）。"""
    if importlib.util.find_spec("rapidocr") is None:
        raise SystemExit("rapidocr 未安装：请先按 environment.yml 安装 rapidocr==3.9.2")
    import rapidocr

    return Path(rapidocr.__file__).resolve().parent / "models"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_v4_models() -> list[str]:
    """返回未就位/哈希不符的问题清单；空列表即三件套全部就位。"""
    problems = []
    try:
        target_dir = _models_dir()
    except SystemExit as exc:
        return [str(exc)]
    for name, _url, expected in V4_MODEL_FILES:
        path = target_dir / name
        if not path.is_file():
            problems.append(f"缺失 {name}")
        elif _sha256(path) != expected:
            problems.append(f"哈希不符 {name}（预期 {expected[:8]}…）")
    return problems


def _download(url: str, dest: Path) -> None:
    """流式下载到同目录 .part 文件（供哈希校验后原子替换）。"""
    request = urllib.request.Request(url, headers={"User-Agent": "mjs-agent-fetch"})
    tmp = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(request, timeout=60) as response, open(tmp, "wb") as handle:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = response.read(_CHUNK)
            if not chunk:
                break
            handle.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  下载中 {done / 1048576:.1f}/{total / 1048576:.1f} MB", end="")
    print()


def fetch() -> int:
    target_dir = _models_dir()
    print(f"目标目录：{target_dir}")
    for name, url, expected in V4_MODEL_FILES:
        dest = target_dir / name
        if dest.is_file() and _sha256(dest) == expected:
            print(f"[跳过] {name} 已就位（sha256 {expected[:8]}…）")
            continue
        if dest.is_file():
            print(f"[修复] {name} 存在但哈希不符，重新下载覆盖")
        try:
            _download(url, dest)
        except OSError as exc:
            print(f"[失败] {name} 下载异常：{exc}", file=sys.stderr)
            return 1
        actual = _sha256(dest.with_name(dest.name + ".part"))
        if actual != expected:
            dest.with_name(dest.name + ".part").unlink(missing_ok=True)
            print(f"[失败] {name} 哈希不符（下载件 {actual[:8]}…，预期 {expected[:8]}…），已删除", file=sys.stderr)
            return 1
        dest.with_name(dest.name + ".part").replace(dest)
        print(f"[就位] {name}（sha256 {expected[:8]}…）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="B1 复核引擎 v4 模型预取/校验")
    parser.add_argument("--check", action="store_true", help="仅校验就位状态，不下载")
    args = parser.parse_args(argv)
    if args.check:
        problems = verify_v4_models()
        if problems:
            print("v4 复核模型未就位：", file=sys.stderr)
            for line in problems:
                print(f"  - {line}", file=sys.stderr)
            return 1
        print("v4 复核模型三件套已就位且哈希匹配")
        return 0
    return fetch()


if __name__ == "__main__":
    sys.exit(main())
