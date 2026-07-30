"""不可信图片输入校验测试。"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from src.capture import image_validation


def _image_bytes(image_format: str = "PNG", size: tuple[int, int] = (2, 2)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "red").save(buffer, format=image_format)
    return buffer.getvalue()


def test_load_local_image_accepts_actual_png_and_jpeg(tmp_path) -> None:
    png_path = tmp_path / "image.png"
    jpeg_path = tmp_path / "image.jpg"
    Image.new("RGB", (2, 3)).save(png_path)
    Image.new("RGB", (3, 2)).save(jpeg_path)

    assert image_validation.load_local_image(png_path).size == (2, 3)
    assert image_validation.load_local_image(jpeg_path).size == (3, 2)


def test_load_local_image_rejects_disguised_non_image(tmp_path) -> None:
    path = tmp_path / "not-an-image.png"
    path.write_bytes(b"not an image")

    with pytest.raises(Exception):
        image_validation.load_local_image(path)


def test_load_png_image_bytes_rejects_non_png() -> None:
    with pytest.raises(ValueError, match="PNG"):
        image_validation.load_png_image_bytes(_image_bytes("JPEG"))


def test_image_validation_rejects_oversized_input(monkeypatch) -> None:
    monkeypatch.setattr(image_validation, "MAX_IMAGE_SIZE_BYTES", 1)

    with pytest.raises(ValueError, match="大小"):
        image_validation.load_png_image_bytes(_image_bytes())


def test_image_validation_rejects_excessive_pixels(monkeypatch) -> None:
    monkeypatch.setattr(image_validation, "MAX_IMAGE_PIXELS", 3)

    with pytest.raises(ValueError, match="像素"):
        image_validation.load_png_image_bytes(_image_bytes(size=(2, 2)))


def test_image_validation_promotes_decompression_bomb_warning(monkeypatch) -> None:
    monkeypatch.setattr(image_validation.Image, "MAX_IMAGE_PIXELS", 3)

    with pytest.raises(image_validation.Image.DecompressionBombWarning):
        image_validation.load_png_image_bytes(_image_bytes(size=(2, 2)))
