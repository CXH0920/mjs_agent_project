"""官网爬虫测试：头像安全下载 + robots.txt 存档。"""

from __future__ import annotations

import json
import urllib.error
from datetime import datetime, timedelta
from email.message import Message
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from src.scraper.official_source import adapter as official_adapter
from src.scraper.official_source import crawler, incremental
from src.scraper.official_source import full as official


class _FakeResponse:
    def __init__(self, data: bytes, content_type: str = "image/png", content_length: int | None = None):
        self._stream = BytesIO(data)
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.headers["Content-Length"] = str(content_length if content_length is not None else len(data))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._stream.close()

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)


def _png_bytes(color: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (4, 4), color).save(output, format="PNG")
    return output.getvalue()


def test_download_hero_images_uses_validated_name_and_png(monkeypatch, tmp_path: Path) -> None:
    image_data = _png_bytes("red")
    monkeypatch.setattr(crawler, "_open_image_response", lambda url: _FakeResponse(image_data))

    count = crawler.download_hero_images(
        [{"name": "曹操", "icon_url": "https://siteres.ztgame.com/avatar.png"}],
        image_dir=tmp_path,
        skip_existing=False,
    )

    assert count == 1
    assert (tmp_path / "曹操.png").read_bytes() == image_data
    assert not list(tmp_path.glob("*.tmp"))


