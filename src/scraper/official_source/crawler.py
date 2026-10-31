"""
名将杀 Agent - 爬虫核心模块

官网数据采集的出站链路：HTTP 抓取（重试 + robots 逐 URL 合规校验）、
全量数据获取编排与头像下载。字段清洗/映射/校验在 hero_fields.py，
本文件保留其兼容 re-export。被 official.py 和 incremental.py 共同使用。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
import urllib.robotparser
import warnings
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

from PIL import Image
from src.config.env import IMAGES_OUTPUT_DIR, PROJECT_ROOT
from src.data.json_repository import atomic_write_json, atomic_write_text
from src.scraper.official_source.adapter import (
    find_card_chunk_url,
    find_chunk_url,
    parse_cards_chunk,
    parse_heroes_chunk,
)
from src.scraper.official_source.hero_fields import (
    LOCAL_EXTENSION_FIELDS,
    carry_over_local_fields,
    clean_html,
    split_skill_desc,
    transform,
    validate_heroes,
)

# 重新导出：caller 长期经 crawler.* 调用（clean_html / carry_over_local_fields /
# split_skill_desc / transform / validate_heroes 实现均已拆至 hero_fields.py）
__all__ = [
    "LOCAL_EXTENSION_FIELDS",
    "carry_over_local_fields",
    "clean_html",
    "fetch",
    "fetch_all_cards_raw",
    "fetch_all_raw",
    "save_json_atomic",
    "split_skill_desc",
    "transform",
    "validate_heroes",
]

logger = logging.getLogger(__name__)

# ============================================================
# 常量
# ============================================================

BAIKE_URL = "https://mjs.ztgame.com/baike/"
SHOUPAIKU_URL = "https://mjs.ztgame.com/shoupaiku/"
BASE_URL = "https://mjs.ztgame.com"

# robots.txt：抓取后存档本地（logs/ 已 gitignore，24 小时内不重复请求）并
# 解析执行——每次抓取前按规则校验目标 URL，被 Disallow 的路径拒绝请求；
# robots 本身获取失败时降级放行并告警（见 _ensure_robots_txt_cached /
# _ensure_robots_allowed）。访问行为披露见 TERMS.md「自动化访问与访问频率」。
ROBOTS_URL = f"{BASE_URL}/robots.txt"
ROBOTS_CACHE_TTL_HOURS = 24
ROBOTS_CACHE_DIR = PROJECT_ROOT / "logs" / "robots_cache"

TIMEOUT = 30
# robots.txt 是百字节小文件，复用主数据 30 秒超时过于保守：站点异常时每个
# 采集子进程都要等满额才降级放行，短超时快速失败即降级
ROBOTS_FETCH_TIMEOUT = 5
# 头像 PNG 仅数百 KB，短于主数据的超时使 CDN 握手挂起时单张等待上限减半
IMAGE_DOWNLOAD_TIMEOUT = 15
MAX_RETRIES = 3
RETRY_DELAY = 2

# 不可重试的 HTTP 状态：404/权限类失败重试只会重复打同一请求
_NON_RETRYABLE_HTTP_CODES = frozenset({400, 401, 403, 404})

# 重定向状态码与跟随上限：自动重定向已禁用，每一跳的目标 URL 都重新过
# robots 校验后再手动跟随（站点 30x 到带 Disallow 规则的其他主机时不会
# 被静默抓取）
_REDIRECT_HTTP_CODES = frozenset({301, 302, 303, 307, 308})
MAX_FETCH_REDIRECTS = 3

ALLOWED_IMAGE_HOSTS = {"siteres.ztgame.com"}
MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 4_000_000
IMAGE_CHUNK_SIZE = 64 * 1024
# 头像逐张请求的间隔与连续失败熔断阈值：全量下载对源站瞬时连发易触发限流
IMAGE_DOWNLOAD_INTERVAL_SECONDS = 0.5
IMAGE_DOWNLOAD_MAX_CONSECUTIVE_FAILURES = 5
MAX_IMAGE_REDIRECTS = 3
SAFE_IMAGE_NAME_PATTERN = re.compile(r"^[\u3400-\u4dbf\u4e00-\u9fffA-Za-z0-9_-]{1,80}$")
WINDOWS_RESERVED_FILENAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# 官网采集以可识别的爬虫标识发起请求，不伪装成浏览器：站点 robots.txt 为
# 「User-agent: * / Allow: /」全站放行，如实标识既满足许可前提，也让运营方
# 在日志里能定位到本项目。自动化访问与频率控制的完整披露见 TERMS.md 第 7 节。
HEADERS = {
    "User-Agent": "mjs-agent/1.0 (+https://github.com/CXH0920/mjs_agent_project)"
}


# ============================================================
# 网络请求
# ============================================================


class RobotsDisallowedError(RuntimeError):
    """目标 URL 被站点 robots.txt 规则禁止抓取。"""


# robots 规则进程内缓存：源（scheme://host[:port]）→ 解析后的规则；
# None 表示规则不可得（robots 获取失败/无存档），按允许处理降级
_robots_rules_by_host: dict[str, urllib.robotparser.RobotFileParser | None] = {}


def _robots_origin(url: str) -> str | None:
    """返回 URL 的源（scheme://host[:port]，非默认端口才带端口）；非 http(s) 为 None。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    port = f":{parsed.port}" if parsed.port not in (None, 80, 443) else ""
    return f"{parsed.scheme}://{host}{port}"


