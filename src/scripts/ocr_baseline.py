# -*- coding: utf-8 -*-
"""
OCR 识别准确率回归基线工具（ocr_baseline.py）
=============================================
用途：为屏幕识别链路（模板匹配之后的 PaddleOCR + 纠错）建立人工标注回归基线，
      用于 A2/A3 等识别层改动前后的量化对比。项目测试全部使用 FakeEngine，
      本工具是唯一以真实模型验证识别行为的手段。

用法：
    # 采集：对模拟器当前画面截图并存为 case（在选将页/巅峰赛页时执行）
    python -m src.scripts.ocr_baseline collect --page-type hero_selection
    python -m src.scripts.ocr_baseline collect --page-type peak
    python -m src.scripts.ocr_baseline collect --image 本地截图.png --page-type hero_selection

    # 标注：编辑 tests/ocr_baseline_cases/labels.json，把 case 的 slots 填上真实武将名
    #       （无文字的槽填空串），并将 annotated 改为 true

    # 对比：跑识别链路并输出与标注的逐槽对比报告
    python -m src.scripts.ocr_baseline run

报告：摘要输出到日志（logs/rag/ocr_baseline.log），逐槽明细写入
      tests/ocr_baseline_cases/report.json，供改动前后 diff。
"""
import argparse
import json
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from src.config.env import PROJECT_ROOT as ROOT
from src.scripts.rag_common import get_script_logger

logger = get_script_logger("ocr_baseline")

BASELINE_DIR = ROOT / "tests" / "ocr_baseline_cases"
CASES_DIR = BASELINE_DIR / "cases"
LABELS_PATH = BASELINE_DIR / "labels.json"
REPORT_PATH = BASELINE_DIR / "report.json"
HEROES_PATH = ROOT / "data" / "heroes.json"

# 固定 ROI 页面的槽位数，与 src/ocr/roi_config.py 的 _PAGE_REQUIREMENTS 对齐
FIXED_SLOT_COUNT = {"hero_selection": 8, "match_guide": 5}
DEFAULT_ADB = r"D:\模拟器\MuMu Player 12\nx_main\adb.exe"
DEFAULT_SERIAL = "127.0.0.1:16416"


def load_labels() -> dict:
    if LABELS_PATH.exists():
        with LABELS_PATH.open("r", encoding="utf-8") as file:
            return json.load(file)
    return {"version": 1, "cases": []}


def save_labels(labels: dict) -> None:
    CASES_DIR.mkdir(parents=True, exist_ok=True)
    with LABELS_PATH.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(labels, file, ensure_ascii=False, indent=2)
        file.write("\n")
    logger.info("标注文件已保存: %s", LABELS_PATH)


def cmd_collect(args: argparse.Namespace) -> None:
    """采集一个 case：模拟器截图或本地图片，登记进 labels.json（slots 留空待标注）。"""
    if args.image:
        source = Path(args.image)
        if not source.exists():
            raise SystemExit(f"图片不存在: {source}")
        data = source.read_bytes()
    else:
        result = subprocess.run(
            [args.adb_path, "-s", args.serial, "exec-out", "screencap", "-p"],
            capture_output=True, timeout=30,
        )
        if result.returncode != 0 or not result.stdout:
            raise SystemExit(f"模拟器截图失败: {result.stderr[:200]!r}")
        data = result.stdout

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    case_name = f"{args.page_type}_{timestamp}.png"
    CASES_DIR.mkdir(parents=True, exist_ok=True)
    case_path = CASES_DIR / case_name
    case_path.write_bytes(data)
    logger.info("截图已保存: %s (%.1fMB)", case_path, len(data) / 1e6)

    slot_count = -1 if args.page_type == "peak" else FIXED_SLOT_COUNT[args.page_type]
    labels = load_labels()
    labels["cases"].append({
        "image": f"cases/{case_name}",
        "page_type": args.page_type,
        # peak 为动态卡数，标注时按实际牌面改为列表；固定页面按槽位数留空串
        "slots": [] if slot_count < 0 else [""] * slot_count,
        # 标注完成后人工改为 true，run 只统计 annotated 的 case
        "annotated": False,
        "note": args.note,
    })
    save_labels(labels)
    logger.info(
        "已登记 case（slots 待标注）：%s，槽位数=%s",
        case_name, "动态(peak)" if slot_count < 0 else slot_count,
    )


def _load_hero_names() -> list[str]:
    with HEROES_PATH.open("r", encoding="utf-8") as file:
        return [item["name"] for item in json.load(file) if item.get("name")]


