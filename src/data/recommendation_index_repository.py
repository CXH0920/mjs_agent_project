"""2v2 武将推荐指数快照计算与读取。"""

from __future__ import annotations

import csv
import json
import logging
import math
import os
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from src.config.env import BUNDLE_ROOT, PROJECT_ROOT, load_env_config

logger = logging.getLogger(__name__)

DATA_DIR = BUNDLE_ROOT / "data"  # 读基线 csv/json（打包只读，frozen 下 __file__ 推导不可靠）
WIN_RATE_CSV = DATA_DIR / "2v2胜率排行.csv"
PICK_RANK_CSV = DATA_DIR / "2v2出场排行.csv"
BAN_RANK_CSV = DATA_DIR / "武将放逐.csv"
HEROES_JSON = DATA_DIR / "heroes.json"
RECOMMENDATION_INDEX_CSV = DATA_DIR / "武将推荐指数.csv"
# stale 状态写可写运行时根（BUNDLE_ROOT 只读，mark_stale 写它会失败）
RECOMMENDATION_INDEX_STATE_FILE = PROJECT_ROOT / "data" / "武将推荐指数状态.json"

DEFAULT_P_FLOOR = 0.2
DEFAULT_BAN_WEIGHT = 0.5
DEFAULT_SIGMOID_K = 10.0
DEFAULT_LOW_WIN_RATE_GAP = 0.05


@dataclass(frozen=True)
class RecommendationIndexConfig:
    """推荐指数的可调参数。"""

    p_floor: float = DEFAULT_P_FLOOR
    ban_weight: float = DEFAULT_BAN_WEIGHT
    sigmoid_k: float = DEFAULT_SIGMOID_K
    low_win_rate_gap: float = DEFAULT_LOW_WIN_RATE_GAP


@dataclass(frozen=True)
class RecommendationIndex:
    """一名武将的推荐指数结果。"""

    hero_id: int | None
    name: str
    win_rate: float | None
    pick_rank: int | None
    ban_rank: int | None
    pick_score: float | None
    ban_score: float | None
    preference: float | None
    sigmoid: float | None
    raw_index: float | None
    score: int | None
    rating: str | None
    order: int | None
    status: str
    reason: str = ""

    @property
    def is_valid(self) -> bool:
        return self.status == "有效"


