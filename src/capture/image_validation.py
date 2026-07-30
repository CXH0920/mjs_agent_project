"""不可信图片输入的统一校验。"""

from __future__ import annotations

import io
from pathlib import Path
import warnings

from PIL import Image

MAX_IMAGE_SIZE_BYTES = 6 * 1024 * 1024
MAX_IMAGE_PIXELS = 4_000_000
_LOCAL_IMAGE_FORMATS = frozenset({"PNG", "JPEG"})


def load_local_image(path: str | Path) -> Image.Image:
    """加载经过大小、格式和像素限制校验的本地 PNG/JPEG 图片。"""
    image_path = Path(path)
    try:
        size = image_path.stat().st_size
    except OSError as error:
        raise ValueError(f"无法读取图片文件: {error}") from error
    if size > MAX_IMAGE_SIZE_BYTES:
        raise ValueError("图片文件大小超过 6 MiB 上限")
    return _load_validated_image(image_path, _LOCAL_IMAGE_FORMATS)


def load_png_image_bytes(data: bytes) -> Image.Image:
    """加载经过大小、格式和像素限制校验的 ADB PNG 数据。"""
    if len(data) > MAX_IMAGE_SIZE_BYTES:
        raise ValueError("图片数据大小超过 6 MiB 上限")
    return _load_validated_image(io.BytesIO(data), {"PNG"})


def _load_validated_image(source: Path | io.BytesIO, allowed_formats: set[str] | frozenset[str]) -> Image.Image:
    """校验图片实际内容并返回已独立加载的图像。"""
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(source) as image:
            image.verify()

        if isinstance(source, io.BytesIO):
            source.seek(0)
        with Image.open(source) as image:
            if image.format not in allowed_formats:
                allowed = "/".join(sorted(allowed_formats))
                raise ValueError(f"图片格式必须为 {allowed}")
            width, height = image.size
            if width * height > MAX_IMAGE_PIXELS:
                raise ValueError("图片像素数超过 4,000,000 上限")
            image.load()
            return image.copy()
