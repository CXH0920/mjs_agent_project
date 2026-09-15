# -*- coding: utf-8 -*-
"""轮询闲置暂停阈值校准工具（calibrate_idle_threshold.py）

对指定目录的截图序列（按文件名时间戳排序）计算相邻帧指纹的
MAD（平均绝对差）分布，验证 src/ui/app/frame_fingerprint.MAD_THRESHOLD
是否落在安全带内：真实画面变化（出牌、结算、界面切换）的 MAD 应
显著高于阈值，静止画面的 MAD 应低于阈值。

游戏大版本更换 UI 后可重跑本工具复核阈值。

用法：
    python -m src.scripts.calibrate_idle_threshold
    python -m src.scripts.calibrate_idle_threshold --dir screenshots
    python -m src.scripts.calibrate_idle_threshold --threshold 5 --verbose
"""
import argparse
import statistics
import sys
from pathlib import Path

from PIL import Image

from src.ui.app.frame_fingerprint import MAD_THRESHOLD, compute_fingerprint, frames_match


def _collect_images(directory: Path) -> list[Path]:
    files = sorted(
        p for p in directory.iterdir()
        if p.suffix.lower() in (".png", ".jpg", ".jpeg") and p.is_file()
    )
    return files


def _percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(int(len(ordered) * ratio), len(ordered) - 1)
    return ordered[index]


def main() -> int:
    parser = argparse.ArgumentParser(description="校准轮询闲置暂停的帧指纹阈值")
    parser.add_argument("--dir", default="screenshots", help="截图目录（默认 screenshots）")
    parser.add_argument("--threshold", type=float, default=MAD_THRESHOLD, help="待验证阈值")
    parser.add_argument("--verbose", action="store_true", help="逐对打印 MAD")
    args = parser.parse_args()

    directory = Path(args.dir)
    files = _collect_images(directory)
    if len(files) < 2:
        print(f"目录 {directory} 下可用截图不足 2 张，无法校准")
        return 1

    fingerprints = []
    for path in files:
        try:
            with Image.open(path) as image:
                fingerprints.append((path.name, compute_fingerprint(image)))
        except Exception as error:
            print(f"跳过无法解析的图片 {path.name}: {error}")
    fingerprints = [(name, fp) for name, fp in fingerprints if fp is not None]
    if len(fingerprints) < 2:
        print("有效指纹不足 2 张，无法校准")
        return 1

    mads: list[tuple[str, float]] = []
    for (name_a, fp_a), (name_b, fp_b) in zip(fingerprints, fingerprints[1:]):
        if fp_a is None or fp_b is None or len(fp_a) != len(fp_b):
            continue
        mad = sum(abs(a - b) for a, b in zip(fp_a, fp_b)) / len(fp_a)
        mads.append((f"{name_a} -> {name_b}", mad))

    values = [mad for _, mad in mads]
    unchanged = [pair for pair in mads if pair[1] < args.threshold]
    print(f"样本: {len(fingerprints)} 张图，{len(mads)} 对相邻帧")
    print(f"MAD 分布: min={min(values):.2f} 中位={statistics.median(values):.2f} "
          f"p25={_percentile(values, 0.25):.2f} p75={_percentile(values, 0.75):.2f} "
          f"max={max(values):.2f}")
    print(f"阈值 {args.threshold}: 判'无变化' {len(unchanged)} 对，判'有变化' {len(mads) - len(unchanged)} 对")

    if args.verbose:
        for pair, mad in mads:
            marker = "无变化" if mad < args.threshold else "有变化"
            print(f"  {mad:8.2f}  {marker}  {pair}")
    elif unchanged:
        for pair, mad in unchanged:
            print(f"  判'无变化'的对: MAD={mad:.2f}  {pair}")

    print("结论: 阈值应低于所有真实变化对的 MAD 下界；'判无变化'的对需人工确认"
          "确为静止画面（手动截图间隔较长，逐对核对文件名时间即可）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