def is_recommendation_index_stale(
    path: Path = RECOMMENDATION_INDEX_STATE_FILE,
    *,
    index_path: Path = RECOMMENDATION_INDEX_CSV,
    source_paths: tuple[Path, ...] | None = None,
) -> bool:
    """返回推荐指数快照是否已被新的官方榜单数据标记为过期。

    自愈校验：即使状态文件被外部误标记为 stale=true，只要三份官方榜单
    CSV 的修改时间均不晚于推荐指数快照，说明没有新榜单数据需要反映，
    忽略 stale 标记并自动写回 false，避免误弹"推荐指数待重建"横幅
    （状态文件曾被 git 历史/命令行操作意外置为 true 的兜底）。
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        logger.warning("无法读取推荐指数状态 %s: %s", path, exc)
        return True
    if not bool(data.get("stale", False)):
        return False
    sources = source_paths if source_paths is not None else (
        WIN_RATE_CSV, PICK_RANK_CSV, BAN_RANK_CSV,
    )
    if _has_newer_source_file(sources, index_path):
        return True
    logger.warning(
        "推荐指数状态被标记 stale=true，但官方榜单数据并未更新（快照不早于榜单），自愈写回 false"
    )
    mark_recommendation_index_stale(False, path)
    return False


def _has_newer_source_file(
    source_paths: tuple[Path, ...],
    index_path: Path,
) -> bool:
    """任一榜单源文件比推荐指数快照新，说明存在尚未反映的新数据。"""
    try:
        index_mtime = index_path.stat().st_mtime
    except OSError:
        return True  # 快照缺失：需要重建
    for source in source_paths:
        try:
            if source.stat().st_mtime > index_mtime:
                return True
        except OSError:
            continue  # 榜单文件缺失时不参与判断，避免误报
    return False


def mark_recommendation_index_stale(
    stale: bool,
    path: Path = RECOMMENDATION_INDEX_STATE_FILE,
) -> None:
    """原子保存推荐指数快照是否待重建的状态。"""
    import traceback
    logger.warning(
        "推荐指数状态标记 stale=%s，调用来源:\n%s",
        stale, "".join(traceback.format_stack(limit=10)),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    # mkstemp 唯一中转名：固定 .tmp 在并发保存时会互相覆盖（与 save_baike_snapshot 同理）
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps({"stale": stale}, ensure_ascii=False, indent=2) + "\n")
        Path(tmp_name).replace(path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def load_recommendation_indexes(
    path: Path = RECOMMENDATION_INDEX_CSV,
) -> dict[str, RecommendationIndex]:
    """读取已人工确认后生成的推荐指数快照。"""
    try:
        with path.open("r", encoding="utf-8", newline="") as file:
            rows = list(csv.DictReader(file))
    except FileNotFoundError:
        logger.info("推荐指数快照不存在: %s", path)
        return {}
    except OSError as exc:
        logger.warning("无法读取推荐指数快照 %s: %s", path, exc)
        return {}

    results = {}
    for row in rows:
        name = (row.get("武将") or "").strip()
        if not name:
            continue
        try:
            results[name] = RecommendationIndex(
                _optional_int(row.get("武将ID")), name, _optional_rate(row.get("胜率")),
                _optional_int(row.get("出场排名")), _optional_int(row.get("禁用排名")),
                _optional_float(row.get("出场得分")), _optional_float(row.get("禁用得分")),
                _optional_float(row.get("偏好得分")), _optional_float(row.get("胜率系数")),
                _optional_float(row.get("原始推荐指数")), _optional_int(row.get("推荐分")),
                (row.get("评级") or "").strip() or None, _optional_int(row.get("推荐排序")),
                (row.get("状态") or "数据不足").strip(), (row.get("异常原因") or "").strip(),
            )
        except ValueError as exc:
            logger.warning("跳过无效推荐指数快照行 %s: %s", name, exc)
    return results


def refresh_recommendation_indexes(
    config: RecommendationIndexConfig | None = None,
    *,
    win_rate_path: Path = WIN_RATE_CSV,
    pick_rank_path: Path = PICK_RANK_CSV,
    ban_rank_path: Path = BAN_RANK_CSV,
    heroes_path: Path = HEROES_JSON,
    output_path: Path = RECOMMENDATION_INDEX_CSV,
) -> dict[str, RecommendationIndex]:
    """按当前三份榜单重建推荐指数 CSV，并返回名称索引。"""
    config = _validate_config(config or _load_runtime_config())
    hero_ids = _load_hero_ids(heroes_path)
    win_rates, win_issues, win_names, win_row_count = _read_win_rates(win_rate_path)
    pick_ranks, pick_issues, pick_names, _pick_row_count = _read_ranks(pick_rank_path, "出场")
    ban_ranks, ban_issues, ban_names, _ban_row_count = _read_ranks(ban_rank_path, "禁用")
    names = sorted(set(hero_ids) | win_names | pick_names | ban_names)
    n = win_row_count
    issues = defaultdict(set)
    for source_issues in (win_issues, pick_issues, ban_issues):
        for name, reasons in source_issues.items():
            issues[name].update(reasons)

    if n == 0:
        logger.warning("推荐指数无法计算：胜率数据为空或不可用")

    _validate_rank_ranges(pick_ranks, n, "出场", issues)
    _validate_rank_ranges(ban_ranks, n, "禁用", issues)
    results: dict[str, RecommendationIndex] = {}
    valid: list[RecommendationIndex] = []
    for name in names:
        for label, source in (("胜率", win_rates), ("出场排名", pick_ranks), ("禁用排名", ban_ranks)):
            if name not in source:
                issues[name].add(f"缺少{label}")
        if name not in hero_ids:
            issues[name].add("缺少武将唯一ID")
        if issues[name] or n <= 0:
            results[name] = _insufficient_result(
                hero_ids.get(name), name, win_rates.get(name), pick_ranks.get(name),
                ban_ranks.get(name), "；".join(sorted(issues[name])) or "胜率数据为空",
            )
            continue

        if n == 1:
            result = RecommendationIndex(
                hero_ids[name], name, win_rates[name], pick_ranks[name], ban_ranks[name],
                None, None, None, None, None, 50, "B", 1, "有效",
            )
        else:
            pick_score = _rank_to_score(pick_ranks[name], n)
            ban_score = _rank_to_score(ban_ranks[name], n)
            preference = (config.p_floor + (1 - config.p_floor) * pick_score) * (
                1 + config.ban_weight * ban_score
            )
            valid.append(RecommendationIndex(
                hero_ids[name], name, win_rates[name], pick_ranks[name], ban_ranks[name],
                pick_score, ban_score, preference, None, None, None, None, None, "有效",
            ))
            continue
        results[name] = result

    if n > 1 and valid:
        results.update(_score_valid_results(valid, config))
    _write_snapshot(output_path, results.values())
    if output_path == RECOMMENDATION_INDEX_CSV:
        mark_recommendation_index_stale(False)
    logger.info(
        "推荐指数快照已生成：有效 %d 条，数据不足 %d 条",
        sum(result.is_valid for result in results.values()),
        sum(not result.is_valid for result in results.values()),
    )
    return results


def _score_valid_results(
    valid: list[RecommendationIndex], config: RecommendationIndexConfig,
) -> dict[str, RecommendationIndex]:
    median_rate = _median([result.win_rate for result in valid if result.win_rate is not None])
    offset = median_rate + 0.02
    low_win_rate = offset - config.low_win_rate_gap
    with_raw = []
    for result in valid:
        sigmoid = 1 / (1 + math.exp(-config.sigmoid_k * (result.win_rate - offset)))
        raw_index = result.win_rate * result.preference * sigmoid
        with_raw.append((result, sigmoid, raw_index))

    raw_values = sorted(raw_index for _, _, raw_index in with_raw)
    p5 = raw_values[max(0, math.ceil(len(raw_values) * 0.05) - 1)]
    p95 = raw_values[min(len(raw_values) - 1, math.ceil(len(raw_values) * 0.95) - 1)]
    degenerate = p95 == p5
    scored: list[RecommendationIndex] = []
    for result, sigmoid, raw_index in with_raw:
        if degenerate:
            score, rating = 50, "B"
        else:
            normalized = min(max((raw_index - p5) / (p95 - p5) * 100, 0), 100)
            score = math.floor(normalized + 0.5)
            rating = _rating_for_score(score)
        scored.append(RecommendationIndex(
            result.hero_id, result.name, result.win_rate, result.pick_rank, result.ban_rank,
            result.pick_score, result.ban_score, result.preference, sigmoid, raw_index,
            score, rating, None, "有效",
        ))

    ranked = sorted(
        scored,
        key=lambda result: (
            result.win_rate < low_win_rate,
            -result.raw_index,
            result.hero_id,
        ),
    )
    return {
        result.name: RecommendationIndex(
            result.hero_id, result.name, result.win_rate, result.pick_rank, result.ban_rank,
            result.pick_score, result.ban_score, result.preference, result.sigmoid,
            result.raw_index, result.score, result.rating, order, "有效",
        )
        for order, result in enumerate(ranked, start=1)
    }


def _load_runtime_config() -> RecommendationIndexConfig:
    config = load_env_config()
    return RecommendationIndexConfig(
        p_floor=config.get("recommendation_p_floor", DEFAULT_P_FLOOR),
        ban_weight=config.get("recommendation_ban_weight", DEFAULT_BAN_WEIGHT),
        sigmoid_k=config.get("recommendation_sigmoid_k", DEFAULT_SIGMOID_K),
        low_win_rate_gap=config.get("recommendation_low_win_rate_gap", DEFAULT_LOW_WIN_RATE_GAP),
    )


def _validate_config(config: RecommendationIndexConfig) -> RecommendationIndexConfig:
    p_floor = _valid_float(config.p_floor, 0.1, 0.5, DEFAULT_P_FLOOR, "P_floor")
    ban_weight = _valid_float(config.ban_weight, 0, 0.5, DEFAULT_BAN_WEIGHT, "禁用影响系数")
    sigmoid_k = _valid_float(config.sigmoid_k, 0.01, None, DEFAULT_SIGMOID_K, "Sigmoid 斜率")
    low_win_rate_gap = _valid_float(
        config.low_win_rate_gap, 0, None, DEFAULT_LOW_WIN_RATE_GAP, "低胜率降级差值",
    )
    return RecommendationIndexConfig(p_floor, ban_weight, sigmoid_k, low_win_rate_gap)


def _valid_float(value, minimum: float, maximum: float | None, default: float, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if not math.isfinite(number) or number < minimum or (maximum is not None and number > maximum):
        logger.warning("推荐指数%s配置无效: %r，使用默认值 %s", label, value, default)
        return default
    return number


def _load_hero_ids(path: Path) -> dict[str, int]:
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        logger.warning("无法加载推荐指数武将ID映射 %s: %s", path, exc)
        return {}
    result = {}
    duplicate_names = set()
    for item in items if isinstance(items, list) else []:
        name, hero_id = item.get("name"), item.get("id")
        if isinstance(name, str) and isinstance(hero_id, int):
            if name in result:
                logger.warning("武将名称重复，无法稳定计算推荐指数: %s", name)
                result.pop(name, None)
                duplicate_names.add(name)
            elif name not in duplicate_names:
                result[name] = hero_id
    return result


def _read_win_rates(
    path: Path,
) -> tuple[dict[str, float], dict[str, set[str]], set[str], int]:
    values, issues, names, row_count = _read_csv(path, "胜率")
    result = {}
    for name, value in values.items():
        try:
            text = value.strip()
            rate = float(text[:-1]) / 100 if text.endswith("%") else float(text)
            if not 0 <= rate <= 1:
                raise ValueError
            result[name] = rate
        except (AttributeError, ValueError):
            issues[name].add("胜率格式或范围无效")
    return result, issues, names, row_count


def _read_ranks(
    path: Path, label: str,
) -> tuple[dict[str, int], dict[str, set[str]], set[str], int]:
    values, issues, names, row_count = _read_csv(path, "排名")
    result = {}
    for name, value in values.items():
        try:
            result[name] = int(value)
        except (TypeError, ValueError):
            issues[name].add(f"{label}排名格式无效")
    return result, issues, names, row_count


def _read_csv(
    path: Path, value_column: str,
) -> tuple[dict[str, str], dict[str, set[str]], set[str], int]:
    values: dict[str, str] = {}
    issues: dict[str, set[str]] = defaultdict(set)
    names: set[str] = set()
    try:
        with path.open("r", encoding="utf-8", newline="") as file:
            rows = list(csv.DictReader(file))
    except OSError as exc:
        logger.warning("无法读取推荐指数%s数据 %s: %s", value_column, path, exc)
        return values, issues, names, 0
    for row in rows:
        name = (row.get("武将") or "").strip()
        if not name:
            logger.warning("推荐指数%s数据存在空武将名: %s", value_column, path)
            continue
        names.add(name)
        if name in values:
            issues[name].add(f"{value_column}数据重复")
            continue
        values[name] = (row.get(value_column) or "").strip()
    return values, issues, names, len(rows)


def _validate_rank_ranges(
    ranks: dict[str, int], n: int, label: str, issues: dict[str, set[str]],
) -> None:
    rank_names: dict[int, list[str]] = defaultdict(list)
    for name, rank in ranks.items():
        if not 1 <= rank <= n:
            issues[name].add(f"{label}排名越界")
        else:
            rank_names[rank].append(name)
    for rank, names in rank_names.items():
        if len(names) > 1:
            for name in names:
                issues[name].add(f"{label}排名重复({rank})")


def _insufficient_result(
    hero_id: int | None, name: str, win_rate: float | None, pick_rank: int | None,
    ban_rank: int | None, reason: str,
) -> RecommendationIndex:
    logger.warning("推荐指数数据不足 %s: %s", name, reason)
    return RecommendationIndex(
        hero_id, name, win_rate, pick_rank, ban_rank, None, None, None, None,
        None, None, None, None, "数据不足", reason,
    )


def _rank_to_score(rank: int, n: int) -> float:
    return min(max(1 - (rank - 1) / (n - 1), 0), 1)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def _rating_for_score(score: int) -> str:
    if score >= 80:
        return "S"
    if score >= 60:
        return "A"
    if score >= 40:
        return "B"
    if score >= 20:
        return "C"
    return "D"


def _write_snapshot(path: Path, results) -> None:
    fieldnames = [
        "武将ID", "武将", "胜率", "出场排名", "禁用排名", "出场得分", "禁用得分", "偏好得分",
        "胜率系数", "原始推荐指数", "推荐分", "评级", "推荐排序", "状态", "异常原因",
    ]
    rows = []
    for result in sorted(results, key=lambda item: (item.hero_id is None, item.hero_id or 0, item.name)):
        rows.append({
            "武将ID": result.hero_id or "", "武将": result.name,
            "胜率": _format_percent(result.win_rate), "出场排名": result.pick_rank or "",
            "禁用排名": result.ban_rank or "", "出场得分": _format_float(result.pick_score),
            "禁用得分": _format_float(result.ban_score), "偏好得分": _format_float(result.preference),
            "胜率系数": _format_float(result.sigmoid), "原始推荐指数": _format_float(result.raw_index),
            "推荐分": result.score if result.score is not None else "", "评级": result.rating or "",
            "推荐排序": result.order or "", "状态": result.status, "异常原因": result.reason,
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="", dir=path.parent,
        prefix=f".{path.name}.", suffix=".tmp", delete=False,
    ) as file:
        temp_path = Path(file.name)
        writer = csv.DictWriter(file, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    try:
        temp_path.replace(path)
    except PermissionError as exc:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError as cleanup_exc:
            logger.warning("无法清理推荐指数临时文件 %s: %s", temp_path, cleanup_exc)
        raise PermissionError(
            f"无法覆盖推荐指数文件 {path.name}，请关闭正在打开该文件的 Excel、编辑器或预览窗口后重试。"
        ) from exc


def _format_float(value: float | None) -> str:
    return f"{value:.8f}" if value is not None else ""


def _format_percent(value: float | None) -> str:
    return f"{value * 100:.2f}%" if value is not None else ""


def _optional_int(value: str | None) -> int | None:
    return int(value) if value else None


def _optional_float(value: str | None) -> float | None:
    return float(value) if value else None


def _optional_rate(value: str | None) -> float | None:
    if not value:
        return None
    return float(value[:-1]) / 100 if value.endswith("%") else float(value)
