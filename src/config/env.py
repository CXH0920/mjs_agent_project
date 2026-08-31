"""
名将杀 Agent - 配置管理

提供 .env 配置文件的解析、加载、保存功能，
以及 API 配置和运行时参数的获取。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# ============================================================
# 路径常量
# ============================================================

IS_FROZEN = getattr(sys, "frozen", False)

if IS_FROZEN:
    # PyInstaller onedir：exe 同级 _internal/ 为只读打包资源根，
    # exe 同级为可写运行时根（config.env / logs / 用户缓存写入处）。
    _exe_dir = Path(sys.executable).resolve().parent
    BUNDLE_ROOT = _exe_dir / "_internal"     # 只读：静态数据/模板/图片/OCR 模型
    PROJECT_ROOT = _exe_dir                  # 可写：config.env/logs/用户运行时数据
else:
    # 开发态：两者均指向项目根（src 的上两级），现有行为不变。
    PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
    BUNDLE_ROOT = PROJECT_ROOT

DEFAULT_ENV_FILE = PROJECT_ROOT / "config.env"
DEFAULT_PRICING_FILE = BUNDLE_ROOT / "config" / "model_pricing.json"
# API 档案含敏感 Key，放可写运行时根（frozen 下为 exe 目录，非只读 _internal）
DEFAULT_PROFILES_FILE = PROJECT_ROOT / "config" / "api_profiles.json"
# 共享资源目录（头像/截图；此前 match/peak/capture 三四处各自推导，收敛于此）
IMAGES_DIR = BUNDLE_ROOT / "images"
SCREENSHOTS_DIR = PROJECT_ROOT / "screenshots"


def is_full_build() -> bool:
    """是否完整版构建（含 RAG 维护页 + Playwright 抓取）。

    开发态恒 True；frozen 下读 BUNDLE_ROOT/.full_build 标记（由 spec --full 写入）。
    精简版无该标记 → UI 守卫据此裁剪知识库维护页（4 页 → 3 页）。
    """
    if not IS_FROZEN:
        return True
    return (BUNDLE_ROOT / ".full_build").exists()

# ============================================================
# DeepSeek API 默认值
# ============================================================

DEFAULT_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEFAULT_MODEL = "deepseek-v4-pro"

# 供应商预设表：UI 选择 provider 时自动预填（用户可覆盖），见设计文档 §4.2。
# model 留空表示使用服务默认模型；requires_key=False 表示本地服务可不填 Key（如 ollama）。
PROVIDER_PRESETS: dict[str, dict] = {
    "deepseek": {"api_url": "https://api.deepseek.com/v1/chat/completions", "model": "deepseek-v4-pro", "requires_key": True},
    "openai": {"api_url": "https://api.openai.com/v1/chat/completions", "model": "", "requires_key": True},
    "ollama": {"api_url": "http://localhost:11434/v1/chat/completions", "model": "", "requires_key": False},
    "openai-compatible": {"api_url": "", "model": "", "requires_key": True},
}

# 供应商展示名（UI 下拉/列表统一引用，避免两处重复定义不一致，B4）。
PROVIDER_LABELS: dict[str, str] = {
    "deepseek": "DeepSeek",
    "openai": "OpenAI",
    "ollama": "Ollama",
    "openai-compatible": "OpenAI 兼容",
}

# ============================================================
# 配置加载
# ============================================================

def parse_env_file(env_path=None):
    """解析标准 .env 格式文件

    支持 KEY=VALUE 格式，忽略空行和 # 注释行，自动去除值两侧的引号。

    Args:
        env_path: .env 文件路径，默认为项目根目录下的 config.env

    Returns:
        dict[str, str]: 解析出的键值对
    """
    if env_path is None:
        env_path = DEFAULT_ENV_FILE
    path = Path(env_path)
    if not path.exists():
        logger.debug(".env 文件不存在: %s，使用默认值", path)
        return {}

    result = {}
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning(".env 文件读取失败 %s: %s", path, e)
        return {}
    except UnicodeDecodeError as e:
        logger.warning(".env 文件编码无效 %s: %s", path, e)
        return {}

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            result[key] = value
    logger.debug("已加载 .env 配置: %s (%d 项)", path, len(result))
    return result

def load_env_config(env_path=None):
    """从 .env 文件加载配置（统一小写键名，便于使用）

    将 config.env 中的大写 KEY 映射为小写键名供程序内部使用。
    若文件不存在或解析失败则返回空 dict。

    Args:
        env_path: .env 文件路径

    Returns:
        dict: 小写键名的配置 dict，如 {"api_key": "...", "api_url": "..."}
    """
    raw = parse_env_file(env_path)
    key_mapping = {
        "DEEPSEEK_API_KEY": "api_key",
        "DEEPSEEK_API_URL": "api_url",
        "DEEPSEEK_MODEL": "model",
        "REQUESTS_PER_MINUTE": "requests_per_minute",
        "HTTP_TIMEOUT": "http_timeout",
        "MAX_RETRIES": "max_retries",
        "LOG_LEVEL": "log_level",
        "LOG_TO_FILE": "log_to_file",
        # 模拟器 (MuMu) 配置
        "MUMU_ADB_PATH": "mumu_adb_path",
        "MUMU_ADB_PORT": "mumu_adb_port",
        "MUMU_OCR_ENABLED": "mumu_ocr_enabled",
        "MUMU_OCR_POLL_MODE": "mumu_ocr_poll_mode",
        "MUMU_OCR_AUTO_SWITCH_TAB": "mumu_ocr_auto_switch_tab",
        "MUMU_OCR_POLL_INTERVAL": "mumu_ocr_poll_interval",
        "MUMU_OCR_MATCH_THRESHOLD": "mumu_ocr_match_threshold",
        "MUMU_OCR_USE_GPU": "mumu_ocr_use_gpu",
        "MUMU_OCR_CPU_THREADS": "mumu_ocr_cpu_threads",
        "MUMU_HERO_SELECTION_THRESHOLD": "mumu_hero_selection_threshold",
        "MUMU_HERO_SELECTION_COOLDOWN": "mumu_hero_selection_cooldown",
        "MUMU_MATCH_GUIDE_THRESHOLD": "mumu_match_guide_threshold",
        "RECOMMENDATION_P_FLOOR": "recommendation_p_floor",
        "RECOMMENDATION_BAN_WEIGHT": "recommendation_ban_weight",
        "RECOMMENDATION_SIGMOID_K": "recommendation_sigmoid_k",
        "RECOMMENDATION_LOW_WIN_RATE_GAP": "recommendation_low_win_rate_gap",
    }
    config = {}
    for env_key, cfg_key in key_mapping.items():
        if env_key in raw:
            value = raw[env_key]
            if cfg_key in ("requests_per_minute", "max_retries", "http_timeout", "mumu_adb_port", "mumu_ocr_poll_interval", "mumu_hero_selection_cooldown", "mumu_ocr_cpu_threads"):
                try:
                    value = int(value)
                except (ValueError, TypeError):
                    logger.warning("配置 %s 值不是有效整数: %s，使用默认值", env_key, value)
                    continue
            elif cfg_key in ("log_to_file", "mumu_ocr_enabled", "mumu_ocr_poll_mode", "mumu_ocr_auto_switch_tab", "mumu_ocr_use_gpu"):
                value = value.lower() in ("true", "1", "yes")
            elif cfg_key in (
                "mumu_ocr_match_threshold", "mumu_hero_selection_threshold", "mumu_match_guide_threshold",
                "recommendation_p_floor", "recommendation_ban_weight", "recommendation_sigmoid_k",
                "recommendation_low_win_rate_gap",
            ):
                try:
                    value = float(value)
                except (ValueError, TypeError):
                    logger.warning("配置 %s 值不是有效浮点数: %s，使用默认值", env_key, value)
                    continue
            config[cfg_key] = value
    return config

def _usable_profile_config(profile: dict) -> dict | None:
    """档案是否可用：enabled + URL 非空 + 供应商 Key 语义。可用返回 config，否则 None。

    空 URL 不回退 DeepSeek 默认（BUG-5：跨供应商场景会把请求发错端点），视为无效跳过。
    空 Key 仅对 requires_key=False 的供应商（如 ollama）允许。
    """
    if not profile.get("enabled", True) or not profile.get("api_url"):
        return None
    provider = profile.get("provider", "deepseek")
    if PROVIDER_PRESETS.get(provider, {}).get("requires_key", True) and not profile.get("api_key"):
        return None
    return {
        "provider": provider,
        "api_key": profile.get("api_key", ""),
        "api_url": profile.get("api_url", ""),
        "model": profile.get("model", "") or DEFAULT_MODEL,
    }


def has_available_api_profile() -> bool:
    """是否存在可用 API 档案（enabled + URL 非空 + 供应商 Key 语义）。

    UI 展示层与生成链路共用的唯一判定（此前后端选择对话框曾复制一份，
    且误用 has_key 展示字段，与生成链路的 api_key 判定存在漂移风险）。
    """
    for profile in load_api_profiles()["profiles"]:
        if _usable_profile_config(profile) is not None:
            return True
    return False


def get_api_config():
    """获取 API 配置（启用档案优先，其次 config.env → 环境变量 → 默认值）

    取 api_profiles.json 中第一个 enabled 档案（同时只允许一个启用，
    启用即当前使用的 API）；档案文件存在但无启用档案（全停用/全删光）时，
    只回退环境变量 + 默认值，刻意不读 config.env 旧键，使"停用"真正生效
    （A1：避免旧 Key 静默回跑）；档案文件不存在（从未配置过档案）时才走
    _legacy_api_config 旧链。

    Returns:
        {"api_key": str, "api_url": str, "model": str}
    """
    for profile in load_api_profiles()["profiles"]:
        config = _usable_profile_config(profile)
        if config:
            return config
    # 档案体系已启用（文件存在）但无启用档案：仅环境变量 + 默认值，不读 config.env 旧键
    if Path(DEFAULT_PROFILES_FILE).exists():
        return _env_var_fallback()
    # 从未配置过档案：旧链兜底（config.env → 环境变量 → 默认值）
    return _legacy_api_config()


def _env_var_fallback() -> dict:
    """档案体系已启用但无可用默认时的兜底：仅环境变量 + 默认值。

    刻意不读 config.env 的 DEEPSEEK_* 键，使"停用/删光档案"语义生效；
    环境变量作为脚本/CI 注入路径的最后兜底（决策 3：长期保留）。
    """
    return {
        "provider": "deepseek",
        "api_key": os.getenv("DEEPSEEK_API_KEY", "") or os.getenv("OPENAI_API_KEY", ""),
        "api_url": os.getenv("DEEPSEEK_API_URL", "") or DEFAULT_API_URL,
        "model": os.getenv("DEEPSEEK_MODEL", "") or DEFAULT_MODEL,
    }


def _legacy_api_config():
    """旧链兜底：config.env > 环境变量 > 默认值（无默认档案时使用）。"""
    config = load_env_config()

    api_key = (
        config.get("api_key", "")
        or os.getenv("DEEPSEEK_API_KEY", "")
        or os.getenv("OPENAI_API_KEY", "")
    )
    api_url = config.get("api_url", "") or DEFAULT_API_URL
    model = config.get("model", "") or DEFAULT_MODEL

    return {"provider": "deepseek", "api_key": api_key, "api_url": api_url, "model": model}


def load_pricing_config(pricing_path=None) -> dict:
    """加载模型价格配置，文件不存在或格式无效时返回空模型表。"""
    path = Path(pricing_path or DEFAULT_PRICING_FILE)
    default = {
        "currency": "CNY",
        "unit": "百万tokens",
        "updated_at": "",
        "models": {},
    }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("models", {}), dict):
            raise ValueError("价格配置必须包含 models 对象")
    except FileNotFoundError:
        logger.warning("模型价格文件不存在: %s", path)
        return default
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
        logger.warning("模型价格配置不可用 %s: %s", path, error)
        return default

    return {
        "currency": str(data.get("currency", default["currency"])),
        "unit": str(data.get("unit", default["unit"])),
        "updated_at": str(data.get("updated_at", default["updated_at"])),
        "models": data.get("models", {}),
    }


def save_pricing_config(pricing_path, data: dict) -> None:
    """以 UTF-8 无 BOM、LF 换行原子写入模型价格配置。"""
    path = Path(pricing_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    with tmp_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    tmp_path.replace(path)


def get_model_pricing(model: str) -> dict | None:
    """获取模型价格；未知模型或无效配置返回 None。"""
    try:
        pricing_data = load_pricing_config()
        pricing = pricing_data["models"][model]
        input_price = pricing["input_per_million"]
        output_price = pricing["output_per_million"]
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0
                   for value in (input_price, output_price)):
            raise ValueError("价格必须为非负数字")
        return {
            "input_per_million": float(input_price),
            "output_per_million": float(output_price),
            "cached_input_per_million": pricing.get("cached_input_per_million"),
            "currency": pricing_data.get("currency", "CNY"),
            "updated_at": pricing_data.get("updated_at", ""),
        }
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        logger.warning("模型 %s 的价格配置不可用: %s", model, error)
    return None

def get_runtime_params():
    """从 config.env 获取运行时参数（带默认值）

    Returns:
        {"requests_per_minute": int, "max_retries": int,
         "http_timeout": int, "log_level": str, "log_to_file": bool}
    """
    config = load_env_config()
    return {
        "requests_per_minute": config.get("requests_per_minute", 30),
        "max_retries": config.get("max_retries", 3),
        "http_timeout": config.get("http_timeout", 300),
        "log_level": config.get("log_level", "INFO"),
        "log_to_file": config.get("log_to_file", True),
    }


def get_mumu_config():
    """从 config.env 获取模拟器配置（带默认值）

    Returns:
        {"mumu_adb_path": str, "mumu_adb_port": int,
         "mumu_ocr_enabled": bool, "mumu_ocr_auto_switch_tab": bool,
         "mumu_ocr_match_threshold": float}
    """
    config = load_env_config()
    return {
        "mumu_adb_path": config.get("mumu_adb_path", ""),
        "mumu_adb_port": config.get("mumu_adb_port", 0),
        "mumu_ocr_enabled": config.get("mumu_ocr_enabled", False),
        "mumu_ocr_poll_mode": config.get("mumu_ocr_poll_mode", False),
        "mumu_ocr_auto_switch_tab": config.get("mumu_ocr_auto_switch_tab", False),
        "mumu_ocr_poll_interval": config.get("mumu_ocr_poll_interval", 2),
        "mumu_ocr_match_threshold": config.get("mumu_ocr_match_threshold", 0.8),
        "mumu_hero_selection_threshold": config.get("mumu_hero_selection_threshold", config.get("mumu_ocr_match_threshold", 0.8)),
        "mumu_hero_selection_cooldown": config.get("mumu_hero_selection_cooldown", 180),
        "mumu_match_guide_threshold": config.get("mumu_match_guide_threshold", 0.8),
        "mumu_ocr_use_gpu": config.get("mumu_ocr_use_gpu", False),
        "mumu_ocr_cpu_threads": config.get("mumu_ocr_cpu_threads", 6),
    }

# ============================================================
# 配置保存
# ============================================================

def save_env_file(env_path, data):
    """原子写入 .env 文件

    保留原文件中的注释行和已有无关配置，
    新增或更新指定配置项。

    Args:
        env_path: .env 文件路径
        data: 要写入的键值对
    """
    existing_keys = set()
    lines = []
    if env_path.exists():
        try:
            content = env_path.read_text(encoding="utf-8")
        except OSError as error:
            # 读不出旧内容就无法保留注释与无关键——直接覆盖会丢用户手写配置，
            # 因此记日志后失败退出（config.env 被占用/权限异常时保存中止）
            logger.error("读取 %s 失败，取消保存: %s", env_path, error)
            raise
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                lines.append(line)
            else:
                key = stripped.split("=")[0].strip() if "=" in stripped else ""
                if key not in data:
                    lines.append(line)
                if key:
                    existing_keys.add(key)

    for key in data:
        if key not in existing_keys:
            lines.append(f"{key}={data[key]}")

    data_copy = dict(data)
    result_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=")[0].strip()
            if key in data_copy:
                result_lines.append(f"{key}={data_copy.pop(key)}")
                continue
        result_lines.append(line)

    for key, value in data_copy.items():
        result_lines.append(f"{key}={value}")

    tmp_path = env_path.with_suffix(".env.tmp")
    tmp_path.write_text("\n".join(result_lines) + "\n", encoding="utf-8")
    tmp_path.replace(env_path)


# ============================================================
# 多 API 档案（api_profiles.json）
# ============================================================

def _as_bool(value, default: bool) -> bool:
    """宽容布尔转换：None 用 default，bool 原值，字符串按 true/1/yes 判定，其余按真值。

    使 default 对 null/缺失值生效（A3：旧实现 None 落到 bool(None)=False，
    令 enabled:null 被误判为停用）。
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


