"""官网头像安全下载测试。"""

from __future__ import annotations

import json
from email.message import Message
from io import BytesIO
from pathlib import Path

from PIL import Image

from src.scraper import crawler, incremental, official, official_adapter


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
        lambda raw: transform_calls.append(raw) or {"id": raw["id"], "faction": ""},
    )
    monkeypatch.setattr(official, "validate_heroes", lambda heroes: heroes)

    official.crawl(output_path=str(output_path), skip_images=True)

    assert transform_calls == raw_list
    assert json.loads(output_path.read_text(encoding="utf-8")) == [
        {"id": 1, "faction": ""},
        {"id": 2, "faction": ""},
    ]


def test_incremental_run_transforms_each_item_once(monkeypatch, tmp_path: Path) -> None:
    raw_list = [{"id": 1}, {"id": 2}]
    transform_calls: list[dict] = []

    monkeypatch.setattr(incremental, "transform", lambda raw: transform_calls.append(raw))

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
