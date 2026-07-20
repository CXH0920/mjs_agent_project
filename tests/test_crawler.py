"""官网头像安全下载测试。"""

from __future__ import annotations

from email.message import Message
from io import BytesIO
from pathlib import Path

from PIL import Image

from src.scraper import crawler


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