def _normalize_profiles(profiles) -> list[dict]:
    """加载/保存前的容错归一化：跳过非法项、补默认字段、修正名称重复、启用互斥。

    启用互斥语义：同时至多一个 enabled=true（启用即当前使用的 API）。
    多个 enabled 时保留第一个、其余置 false（打 warning），保证数据层面互斥，
    打开配置即生效——避免历史文件多 enabled 导致界面显示多个启用。
    is_default 字段已废弃，旧文件中的 is_default 被静默丢弃。
    """
    result: list[dict] = []
    seen: set[str] = set()
    enabled_seen = False
    for i, raw in enumerate(profiles):
        if not isinstance(raw, dict):
            logger.warning("API 档案第 %d 项不是对象，已跳过", i + 1)
            continue
        name = str(raw.get("name", "")).strip() or f"profile-{i + 1}"
        if name in seen:
            suffix = 2
            while f"{name}-{suffix}" in seen:
                suffix += 1
            logger.warning("API 档案名称重复: %s，已重命名为 %s-%d", name, name, suffix)
            name = f"{name}-{suffix}"
        enabled = _as_bool(raw.get("enabled"), True)
        if enabled:
            if enabled_seen:
                logger.warning("存在多个启用的 API 档案，仅保留第一个，其余停用: %s", name)
                enabled = False
            else:
                enabled_seen = True
        result.append({
            "name": name,
            "provider": str(raw.get("provider", "openai-compatible")).strip() or "openai-compatible",
            "api_key": str(raw.get("api_key", "")),
            "api_url": str(raw.get("api_url", "")).strip(),
            "model": str(raw.get("model", "")).strip(),
            "enabled": enabled,
            "note": str(raw.get("note", "")),
        })
        seen.add(name)
    return result


