"""
名将杀 Agent - Playwright 浏览器自动化生成器

基于 Playwright + Edge 连接 DeepSeek 网页版，实现消息自动发送、
流式回复等待、内容提取与结构化存储。

实现与 AIBatchGenerator 相同的 generate_guide / generate_synergy 接口。

=== ETL 数据流 ===
浏览器 AI 回复(原始 HTML/文本)
  │ 1. Extract
  ▼
_send_and_wait() → 原始回复文本(str)
  │ 2. Transform
  ▼
_extract_json() → 解析为 Python dict
_convert_ids_to_int() → 字段类型转换
inject hero_id / hero_a_id / hero_b_id
  │ 3. Load
  ▼
_validate_guide() / _validate_synergy() → Pydantic 校验
  ▼
返回校验通过的 dict → _save_json() → JSON 文件
"""

from __future__ import annotations

import logging
import random
import time
from src.config.env import BUNDLE_ROOT
from src.scraper.ai.browser_session import DeepSeekBrowserSession
from src.scraper.ai.prompt_utils import (
    load_prompt,
    build_guide_prompt,
    build_synergy_prompt,
)
from src.scraper.ai.json_extract import extract_json
from src.scraper.ai.utils import (
    convert_ids_to_int,
    has_required_guide_fields,
    has_required_synergy_fields,
    validate_guide,
    validate_synergy,
)

logger = logging.getLogger(__name__)

PROMPT_DIR = BUNDLE_ROOT / "docs" / "prompts"
GUIDE_PROMPT_FILE = PROMPT_DIR / "hero_guide.md"
SYNERGY_PROMPT_FILE = PROMPT_DIR / "synergy_score.md"


def _browser_rag_max_chars() -> int:
    """浏览器模式 RAG 语料预算（config.env RAG_BROWSER_PROMPT_CHARS，默认 3000）。"""
    try:
        from src.rag import config as rag_config
        return int(rag_config.RAG_BROWSER_PROMPT_CHARS)
    except Exception as error:
        logger.warning("RAG_BROWSER_PROMPT_CHARS 读取失败，回退默认 3000: %s", error)
        return 3000


# 网页版长会话中格式指令遵循度会衰减：把简短输出格式要求放在消息末尾，
# 利用“最近指令权重最高”的特性稳住 JSON 输出结构
GUIDE_FORMAT_REMINDER = (
    "【输出格式重申】请严格按最开始的要求输出：先攻略正文，再单独一行 --- 分隔，"
    "最后输出 JSON（字段与最开始要求一致），不要输出任何额外说明。"
)
SYNERGY_FORMAT_REMINDER = (
    "【输出格式重申】请严格按最开始的要求输出 JSON（字段与最开始要求一致），"
    "不要输出任何额外说明。"
)


# JSON 提取失败时发送的纠正消息（相当于代码版“重新生成”）
GUIDE_RETRY_PROMPT = (
    "你上一条回复没有按要求输出。请重新输出：先攻略正文，再单独一行 --- 分隔，"
    "最后输出 JSON，字段与最开始要求一致，不要输出任何额外说明。"
)
SYNERGY_RETRY_PROMPT = (
    "你上一条回复没有按要求输出。请重新输出 JSON，字段与最开始要求一致，"
    "不要输出任何额外说明。"
)