def test_download_hero_images_rejects_unsafe_name_without_request(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(crawler, "_open_image_response", lambda url: (_ for _ in ()).throw(AssertionError()))

    count = crawler.download_hero_images(
        [{"name": "..\\config", "icon_url": "https://siteres.ztgame.com/avatar.png"}],
        image_dir=tmp_path,
    )

    assert count == 0
    assert not list(tmp_path.iterdir())


def test_download_hero_images_keeps_existing_file_when_validation_fails(monkeypatch, tmp_path: Path) -> None:
    destination = tmp_path / "曹操.png"
    previous_data = _png_bytes("blue")
    destination.write_bytes(previous_data)
    monkeypatch.setattr(crawler, "_open_image_response", lambda url: _FakeResponse(b"not an image"))

    count = crawler.download_hero_images(
        [{"name": "曹操", "icon_url": "https://siteres.ztgame.com/avatar.png"}],
        image_dir=tmp_path,
        skip_existing=False,
    )

    assert count == 0
    assert destination.read_bytes() == previous_data
    assert not list(tmp_path.glob("*.tmp"))


def test_download_hero_images_keeps_existing_file_when_response_is_too_large(monkeypatch, tmp_path: Path) -> None:
    destination = tmp_path / "曹操.png"
    previous_data = _png_bytes("blue")
    destination.write_bytes(previous_data)
    monkeypatch.setattr(
        crawler,
        "_open_image_response",
        lambda url: _FakeResponse(_png_bytes("red"), content_length=crawler.MAX_IMAGE_SIZE_BYTES + 1),
    )

    count = crawler.download_hero_images(
        [{"name": "曹操", "icon_url": "https://siteres.ztgame.com/avatar.png"}],
        image_dir=tmp_path,
        skip_existing=False,
    )

    assert count == 0
    assert destination.read_bytes() == previous_data
    assert not list(tmp_path.glob("*.tmp"))


def test_validate_image_url_only_allows_official_https_host() -> None:
    crawler._validate_image_url("https://siteres.ztgame.com/avatar.png")

    for url in ("http://siteres.ztgame.com/avatar.png", "https://example.com/avatar.png"):
        try:
            crawler._validate_image_url(url)
        except ValueError:
            continue
        raise AssertionError(f"未拒绝不安全 URL: {url}")


def test_save_json_atomic_replaces_target_without_tmp_file(tmp_path: Path) -> None:
    target = tmp_path / "heroes.json"
    target.write_text('[{"id": 1}]', encoding="utf-8")

    crawler.save_json_atomic(target, [{"id": 2}])

    assert json.loads(target.read_text(encoding="utf-8")) == [{"id": 2}]
    assert not target.with_suffix(".tmp").exists()
    assert b"\r\n" not in target.read_bytes()
    assert target.read_bytes().endswith(b"\n")


def test_official_crawl_transforms_each_item_once(monkeypatch, tmp_path: Path) -> None:
    raw_list = [{"id": 1}, {"id": 2}]
    transform_calls: list[dict] = []
    output_path = tmp_path / "heroes.json"

    monkeypatch.setattr(official, "fetch", lambda _url: "source")
    monkeypatch.setattr(official, "find_chunk_url", lambda _html: "chunk")
    monkeypatch.setattr(official, "parse_heroes_chunk", lambda _js: raw_list)
    monkeypatch.setattr(
        official,
        "transform",
        lambda raw: transform_calls.append(raw)
        or {
            "id": raw["id"],
            "faction": "",
            "skills": [{"name": f"s{raw['id']}", "description": "描述"}],
        },
    )
    monkeypatch.setattr(official, "validate_heroes", lambda heroes: heroes)

    official.crawl(output_path=str(output_path), skip_images=True)

    assert transform_calls == raw_list
    assert json.loads(output_path.read_text(encoding="utf-8")) == [
        {"id": 1, "faction": "", "skills": [{"name": "s1", "description": "描述"}]},
        {"id": 2, "faction": "", "skills": [{"name": "s2", "description": "描述"}]},
    ]


def test_incremental_run_transforms_each_item_once(monkeypatch, tmp_path: Path) -> None:
    raw_list = [{"id": 1}, {"id": 2}]
    transform_calls: list[dict] = []

    monkeypatch.setattr(
        incremental,
        "transform",
        lambda raw: transform_calls.append(raw)
        or {"id": raw["id"], "name": f"武将{raw['id']}", "faction": "", "skills": []},
    )

    incremental.run(raw_list, tmp_path / "heroes.json", dry_run=True)

    assert transform_calls == raw_list


def test_official_adapter_parses_saved_contract_samples() -> None:
    sample_dir = Path(__file__).parent / "test_data" / "official_adapter"
    html = (sample_dir / "baike_page.html").read_text(encoding="utf-8")
    js_text = (sample_dir / "heroes_chunk.js").read_text(encoding="utf-8")

    assert official_adapter.find_chunk_url(html) == (
        "https://mjs.ztgame.com/_nuxt/mjbk.a1b2c3.js"
    )
    assert official_adapter.parse_heroes_chunk(js_text) == [
        {
            "id": 101,
            "name": "[测试武将]",
            "note": "字符串中的 ] 不应结束数组",
            "missing": None,
            "skills": [{"name": "测试技能"}],
        }
    ]


def test_download_hero_images_aborts_after_consecutive_failures(monkeypatch, tmp_path: Path) -> None:
    """连续失败达熔断阈值即中止，不再向源站请求剩余条目；每次尝试后都有间隔。"""
    sleeps: list[float] = []
    monkeypatch.setattr(crawler.time, "sleep", lambda seconds: sleeps.append(seconds))
    requested: list[str] = []

    def failing_download(url: str, dest) -> None:
        requested.append(url)
        raise RuntimeError("network down")

    monkeypatch.setattr(crawler, "_download_hero_image", failing_download)
    raw_list = [
        {"name": f"武将{index}", "icon_url": f"https://siteres.ztgame.com/{index}.png"}
        for index in range(10)
    ]

    count = crawler.download_hero_images(raw_list, image_dir=tmp_path, skip_existing=False)

    assert count == 0
    assert len(requested) == crawler.IMAGE_DOWNLOAD_MAX_CONSECUTIVE_FAILURES  # 熔断后不再请求
    # 终止那次失败后直接中止，不再为放弃的请求支付间隔等待
    assert sleeps == [crawler.IMAGE_DOWNLOAD_INTERVAL_SECONDS] * (len(requested) - 1)


def test_download_hero_images_resets_failure_streak_on_success(monkeypatch, tmp_path: Path) -> None:
    """失败后成功的条目会重置连续失败计数：散布的零星失败不触发熔断。"""
    monkeypatch.setattr(crawler.time, "sleep", lambda seconds: None)
    outcomes = ["fail", "fail", "ok", "fail", "fail", "ok", "ok", "ok"]
    expected_attempts = len(outcomes)  # 运行会 pop 消耗 outcomes，先留存总数
    attempted: list[str] = []

    def flaky_download(url: str, dest) -> None:
        attempted.append(url)
        if outcomes.pop(0) == "fail":
            raise RuntimeError("transient")

    monkeypatch.setattr(crawler, "_download_hero_image", flaky_download)
    raw_list = [
        {"name": f"武将{index}", "icon_url": f"https://siteres.ztgame.com/{index}.png"}
        for index in range(len(outcomes))
    ]

    count = crawler.download_hero_images(raw_list, image_dir=tmp_path, skip_existing=False)

    assert count == 4
    assert len(attempted) == expected_attempts  # 全部条目都被尝试，未熔断


def test_js_to_json_matches_golden_snapshot() -> None:
    """黄金快照特征化：状态机版 js_to_json 与改造前正则版在全部样本上输出逐字段一致。"""
    golden = json.loads(
        (Path(__file__).parent / "test_data" / "official_adapter" / "js_to_json_golden.json")
        .read_text(encoding="utf-8"))
    for case in golden["cases"]:
        if case["name"] == "saved_contract":
            continue  # 该样本需先经 extract_js_array 提取数组，由下方完整链路断言覆盖
        assert official_adapter.js_to_json(case["input"]) == case["output"], case["name"]

    saved_contract = next(case for case in golden["cases"] if case["name"] == "saved_contract")
    assert official_adapter.parse_heroes_chunk(saved_contract["input"]) == saved_contract["output"]


def test_js_to_json_parses_key_like_text_inside_strings() -> None:
    """修复验证：字符串内的 {x:1}、,a:b、尾部 undefined 不再被误改写（旧实现整批解析失败）。"""
    cases = [
        ('[{desc:"效果{x:1}",name:"甲"}]',
         [{"desc": "效果{x:1}", "name": "甲"}]),
        ('[{desc:"效果{x:1}且,a:b",name:"甲"}]',
         [{"desc": "效果{x:1}且,a:b", "name": "甲"}]),
        ('[{desc:"时机:出牌阶段",tag:"值:undefined"}]',
         [{"desc": "时机:出牌阶段", "tag": "值:undefined"}]),
        ('[{a: undefined , b:1}]',
         [{"a": None, "b": 1}]),
    ]
    for text, expected in cases:
        assert official_adapter.js_to_json(text) == expected, text


# ============================================================
# robots.txt 存档
# ============================================================


def test_fetch_host_rules_uses_short_timeout(monkeypatch) -> None:
    """次级主机 robots 抓取用短超时：站点异常时快速失败降级，不拖慢采集。"""
    timeouts: list[float] = []

    def fake_urlopen(req, timeout=0):
        timeouts.append(timeout)
        raise OSError("boom")

    monkeypatch.setattr(crawler.urllib.request, "urlopen", fake_urlopen)
    assert crawler._fetch_host_rules("https://siteres.ztgame.com") is None
    assert timeouts == [crawler.ROBOTS_FETCH_TIMEOUT]


def test_cache_robots_txt_uses_short_timeout(monkeypatch, tmp_path: Path) -> None:
    """主站 robots 存档刷新同样用短超时（与主数据 30 秒解耦）。"""
    timeouts: list[float] = []

    def fake_urlopen(req, timeout=0):
        timeouts.append(timeout)
        return _FakeResponse(b"Allow: /", content_type="text/plain")

    monkeypatch.setattr(crawler.urllib.request, "urlopen", fake_urlopen)
    assert crawler.cache_robots_txt(tmp_path / "robots_cache") is True
    assert timeouts == [crawler.ROBOTS_FETCH_TIMEOUT]


def test_open_image_response_uses_short_image_timeout(monkeypatch) -> None:
    """图片下载用短于主数据的超时：CDN 握手挂起时单张等待上限减半。"""
    timeouts: list[float] = []

    class _FakeOpener:
        def open(self, request, timeout=0):
            timeouts.append(timeout)
            response = _FakeResponse(_png_bytes("red"))
            response.geturl = lambda: request.full_url
            return response

    monkeypatch.setattr(crawler.urllib.request, "build_opener", lambda *_a, **_k: _FakeOpener())
    monkeypatch.setattr(crawler, "_ensure_robots_allowed", lambda url: None)
    with crawler._open_image_response("https://siteres.ztgame.com/a.png") as response:
        response.read()
    assert timeouts == [crawler.IMAGE_DOWNLOAD_TIMEOUT]


def test_cache_robots_txt_writes_archive_and_meta(monkeypatch, tmp_path: Path) -> None:
    requests: list[str] = []

    def fake_urlopen(req, timeout=0):
        requests.append(req.full_url)
        return _FakeResponse(b"User-agent: *\nAllow: /", content_type="text/plain")

    monkeypatch.setattr(crawler.urllib.request, "urlopen", fake_urlopen)
    cache_dir = tmp_path / "robots_cache"

    assert crawler.cache_robots_txt(cache_dir) is True
    assert requests == [crawler.ROBOTS_URL]
    assert "Allow: /" in (cache_dir / "robots.txt").read_text(encoding="utf-8")
    meta = json.loads((cache_dir / "robots_meta.json").read_text(encoding="utf-8"))
    assert meta["url"] == crawler.ROBOTS_URL
    assert len(meta["sha256"]) == 64


def test_cache_robots_txt_atomic_write_replaces_without_temp_residue(
    monkeypatch, tmp_path: Path,
) -> None:
    """存档写走临时文件 + 原子替换：旧存档被整体替换，完成后不留 .tmp。

    回归背景：直写在 TTL 边界多进程并发刷新时会留下半截文件，并发读者按
    缺失 Disallow 的规则放行（合规链 fail-open）。
    """
    monkeypatch.setattr(
        crawler.urllib.request, "urlopen",
        lambda req, timeout=0: _FakeResponse(b"User-agent: *\nAllow: /", content_type="text/plain"),
    )
    cache_dir = tmp_path / "robots_cache"
    cache_dir.mkdir()
    (cache_dir / "robots.txt").write_text("旧规则", encoding="utf-8")

    assert crawler.cache_robots_txt(cache_dir) is True

    assert (cache_dir / "robots.txt").read_text(encoding="utf-8") == "User-agent: *\nAllow: /"
    assert list(cache_dir.glob("*.tmp")) == []


def test_cache_robots_txt_skips_request_when_fresh(monkeypatch, tmp_path: Path) -> None:
    cache_dir = tmp_path / "robots_cache"
    cache_dir.mkdir()
    meta = {"fetched_at": datetime.now().isoformat(timespec="seconds")}
    (cache_dir / "robots_meta.json").write_text(json.dumps(meta), encoding="utf-8")

    def refuse(*_args, **_kwargs):
        raise AssertionError("缓存新鲜时不应发起 robots 请求")

    monkeypatch.setattr(crawler.urllib.request, "urlopen", refuse)
    assert crawler.cache_robots_txt(cache_dir) is False


def test_cache_robots_txt_refetches_when_stale(monkeypatch, tmp_path: Path) -> None:
    cache_dir = tmp_path / "robots_cache"
    cache_dir.mkdir()
    stale = datetime.now() - timedelta(hours=crawler.ROBOTS_CACHE_TTL_HOURS + 1)
    meta = {"fetched_at": stale.isoformat(timespec="seconds")}
    (cache_dir / "robots_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setattr(
        crawler.urllib.request, "urlopen",
        lambda req, timeout=0: _FakeResponse(b"Allow: /", content_type="text/plain"),
    )

    assert crawler.cache_robots_txt(cache_dir) is True


def test_fetch_tolerates_robots_archive_failure(monkeypatch, tmp_path: Path) -> None:
    """robots 存档失败仅告警，不阻断采集主流程，也不留半成品存档。"""
    monkeypatch.setattr(crawler, "ROBOTS_CACHE_DIR", tmp_path / "rc")
    seen: list[str] = []

    def fake_urlopen(req, timeout=0):
        seen.append(req.full_url)
        if req.full_url == crawler.ROBOTS_URL:
            raise urllib.error.URLError("robots 不可达")
        return _FakeResponse(b"<html></html>", content_type="text/html")

    monkeypatch.setattr(crawler.urllib.request, "urlopen", fake_urlopen)
    assert crawler.fetch("https://mjs.ztgame.com/baike/") == "<html></html>"
    assert crawler.ROBOTS_URL in seen
    assert not (tmp_path / "rc" / "robots.txt").exists()


def test_fetch_archives_robots_before_first_request(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(crawler, "ROBOTS_CACHE_DIR", tmp_path / "rc")
    seen: list[str] = []

    def fake_urlopen(req, timeout=0):
        seen.append(req.full_url)
        return _FakeResponse(b"<html></html>", content_type="text/html")

    monkeypatch.setattr(crawler.urllib.request, "urlopen", fake_urlopen)
    crawler.fetch("https://mjs.ztgame.com/baike/")
    assert seen[0] == crawler.ROBOTS_URL
    assert (tmp_path / "rc" / "robots.txt").exists()


def _archive_robots(cache_dir: Path, content: str) -> None:
    """预置一份新鲜存档（等价 cache_robots_txt 刚成功抓取后的磁盘状态）。"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    meta = {"fetched_at": datetime.now().isoformat(timespec="seconds")}
    (cache_dir / "robots.txt").write_text(content, encoding="utf-8")
    (cache_dir / "robots_meta.json").write_text(json.dumps(meta), encoding="utf-8")


def test_fetch_rejects_disallowed_url(monkeypatch, tmp_path: Path) -> None:
    """存档规则 Disallow 目标路径时，fetch 在发起请求前拒绝并抛 RobotsDisallowedError。"""
    monkeypatch.setattr(crawler, "ROBOTS_CACHE_DIR", tmp_path / "rc")
    monkeypatch.setattr(crawler, "_robots_rules_by_host", {})
    _archive_robots(tmp_path / "rc", "User-agent: *\nDisallow: /baike/\n")

    def refuse_network(*_args, **_kwargs):
        raise AssertionError("被 Disallow 的 URL 不应发起任何网络请求")

    monkeypatch.setattr(crawler.urllib.request, "urlopen", refuse_network)

    with pytest.raises(crawler.RobotsDisallowedError) as excinfo:
        crawler.fetch("https://mjs.ztgame.com/baike/")
    assert "/baike/" in str(excinfo.value)


def test_fetch_allows_url_permitted_by_archive(monkeypatch, tmp_path: Path) -> None:
    """存档规则允许目标路径时，fetch 行为与无校验版本一致（正常返回内容）。"""
    monkeypatch.setattr(crawler, "ROBOTS_CACHE_DIR", tmp_path / "rc")
    monkeypatch.setattr(crawler, "_robots_rules_by_host", {})
    _archive_robots(tmp_path / "rc", "User-agent: *\nAllow: /\n")

    monkeypatch.setattr(
        crawler.urllib.request, "urlopen",
        lambda req, timeout=0: _FakeResponse(b"<html></html>", content_type="text/html"),
    )
    assert crawler.fetch("https://mjs.ztgame.com/baike/") == "<html></html>"


def test_fetch_checks_secondary_host_robots_once(monkeypatch, tmp_path: Path) -> None:
    """非主站主机（如公告 API）首次访问时抓取该站 robots 并进程内缓存；Disallow 则拒绝。"""
    # 主站存档必须一并隔离：否则 _ensure_robots_txt_cached 会读写真实
    # logs/robots_cache，把本测试的 mock 规则泄漏进真实存档（曾致污染）
    monkeypatch.setattr(crawler, "ROBOTS_CACHE_DIR", tmp_path / "rc")
    _archive_robots(tmp_path / "rc", "Allow: /\n")
    monkeypatch.setattr(crawler, "_robots_rules_by_host", {})
    robots_calls: list[str] = []

    def fake_urlopen(req, timeout=0):
        if req.full_url.endswith("/robots.txt"):
            robots_calls.append(req.full_url)
            return _FakeResponse(b"User-agent: *\nDisallow: /api/\n", content_type="text/plain")
        raise AssertionError("被 Disallow 的 URL 不应请求目标地址")

    monkeypatch.setattr(crawler.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(crawler.RobotsDisallowedError):
        crawler.fetch("https://ucmsv2api.ztgame.com/api/news/list?site=mjs")
    # 主站存档新鲜未重抓，robots 请求只发生在次级主机上
    assert robots_calls == ["https://ucmsv2api.ztgame.com/robots.txt"]


def test_fetch_allows_when_secondary_robots_missing(monkeypatch, tmp_path: Path) -> None:
    """次级主机 robots.txt 不可得（如 404）时按允许处理，不阻断采集（降级语义与主站一致）。"""
    monkeypatch.setattr(crawler, "ROBOTS_CACHE_DIR", tmp_path / "rc")
    _archive_robots(tmp_path / "rc", "Allow: /\n")
    monkeypatch.setattr(crawler, "_robots_rules_by_host", {})

    def fake_urlopen(req, timeout=0):
        if req.full_url.endswith("/robots.txt"):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", None, None)
        return _FakeResponse(b"payload", content_type="application/json")

    monkeypatch.setattr(crawler.urllib.request, "urlopen", fake_urlopen)

    assert crawler.fetch("https://ucmsv2api.ztgame.com/api/news/list") == "payload"
    # 再次访问同主机：失败的规则获取也已缓存，不重复请求 robots.txt
    assert crawler.fetch("https://ucmsv2api.ztgame.com/api/other") == "payload"


def test_download_hero_image_blocked_by_robots(monkeypatch, tmp_path: Path) -> None:
    """图片域 robots 全站 Disallow 时，头像下载在请求图片前被拒绝。"""
    monkeypatch.setattr(crawler, "_robots_rules_by_host", {})

    def fake_urlopen(req, timeout=0):
        if req.full_url.endswith("/robots.txt"):
            return _FakeResponse(b"User-agent: *\nDisallow: /\n", content_type="text/plain")
        raise AssertionError("被 Disallow 的图片 URL 不应发起下载")

    monkeypatch.setattr(crawler.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(crawler.RobotsDisallowedError):
        crawler._download_hero_image("https://siteres.ztgame.com/a.png", tmp_path / "a.png")
    assert not list(tmp_path.iterdir())  # 未留下任何临时文件


class TestSplitSkillDesc:
    """技能描述拆分：官网标题格式不统一的真实样本回归"""

    def test_bare_title_without_strong(self) -> None:
        """官网样本（周处·有进无退）：首个标题漏包 <strong>，描述不得丢失"""
        raw = (
            "<p>技能描述</p>\n\n"
            "<p>你的摸牌阶段和弃牌阶段改为出牌阶段。</p>\n\n"
            "<p>&nbsp;</p>\n\n"
            "<p><strong>结算详情</strong></p>\n\n"
            "<p>1.&ldquo;出牌阶段限1次&rdquo;的技能在各出牌阶段分别计算。</p>\n\n"
            "<p>2.若摸牌阶段被跳过，则由该阶段改为的出牌阶段也被跳过。</p>\n\n"
            "<p><strong>技能典故</strong></p>\n\n"
            "<p>处按剑曰:&ldquo;此是吾效节授命之日。&rdquo;</p>"
        )
        parts = crawler.split_skill_desc(raw)
        assert parts["description"] == "你的摸牌阶段和弃牌阶段改为出牌阶段。"
        assert parts["settlement"] == (
            "1.“出牌阶段限1次”的技能在各出牌阶段分别计算。 "
            "2.若摸牌阶段被跳过，则由该阶段改为的出牌阶段也被跳过。"
        )

    def test_detail_title_used_as_description(self) -> None:
        """官网样本（王戎·道旁苦李）：标题为“技能详情”，按描述认领而非落空"""
        raw = (
            "<p><strong>技能详情</strong></p>\n\n"
            "<p>当有&clubs;牌因弃置进入弃牌堆时，你可以获得这些牌，并交给一名其他角色。"
            "这些牌无法被打出或主动弃置，直到下一轮结束。</p>\n\n"
            "<p>&nbsp;</p>\n\n"
            "<p><strong>结算详情</strong></p>\n\n"
            "<p>1.无法被主动弃置的牌在弃牌阶段即使大于手牌上限也无法被弃置。</p>"
        )
        parts = crawler.split_skill_desc(raw)
        assert parts["description"] == (
            "当有♣牌因弃置进入弃牌堆时，你可以获得这些牌，并交给一名其他角色。"
            "这些牌无法被打出或主动弃置，直到下一轮结束。"
        )
        assert parts["settlement"] == "1.无法被主动弃置的牌在弃牌阶段即使大于手牌上限也无法被弃置。"

    def test_standard_sections_unchanged(self) -> None:
        """标准格式回归：描述/结算取值不变，典故与设计思路仍被丢弃"""
        raw = (
            "<p><strong>技能描述</strong></p>\n\n"
            "<p>锁定技，你不能使用锦囊牌。</p>\n\n"
            "<p><strong>结算详情</strong></p>\n\n"
            "<p>1.装备牌不受限制。</p>\n\n"
            "<p><strong>技能典故</strong></p>\n\n"
            "<p>典故内容。</p>\n\n"
            "<p><strong>设计思路</strong></p>\n\n"
            "<p>思路内容。</p>"
        )
        parts = crawler.split_skill_desc(raw)
        assert parts["description"] == "锁定技，你不能使用锦囊牌。"
        assert parts["settlement"] == "1.装备牌不受限制。"


def _hero_dict(hero_id: int, skills: list[dict]) -> dict:
    return {"id": hero_id, "faction": "魏", "skills": skills}


class TestFullWriteGuard:
    """full.py 写入守卫：官网改版产出的空数据不得覆盖 heroes.json"""

    def test_empty_validated_rejected(self, tmp_path: Path) -> None:
        reason = official.refuse_write_reason([], tmp_path / "heroes.json")
        assert reason is not None and "校验通过条数为 0" in reason

    def test_zero_skills_rejected(self, tmp_path: Path) -> None:
        reason = official.refuse_write_reason([_hero_dict(1, [])], tmp_path / "heroes.json")
        assert reason is not None and "技能总数为 0" in reason

    def test_empty_desc_over_ratio_rejected(self, tmp_path: Path) -> None:
        skills = [{"name": f"s{i}", "description": "d"} for i in range(3)]
        skills.append({"name": "bad", "description": ""})
        reason = official.refuse_write_reason([_hero_dict(1, skills)], tmp_path / "heroes.json")
        assert reason is not None and "空技能描述占比" in reason

    def test_empty_desc_under_ratio_allowed(self, tmp_path: Path) -> None:
        skills = [{"name": f"s{i}", "description": "d"} for i in range(9)]
        skills.append({"name": "bad", "description": ""})
        assert official.refuse_write_reason([_hero_dict(1, skills)], tmp_path / "heroes.json") is None

    def test_count_drop_rejected(self, tmp_path: Path) -> None:
        output_path = tmp_path / "heroes.json"
        output_path.write_text(json.dumps([_hero_dict(i, [{"name": "s", "description": "d"}]) for i in range(10)]), encoding="utf-8")
        reason = official.refuse_write_reason([_hero_dict(1, [{"name": "s", "description": "d"}])], output_path)
        assert reason is not None and "跌幅" in reason

    def test_corrupt_old_file_allowed(self, tmp_path: Path) -> None:
        """旧文件损坏时放行：全量重爬正是恢复手段"""
        output_path = tmp_path / "heroes.json"
        output_path.write_text("{broken", encoding="utf-8")
        assert official.refuse_write_reason([_hero_dict(1, [{"name": "s", "description": "d"}])], output_path) is None

    def test_missing_old_file_allowed(self, tmp_path: Path) -> None:
        assert official.refuse_write_reason([_hero_dict(1, [{"name": "s", "description": "d"}])], tmp_path / "heroes.json") is None

    def test_parse_dropped_local_hero_rejected(self, tmp_path: Path) -> None:
        """本地已有、官网也存在、但校验未通过的武将 → 疑似解析丢失，拒绝覆盖"""
        output_path = tmp_path / "heroes.json"
        output_path.write_text(json.dumps([_hero_dict(i, [{"name": "s", "description": "d"}]) for i in range(1, 11)]), encoding="utf-8")
        validated = [_hero_dict(i, [{"name": "s", "description": "d"}]) for i in range(1, 10)]
        reason = official.refuse_write_reason(validated, output_path, raw_ids=set(range(1, 11)))
        assert reason is not None and "疑似解析丢失" in reason and "10" in reason

    def test_hero_absent_from_official_allowed(self, tmp_path: Path) -> None:
        """官网真下架（原始数据已无该 ID）→ 不按解析丢失拦截"""
        output_path = tmp_path / "heroes.json"
        output_path.write_text(json.dumps([_hero_dict(i, [{"name": "s", "description": "d"}]) for i in range(1, 11)]), encoding="utf-8")
        validated = [_hero_dict(i, [{"name": "s", "description": "d"}]) for i in range(1, 10)]
        assert official.refuse_write_reason(validated, output_path, raw_ids=set(range(1, 10))) is None

    def test_crawl_blocked_when_desc_all_empty(self, monkeypatch, tmp_path: Path) -> None:
        """集成：官网改版导致描述全空 → 守卫拦截，旧文件字节不变"""
        output_path = tmp_path / "heroes.json"
        old_bytes = json.dumps([_hero_dict(1, [{"name": "旧技能", "description": "旧描述"}])]).encode("utf-8")
        output_path.write_bytes(old_bytes)

        monkeypatch.setattr(official, "fetch", lambda _url: "source")
        monkeypatch.setattr(official, "find_chunk_url", lambda _html: "chunk")
        monkeypatch.setattr(official, "parse_heroes_chunk", lambda _js: [{"id": 1}])
        monkeypatch.setattr(
            official,
            "transform",
            lambda raw: _hero_dict(raw["id"], [{"name": "新技能", "description": ""}]),
        )
        monkeypatch.setattr(official, "validate_heroes", lambda heroes: heroes)

        with pytest.raises(SystemExit) as excinfo:
            official.crawl(output_path=str(output_path), skip_images=True)
        assert excinfo.value.code == 1
        assert output_path.read_bytes() == old_bytes

    def test_crawl_writes_when_guard_passes(self, monkeypatch, tmp_path: Path) -> None:
        output_path = tmp_path / "heroes.json"
        monkeypatch.setattr(official, "fetch", lambda _url: "source")
        monkeypatch.setattr(official, "find_chunk_url", lambda _html: "chunk")
        monkeypatch.setattr(official, "parse_heroes_chunk", lambda _js: [{"id": 1}])
        monkeypatch.setattr(
            official,
            "transform",
            lambda raw: _hero_dict(raw["id"], [{"name": "技能", "description": "描述"}]),
        )
        monkeypatch.setattr(official, "validate_heroes", lambda heroes: heroes)

        official.crawl(output_path=str(output_path), skip_images=True)
        assert json.loads(output_path.read_text(encoding="utf-8")) == [
            _hero_dict(1, [{"name": "技能", "description": "描述"}])
        ]
        # 首次写入（无旧文件）不产生快照
        assert not (output_path.parent / "backups").exists()

    def test_crawl_snapshots_existing_file_before_overwrite(self, monkeypatch, tmp_path: Path) -> None:
        output_path = tmp_path / "heroes.json"
        output_path.write_text(json.dumps([_hero_dict(9, [{"name": "旧", "description": "旧"}])]), encoding="utf-8")
        monkeypatch.setattr(official, "fetch", lambda _url: "source")
        monkeypatch.setattr(official, "find_chunk_url", lambda _html: "chunk")
        monkeypatch.setattr(official, "parse_heroes_chunk", lambda _js: [{"id": 1}])
        monkeypatch.setattr(
            official,
            "transform",
            lambda raw: _hero_dict(raw["id"], [{"name": "技能", "description": "描述"}]),
        )
        monkeypatch.setattr(official, "validate_heroes", lambda heroes: heroes)

        official.crawl(output_path=str(output_path), skip_images=True)

        backups = list((tmp_path / "backups").glob("heroes-*.json"))
        assert len(backups) == 1
        assert json.loads(backups[0].read_text(encoding="utf-8")) == [
            _hero_dict(9, [{"name": "旧", "description": "旧"}])
        ]


class TestLocalExtensionFieldsPreserved:
    """本地扩展字段（称号/难度/模式梯队）不得被采集覆盖（VUL-B4-001）。

    title/difficulty/mode_viability 官网不提供、由用户手工维护，且
    hero_content_hash 刻意排除它们；采集若重新赋值，全量或指定采集会把
    用户编辑静默打回默认值。
    """

    def test_transform_does_not_emit_local_extension_fields(self) -> None:
        hero = crawler.transform({"id": 1, "name": "祖逖", "dynasty": "东晋"})

        assert hero is not None
        for field in crawler.LOCAL_EXTENSION_FIELDS:
            assert field not in hero

    def test_carry_over_restores_fields_from_previous_data(self) -> None:
        previous = [
            {"id": 1, "title": "闻鸡起舞者", "difficulty": 3, "mode_viability": {"2v2": "T1"}},
            {"id": 2, "title": "", "difficulty": 2, "mode_viability": {}},
        ]
        new_heroes = [{"id": 1, "name": "祖逖"}, {"id": 2, "name": "甲"}]

        carried = crawler.carry_over_local_fields(new_heroes, previous)

        assert carried[0]["title"] == "闻鸡起舞者"
        assert carried[0]["difficulty"] == 3
        assert carried[0]["mode_viability"] == {"2v2": "T1"}
        assert carried[1]["difficulty"] == 2

    def test_carry_over_ignores_missing_previous_file(self) -> None:
        new_heroes = [{"id": 1, "name": "祖逖"}]

        assert crawler.carry_over_local_fields(new_heroes, None) == new_heroes
        assert "title" not in new_heroes[0]

    def test_carry_over_leaves_new_heroes_untouched(self) -> None:
        """官网新增武将无旧记录，保留模型默认值。"""
        carried = crawler.carry_over_local_fields(
            [{"id": 99, "name": "新武将"}], [{"id": 1, "title": "旧称号", "difficulty": 5}],
        )

        assert "title" not in carried[0]
        assert "difficulty" not in carried[0]

    def test_incremental_replace_preserves_local_fields(self, monkeypatch, tmp_path: Path) -> None:
        """指定武将采集（replace 模式）不得把用户改过的难度打回默认值。"""
        output_path = tmp_path / "heroes.json"
        output_path.write_text(json.dumps([{
            "id": 1, "name": "祖逖", "title": "闻鸡起舞者", "faction": "东晋",
            "position": "攻击", "max_hp": 6, "max_hand": 3, "gender": "男",
            "skills": [{"name": "孤军自募", "description": "旧", "settlement": ""}],
            "difficulty": 3, "mode_viability": {"2v2": "T1"},
            "last_updated": "2026-10-01", "icon_url": "",
        }], ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(
            incremental,
            "transform",
            lambda raw: {
                "id": raw["id"], "name": raw["name"], "faction": "东晋",
                "position": "攻击", "max_hp": 6, "max_hand": 3, "gender": "男",
                "skills": [{"name": "孤军自募", "description": "新", "settlement": ""}],
                "icon_url": "", "last_updated": "2026-10-19",
            },
        )
        monkeypatch.setattr(incremental, "validate_heroes", lambda heroes: heroes)

        incremental.run([{"id": 1, "name": "祖逖"}], output_path,
                        dry_run=False, replace_ids={1}, skip_images=True)

        saved = json.loads(output_path.read_text(encoding="utf-8"))[0]
        assert saved["title"] == "闻鸡起舞者"
        assert saved["difficulty"] == 3
        assert saved["mode_viability"] == {"2v2": "T1"}
        # 官网字段照常更新
        assert saved["skills"][0]["description"] == "新"
        assert saved["last_updated"] == "2026-10-19"


class TestCleanHtmlTagStripping:
    """clean_html 必须剥除解码后显形的标签，但不得吞掉正文里的比较符（VUL-B1-001）。

    官网正文存在 `&lt;` 实体（如「手牌上限&lt;5」），解码后是字面 `<`；同时技能
    描述里有大量 `->`、`>0` 等比较符。实现必须区分「像标签的序列」与正文符号。
    """

    def test_strips_tag_revealed_by_entity_decoding(self) -> None:
        assert crawler.clean_html("<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>") == "alert(1)"

    def test_strips_nested_entity_encoded_tag(self) -> None:
        """多层编码（&amp;lt;）需要解码与剥离交替多轮。"""
        assert crawler.clean_html("&amp;lt;script&amp;gt;x&amp;lt;/script&amp;gt;") == "x"

    def test_strips_entity_encoded_img_onerror(self) -> None:
        assert crawler.clean_html("&lt;img src=x onerror=alert(1)&gt;") == ""

    def test_keeps_less_than_comparison(self) -> None:
        """真实数据用例：手牌上限&lt;5 必须保留为 <5，不能被当标签吞掉。"""
        assert crawler.clean_html("<p>手牌上限&lt;5，则失去效果。</p>") == "手牌上限<5，则失去效果。"

    def test_keeps_arrow_sequence(self) -> None:
        """真实数据用例：阶段顺序 开始-&gt;摸牌-&gt;出牌 必须保留。"""
        assert crawler.clean_html("阶段顺序：开始-&gt;摸牌-&gt;出牌") == "阶段顺序：开始->摸牌->出牌"

    def test_keeps_plain_comparison_between_text(self) -> None:
        assert crawler.clean_html("a &lt; b 且 c &gt; d") == "a < b 且 c > d"

    def test_plain_tags_still_stripped(self) -> None:
        assert crawler.clean_html("<b>加粗</b>正文") == "加粗正文"


class TestInvalidHeroIdHandling:
    """本地数据缺有效 id 时按损坏语义中止，不得在抓取完成后抛 KeyError（VUL-B4-002）。"""

    @pytest.mark.parametrize("bad_id", [0, -1, "abc", None])
    def test_load_existing_ids_treats_bad_id_as_corrupt(self, tmp_path: Path, bad_id) -> None:
        path = tmp_path / "heroes.json"
        path.write_text(json.dumps([{"id": 1}, {"id": bad_id}]), encoding="utf-8")

        assert incremental.load_existing_ids(path) is None
        assert len(list(tmp_path.glob("heroes.corrupt-*.json"))) == 1
        assert not path.exists()

    def test_load_existing_ids_accepts_valid_file(self, tmp_path: Path) -> None:
        path = tmp_path / "heroes.json"
        path.write_text(json.dumps([{"id": 1}, {"id": 2}]), encoding="utf-8")

        assert incremental.load_existing_ids(path) == {1, 2}
        assert not list(tmp_path.glob("heroes.corrupt-*.json"))

    def test_replace_mode_aborts_instead_of_keyerror(self, tmp_path: Path, monkeypatch) -> None:
        """替换模式遇缺 id 记录：应 SystemExit(1)，而不是 KeyError 裸崩。"""
        path = tmp_path / "heroes.json"
        path.write_text(json.dumps([{"id": 1, "name": "甲"}, {"name": "缺id"}],
                                   ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(
            incremental, "transform",
            lambda raw: {"id": raw["id"], "name": "x", "faction": "", "skills": []},
        )
        monkeypatch.setattr(incremental, "validate_heroes", lambda heroes: heroes)

        with pytest.raises(SystemExit) as excinfo:
            incremental.run([{"id": 1}], path, dry_run=False, replace_ids={1}, skip_images=True)

        assert excinfo.value.code == 1
        assert list(tmp_path.glob("heroes.corrupt-*.json"))