def load_api_profiles(profiles_path=None) -> dict:
    """读取 API 档案配置；文件不存在或损坏时返回空档案列表，不阻断调用方。

    Returns:
        {"version": int, "profiles": [{"name", "provider", "api_key", "api_url",
         "model", "enabled", "note"}, ...]}
    """
    path = Path(profiles_path or DEFAULT_PROFILES_FILE)
    default = {"version": 1, "profiles": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("profiles", []), list):
            raise ValueError("API 档案配置必须包含 profiles 列表")
    except FileNotFoundError:
        return default
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
        logger.warning("API 档案配置不可用 %s: %s", path, error)
        return default
    return {
        "version": data.get("version", 1),
        "profiles": _normalize_profiles(data.get("profiles", [])),
    }


def save_api_profiles(data: dict, profiles_path=None) -> None:
    """原子写入 API 档案配置（UTF-8 无 BOM、LF、indent=2），保存前归一化（启用互斥/名称去重）。

    profiles 为空时不写空文件（已存在则删除）——让"删光档案→保存"回到"从未配置"
    状态，get_api_config 走 _legacy_api_config 读 config.env 旧键兜底（BUG-4 修复），
    避免空文件被误判为"仍在档案体系"导致旧 Key 静默旁路。
    """
    path = Path(profiles_path or DEFAULT_PROFILES_FILE)
    if not isinstance(data, dict):
        data = {}
    profiles = _normalize_profiles(data.get("profiles", []))
    if not profiles:
        # 空档案：不落盘空文件；已有文件则删除，保持"从未配置档案"状态
        if path.exists():
            try:
                path.unlink()
            except OSError as error:
                logger.warning("删除空档案文件失败 %s: %s", path, error)
        return
    payload = {
        "version": int(data.get("version", 1)),
        "profiles": profiles,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with tmp_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    tmp_path.replace(path)


def list_api_profiles() -> list[dict]:
    """返回供 UI 展示的档案列表；api_key 一律以 has_key 标记代替，不回显明文。"""
    result = []
    for p in load_api_profiles()["profiles"]:
        result.append({
            "name": p["name"],
            "provider": p["provider"],
            "has_key": bool(p.get("api_key")),
            "api_url": p["api_url"],
            "model": p["model"],
            "enabled": p["enabled"],
            "note": p["note"],
        })
    return result


def get_api_profile(name: str) -> dict | None:
    """按名称取完整档案（含 api_key），仅供任务解析路径使用，不得进入日志/UI。"""
    if not name:
        return None
    for p in load_api_profiles()["profiles"]:
        if p["name"] == name:
            return dict(p)
    return None


def resolve_api_config(name: str | None = None) -> dict:
    """任务侧唯一 API 解析入口。

    - name 指定且启用：取该档案三件套。
    - name 指定但不存在/停用：warning 并回退默认解析。
    - name 为空：走 get_api_config()（默认档案 → 旧链兜底）。

    Returns:
        {"api_key": str, "api_url": str, "model": str}
    """
    if name:
        profile = get_api_profile(name)
        config = _usable_profile_config(profile) if profile else None
        if config:
            return config
        logger.warning("API 档案不存在或已停用: %s，回退默认配置", name)
    return get_api_config()


def migrate_legacy_api_config(env_path=None, profiles_path=None) -> bool:
    """把旧 config.env 的 DEEPSEEK_* 三件套迁移为默认档案（幂等）。

    档案文件已存在或三件套全空时不迁移，避免覆盖用户配置。

    Returns:
        bool: 本次是否实际创建了档案
    """
    path = Path(profiles_path or DEFAULT_PROFILES_FILE)
    if path.exists():
        return False
    legacy = parse_env_file(env_path)
    api_key = legacy.get("DEEPSEEK_API_KEY", "")
    api_url = legacy.get("DEEPSEEK_API_URL", "")
    model = legacy.get("DEEPSEEK_MODEL", "")
    if not any((api_key, api_url, model)):
        return False
    save_api_profiles(
        {
            "version": 1,
            "profiles": [{
                "name": "deepseek-main",
                "provider": "deepseek",
                "api_key": api_key,
                "api_url": api_url or DEFAULT_API_URL,
                "model": model or DEFAULT_MODEL,
                "enabled": True,
                "note": "由旧配置自动迁移",
            }],
        },
        profiles_path=path,
    )
    logger.info("已将旧 API 配置迁移为默认档案 deepseek-main: %s", path)
    return True
