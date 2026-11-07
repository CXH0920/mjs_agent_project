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
extract_json() → 解析为 Python dict
normalize_and_validate_guide/synergy() → 注 ID/ID 转换/兼容 shim/Pydantic 校验
  │ 3. Load
  ▼
返回校验通过的 dict → _save_json() → JSON 文件
"""

from __future__ import annotations

import logging
import random
import time

from src.config.env import BUNDLE_ROOT
from src.scraper.ai.browser_session import DeepSeekBrowserSession
from src.scraper.ai.json_extract import extract_json
from src.scraper.ai.prompt_utils import (
    build_guide_prompt,
    build_synergy_prompt,
    load_prompt,
)
from src.scraper.ai.utils import (
    normalize_and_validate_guide,
    normalize_and_validate_synergy,
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

    # 类级默认：object.__new__ 绕过构造器的实例（测试惯用法）也能读到未取消
    _cancelled: bool = False

    def __init__(
        self,
        browser_config: dict | None = None,
        chat_config: dict | None = None,
    ):
        self._session = DeepSeekBrowserSession(browser_config, chat_config)
        # 相邻生成的休息标志：上次已向站点发出请求即置位（见 _random_rest）
        self._guide_rest_required = False
        self._synergy_rest_required = False
        # 取消标志：与 AIBatchGenerator.cancel 同语义（置位后入口退出，不打断 in-flight）
        self._cancelled = False

        logger.info("[PlaywrightGenerator] 初始化完成")

    def cancel(self) -> None:
        """请求中断：下一次生成入口直接退出，进行中的休息等待提前结束。

        与 AIBatchGenerator.cancel 语义对齐（第 6 批审查 P3 接口漂移）——
        不打断进行中的页面等待（靠既有超时退出），置位后由 generate_* 入口
        与 _random_rest 的小步等待检查。
        """
        self._cancelled = True

    # ---------------------------------------------------------------
    # 公开接口（与 AIBatchGenerator 保持一致）
    # ---------------------------------------------------------------

    def generate_guide(self, hero: dict) -> tuple[dict | None, dict | None]:
        """为单个武将生成攻略（浏览器模式不返回 usage）

        每个武将都发送完整 system prompt + 武将数据（与 API 模式对齐），
        避免网页版会话中格式指令衰减。
        每次向站点发出请求后（无论成败），在下一次生成前随机休息 60-180 秒。
        """
        hero_name = hero.get("name", "?")
        hero_id = hero.get("id", 0)

        if self._cancelled:
            logger.info("[攻略] 生成已取消，跳过: %s", hero_name)
            return None, None
        logger.info("[攻略] 开始生成: %s (id=%s)", hero_name, hero_id)

        if self._guide_rest_required:
            self._random_rest()
            self._guide_rest_required = False
            if self._cancelled:
                logger.info("[攻略] 休息期间取消，跳过生成: %s", hero_name)
                return None, None

        # 每个武将都重发完整 system prompt + 数据（与 API 模式对齐），
        # 避免网页版会话中首轮格式指令在后续轮次衰减导致输出偏离 JSON
        system_prompt = load_prompt(GUIDE_PROMPT_FILE)
        if not system_prompt:
            logger.error("[攻略] prompt 模板未找到: %s", GUIDE_PROMPT_FILE)
            return None, None

        user_prompt = build_guide_prompt(hero, rag_max_chars=_browser_rag_max_chars())
        full_prompt = f"{system_prompt}\n\n---\n\n{user_prompt}\n\n{GUIDE_FORMAT_REMINDER}"
        logger.info("[攻略] 发送 system prompt + %s 数据（%d 字符）", hero_name, len(full_prompt))

        # 失败的生成同样消耗站点访问配额：置位以「已发出请求」为准而非成功，
        # 否则连续失败批次会退化为零间隔连发（TERMS 第 7 节披露的间隔与成败无关）
        self._guide_rest_required = True
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

        result = normalize_and_validate_guide(
            raw, hero, logger_prefix=f"[攻略] {hero_name}: ",
        )
        if result is None:
            return None, None

        logger.info("[攻略] %s: 校验通过, 结果字段: %s", hero_name, list(result.keys()))

        return result, None

    def generate_synergy(self, hero_a: dict, hero_b: dict) -> tuple[dict | None, dict | None]:
        """为武将对生成相性评分（浏览器模式不返回 usage）

        每对武将都发送完整 system prompt + 武将数据（与 API 模式对齐），
        避免网页版会话中格式指令衰减。
        """
        name_a = hero_a.get("name", "?")
        name_b = hero_b.get("name", "?")

        if self._cancelled:
            logger.info("[相性] 生成已取消，跳过: %s <-> %s", name_a, name_b)
            return None, None
        logger.info("[相性] 开始生成: %s <-> %s", name_a, name_b)

        if self._synergy_rest_required:
            self._random_rest()
            self._synergy_rest_required = False
            if self._cancelled:
                logger.info("[相性] 休息期间取消，跳过生成: %s <-> %s", name_a, name_b)
                return None, None

        # 每对武将都重发完整 system prompt + 数据（与 API 模式对齐）
        system_prompt = load_prompt(SYNERGY_PROMPT_FILE)
        if not system_prompt:
            logger.error("[相性] prompt 模板未找到: %s", SYNERGY_PROMPT_FILE)
            return None, None

        user_prompt = build_synergy_prompt(hero_a, hero_b, rag_max_chars=_browser_rag_max_chars())
        full_prompt = f"{system_prompt}\n\n---\n\n{user_prompt}\n\n{SYNERGY_FORMAT_REMINDER}"
        logger.info("[相性] 发送 system prompt + %s <-> %s 数据（%d 字符）", name_a, name_b, len(full_prompt))

        # 同 generate_guide：以「已发出请求」为准置位，失败链路不豁免休息
        self._synergy_rest_required = True
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

        result = normalize_and_validate_synergy(
            raw, hero_a, hero_b, logger_prefix=f"[相性] {name_a} <-> {name_b}: ",
        )
        if result is None:
            return None, None

        logger.info("[相性] %s <-> %s: 校验通过, 评分 %s",
                    name_a, name_b, result.get("score", "?"))

        return result, None

    def _random_rest(self) -> None:
        """相邻两次生成之间随机休息 60-180 秒，拉开请求间隔、降低访问密度。

        自动化访问与频率控制的完整披露见 TERMS.md「自动化访问与访问频率」。
        小步等待并在每步检查取消标志：用户主动中断后不再有下一次生成，
        提前结束等待不违背间隔披露（间隔约束的是相邻两次生成）。
        """
        rest = random.randint(60, 180)
        # 进度窗口靠解析 stdout 的 [休息] 行展示冷却倒计时；
        # 子进程 root 日志级别为 WARNING，logger.info 到不了 stdout，必须 print
        print(f"  [休息] 随机休息 {rest} 秒...", flush=True)
        logger.info("[休息] 随机休息 %d 秒...", rest)
        for _ in range(rest * 2):  # 0.5s 步进凑满 rest 秒
            if self._cancelled:
                logger.info("[休息] 已取消，提前结束等待")
                return
            time.sleep(0.5)

    def close(self):
        """关闭浏览器上下文"""
        logger.info("[PlaywrightGenerator] 关闭浏览器...")
        self._session.close()
        self._guide_rest_required = False
        self._synergy_rest_required = False
        self._cancelled = False
        logger.info("[PlaywrightGenerator] 浏览器已关闭")

    def _send_and_wait(self, prompt: str) -> str | None:
        """兼容内部旧调用，委托浏览器会话完成发送和提取。"""
        return self._session.send_and_wait(prompt)
