"""
名将杀 Agent - 配置管理

提供 .env 配置文件的解析、加载、保存功能，
以及运行时参数、模型价格与模拟器配置的获取。
API 档案（api_profiles.json）域见 profiles.py。
"""

from __future__ import annotations

import json
import logging
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
# 头像下载输出目录：crawler 写入用，可写运行时根（区别于只读的 IMAGES_DIR）。
# 注意 frozen 态后果：UI 头像读取固定自只读 IMAGES_DIR，下载到本目录的新头像
# 不会被 UI 读取；运行时回退读取属独立需求，尚未实现。
IMAGES_OUTPUT_DIR = PROJECT_ROOT / "images"
# OCR 混淆白名单：UI"白名单配置"界面写入、识别侧（character_similarity）读取，
# 路径单一事实源在此，双侧只引用不再各自拼接。
OCR_CONFUSION_OVERRIDES_PATH = PROJECT_ROOT / "data" / "ocr_confusion_overrides.json"


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
DEFAULT_MODEL = "deepseek-v4-flash"

# 供应商预设表：UI 选择 provider 时自动预填（用户可覆盖），见设计文档 §4.2。
# model 留空表示使用服务默认模型；requires_key=False 表示本地服务可不填 Key（如 ollama）。
PROVIDER_PRESETS: dict[str, dict] = {
    "deepseek": {"api_url": "https://api.deepseek.com/v1/chat/completions", "model": "deepseek-v4-flash", "requires_key": True},
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
        "MAX_OUTPUT_TOKENS": "max_output_tokens",
        "LOG_LEVEL": "log_level",
        "LOG_TO_FILE": "log_to_file",
        # 私有数据仓根目录（src/scripts/pull_data.py 用；默认项目同级 mjs_data_private）
        "MJS_DATA_REPO": "mjs_data_repo",
        # 模拟器 (MuMu) 配置
        "MUMU_ADB_PATH": "mumu_adb_path",
        "MUMU_ADB_PORT": "mumu_adb_port",
        "MUMU_OCR_ENABLED": "mumu_ocr_enabled",
        "MUMU_OCR_POLL_MODE": "mumu_ocr_poll_mode",
        "MUMU_OCR_POLL_IDLE_PAUSE": "mumu_ocr_poll_idle_pause",
        "MUMU_OCR_AUTO_SWITCH_TAB": "mumu_ocr_auto_switch_tab",
        "MUMU_OCR_POLL_INTERVAL": "mumu_ocr_poll_interval",
        "MUMU_OCR_MATCH_THRESHOLD": "mumu_ocr_match_threshold",
        "MUMU_OCR_USE_GPU": "mumu_ocr_use_gpu",
        "MUMU_OCR_CPU_THREADS": "mumu_ocr_cpu_threads",
        "MUMU_OCR_RECHECK_ENABLED": "mumu_ocr_recheck_enabled",
        "MUMU_SCREENSHOT_MODE": "mumu_screenshot_mode",
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
            if cfg_key in ("requests_per_minute", "max_retries", "max_output_tokens", "http_timeout", "mumu_adb_port", "mumu_ocr_poll_interval", "mumu_hero_selection_cooldown", "mumu_ocr_cpu_threads"):
                try:
                    value = int(value)
                except (ValueError, TypeError):
                    logger.warning("配置 %s 值不是有效整数: %s，使用默认值", env_key, value)
                    continue
            elif cfg_key in ("log_to_file", "mumu_ocr_enabled", "mumu_ocr_poll_mode", "mumu_ocr_poll_idle_pause", "mumu_ocr_auto_switch_tab", "mumu_ocr_use_gpu", "mumu_ocr_recheck_enabled"):
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
    # 函数内导入：config 是被 src.data 各仓库依赖的底层包，
    # 模块级导入 src.data.json_repository 会触发 src.data.__init__ 循环初始化
    from src.data.json_repository import atomic_write_text

    path = Path(pricing_path)
    content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(path, content)


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
         "max_output_tokens": int, "http_timeout": int,
         "log_level": str, "log_to_file": bool}
    """
    config = load_env_config()
    return {
        "requests_per_minute": config.get("requests_per_minute", 30),
        "max_retries": config.get("max_retries", 3),
        # 与 AIBatchGenerator.MAX_OUTPUT_TOKENS 默认一致；思考型模型可按供应商上限调大
        "max_output_tokens": config.get("max_output_tokens", 32_768),
        "http_timeout": config.get("http_timeout", 300),
        "log_level": config.get("log_level", "INFO"),
        "log_to_file": config.get("log_to_file", True),
    }


def get_mumu_config():
    """从 config.env 获取模拟器配置（带默认值）

    Returns:
        {"mumu_adb_path": str, "mumu_adb_port": int,
         "mumu_screenshot_mode": str,
         "mumu_ocr_enabled": bool, "mumu_ocr_auto_switch_tab": bool,
         "mumu_ocr_match_threshold": float}
    """
    config = load_env_config()
    return {
        "mumu_adb_path": config.get("mumu_adb_path", ""),
        "mumu_adb_port": config.get("mumu_adb_port", 0),
        "mumu_screenshot_mode": config.get("mumu_screenshot_mode", "auto"),
        "mumu_ocr_enabled": config.get("mumu_ocr_enabled", False),
        "mumu_ocr_poll_mode": config.get("mumu_ocr_poll_mode", False),
        "mumu_ocr_poll_idle_pause": config.get("mumu_ocr_poll_idle_pause", True),
        "mumu_ocr_auto_switch_tab": config.get("mumu_ocr_auto_switch_tab", False),
        "mumu_ocr_poll_interval": config.get("mumu_ocr_poll_interval", 2),
        "mumu_ocr_match_threshold": config.get("mumu_ocr_match_threshold", 0.8),
        "mumu_hero_selection_threshold": config.get("mumu_hero_selection_threshold", config.get("mumu_ocr_match_threshold", 0.8)),
        "mumu_hero_selection_cooldown": config.get("mumu_hero_selection_cooldown", 180),
        "mumu_match_guide_threshold": config.get("mumu_match_guide_threshold", 0.8),
        "mumu_ocr_use_gpu": config.get("mumu_ocr_use_gpu", False),
        "mumu_ocr_cpu_threads": config.get("mumu_ocr_cpu_threads", 6),
        "mumu_ocr_recheck_enabled": config.get("mumu_ocr_recheck_enabled", False),
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

    # 函数内导入原因同 save_pricing_config（避免 config↔data 循环初始化）
    from src.data.json_repository import atomic_write_text

    atomic_write_text(env_path, "\n".join(result_lines) + "\n")

