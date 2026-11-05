# -*- coding: utf-8 -*-
"""B1 复核引擎（PP-OCRv4 ONNX）模型预取（fetch_recheck_models.py）

rapidocr 3.9.2 wheel 只物理内置 v6 三件套；v4 复核引擎所需的 det/rec/字典
三文件由本脚本从 RapidOCR 官方 modelscope 分发预取到 rapidocr 包的 models/
目录，与 v6 同目录同机制，仓库零模型字节。运行时（engine_loader）显式指向
这些文件，缺文件熔断、绝不在线下载（离线纪律见设计文档 §十四）。

模型清单与就位校验住能力域（src/ocr/recheck_model_assets.py），本脚本与
doctor 体检共用同一份口径；此处只剩下载与 CLI。

幂等：已存在且 SHA256 匹配则跳过；不匹配（下载损坏/上游漂移）视为异常并
用哈希校验后的下载件覆盖修复。

用法：
    python -m src.scripts.fetch_recheck_models            # 预取/修复（需一次网络）
    python -m src.scripts.fetch_recheck_models --check    # 仅校验就位状态（打包前置）
"""
import argparse
import sys
import urllib.request
from pathlib import Path

from src.ocr.recheck_model_assets import (
    V4_MODEL_FILES,
    models_dir,
    sha256_stream,
    verify_v4_models,
)

_CHUNK = 1024 * 1024


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
    target_dir = models_dir()
    print(f"目标目录：{target_dir}")
    for name, url, expected in V4_MODEL_FILES:
        dest = target_dir / name
        if dest.is_file() and sha256_stream(dest) == expected:
            print(f"[跳过] {name} 已就位（sha256 {expected[:8]}…）")
            continue
        if dest.is_file():
            print(f"[修复] {name} 存在但哈希不符，重新下载覆盖")
        try:
            _download(url, dest)
        except OSError as exc:
            print(f"[失败] {name} 下载异常：{exc}", file=sys.stderr)
            return 1
        actual = sha256_stream(dest.with_name(dest.name + ".part"))
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