def _warn_unsupported_rule_syntax(content: str) -> None:
    """检测 stdlib robotparser 不支持的规则语法并告警。

    robotparser 只做字面前缀匹配（首条命中即返回，非 RFC 9309 最长匹配）：
    通配符（整段 ``*`` 除外，其按「匹配全部」特判）与 ``$`` 行尾锚点都被当
    普通字符——站点一旦使用这些写法，校验会静默放行站点意图禁止的路径。
    命中即打 warning 请人工核对存档，方向是过放行时宁可提醒不可静默。
    """
    for line in content.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if not stripped:
            continue
        field, sep, value = stripped.partition(":")
        if not sep or field.strip().lower() not in ("disallow", "allow"):
            continue
        path = value.strip()
        if ("*" in path and path != "*") or "$" in path:
            logger.warning(
                "robots.txt 规则 %r 含通配符/$ 锚点，标准库解析器按字面前缀匹配，"
                "可能放行站点意图禁止的路径，请人工核对存档规则", stripped,
            )


def _parse_robots_rules(content: str) -> urllib.robotparser.RobotFileParser:
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(content.splitlines())
    _warn_unsupported_rule_syntax(content)
    return parser


def _load_archived_rules() -> urllib.robotparser.RobotFileParser | None:
    """主站规则直接读本地存档（fetch 入口已先刷新存档，不重复请求网络）。"""
    try:
        content = (ROBOTS_CACHE_DIR / "robots.txt").read_text(encoding="utf-8")
    except OSError:
        return None
    return _parse_robots_rules(content)


def _fetch_host_rules(origin: str) -> urllib.robotparser.RobotFileParser | None:
    """次级主机（公告 API / 图片域等）：独立请求一次该站 robots.txt 并缓存。

    自带请求而不复用 fetch()：fetch 每次调用都会触发本校验，复用会递归。
    获取失败（含 404）按 RFC 9309 不可得语义降级为允许并缓存，进程内不重试。
    """
    try:
        req = urllib.request.Request(f"{origin}/robots.txt", headers=HEADERS)
        with urllib.request.urlopen(req, timeout=ROBOTS_FETCH_TIMEOUT) as resp:
            return _parse_robots_rules(resp.read().decode("utf-8", errors="replace"))
    except Exception as e:
        logger.warning("robots.txt 获取失败（按允许处理） %s: %s", origin, e)
        return None


def _rules_for_url(url: str) -> urllib.robotparser.RobotFileParser | None:
    """取 URL 所属站点的 robots 规则；主站读存档，其他主机抓取一次并缓存。"""
    origin = _robots_origin(url)
    if origin is None:
        return None
    if origin not in _robots_rules_by_host:
        if origin == _robots_origin(ROBOTS_URL):
            _robots_rules_by_host[origin] = _load_archived_rules()
        else:
            _robots_rules_by_host[origin] = _fetch_host_rules(origin)
    return _robots_rules_by_host[origin]


