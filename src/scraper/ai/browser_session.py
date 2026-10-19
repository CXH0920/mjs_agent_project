"""DeepSeek 网页版的 Playwright 会话封装。"""

from __future__ import annotations

import logging
import time
import traceback
from typing import Any

from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from src.config.env import PROJECT_ROOT
from src.scraper.ai.utils import safe_url_origin

logger = logging.getLogger(__name__)

# 浏览器自动化访问 chat.deepseek.com（含隐藏自动化标记的启动参数）的披露
# 见 TERMS.md「自动化访问与访问频率」
DEFAULT_BROWSER_CONFIG: dict[str, Any] = {
    "channel": "msedge",
    "user_data_dir": str(PROJECT_ROOT / "data" / "edge_profile"),
    "headless": False,
    "slow_mo": 50,
    "args": ["--disable-blink-features=AutomationControlled"],
}

DEFAULT_CHAT_CONFIG: dict[str, Any] = {
    "url": "https://chat.deepseek.com/",
    "input_selector": "textarea[placeholder*='DeepSeek']",
    "assistant_selector": "div.ds-assistant-message-main-content",
    "content_class": "",
    "login_timeout": 120000,
    "response_timeout": 180000,
}


class DeepSeekBrowserSession:
    """管理 DeepSeek 页面及一轮消息的发送、等待和提取。"""

    def __init__(
        self,
        browser_config: dict | None = None,
        chat_config: dict | None = None,
    ) -> None:
        self._browser_cfg = {**DEFAULT_BROWSER_CONFIG, **(browser_config or {})}
        self._chat_cfg = {**DEFAULT_CHAT_CONFIG, **(chat_config or {})}
        self._playwright = None
        self._context = None
        self._page: Page | None = None
        self._started = False

        logger.info("  Edge 用户数据目录已配置")
        logger.info("  DeepSeek URL: %s", safe_url_origin(self._chat_cfg["url"]))
        logger.info("  输入框选择器: %s", self._chat_cfg.get("input_selector"))
        logger.info("  回复选择器: %s", self._chat_cfg.get("assistant_selector"))

    def close(self) -> None:
        """关闭浏览器上下文和 Playwright。"""
        logger.info("[DeepSeekBrowserSession] 关闭浏览器...")
        if self._context:
            try:
                self._context.close()
                logger.debug("浏览器上下文已关闭")
            except Exception as exc:
                logger.error("关闭浏览器上下文异常: %s", exc)
                logger.debug(traceback.format_exc())
            self._context = None
        if self._playwright:
            try:
                self._playwright.stop()
                logger.debug("Playwright 已停止")
            except Exception as exc:
                logger.error("停止 Playwright 异常: %s", exc)
                logger.debug(traceback.format_exc())
            self._playwright = None
        self._page = None
        self._started = False
        logger.info("[DeepSeekBrowserSession] 浏览器已关闭")

    def send_and_wait(self, prompt: str) -> str | None:
        """发送 prompt，等待流式回复稳定后返回最后一条消息。"""
        self._ensure_browser()
        page = self._page
        if page is None:
            raise RuntimeError("浏览器页面未初始化")
        input_selector = self._chat_cfg.get("input_selector", "textarea")
        logger.info("[发送] 填入 prompt（%d 字符）...", len(prompt))

        try:
            page.fill(input_selector, prompt)
        except Exception as exc:
            logger.error("[发送] 填入 prompt 失败: %s", exc)
            logger.debug(traceback.format_exc())
            self._invalidate_page("填入 prompt 失败")
            return None

        logger.info("[发送] 按 Enter 发送...")
        try:
            page.keyboard.press("Enter")
            logger.info("[发送] 已发送")
        except Exception as exc:
            logger.error("[发送] Enter 键操作失败: %s", exc)
            logger.debug(traceback.format_exc())
            self._invalidate_page("Enter 键操作失败")
            return None

        assistant_selector = self._chat_cfg.get(
            "assistant_selector", "div.ds-assistant-message-main-content",
        )
        timeout = self._chat_cfg.get("response_timeout", 180000)
        logger.info("[等待] 等待 AI 开始回复（超时 %d ms）...", timeout)

        before_count = 0
        try:
            before_count = page.locator(assistant_selector).count()
            logger.debug("[等待] 发送前 assistant 消息数: %d", before_count)
        except Exception as exc:
            logger.warning("[等待] 获取初始消息数失败: %s", exc)

        if not self._wait_for_new_message(page, assistant_selector, before_count, timeout):
            return None

        if not self._wait_until_stable(page, assistant_selector, timeout):
            return None

        logger.info("[提取] 开始提取最后一条回复...")
        return self._extract_last_reply(page, assistant_selector)

    def _wait_for_new_message(
        self, page: Page, assistant_selector: str, before_count: int, timeout: int,
    ) -> bool:
        """等待出现新回复气泡；超时则标记页面需重建并返回 False。

        与内容稳定阶段各用独立超时预算：共用同一 deadline 时，首段耗时接近
        上限会让次段几乎没有剩余时间，直接判超时。
        """
        deadline = time.time() + timeout / 1000
        while time.time() < deadline:
            try:
                current = page.locator(assistant_selector).count()
                if current > before_count:
                    logger.info("[等待] 检测到新回复开始（%d → %d）", before_count, current)
                    return True
            except Exception as exc:
                logger.warning("[等待] 检测消息数异常: %s", exc)
            page.wait_for_timeout(500)
        logger.error("[等待] 回复超时（%d ms），未检测到新消息", timeout)
        self._page_diagnostics()
        self._invalidate_page("未检测到新回复")
        return False

    def _wait_until_stable(self, page: Page, assistant_selector: str, timeout: int) -> bool:
        """等待回复内容停止增长（连续 3 轮长度不变）；超时返回 False。"""
        deadline = time.time() + timeout / 1000
        last_len = -1
        stable_rounds = 0
        logger.info("[等待] 等待内容生成完毕...")

        while time.time() < deadline:
            try:
                messages = page.locator(assistant_selector)
                current = messages.nth(messages.count() - 1)
                text = current.text_content()
                text_len = len(text)

                if text_len == last_len and text_len > 0:
                    stable_rounds += 1
                    logger.debug("[等待] 内容稳定 %d/3（%d 字符）", stable_rounds, text_len)
                    if stable_rounds >= 3:
                        logger.info("[等待] 回复生成完毕（共 %d 字符）", text_len)
                        page.wait_for_timeout(1000)
                        return True
                elif text_len > last_len:
                    stable_rounds = 0
                    last_len = text_len
                    logger.debug("[等待] 内容增长中...（当前 %d 字符）", text_len)
                else:
                    stable_rounds = 0
                    logger.debug("[等待] 内容长度未变化（%d）, 继续等待", text_len)
            except Exception as exc:
                logger.warning("[等待] 轮询内容异常: %s", exc)
                logger.debug(traceback.format_exc())
            page.wait_for_timeout(2000)
        logger.error("[等待] 回复内容稳定超时")
        self._invalidate_page("回复内容未稳定")
        return False

    def _extract_last_reply(self, page: Page, assistant_selector: str) -> str | None:
        """提取最后一条 assistant 消息文本；失败时标记页面需重建。"""
        try:
            messages = page.locator(assistant_selector)
            count = messages.count()
            logger.debug("[提取] 共有 %d 条 assistant 消息", count)
            if count == 0:
                logger.error("[提取] 未找到任何 AI 回复消息（选择器: %s）", assistant_selector)
                self._invalidate_page("未找到回复消息")
                return None

            last_message = messages.nth(count - 1)
            content_class = self._chat_cfg.get("content_class", "")
            if content_class:
                reply = last_message.locator(content_class).text_content()
                logger.debug("[提取] 使用内容选择器: %s", content_class)
            else:
                reply = last_message.text_content()
                logger.debug("[提取] 直接取元素文本")

            logger.info("[提取] 成功（%d 字符）", len(reply))
            return reply
        except Exception as exc:
            logger.error("[提取] 提取回复时出错: %s", exc)
            logger.debug(traceback.format_exc())
            self._invalidate_page("提取回复出错")
            return None

    def _invalidate_page(self, reason: str) -> None:
        """标记当前页面不可再用，下次 send_and_wait 会重建浏览器上下文。

        超时或选择器失效后页面可能停在验证码/登录失效页，复用会让后续每个
        请求都等满超时并级联失败。不在此处立即 close（会连带关闭用户正在
        观看的窗口），只置标志，由 _ensure_browser 在下一次调用时重建。
        """
        if not self._started:
            return
        logger.warning("[恢复] 页面状态异常（%s），下次请求将重建浏览器上下文", reason)
        self._started = False

    def _ensure_browser(self) -> None:
        """惰性启动 Edge 并等待 DeepSeek 输入框可用。"""
        if self._started and self._page:
            logger.debug("浏览器已就绪，复用现有页面")
            return

        # 上一轮被 _invalidate_page 标记失效（超时/选择器失效）：先释放旧上下文，
        # 否则覆盖 _playwright/_context 会泄漏 Edge 进程并继续占用 user_data_dir 锁。
        if self._playwright or self._context:
            logger.info("[恢复] 释放失效的浏览器上下文后重建...")
            self.close()

        logger.info("[浏览器] 正在启动 Edge...")
        logger.info("[浏览器] 用户数据目录已配置")
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
        except Exception as exc:
            error_msg = str(exc).lower()
            logger.error("[浏览器] 启动失败: %s", exc)
            logger.debug(traceback.format_exc())
            if "user data" in error_msg or "locked" in error_msg:
                logger.error("→ Edge 用户数据目录被锁定。请完全关闭所有 Edge 窗口后重试。")
            elif "non-default" in error_msg or "user-data-dir" in error_msg:
                logger.error("→ Edge 要求使用非默认用户数据目录。请勿将 user_data_dir 指向 Edge 默认的 User Data 路径。")
            elif "channel" in error_msg or "executable" in error_msg:
                logger.error("→ 未找到 Edge 浏览器。请确认已安装 Microsoft Edge。")
            self.close()
            raise

        # 导航/登录等待失败时同样要清理已创建的 Playwright 实例，否则 Edge 进程与
        # user_data_dir 锁残留，下次启动报 locked（launch 失败分支已有 close，此处分担）
        logger.info("[浏览器] 正在导航到 %s ...", safe_url_origin(self._chat_cfg["url"]))
        try:
            self._page.goto(self._chat_cfg["url"])
            logger.info("[浏览器] 页面加载完成: %s", self._page.title())

            if not self._wait_for_login():
                raise RuntimeError("DeepSeek 登录超时，请先手动登录")
        except Exception:
            self.close()
            raise

        self._started = True
        logger.info("[DeepSeekBrowserSession] 浏览器就绪")

    def _wait_for_login(self) -> bool:
        """等待输入框出现，以确认登录完成。"""
        if self._page is None:
            return False
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
            logger.warning("[登录] 当前页面 URL: %s", safe_url_origin(self._page.url))
            self._page_diagnostics()
            return False

    def _page_diagnostics(self) -> None:
        """输出页面结构诊断信息，用于选择器失效时排查。"""
        if self._page is None:
            return
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
                return results.join('\n');
            }""")
            logger.warning("页面元素:\n%s", info)
        except Exception as exc:
            logger.warning("页面诊断执行失败: %s", exc)
        logger.warning("========== 页面诊断结束 ==========")
