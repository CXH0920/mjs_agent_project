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
from typing import NamedTuple

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
# 用户可改配置，读写均在运行时根（frozen 首启从打包默认部署副本，升级不覆盖）
DEFAULT_PRICING_FILE = PROJECT_ROOT / "config" / "model_pricing.json"
# API 档案含敏感 Key，放可写运行时根（frozen 下为 exe 目录，非只读 _internal）
DEFAULT_PROFILES_FILE = PROJECT_ROOT / "config" / "api_profiles.json"
# 共享资源目录（头像/截图；此前 match/peak/capture 三四处各自推导，收敛于此）
IMAGES_DIR = BUNDLE_ROOT / "images"
SCREENSHOTS_DIR = PROJECT_ROOT / "screenshots"
# 头像下载输出目录：crawler 写入用，可写运行时根；读取侧两级回退
# （本目录优先、IMAGES_DIR 打包基线兜底）见 ui/shared/portrait
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
# max_output_tokens 单一事实源：下方键元表默认值与 api_generator.MAX_OUTPUT_TOKENS
# 均引用本常量（此前两处硬编码 32_768 靠注释同步）；思考型模型可按供应商上限在 config.env 调大
DEFAULT_MAX_OUTPUT_TOKENS = 32_768

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

# ============================================================
# 配置键元表（单一事实源）
# ============================================================

# config.env 全量键登记的唯一事实源：键映射、类型转换、两个 getter 的
# 默认值全部由此派生。此前四处分头登记（key_mapping / 三张类型转换清单 /
# get_runtime_params 与 get_mumu_config 默认值），截图模式键两次漏登记
# 成死开关（063dc47、T1 运维加固），故收口为单表，新键只改这里。
# getter 域：runtime → get_runtime_params；mumu → get_mumu_config；
# 空 → 透传键（调用方直接从 load_env_config 取值，无默认值登记）。
class _EnvKeySpec(NamedTuple):
    cfg_key: str            # 内部小写键名
    type: object            # int/bool/float/str，驱动解析时的类型转换
    default: object         # getter 缺省值（透传键为 None）
    getter: str = ""        # 归属 getter 域："runtime" | "mumu" | ""
    default_from: str = ""  # 缺省回退到另一键的实配值（级联默认）