class PlaywrightGenerator:
    """基于 Playwright + Edge 浏览器自动化的 AI 生成器"""

    def __init__(
        self,
        browser_config: dict | None = None,
        chat_config: dict | None = None,
    ):
        self._session = DeepSeekBrowserSession(browser_config, chat_config)
        # 控制 system prompt 只发一次
        self._guide_rest_required = False
        self._synergy_rest_required = False

        logger.info("[PlaywrightGenerator] 初始化完成")

    # ---------------------------------------------------------------
    # 公开接口（与 AIBatchGenerator 保持一致）
    # ---------------------------------------------------------------

    def generate_guide(self, hero: dict) -> tuple[dict | None, dict | None]:
        """为单个武将生成攻略（浏览器模式不返回 usage）

        每个武将都发送完整 system prompt + 武将数据（与 API 模式对齐），
        避免网页版会话中格式指令衰减。
        每次成功生成后，在下一次请求前随机休息 60-180 秒。
        """
        hero_name = hero.get("name", "?")
        hero_id = hero.get("id", 0)
        logger.info("[攻略] 开始生成: %s (id=%s)", hero_name, hero_id)

        if self._guide_rest_required:
            self._random_rest()
            self._guide_rest_required = False

        # 每个武将都重发完整 system prompt + 数据（与 API 模式对齐），
        # 避免网页版会话中首轮格式指令在后续轮次衰减导致输出偏离 JSON
        system_prompt = load_prompt(GUIDE_PROMPT_FILE)
        if not system_prompt:
            logger.error("[攻略] prompt 模板未找到: %s", GUIDE_PROMPT_FILE)
            return None, None

        user_prompt = build_guide_prompt(hero, rag_max_chars=_browser_rag_max_chars())
        full_prompt = f"{system_prompt}\n\n---\n\n{user_prompt}\n\n{GUIDE_FORMAT_REMINDER}"
        logger.info("[攻略] 发送 system prompt + %s 数据（%d 字符）", hero_name, len(full_prompt))

        reply = self._send_and_wait(full_prompt)
        if not reply:
            logger.warning("[攻略] %s: 获取回复为空", hero_name)
            return None, None

        logger.info("[攻略] %s: 收到回复 %d 字符", hero_name, len(reply))

        try:
            raw = extract_json(reply)
            logger.info("[攻略] %s: JSON 提取成功, 字段: %s", hero_name, list(raw.keys()))
        except ValueError:
            logger.warning("[攻略] %s: JSON 提取失败，发送格式纠正消息重试", hero_name)
            retry_reply = self._send_and_wait(GUIDE_RETRY_PROMPT)
            if not retry_reply:
                logger.error("[攻略] %s: 纠正重试无回复", hero_name)
                return None, None
            try:
                raw = extract_json(retry_reply)
                logger.info("[攻略] %s: 纠正后 JSON 提取成功, 字段: %s", hero_name, list(raw.keys()))
            except ValueError:
                logger.error("[攻略] %s: 纠正后仍 JSON 提取失败（回复长度 %d）", hero_name, len(retry_reply))
                return None, None

        raw["hero_id"] = hero_id
        convert_ids_to_int(raw, ["synergizes_with"])
        if not has_required_guide_fields(raw):
            logger.warning("[攻略] %s: 必填字段缺失", hero_name)
            return None, None
        result = validate_guide(raw)
        if result is None:
            logger.error("[攻略] %s: Pydantic 校验失败", hero_name)
            logger.debug("[攻略] %s: 校验失败字段: %s", hero_name, sorted(raw))
            return None, None

        logger.info("[攻略] %s: 校验通过, 结果字段: %s", hero_name, list(result.keys()))

        self._guide_rest_required = True

        return result, None

    def generate_synergy(self, hero_a: dict, hero_b: dict) -> tuple[dict | None, dict | None]:
        """为武将对生成相性评分（浏览器模式不返回 usage）

        每对武将都发送完整 system prompt + 武将数据（与 API 模式对齐），
        避免网页版会话中格式指令衰减。
        """
        name_a = hero_a.get("name", "?")
        name_b = hero_b.get("name", "?")
        logger.info("[相性] 开始生成: %s <-> %s", name_a, name_b)

        if self._synergy_rest_required:
            self._random_rest()
            self._synergy_rest_required = False

        # 每对武将都重发完整 system prompt + 数据（与 API 模式对齐）
        system_prompt = load_prompt(SYNERGY_PROMPT_FILE)
        if not system_prompt:
            logger.error("[相性] prompt 模板未找到: %s", SYNERGY_PROMPT_FILE)
            return None, None

        user_prompt = build_synergy_prompt(hero_a, hero_b, rag_max_chars=_browser_rag_max_chars())
        full_prompt = f"{system_prompt}\n\n---\n\n{user_prompt}\n\n{SYNERGY_FORMAT_REMINDER}"
        logger.info("[相性] 发送 system prompt + %s <-> %s 数据（%d 字符）", name_a, name_b, len(full_prompt))

        reply = self._send_and_wait(full_prompt)
        if not reply:
            logger.warning("[相性] %s <-> %s: 获取回复为空", name_a, name_b)
            return None, None

        logger.info("[相性] %s <-> %s: 收到回复 %d 字符", name_a, name_b, len(reply))

        try:
            raw = extract_json(reply)
            logger.info("[相性] %s <-> %s: JSON 提取成功, 字段: %s",
                        name_a, name_b, list(raw.keys()))
        except ValueError:
            logger.warning("[相性] %s <-> %s: JSON 提取失败，发送格式纠正消息重试", name_a, name_b)
            retry_reply = self._send_and_wait(SYNERGY_RETRY_PROMPT)
            if not retry_reply:
                logger.error("[相性] %s <-> %s: 纠正重试无回复", name_a, name_b)
                return None, None
            try:
                raw = extract_json(retry_reply)
                logger.info("[相性] %s <-> %s: 纠正后 JSON 提取成功, 字段: %s", name_a, name_b, list(raw.keys()))
            except ValueError:
                logger.error("[相性] %s <-> %s: 纠正后仍 JSON 提取失败（回复长度 %d）", name_a, name_b, len(retry_reply))
                return None, None

        raw["hero_a_id"] = hero_a.get("id", 0)
        raw["hero_b_id"] = hero_b.get("id", 0)

        if "combat_synergy" in raw and "combo_ceiling" not in raw:
            logger.info("[相性] %s <-> %s: 兼容字段 combat_synergy → combo_ceiling",
                        name_a, name_b)
            raw["combo_ceiling"] = raw.pop("combat_synergy")

        if not has_required_synergy_fields(raw):
            logger.warning("[相性] %s <-> %s: 必填字段缺失", name_a, name_b)
            return None, None

        result = validate_synergy(raw)
        if result is None:
            logger.error("[相性] %s <-> %s: Pydantic 校验失败", name_a, name_b)
            logger.debug("[相性] %s <-> %s: 校验失败字段: %s", name_a, name_b, sorted(raw))
            return None, None

        logger.info("[相性] %s <-> %s: 校验通过, 评分 %s",
                    name_a, name_b, result.get("score", "?"))

        self._synergy_rest_required = True

        return result, None

    def _random_rest(self) -> None:
        """随机休息 60-180 秒，避免触发风控"""
        rest = random.randint(60, 180)
        # 进度窗口靠解析 stdout 的 [休息] 行展示冷却倒计时；
        # 子进程 root 日志级别为 WARNING，logger.info 到不了 stdout，必须 print
        print(f"  [休息] 随机休息 {rest} 秒...", flush=True)
        logger.info("[休息] 随机休息 %d 秒...", rest)
        time.sleep(rest)

    def close(self):
        """关闭浏览器上下文"""
        logger.info("[PlaywrightGenerator] 关闭浏览器...")
        self._session.close()
        self._guide_rest_required = False
        self._synergy_rest_required = False
        logger.info("[PlaywrightGenerator] 浏览器已关闭")

    def _send_and_wait(self, prompt: str) -> str | None:
        """兼容内部旧调用，委托浏览器会话完成发送和提取。"""
        return self._session.send_and_wait(prompt)
