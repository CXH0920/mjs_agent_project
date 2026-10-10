"""config.env 键元表单源守护测试。

键映射、类型转换与 getter 默认值四处手工同步的年代，截图模式键两次
漏登记成死开关（063dc47、T1 运维加固）。单源化（P1-4/P1-7）后本文件锁死：

- 元表键全集 == load_env_config 实际认识的键全集（漏登记防复发）
- 两个 getter 输出键集与默认值快照（消费方遍布全仓，schema 不得漂移）
- 类型转换、级联默认、max_output_tokens 单一事实源
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from src.config import env


def _use_env_file(monkeypatch, tmp_path: Path, content: str) -> Path:
    """把 DEFAULT_ENV_FILE 指向临时配置文件，隔离本地 config.env。"""
    env_file = tmp_path / "config.env"
    env_file.write_text(content, encoding="utf-8")
    monkeypatch.setattr(env, "DEFAULT_ENV_FILE", env_file)
    return env_file


def _use_absent_env_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(env, "DEFAULT_ENV_FILE", tmp_path / "absent.env")


# 全量登记键哨兵：env_key → (写入原文, 期望 cfg_key, 期望解析值)。
# 期望侧为独立手抄副本，故意不复用 _ENV_KEY_SPECS——漏登记时两侧对不上。
_SENTINELS: dict[str, tuple[str, str, object]] = {
    "DEEPSEEK_API_KEY": ("sk-test", "api_key", "sk-test"),
    "DEEPSEEK_API_URL": ("https://example.test/v1", "api_url", "https://example.test/v1"),
    "DEEPSEEK_MODEL": ("test-model", "model", "test-model"),
    "REQUESTS_PER_MINUTE": ("10", "requests_per_minute", 10),
    "HTTP_TIMEOUT": ("120", "http_timeout", 120),
    "MAX_RETRIES": ("5", "max_retries", 5),
    "MAX_OUTPUT_TOKENS": ("65536", "max_output_tokens", 65536),
    "LOG_LEVEL": ("DEBUG", "log_level", "DEBUG"),
    "LOG_TO_FILE": ("yes", "log_to_file", True),
    "MJS_DATA_REPO": ("G:/mjs_data_private", "mjs_data_repo", "G:/mjs_data_private"),
    "MUMU_ADB_PATH": ("G:/adb.exe", "mumu_adb_path", "G:/adb.exe"),
    "MUMU_ADB_PORT": ("16384", "mumu_adb_port", 16384),
    "MUMU_OCR_ENABLED": ("true", "mumu_ocr_enabled", True),
    "MUMU_OCR_POLL_MODE": ("1", "mumu_ocr_poll_mode", True),
    "MUMU_OCR_POLL_IDLE_PAUSE": ("false", "mumu_ocr_poll_idle_pause", False),
    "MUMU_OCR_AUTO_SWITCH_TAB": ("no", "mumu_ocr_auto_switch_tab", False),
    "MUMU_OCR_POLL_INTERVAL": ("3", "mumu_ocr_poll_interval", 3),
    "MUMU_OCR_MATCH_THRESHOLD": ("0.75", "mumu_ocr_match_threshold", 0.75),
    "MUMU_OCR_CPU_THREADS": ("8", "mumu_ocr_cpu_threads", 8),
    "MUMU_OCR_PRIMARY_ENGINE": ("v4", "mumu_ocr_primary_engine", "v4"),
    "MUMU_OCR_RECHECK_ENABLED": ("yes", "mumu_ocr_recheck_enabled", True),
    "MUMU_SCREENSHOT_MODE": ("raw", "mumu_screenshot_mode", "raw"),
    "MUMU_HERO_SELECTION_THRESHOLD": ("0.9", "mumu_hero_selection_threshold", 0.9),
    "MUMU_HERO_SELECTION_COOLDOWN": ("60", "mumu_hero_selection_cooldown", 60),
    "MUMU_MATCH_GUIDE_THRESHOLD": ("0.85", "mumu_match_guide_threshold", 0.85),
    "RECOMMENDATION_P_FLOOR": ("0.01", "recommendation_p_floor", 0.01),
    "RECOMMENDATION_BAN_WEIGHT": ("2.5", "recommendation_ban_weight", 2.5),
    "RECOMMENDATION_SIGMOID_K": ("12", "recommendation_sigmoid_k", 12.0),
    "RECOMMENDATION_LOW_WIN_RATE_GAP": ("0.15", "recommendation_low_win_rate_gap", 0.15),
    "DATA_FRESHNESS_TIMELINE_STALE_DAYS": ("21", "data_freshness_timeline_stale_days", 21),
}


def test_every_registered_key_parses_and_coerces(monkeypatch, tmp_path) -> None:
    """元表键全集锁：每个登记键都被 load_env_config 映射并按类型转换。

    键只登记了一环而漏另一环（063dc47 死开关形态）时在此失败；
    未登记键（UNKNOWN_KEY）应被丢弃。
    """
    lines = [f"{key}={raw}" for key, (raw, _cfg, _parsed) in _SENTINELS.items()]
    lines.append("UNKNOWN_KEY=whatever")
    env_file = _use_env_file(monkeypatch, tmp_path, "\n".join(lines))
    expected = {cfg: parsed for _raw, cfg, parsed in _SENTINELS.values()}
    assert env.load_env_config(env_file) == expected


def test_runtime_params_defaults_snapshot(monkeypatch, tmp_path) -> None:
    """get_runtime_params 输出 schema 与默认值快照（键集缺一不可）。"""
    _use_absent_env_file(monkeypatch, tmp_path)
    assert env.get_runtime_params() == {
        "requests_per_minute": 30,
        "max_retries": 3,
        "max_output_tokens": 32_768,
        "http_timeout": 300,
        "log_level": "INFO",
        "log_to_file": True,
    }


def test_mumu_config_defaults_snapshot(monkeypatch, tmp_path) -> None:
    """get_mumu_config 输出 schema 与默认值快照（键集缺一不可）。"""
    _use_absent_env_file(monkeypatch, tmp_path)
    assert env.get_mumu_config() == {
        "mumu_adb_path": "",
        "mumu_adb_port": 0,
        "mumu_screenshot_mode": "auto",
        "mumu_ocr_enabled": False,
        "mumu_ocr_poll_mode": False,
        "mumu_ocr_poll_idle_pause": True,
        "mumu_ocr_auto_switch_tab": False,
        "mumu_ocr_poll_interval": 2,
        "mumu_ocr_match_threshold": 0.8,
        "mumu_hero_selection_threshold": 0.8,
        "mumu_hero_selection_cooldown": 180,
        "mumu_match_guide_threshold": 0.8,
        "mumu_ocr_primary_engine": "v6",
        "mumu_ocr_cpu_threads": 6,
        "mumu_ocr_recheck_enabled": False,
    }


def test_hero_selection_threshold_cascades_from_match_threshold(monkeypatch, tmp_path) -> None:
    """未显式配置时，武将选择阈值缺省跟随 match_threshold 的实配值。"""
    _use_env_file(monkeypatch, tmp_path, "MUMU_OCR_MATCH_THRESHOLD=0.65\n")
    config = env.get_mumu_config()
    assert config["mumu_ocr_match_threshold"] == 0.65
    assert config["mumu_hero_selection_threshold"] == 0.65

    _use_env_file(
        monkeypatch, tmp_path,
        "MUMU_OCR_MATCH_THRESHOLD=0.65\nMUMU_HERO_SELECTION_THRESHOLD=0.9\n",
    )
    assert env.get_mumu_config()["mumu_hero_selection_threshold"] == 0.9


def test_invalid_numeric_values_fall_back_to_defaults(monkeypatch, tmp_path) -> None:
    """非法 int/float 值丢弃并回退默认值（沿用解析期告警语义）。"""
    _use_env_file(monkeypatch, tmp_path, "REQUESTS_PER_MINUTE=abc\nMUMU_OCR_MATCH_THRESHOLD=xyz\n")
    assert env.get_runtime_params()["requests_per_minute"] == 30
    assert env.get_mumu_config()["mumu_ocr_match_threshold"] == 0.8


@pytest.mark.parametrize(
    "raw,expected",
    [("true", True), ("1", True), ("yes", True), ("TRUE", True),
     ("false", False), ("0", False), ("off", False)],
)
def test_bool_coercion_variants(monkeypatch, tmp_path, raw: str, expected: bool) -> None:
    _use_env_file(monkeypatch, tmp_path, f"LOG_TO_FILE={raw}\n")
    assert env.get_runtime_params()["log_to_file"] is expected


def test_max_output_tokens_single_source() -> None:
    """32_768 只允许存在一个事实源：env 常量，api_generator 仅引用。"""
    from src.scraper.ai.api_generator import MAX_OUTPUT_TOKENS

    assert env.DEFAULT_MAX_OUTPUT_TOKENS == 32_768
    assert MAX_OUTPUT_TOKENS is env.DEFAULT_MAX_OUTPUT_TOKENS


def test_table_keys_partition_into_getters_and_passthrough(monkeypatch, tmp_path) -> None:
    """元表键要么被某个 getter 服务，要么是登记在册的透传键——不允许无人认领。"""
    _use_absent_env_file(monkeypatch, tmp_path)
    getter_keys = set(env.get_runtime_params()) | set(env.get_mumu_config())
    table_keys = {spec.cfg_key for spec in env._ENV_KEY_SPECS.values()}
    assert table_keys - getter_keys == {
        "api_key", "api_url", "model", "mjs_data_repo",
        "recommendation_p_floor", "recommendation_ban_weight",
        "recommendation_sigmoid_k", "recommendation_low_win_rate_gap",
        "data_freshness_timeline_stale_days",
    }


# ---------------------------------------------------------------------------
# pricing 域：文件缺失 / 格式无效 / 非法价格的回退语义（此前无直测）
# ---------------------------------------------------------------------------

_EMPTY_PRICING = {
    "currency": "CNY",
    "unit": "百万tokens",
    "updated_at": "",
    "models": {},
}


def _use_pricing_file(monkeypatch, tmp_path: Path, content: str | None) -> Path:
    """把 DEFAULT_PRICING_FILE 指向临时文件；content=None 表示文件不存在。"""
    pricing_file = tmp_path / "model_pricing.json"
    if content is not None:
        pricing_file.write_text(content, encoding="utf-8")
    monkeypatch.setattr(env, "DEFAULT_PRICING_FILE", pricing_file)
    return pricing_file


def test_load_pricing_config_missing_file_falls_back_to_empty(monkeypatch, tmp_path) -> None:
    _use_pricing_file(monkeypatch, tmp_path, None)
    assert env.load_pricing_config() == _EMPTY_PRICING


@pytest.mark.parametrize("content", ["不是 JSON", "[1, 2]"], ids=["garbage", "not_dict"])
def test_load_pricing_config_invalid_file_falls_back_to_empty(
    monkeypatch, tmp_path, content: str
) -> None:
    _use_pricing_file(monkeypatch, tmp_path, content)
    assert env.load_pricing_config() == _EMPTY_PRICING


def test_load_pricing_config_reads_model_table(monkeypatch, tmp_path) -> None:
    _use_pricing_file(monkeypatch, tmp_path, json.dumps({
        "currency": "USD",
        "models": {"m": {"input_per_million": 1, "output_per_million": 2}},
    }, ensure_ascii=False))
    data = env.load_pricing_config()
    assert data["currency"] == "USD"
    assert data["unit"] == "百万tokens"  # 未登记字段回默认
    assert data["models"]["m"] == {"input_per_million": 1, "output_per_million": 2}


def test_get_model_pricing_normalizes_valid_entry(monkeypatch, tmp_path) -> None:
    _use_pricing_file(monkeypatch, tmp_path, json.dumps({
        "currency": "USD",
        "models": {"m": {
            "input_per_million": 1, "output_per_million": 2.5, "cached_input_per_million": 0.1,
        }},
    }, ensure_ascii=False))
    assert env.get_model_pricing("m") == {
        "input_per_million": 1.0,
        "output_per_million": 2.5,
        "cached_input_per_million": 0.1,
        "currency": "USD",
        "updated_at": "",
    }


@pytest.mark.parametrize(
    ("input_price", "output_price"),
    [(-1, 2), (1, "x"), (True, 2)],
    ids=["negative", "non_numeric", "bool_not_price"],
)
def test_get_model_pricing_rejects_invalid_prices(
    monkeypatch, tmp_path, input_price, output_price
) -> None:
    _use_pricing_file(monkeypatch, tmp_path, json.dumps(
        {"models": {"m": {
            "input_per_million": input_price, "output_per_million": output_price,
        }}}
    ))
    assert env.get_model_pricing("m") is None


def test_get_model_pricing_unknown_model_returns_none(monkeypatch, tmp_path) -> None:
    _use_pricing_file(monkeypatch, tmp_path, json.dumps({"models": {}}))
    assert env.get_model_pricing("nope") is None