def _ensure_robots_allowed(url: str) -> None:
    """抓取前校验 robots 规则：明确 Disallow 则拒绝；规则不可得时降级放行。"""
    rules = _rules_for_url(url)
    if rules is None:
        return
    if not rules.can_fetch(HEADERS["User-Agent"], url):
        raise RobotsDisallowedError(f"目标 URL 被站点 robots.txt 禁止抓取: {url}")


def _robots_cache_fresh(meta_path: Path) -> bool:
    """存档时间在 TTL 内视为新鲜；元数据缺失或损坏按过期处理。"""
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(meta["fetched_at"])
    except (OSError, KeyError, TypeError, ValueError) as e:
        logger.info("robots.txt 缓存缺失或损坏，将重新抓取: %s", e)
        return False
    elapsed = datetime.now() - fetched_at
    return elapsed.total_seconds() < ROBOTS_CACHE_TTL_HOURS * 3600


def cache_robots_txt(cache_dir: Path | None = None) -> bool:
    """抓取并存档站点 robots.txt，返回本次是否发生新的抓取。

    自带独立请求而不复用 fetch()：fetch 每次调用前都会触发存档检查，
    复用会互相递归。
    """
    cache_dir = cache_dir or ROBOTS_CACHE_DIR
    if _robots_cache_fresh(cache_dir / "robots_meta.json"):
        return False
    req = urllib.request.Request(ROBOTS_URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=ROBOTS_FETCH_TIMEOUT) as resp:
        content = resp.read().decode("utf-8", errors="replace")
    # 原子替换而非直写：TTL 边界多进程并发刷新时，write_text 的截断-写入
    # 窗口会让并发读者解析到半截规则（Disallow 缺失即静默放行）；写序保持
    # robots.txt 先就位、meta 后提交新鲜度，读者只能看到完整提交
    atomic_write_text(cache_dir / "robots.txt", content)
    meta = {
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "url": ROBOTS_URL,
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }
    atomic_write_json(cache_dir / "robots_meta.json", meta)
    # 新存档落地后失效主站规则缓存，长进程下一次校验读新规则
    robots_origin = _robots_origin(ROBOTS_URL)
    if robots_origin is not None:
        _robots_rules_by_host.pop(robots_origin, None)
    logger.info("robots.txt 已存档: %s", cache_dir / "robots.txt")
    return True


def _ensure_robots_txt_cached() -> None:
    """robots 存档是合规证据而非功能依赖，失败仅告警，不阻断正常采集。"""
    try:
        cache_robots_txt()
    except Exception as e:
        logger.warning("robots.txt 存档失败（不阻断采集）: %s", e)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """禁用自动重定向：每一跳由 fetch / 图片下载逻辑校验后再手动跟随。"""

    def redirect_request(self, req, fp, code, msg, headers, _newurl):
        return None


def _open_response_no_redirect(url: str, timeout: float):
    """打开单跳响应：30x 不自动跟随（由调用方取 Location 校验后重进）。"""
    opener = urllib.request.build_opener(_NoRedirectHandler())
    return opener.open(urllib.request.Request(url, headers=HEADERS), timeout=timeout)