def _recognize_case(image_path: Path, case: dict, hero_names: list[str]) -> dict:
    """按页面类型跑一次完整识别，返回归一化的逐槽结果。"""
    from src.ocr.card_grid_detector import derive_name_rois, detect_selection_cards
    from src.ocr.recognizer import GeneralRecognizer

    image = cv2.imdecode(np.fromfile(image_path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return {"error": "图片无法读取"}
    page_type = case["page_type"]

    if page_type == "peak":
        cards = detect_selection_cards(image)
        if cards is None:
            return {"error": "未检出 2v2 牌面（8~14 张卡）"}
        rois = [list(roi) for roi in derive_name_rois(cards)]
        recognizer = GeneralRecognizer(
            hero_names=hero_names, page_type="hero_selection", rois=rois,
        )
        slots = recognizer.recognize(image)
        return {"slots": slots}

    recognizer = GeneralRecognizer(hero_names=hero_names, page_type=page_type)
    return {"slots": recognizer.recognize(image)}


def _classify(expected: str, slot_result: dict) -> str:
    name = str(slot_result.get("name") or "")
    if expected and name == expected:
        return "correct"
    if expected and not name:
        return "missed"
    if expected and name != expected:
        return "wrong"
    if not expected and name:
        return "extra"
    return "empty_ok"


def cmd_run(args: argparse.Namespace) -> None:
    """对全部标注 case 跑识别链路，输出逐槽对比报告。"""
    labels = load_labels()
    cases = [case for case in labels["cases"] if case.get("annotated")]
    if not cases:
        raise SystemExit("labels.json 中没有已标注（annotated=true）的 case，先执行 collect 并完成标注")

    hero_names = _load_hero_names()
    from src.ocr.recognizer import GeneralRecognizer

    # 提前加载模型与汉字特征，避免首例的初始化开销混入统计
    warm = GeneralRecognizer(hero_names=hero_names, page_type="hero_selection")
    warm.warmup()
    logger.info("OCR 模型预热完成，开始识别 %d 个 case", len(cases))

    slot_reports = []
    counter: Counter[str] = Counter()
    for case in cases:
        image_path = BASELINE_DIR / case["image"]
        outcome = _recognize_case(image_path, case, hero_names)
        if "error" in outcome:
            logger.warning("case 跳过 %s: %s", case["image"], outcome["error"])
            slot_reports.append({"image": case["image"], "error": outcome["error"]})
            continue
        expected_slots = list(case["slots"])
        if len(expected_slots) != len(outcome["slots"]):
            logger.warning(
                "case 标注数不符 %s: 标注 %d 槽，识别 %d 槽",
                case["image"], len(expected_slots), len(outcome["slots"]),
            )
        for index, slot_result in enumerate(outcome["slots"]):
            expected = expected_slots[index] if index < len(expected_slots) else ""
            category = _classify(expected, slot_result)
            counter[category] += 1
            slot_reports.append({
                "image": case["image"],
                "slot": index + 1,
                "expected": expected,
                "category": category,
                "name": slot_result.get("name", ""),
                "resolution": slot_result.get("resolution", ""),
                "raw_name": slot_result.get("raw_name", ""),
                "confidence": slot_result.get("confidence", 0.0),
                "candidates": slot_result.get("candidates", []),
            })

    labeled = counter["correct"] + counter["missed"] + counter["wrong"]
    summary = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "case_count": len(cases),
        "slot_total": len(slot_reports) - sum(1 for item in slot_reports if "error" in item),
        "correct": counter["correct"],
        "missed": counter["missed"],
        "wrong": counter["wrong"],
        "extra": counter["extra"],
        "empty_ok": counter["empty_ok"],
        "slot_accuracy": round(counter["correct"] / labeled, 4) if labeled else None,
        "resolution_distribution": dict(Counter(
            item["resolution"] for item in slot_reports if "resolution" in item
        )),
    }
    report = {"summary": summary, "slots": slot_reports}
    with REPORT_PATH.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
        file.write("\n")

    logger.info(
        "基线对比完成：标注槽=%d，正确=%d，识别不出=%d，认错=%d，多识=%d，槽位准确率=%s",
        labeled, counter["correct"], counter["missed"], counter["wrong"],
        counter["extra"],
        f"{summary['slot_accuracy']:.2%}" if summary["slot_accuracy"] is not None else "n/a",
    )
    logger.info("resolution 分布: %s", summary["resolution_distribution"])
    logger.info("逐槽明细已写入: %s", REPORT_PATH)
    for item in slot_reports:
        if item.get("category") in {"missed", "wrong"}:
            logger.info(
                "  [%s] %s 槽%s: 标注=%r 识别=%r(%s) 原文=%r 候选=%s",
                item["category"], item["image"], item["slot"],
                item["expected"], item["name"], item["resolution"],
                item["raw_name"], item["candidates"],
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="OCR 识别准确率回归基线工具")
    sub = parser.add_subparsers(dest="command", required=True)

    collect = sub.add_parser("collect", help="采集一个 case（模拟器截图或本地图片）")
    collect.add_argument("--page-type", required=True,
                         choices=(*FIXED_SLOT_COUNT, "peak"), help="页面类型")
    collect.add_argument("--image", help="本地图片路径（不传则对模拟器截图）")
    collect.add_argument("--adb-path", default=DEFAULT_ADB)
    collect.add_argument("--serial", default=DEFAULT_SERIAL)
    collect.add_argument("--note", default="", help="备注（如对局阶段、字体特殊状态）")
    collect.set_defaults(func=cmd_collect)

    run = sub.add_parser("run", help="按标注跑识别链路并输出对比报告")
    run.set_defaults(func=cmd_run)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
