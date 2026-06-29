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

import json
import logging
import random
import re
import time
import traceback
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout, sync_playwright

from src.scraper.ai_utils import (
    load_prompt,
    extract_json,
    convert_ids_to_int,
    validate_guide,
    validate_synergy,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PROMPT_DIR = PROJECT_ROOT / "docs" / "prompts"
GUIDE_PROMPT_FILE = PROMPT_DIR / "hero_guide.md"
SYNERGY_PROMPT_FILE = PROMPT_DIR / "synergy_score.md"

DEFAULT_BROWSER_CONFIG: dict[str, Any] = {
    "channel": "msedge",
    "user_data_dir": str(Path.home() / "AppData/Local/Microsoft/Edge/User Data"),
    "headless": False,
    "slow_mo": 50,
    "args": ["--disable-blink-features=AutomationControlled"],
}

DEFAULT_CHAT_CONFIG: dict[str, Any] = {
    "url": "https://chat.deepseek.com/",
    "input_selector": "textarea[placeholder*='DeepSeek']",
    "assistant_selector": "div.ds-assistant-message-main-content",
    "content_class": "",
    "login_timeout": 15000,
    "response_timeout": 180000,
}


class PlaywrightGenerator:
    """基于 Playwright + Edge 浏览器自动化的 AI 生成器"""

    def __init__(
        self,
        browser_config: dict | None = None,
        chat_config: dict | None = None,
    ):
        self._browser_cfg = {**DEFAULT_BROWSER_CONFIG, **(browser_config or {})}
        self._chat_cfg = {**DEFAULT_CHAT_CONFIG, **(chat_config or {})}
        self._playwright = None
        self._context = None
        self._page: Page | None = None
        self._started = False
        # 控制 system prompt 只发一次
        self._guide_system_sent = False
        self._synergy_system_sent = False

        logger.info("[PlaywrightGenerator] 初始化完成")
        logger.info("  Edge 用户数据目录: %s", self._browser_cfg["user_data_dir"])
        logger.info("  DeepSeek URL: %s", self._chat_cfg["url"])
        logger.info("  输入框选择器: %s", self._chat_cfg.get("input_selector"))
        logger.info("  回复选择器: %s", self._chat_cfg.get("assistant_selector"))

    # ---------------------------------------------------------------
    # 公开接口（与 AIBatchGenerator 保持一致）
    # ---------------------------------------------------------------

    def generate_guide(self, hero: dict) -> tuple[dict | None, dict | None]:
        """为单个武将生成攻略（浏览器模式不返回 usage）

        第一次调用发送 system prompt + 武将数据，后续只发送武将数据（带 ID），
        让 AI 在同一会话中根据已设定的规则持续生成。
        后续调用每次生成完成后随机休息 60-180 秒。
        """
        hero_name = hero.get("name", "?")
        hero_id = hero.get("id", 0)
        logger.info("[攻略] 开始生成: %s (id=%s)", hero_name, hero_id)

        is_first_call = not self._guide_system_sent

        if is_first_call:
            # 第一次：加载 system prompt 并与第一条数据拼接发送
            system_prompt = load_prompt(GUIDE_PROMPT_FILE)
            if not system_prompt:
                logger.error("[攻略] prompt 模板未找到: %s", GUIDE_PROMPT_FILE)
                return None, None

            user_prompt = self._build_guide_prompt(hero)
            full_prompt = f"{system_prompt}\n\n---\n\n{user_prompt}"
            logger.info("[攻略] 首次发送 system prompt + %s 数据", hero_name)
            logger.debug("[攻略] 首次发送总长度: %d 字符", len(full_prompt))

            reply = self._send_and_wait(full_prompt)
            if not reply:
                logger.warning("[攻略] %s: 首次发送获取回复为空", hero_name)
                return None, None

            self._guide_system_sent = True
            logger.info("[攻略] system prompt 已发送，后续只发武将数据")
        else:
            # 后续：只发武将数据（带 ID）
            data_prompt = self._build_guide_prompt(hero)
            logger.info("[攻略] 发送 %s 数据（%d 字符）", hero_name, len(data_prompt))
            reply = self._send_and_wait(data_prompt)
            if not reply:
                logger.warning("[攻略] %s: 获取回复为空", hero_name)
                return None, None

        logger.info("[攻略] %s: 原始回复 %d 字符", hero_name, len(reply))
        logger.debug("[攻略] %s: 原始回复前200字:\n%s", hero_name, reply[:200])

        try:
            raw = extract_json(reply)
            logger.info("[攻略] %s: JSON 提取成功, 字段: %s", hero_name, list(raw.keys()))
        except ValueError:
            logger.error("[攻略] %s: JSON 提取失败", hero_name)
            logger.error("[攻略] %s: 原始回复全文(%d字符):\n%s",
                         hero_name, len(reply), reply)
            return None, None

        raw["hero_id"] = hero_id
        convert_ids_to_int(raw, ["counters", "synergizes_with"])
        logger.debug("[攻略] %s: 提取后的原始数据:\n%s", hero_name,
                     json.dumps(raw, ensure_ascii=False, indent=2))

        result = validate_guide(raw)
        if result is None:
            logger.error("[攻略] %s: Pydantic 校验失败", hero_name)
            logger.error("[攻略] %s: 完整原始数据:\n%s", hero_name,
                         json.dumps(raw, ensure_ascii=False, indent=2))
            return None, None

        logger.info("[攻略] %s: 校验通过, 结果字段: %s", hero_name, list(result.keys()))

        # 后续调用（非首次）每次执行完成后随机休息 60-180 秒
        if not is_first_call:
            self._random_rest()

        return result, None

    def generate_synergy(self, hero_a: dict, hero_b: dict) -> tuple[dict | None, dict | None]:
        """为武将对生成相性评分（浏览器模式不返回 usage）

        第一次调用发送 system prompt + 第一对数据，后续只发送武将数据（带 ID）。
        后续调用每次生成完成后随机休息 60-180 秒。
        """
        name_a = hero_a.get("name", "?")
        name_b = hero_b.get("name", "?")
        logger.info("[相性] 开始生成: %s <-> %s", name_a, name_b)

        is_first_call = not self._synergy_system_sent

        if is_first_call:
            # 第一次：system prompt + 第一对数据
            system_prompt = load_prompt(SYNERGY_PROMPT_FILE)
            if not system_prompt:
                logger.error("[相性] prompt 模板未找到: %s", SYNERGY_PROMPT_FILE)
                return None, None

            user_prompt = self._build_synergy_prompt(hero_a, hero_b)
            full_prompt = f"{system_prompt}\n\n---\n\n{user_prompt}"
            logger.info("[相性] 首次发送 system prompt + %s <-> %s 数据", name_a, name_b)
            logger.debug("[相性] 首次发送总长度: %d 字符", len(full_prompt))

            reply = self._send_and_wait(full_prompt)
            if not reply:
                logger.warning("[相性] 首次发送获取回复为空")
                return None, None

            self._synergy_system_sent = True
            logger.info("[相性] system prompt 已发送，后续只发武将数据")
        else:
            # 后续：只发武将数据（带 ID）
            data_prompt = self._build_synergy_prompt(hero_a, hero_b)
            logger.info("[相性] 发送 %s <-> %s 数据（%d 字符）",
                        name_a, name_b, len(data_prompt))
            reply = self._send_and_wait(data_prompt)
            if not reply:
                logger.warning("[相性] %s <-> %s: 获取回复为空", name_a, name_b)
                return None, None

        logger.info("[相性] %s <-> %s: 原始回复 %d 字符", name_a, name_b, len(reply))
        logger.debug("[相性] %s <-> %s: 原始回复前200字:\n%s",
                     name_a, name_b, reply[:200])

        try:
            raw = extract_json(reply)
            logger.info("[相性] %s <-> %s: JSON 提取成功, 字段: %s",
                        name_a, name_b, list(raw.keys()))
        except ValueError:
            logger.error("[相性] %s <-> %s: JSON 提取失败", name_a, name_b)
            logger.error("[相性] %s <-> %s: 原始回复全文(%d字符):\n%s",
                         name_a, name_b, len(reply), reply)
            return None, None

        raw["hero_a_id"] = hero_a.get("id", 0)
        raw["hero_b_id"] = hero_b.get("id", 0)

        if "combat_synergy" in raw and "combo_ceiling" not in raw:
            logger.info("[相性] %s <-> %s: 兼容字段 combat_synergy → combo_ceiling",
                        name_a, name_b)
            raw["combo_ceiling"] = raw.pop("combat_synergy")

        logger.debug("[相性] %s <-> %s: 提取后的原始数据:\n%s",
                     name_a, name_b, json.dumps(raw, ensure_ascii=False, indent=2))

        result = validate_synergy(raw)
        if result is None:
            logger.error("[相性] %s <-> %s: Pydantic 校验失败", name_a, name_b)
            logger.error("[相性] %s <-> %s: 完整原始数据:\n%s",
                         name_a, name_b, json.dumps(raw, ensure_ascii=False, indent=2))
            return None, None

        logger.info("[相性] %s <-> %s: 校验通过, 评分 %s",
                    name_a, name_b, result.get("score", "?"))

        # 后续调用（非首次）每次执行完成后随机休息 60-180 秒
        if not is_first_call:
            self._random_rest()

        return result, None

    def _random_rest(self) -> None:
        """随机休息 60-180 秒，避免触发风控"""
        rest = random.randint(60, 180)
        logger.info("[休息] 随机休息 %d 秒...", rest)
        time.sleep(rest)

    def close(self):
        """关闭浏览器上下文"""
        logger.info("[PlaywrightGenerator] 关闭浏览器...")
        if self._context:
            try:
                self._context.close()
                logger.debug("浏览器上下文已关闭")
            except Exception as e:
                logger.error("关闭浏览器上下文异常: %s", e)
                logger.debug(traceback.format_exc())
            self._context = None
        if self._playwright:
            try:
                self._playwright.stop()
                logger.debug("Playwright 已停止")
            except Exception as e:
                logger.error("停止 Playwright 异常: %s", e)
                logger.debug(traceback.format_exc())
            self._playwright = None
        self._page = None
        self._started = False
        self._guide_system_sent = False
        self._synergy_system_sent = False
        logger.info("[PlaywrightGenerator] 浏览器已关闭")

    # ---------------------------------------------------------------
    # 浏览器生命周期
    # ---------------------------------------------------------------

    def _ensure_browser(self) -> None:
        """惰性启动 Edge 浏览器"""
        if self._started and self._page:
            logger.debug("浏览器已就绪，复用现有页面")
            return

        logger.info("[浏览器] 正在启动 Edge...")
        logger.info("[浏览器] 用户数据目录: %s", self._browser_cfg["user_data_dir"])
        logger.info("[浏览器] 请确保已完全关闭所有 Edge 窗口")

        self._playwright = sync_playwright().start()
        try:
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=self._browser_cfg["user_data_dir"],
                channel=self._browser_cfg.get("channel", "msedge"),
                headless=self._browser_cfg.get("headless", False),
                slow_mo=self._browser_cfg.get("slow_mo", 50),
                args=self._browser_cfg.get("args", []),
            )
            self._page = self._context.new_page()
            logger.info("[浏览器] Edge 启动成功")
        except Exception as e:
            error_msg = str(e).lower()
            logger.error("[浏览器] 启动失败: %s", e)
            logger.debug(traceback.format_exc())
            if "user data" in error_msg or "locked" in error_msg:
                logger.error("→ Edge 用户数据目录被锁定。请完全关闭所有 Edge 窗口后重试。")
            elif "channel" in error_msg or "executable" in error_msg:
                logger.error("→ 未找到 Edge 浏览器。请确认已安装 Microsoft Edge。")
            self.close()
            raise

        # 导航到 DeepSeek
        logger.info("[浏览器] 正在导航到 %s ...", self._chat_cfg["url"])
        self._page.goto(self._chat_cfg["url"])
        logger.info("[浏览器] 页面加载完成: %s", self._page.title())

        if not self._wait_for_login():
            raise RuntimeError("DeepSeek 登录超时，请先手动登录")

        self._started = True
        logger.info("[PlaywrightGenerator] 浏览器就绪，可以开始生成")

    def _wait_for_login(self) -> bool:
        """等待输入框出现（确认已登录）"""
        selector = self._chat_cfg.get("input_selector", "textarea")
        timeout = self._chat_cfg.get("login_timeout", 15000)
        logger.info("[登录] 等待输入框 %s ...", selector)
        try:
            self._page.wait_for_selector(selector, timeout=timeout)
            logger.info("[登录] 成功，输入框可用")
            return True
        except PlaywrightTimeout:
            logger.warning("[登录] 超时（%d ms），可能未登录或遇到验证码", timeout)
            logger.warning("[登录] 当前页面标题: %s", self._page.title())
            logger.warning("[登录] 当前页面 URL: %s", self._page.url)
            # 输出页面诊断信息
            self._page_diagnostics()
            return False

    def _page_diagnostics(self) -> None:
        """输出页面结构诊断信息，用于选择器失效时排查"""
        logger.warning("========== 页面诊断开始 ==========")
        try:
            info = self._page.evaluate("""() => {
                const results = [];
                document.querySelectorAll('textarea').forEach(el => {
                    results.push('textarea: id=' + (el.id||'none') +
                        ' name=' + (el.name||'') +
                        ' placeholder=' + (el.placeholder||'') +
                        ' class=' + (el.className||'').substring(0,60));
                });
                document.querySelectorAll('[contenteditable]').forEach(el => {
                    results.push('contenteditable: tag=' + el.tagName +
                        ' id=' + (el.id||'none') +
                        ' placeholder=' + (el.getAttribute('placeholder')||''));
                });
                results.push('--- buttons ---');
                document.querySelectorAll('button').forEach(el => {
                    const r = el.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0)
                        results.push('button: text=' + (el.textContent||'').trim().substring(0,40));
                });
                results.push('--- body text preview ---');
                results.push((document.body ? document.body.textContent : '').trim().substring(0,300));
                return results.join('\\n');
            }""")
            logger.warning("页面元素:\n%s", info)
        except Exception as e:
            logger.warning("页面诊断执行失败: %s", e)
        logger.warning("========== 页面诊断结束 ==========")

    # ---------------------------------------------------------------
    # 消息发送与回复提取 (ETL Step 1: Extract)
    # ---------------------------------------------------------------

    def _send_and_wait(self, prompt: str) -> str | None:
        """发送 prompt 并等待回复完成

        === ETL Step 1: Extract ===
        输入: 拼接好的全量 prompt
        输出: AI 回复的原始文本（含分析内容 + --- 分隔线 + JSON 代码块）
        """
        self._ensure_browser()
        page = self._page
        cfg = self._chat_cfg
        input_selector = cfg.get("input_selector", "textarea")
        logger.info("[发送] 填入 prompt（%d 字符）...", len(prompt))

        try:
            page.fill(input_selector, prompt)
        except Exception as e:
            logger.error("[发送] 填入 prompt 失败: %s", e)
            logger.debug(traceback.format_exc())
            return None

        # 按 Enter 发送
        logger.info("[发送] 按 Enter 发送...")
        try:
            page.keyboard.press("Enter")
            logger.info("[发送] 已发送")
        except Exception as e:
            logger.error("[发送] Enter 键操作失败: %s", e)
            logger.debug(traceback.format_exc())
            return None

        # ----------------------------------------
        # 等待回复开始（检测新的 assistant 消息）
        # ----------------------------------------
        assistant_selector = cfg.get("assistant_selector", "div.ds-assistant-message-main-content")
        timeout = cfg.get("response_timeout", 180000)
        deadline = time.time() + timeout / 1000
        logger.info("[等待] 等待 AI 开始回复（超时 %d ms）...", timeout)

        before_count = 0
        try:
            before_count = page.locator(assistant_selector).count()
            logger.debug("[等待] 发送前 assistant 消息数: %d", before_count)
        except Exception as e:
            logger.warning("[等待] 获取初始消息数失败: %s", e)

        while time.time() < deadline:
            try:
                current = page.locator(assistant_selector).count()
                if current > before_count:
                    logger.info("[等待] 检测到新回复开始（%d → %d）", before_count, current)
                    break
            except Exception as e:
                logger.warning("[等待] 检测消息数异常: %s", e)

            page.wait_for_timeout(500)
        else:
            logger.error("[等待] 回复超时（%d ms），未检测到新消息", timeout)
            self._page_diagnostics()
            return None

        # ----------------------------------------
        # 等待内容稳定（轮询长度不再增长）
        # ----------------------------------------
        last_len = -1
        stable_rounds = 0
        STABLE_THRESHOLD = 3
        CHECK_INTERVAL = 2
        logger.info("[等待] 等待内容生成完毕...")

        while time.time() < deadline:
            try:
                msgs = page.locator(assistant_selector)
                current = msgs.nth(msgs.count() - 1)
                text = current.inner_text()
                text_len = len(text)

                if text_len == last_len and text_len > 0:
                    stable_rounds += 1
                    logger.debug("[等待] 内容稳定 %d/3（%d 字符）", stable_rounds, text_len)
                    if stable_rounds >= STABLE_THRESHOLD:
                        logger.info("[等待] 回复生成完毕（共 %d 字符）", text_len)
                        page.wait_for_timeout(1000)
                        break
                elif text_len > last_len:
                    stable_rounds = 0
                    last_len = text_len
                    logger.debug("[等待] 内容增长中...（当前 %d 字符）", text_len)
                else:
                    stable_rounds = 0
                    logger.debug("[等待] 内容长度未变化（%d）, 继续等待", text_len)
            except Exception as e:
                logger.warning("[等待] 轮询内容异常: %s", e)
                logger.debug(traceback.format_exc())

            page.wait_for_timeout(CHECK_INTERVAL * 1000)
        else:
            logger.error("[等待] 回复内容稳定超时")
            return None

        # ----------------------------------------
        # 提取最后一条回复
        # ----------------------------------------
        logger.info("[提取] 开始提取最后一条回复...")
        try:
            msgs = page.locator(assistant_selector)
            count = msgs.count()
            logger.debug("[提取] 共有 %d 条 assistant 消息", count)
            if count == 0:
                logger.error("[提取] 未找到任何 AI 回复消息（选择器: %s）", assistant_selector)
                return None

            last_msg = msgs.nth(count - 1)
            content_class = cfg.get("content_class", "")
            if content_class:
                reply = last_msg.locator(content_class).inner_text()
                logger.debug("[提取] 使用内容选择器: %s", content_class)
            else:
                reply = last_msg.inner_text()
                logger.debug("[提取] 直接取元素文本")

            logger.info("[提取] 成功（%d 字符）", len(reply))
            return reply
        except Exception as e:
            logger.error("[提取] 提取回复时出错: %s", e)
            logger.debug(traceback.format_exc())
            return None

    @staticmethod
    def _build_guide_prompt(hero: dict) -> str:
        """构建单个武将的攻略 prompt（始终包含武将 ID）"""
        lines = [f"武将ID: {hero.get('id', 0)}"]
        lines.append(f"武将: {hero.get('name', '')}")
        lines.append(f"势力: {hero.get('faction', '')}")
        lines.append(f"定位: {hero.get('position', '')}")
        lines.append(f"体力: {hero.get('max_hp', 4)}  手牌: {hero.get('max_hand', 4)}")
        lines.append(f"性别: {hero.get('gender', '男')}")
        lines.append(f"难度: {hero.get('difficulty', 2)}")
        if hero.get("skills"):
            lines.append("")
            lines.append("技能:")
            for sk in hero["skills"]:
                lines.append(f"  - {sk.get('name', '')}: {sk.get('description', '')}")
        return "\n".join(lines)

    @staticmethod
    def _build_synergy_prompt(hero_a: dict, hero_b: dict) -> str:
        """构建武将对的相性评分 prompt（始终包含武将 ID）"""
        def hero_block(label: str, h: dict) -> list[str]:
            lines = [f"## {label}: {h.get('name', '')} (ID={h.get('id', 0)})"]
            lines.append(f"  势力: {h.get('faction', '')}")
            lines.append(f"  定位: {h.get('position', '')}")
            lines.append(f"  体力/手牌: {h.get('max_hp', 4)}/{h.get('max_hand', 4)}")
            if h.get("skills"):
                lines.append("  技能:")
                for sk in h["skills"]:
                    lines.append(f"    - {sk.get('name', '')}: {sk.get('description', '')}")
            return lines

        lines = []
        lines.extend(hero_block("武将 A", hero_a))
        lines.append("")
        lines.extend(hero_block("武将 B", hero_b))
        return "\n".join(lines)