def fetch(url: str, binary: bool = False) -> str | bytes:
    """带重试机制的 HTTP GET 请求（重定向逐跳过 robots 校验后再跟随）

    binary=True 时返回原始 bytes（用于下载图片等二进制资源），
    否则解码为 utf-8 字符串返回。
    每次调用前先确保站点 robots.txt 已存档（24 小时缓存，失败不阻断），
    并按已解析的 robots 规则校验目标 URL（被 Disallow 时抛
    RobotsDisallowedError，规则不可得时放行）。自动重定向已禁用：
    站点把数据 URL 30x 到带 Disallow 规则的其他主机/路径时，每一跳
    的目标 URL 都会重新校验，不会被静默抓取。
    """
    _ensure_robots_txt_cached()
    _ensure_robots_allowed(url)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            current_url = url
            for _ in range(MAX_FETCH_REDIRECTS + 1):
                try:
                    with _open_response_no_redirect(current_url, TIMEOUT) as resp:
                        data = resp.read()
                        return data if binary else data.decode("utf-8")
                except urllib.error.HTTPError as error:
                    if error.code not in _REDIRECT_HTTP_CODES:
                        raise
                    location = error.headers.get("Location")
                    error.close()
                    if not location:
                        raise ValueError(
                            f"重定向缺少 Location 响应头: {current_url}"
                        ) from error
                    current_url = urljoin(current_url, location)
                    _ensure_robots_allowed(current_url)
            raise ValueError(f"重定向次数超过上限 {MAX_FETCH_REDIRECTS}: {url}")
        except RobotsDisallowedError:
            # robots 拒绝是确定性结果（规则不变重试必然同拒），立即上抛，
            # 不消耗重试轮次——重试只会重复访问初始 URL 与重定向链
            raise
        except urllib.error.HTTPError as e:
            if e.code in _NON_RETRYABLE_HTTP_CODES:
                # 404/权限类失败重试无意义，立即抛出让调用方记录该条并继续
                logger.error("请求不可重试: HTTP %s — %s", e.code, url)
                raise
            logger.warning("请求失败 [%d/%d]: %s — %s", attempt, MAX_RETRIES, url, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
            else:
                raise
        except Exception as e:
            logger.warning("请求失败 [%d/%d]: %s — %s", attempt, MAX_RETRIES, url, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
            else:
                raise


def save_json_atomic(path: Path, data: list[dict]) -> None:
    """将 JSON 数据写入唯一临时文件后原子替换目标文件（统一委托 json_repository）。"""
    atomic_write_json(path, data)


# ============================================================
# 高阶流程：全量获取原始数据
# ============================================================


def fetch_all_raw() -> list[dict]:
    """从官网获取全部武将的原始数据。"""
    html = fetch(BAIKE_URL)
    chunk_url = find_chunk_url(html)
    # 本函数会被 GUI 进程（公告服务/主窗口）直接调用，进度一律走 logger；
    # CLI 侧进度通道只由 __main__ 入口的 print 承担
    logger.info("官网数据 chunk: %s", chunk_url)
    js_text = fetch(chunk_url)
    raw_list = parse_heroes_chunk(js_text)
    logger.info("官网原始数据: %d 条", len(raw_list))
    return raw_list


def fetch_all_cards_raw() -> list[dict]:
    """从官网手牌库获取全部卡牌的原始数据。"""
    html = fetch(SHOUPAIKU_URL)
    chunk_url = find_card_chunk_url(html)
    logger.info("官网卡牌数据 chunk: %s", chunk_url)
    js_text = fetch(chunk_url)
    raw_list = parse_cards_chunk(js_text)
    logger.info("官网卡牌原始数据: %d 条", len(raw_list))
    return raw_list


# ============================================================
# 头像下载
# ============================================================

IMAGES_DIR = IMAGES_OUTPUT_DIR


def _safe_image_name(raw_name: str) -> str:
    """验证官网角色名可作为固定 PNG 文件名。"""
    name = unicodedata.normalize("NFC", clean_html(raw_name))
    if not SAFE_IMAGE_NAME_PATTERN.fullmatch(name):
        raise ValueError("角色名包含不允许的文件名字符")
    if name.upper() in WINDOWS_RESERVED_FILENAMES:
        raise ValueError("角色名是 Windows 保留文件名")
    return name


def _validate_image_url(url: str) -> None:
    """仅允许从官方 HTTPS 图片域名下载。"""
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.hostname.lower() not in ALLOWED_IMAGE_HOSTS
        or parsed.username
        or parsed.password
    ):
        raise ValueError("图片 URL 不属于允许的官方 HTTPS 域名")


def _open_image_response(url: str):
    """打开图片响应，并在每次重定向后重新验证目标 URL。"""
    # 图片域与主站不同源，robots 规则经 _rules_for_url 按主机分别获取；
    # 现有图片下载测试整体 mock 本函数，此校验不进入其测试路径
    _ensure_robots_allowed(url)
    opener = urllib.request.build_opener(_NoRedirectHandler())
    current_url = url
    for _ in range(MAX_IMAGE_REDIRECTS + 1):
        _validate_image_url(current_url)
        request = urllib.request.Request(current_url, headers=HEADERS)
        try:
            response = opener.open(request, timeout=IMAGE_DOWNLOAD_TIMEOUT)
        except urllib.error.HTTPError as error:
            if error.code not in {301, 302, 303, 307, 308}:
                raise
            location = error.headers.get("Location")
            error.close()
            if not location:
                raise ValueError("图片重定向缺少 Location 响应头")
            current_url = urljoin(current_url, location)
            continue

        _validate_image_url(response.geturl())
        return response

    raise ValueError("图片重定向次数超过上限")


def _validate_image_file(path: Path) -> None:
    """确认临时文件是尺寸受限的 PNG 图片。"""
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(path) as image:
            image.verify()

        with Image.open(path) as image:
            if image.format != "PNG":
                raise ValueError("图片格式必须为 PNG")
            width, height = image.size
            if width * height > MAX_IMAGE_PIXELS:
                raise ValueError("图片像素数超过上限")
            image.load()


def _download_hero_image(icon_url: str, dest: Path) -> None:
    """安全下载、验证并原子替换单张头像。"""
    temp_path: Path | None = None
    try:
        with _open_image_response(icon_url) as response:
            content_type = response.headers.get_content_type().lower()
            if content_type != "image/png":
                raise ValueError(f"图片 MIME 类型不允许: {content_type}")

            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_IMAGE_SIZE_BYTES:
                raise ValueError("图片响应大小超过上限")

            with tempfile.NamedTemporaryFile(
                mode="wb", dir=dest.parent, prefix=f".{dest.stem}.", suffix=".tmp", delete=False
            ) as temp_file:
                temp_path = Path(temp_file.name)
                downloaded = 0
                while chunk := response.read(IMAGE_CHUNK_SIZE):
                    downloaded += len(chunk)
                    if downloaded > MAX_IMAGE_SIZE_BYTES:
                        raise ValueError("图片响应大小超过上限")
                    temp_file.write(chunk)

        _validate_image_file(temp_path)
        temp_path.replace(dest)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def download_hero_images(
    raw_list: list[dict],
    image_dir: str | Path | None = None,
    skip_existing: bool = True,
) -> int:
    """从原始 JS 数据中提取 icon_url 并将头像下载到本地

    Args:
        raw_list: 原始 JS chunk 数据（含 icon_url 字段）。
        image_dir: 输出目录，默认 project_root/images/。
        skip_existing: True 时跳过已存在的文件。

    Returns:
        成功下载的头像数量。
    """
    out_dir = Path(image_dir) if image_dir else IMAGES_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    consecutive_failures = 0
    for raw in raw_list:
        icon_url = raw.get("icon_url", "")
        if not icon_url:
            continue

        try:
            name = _safe_image_name(raw.get("name", ""))
        except ValueError as error:
            logger.warning("跳过不安全的头像文件名 %r: %s", raw.get("name"), error)
            continue

        dest = out_dir / f"{name}.png"
        if not dest.resolve().is_relative_to(out_dir.resolve()):
            logger.warning("跳过越界的头像输出路径: %s", name)
            continue

        if skip_existing and dest.exists():
            continue

        try:
            _download_hero_image(icon_url, dest)
        except Exception as e:
            consecutive_failures += 1
            logger.warning("头像下载失败 %s: %s", name, e)
            if consecutive_failures >= IMAGE_DOWNLOAD_MAX_CONSECUTIVE_FAILURES:
                logger.error(
                    "连续 %d 次头像下载失败，中止本次下载（已下载 %d 张），请检查网络后重试",
                    consecutive_failures, count,
                )
                break
        else:
            consecutive_failures = 0
            count += 1
        # 成功与失败都真实请求了源站，间隔放在尝试之后、跳过项不占用
        time.sleep(IMAGE_DOWNLOAD_INTERVAL_SECONDS)

    return count