_ENV_KEY_SPECS: dict[str, _EnvKeySpec] = {
    # ── get_runtime_params 域（输出键序即本域登记序）──
    "REQUESTS_PER_MINUTE": _EnvKeySpec("requests_per_minute", int, 30, "runtime"),
    "MAX_RETRIES": _EnvKeySpec("max_retries", int, 3, "runtime"),
    "MAX_OUTPUT_TOKENS": _EnvKeySpec("max_output_tokens", int, DEFAULT_MAX_OUTPUT_TOKENS, "runtime"),
    "HTTP_TIMEOUT": _EnvKeySpec("http_timeout", int, 300, "runtime"),
    "LOG_LEVEL": _EnvKeySpec("log_level", str, "INFO", "runtime"),
    "LOG_TO_FILE": _EnvKeySpec("log_to_file", bool, True, "runtime"),
    # ── get_mumu_config 域（输出键序即本域登记序）──
    "MUMU_ADB_PATH": _EnvKeySpec("mumu_adb_path", str, "", "mumu"),
    "MUMU_ADB_PORT": _EnvKeySpec("mumu_adb_port", int, 0, "mumu"),
    "MUMU_SCREENSHOT_MODE": _EnvKeySpec("mumu_screenshot_mode", str, "auto", "mumu"),
    "MUMU_OCR_ENABLED": _EnvKeySpec("mumu_ocr_enabled", bool, False, "mumu"),
    "MUMU_OCR_POLL_MODE": _EnvKeySpec("mumu_ocr_poll_mode", bool, False, "mumu"),
    "MUMU_OCR_POLL_IDLE_PAUSE": _EnvKeySpec("mumu_ocr_poll_idle_pause", bool, True, "mumu"),
    "MUMU_OCR_AUTO_SWITCH_TAB": _EnvKeySpec("mumu_ocr_auto_switch_tab", bool, False, "mumu"),
    "MUMU_OCR_POLL_INTERVAL": _EnvKeySpec("mumu_ocr_poll_interval", int, 2, "mumu"),
    "MUMU_OCR_MATCH_THRESHOLD": _EnvKeySpec("mumu_ocr_match_threshold", float, 0.8, "mumu"),
    "MUMU_HERO_SELECTION_THRESHOLD": _EnvKeySpec(
        "mumu_hero_selection_threshold", float, 0.8, "mumu",
        default_from="mumu_ocr_match_threshold",
    ),
    "MUMU_HERO_SELECTION_COOLDOWN": _EnvKeySpec("mumu_hero_selection_cooldown", int, 180, "mumu"),
    "MUMU_MATCH_GUIDE_THRESHOLD": _EnvKeySpec("mumu_match_guide_threshold", float, 0.8, "mumu"),
    "MUMU_OCR_PRIMARY_ENGINE": _EnvKeySpec("mumu_ocr_primary_engine", str, "v6", "mumu"),
    "MUMU_OCR_CPU_THREADS": _EnvKeySpec("mumu_ocr_cpu_threads", int, 6, "mumu"),
    "MUMU_OCR_RECHECK_ENABLED": _EnvKeySpec("mumu_ocr_recheck_enabled", bool, False, "mumu"),
    # ── 透传键（无 getter，调用方直接 load_env_config 取）──
    "DEEPSEEK_API_KEY": _EnvKeySpec("api_key", str, None),
    "DEEPSEEK_API_URL": _EnvKeySpec("api_url", str, None),
    "DEEPSEEK_MODEL": _EnvKeySpec("model", str, None),
    # 私有数据仓根目录（src/scripts/pull_data.py 用；默认项目同级 mjs_data_private）
    "MJS_DATA_REPO": _EnvKeySpec("mjs_data_repo", str, None),
    "RECOMMENDATION_P_FLOOR": _EnvKeySpec("recommendation_p_floor", float, None),
    "RECOMMENDATION_BAN_WEIGHT": _EnvKeySpec("recommendation_ban_weight", float, None),
    "RECOMMENDATION_SIGMOID_K": _EnvKeySpec("recommendation_sigmoid_k", float, None),
    "RECOMMENDATION_LOW_WIN_RATE_GAP": _EnvKeySpec("recommendation_low_win_rate_gap", float, None),
    # 数据新鲜度芯片：时间轴距今天数超过该值视为不健康，武将新鲜度退化为纯年龄判定
    "DATA_FRESHNESS_TIMELINE_STALE_DAYS": _EnvKeySpec("data_freshness_timeline_stale_days", int, None),
}


def load_env_config(env_path=None):
    """从 .env 文件加载配置（统一小写键名，便于使用）

    键映射与类型转换由 _ENV_KEY_SPECS 统一驱动，新键只登记元表。
    若文件不存在或解析失败返回空 dict。

    Args:
        env_path: .env 文件路径

    Returns:
        dict: 小写键名的配置 dict，如 {"api_key": "...", "api_url": "..."}
    """
    raw = parse_env_file(env_path)
    config = {}
    for env_key, spec in _ENV_KEY_SPECS.items():
        if env_key not in raw:
            continue
        value = raw[env_key]
        if spec.type is int or spec.type is float:
            kind = "整数" if spec.type is int else "浮点数"
            try:
                value = spec.type(value)
            except (ValueError, TypeError):
                logger.warning("配置 %s 值不是有效%s: %s，使用默认值", env_key, kind, value)
                continue
        elif spec.type is bool:
            value = value.lower() in ("true", "1", "yes")
        config[spec.cfg_key] = value
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

def _config_with_defaults(config: dict, getter: str) -> dict:
    """按元表为指定 getter 域的键补默认值（default_from 表示缺省回退到另一键实配值）。"""
    result = {}
    for spec in _ENV_KEY_SPECS.values():
        if spec.getter != getter:
            continue
        if spec.default_from:
            default = config.get(spec.default_from, spec.default)
        else:
            default = spec.default
        result[spec.cfg_key] = config.get(spec.cfg_key, default)
    return result


def get_runtime_params():
    """从 config.env 获取运行时参数（键集与默认值见 _ENV_KEY_SPECS 的 runtime 域）"""
    return _config_with_defaults(load_env_config(), "runtime")


def get_mumu_config():
    """从 config.env 获取模拟器配置（键集与默认值见 _ENV_KEY_SPECS 的 mumu 域）"""
    return _config_with_defaults(load_env_config(), "mumu")

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

