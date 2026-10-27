"""对局攻略页锚点判定测试（双锚点 AND + 期望位置校验）。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import cv2
import numpy as np
import pytest
from src.ocr import template_manager as tm_module
from src.ocr.template_manager import TemplateManager

# 与随包 anchors.json 一致的参考坐标（合成资源，纹理独立于入库图片）
ANCHOR_SLOTS = ((65, 810), (75, 1230))
ANCHOR_SIZE = (70, 50)
SHOT_DIR = Path(__file__).resolve().parent.parent / "screenshots" / "test_2v2_duel"
REAL_NEGATIVE = {
    "screenshot_20260728_172152.png",
    "screenshot_20260729_222227.png",
    "screenshot_20260729_222259.png",
    "screenshot_20260907_182814.png",
}


def _anchor_texture(seed: int) -> np.ndarray:
    """低频渐变纹理：小图随机放大而成，等比缩放后仍保持高相关。"""
    rng = np.random.default_rng(seed)
    base = rng.integers(0, 256, (8, 12), dtype=np.uint8)
    return cv2.resize(base, ANCHOR_SIZE, interpolation=cv2.INTER_LINEAR)


class _AnchorEnv:
    """合成锚点资源 + 贴图工具：黑底参考帧按参考坐标 × scale 贴锚点纹理。"""

    def __init__(self, tmp_path: Path) -> None:
        assets = tmp_path / "match_guide"
        assets.mkdir()
        self.textures = [_anchor_texture(101), _anchor_texture(102)]
        anchors = []
        for i, (name, (x, y)) in enumerate(zip(("gift", "zhengli"), ANCHOR_SLOTS, strict=False)):
            ok, buf = cv2.imencode(".png", self.textures[i])
            assert ok
            (assets / f"anchor_{name}.png").write_bytes(buf.tobytes())
            anchors.append(
                {"name": name, "file": f"anchor_{name}.png", "x": x, "y": y,
                 "w": ANCHOR_SIZE[0], "h": ANCHOR_SIZE[1]})
        manifest = {"reference_width": 2560, "reference_height": 1440, "anchors": anchors}
        (assets / "anchors.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.manifest_path = assets / "anchors.json"

    def manager(self) -> TemplateManager:
        return TemplateManager(template_name="match_guide", anchor_manifest_path=self.manifest_path)

    def frame(
        self,
        scale: float = 1.0,
        shape: tuple[int, int] = (1440, 2560),
        offset: tuple[int, int] = (0, 0),
        include: tuple[bool, bool] = (True, True),
    ) -> np.ndarray:
        frame = np.zeros((shape[0], shape[1], 3), dtype=np.uint8)
        for i, (x, y) in enumerate(ANCHOR_SLOTS):
            if not include[i]:
                continue
            texture = self.textures[i]
            if scale != 1.0:
                texture = cv2.resize(
                    texture,
                    (max(1, round(ANCHOR_SIZE[0] * scale)), max(1, round(ANCHOR_SIZE[1] * scale))),
                    interpolation=cv2.INTER_AREA,
                )
            pos_x = round(x * scale) + offset[0]
            pos_y = round(y * scale) + offset[1]
            frame[pos_y:pos_y + texture.shape[0], pos_x:pos_x + texture.shape[1]] = texture[:, :, np.newaxis]
        return frame


@pytest.fixture()
def anchor_env(tmp_path) -> _AnchorEnv:
    return _AnchorEnv(tmp_path)


def _noise_frame(shape: tuple[int, int] = (1440, 2560)) -> np.ndarray:
    rng = np.random.default_rng(7)
    return rng.integers(0, 256, (shape[0], shape[1], 3), dtype=np.uint8)


def test_anchor_matches_base_scale_frame(anchor_env) -> None:
    manager = anchor_env.manager()
    matched, confidence = manager.match(anchor_env.frame(), threshold=0.8)

    assert matched
    assert confidence >= 0.85
    assert manager.last_match_strategy == "anchors_local"
    assert manager.last_match_scale == 1.0


def test_anchor_matches_uniformly_scaled_frame(anchor_env) -> None:
    """等比分辨率（720p）下基准缩放即真实渲染比例，直接局部命中。"""
    manager = anchor_env.manager()
    matched, confidence = manager.match(anchor_env.frame(scale=0.5, shape=(720, 1280)), threshold=0.8)

    assert matched
    assert confidence >= 0.85
    assert manager.last_match_strategy == "anchors_local"
    assert manager.last_match_scale == 0.5


def test_anchor_rejects_displaced_patches(anchor_env) -> None:
    """贴片整体偏移 20px（超出全图兜底宽容差 12px）时分数再高也拒识。"""
    manager = anchor_env.manager()
    matched, _ = manager.match(anchor_env.frame(offset=(20, 20)), threshold=0.8)

    assert not matched
    assert manager.last_match_strategy == "anchors_miss"


def test_anchor_full_fallback_absorbs_slight_shift(anchor_env) -> None:
    """偏移 8px：基准层容差（6px）拒绝、全图兜底宽容差（12px）吸收命中。"""
    manager = anchor_env.manager()
    matched, confidence = manager.match(anchor_env.frame(offset=(8, 8)), threshold=0.8)

    assert matched
    assert confidence >= 0.85
    assert manager.last_match_strategy == "anchors_full_fallback"


def test_anchor_rejects_single_patch(anchor_env) -> None:
    manager = anchor_env.manager()
    matched, _ = manager.match(anchor_env.frame(include=(True, False)), threshold=0.8)

    assert not matched


def test_anchor_rejects_noise_frame(anchor_env) -> None:
    manager = anchor_env.manager()
    matched, _ = manager.match(_noise_frame(), threshold=0.8)

    assert not matched
    assert manager.last_match_strategy == "anchors_miss"


def test_anchor_falls_back_to_template_when_manifest_missing(tmp_path) -> None:
    """锚点清单缺失时回退大模板漏斗，strategy 回到大模板语义。"""
    manager = TemplateManager(
        template_name="match_guide", anchor_manifest_path=tmp_path / "missing.json")

    matched, _ = manager.match(_noise_frame(), threshold=0.8)

    assert not matched
    assert not manager.last_match_strategy.startswith("anchors")


def test_anchor_active_even_when_template_missing(anchor_env, tmp_path, monkeypatch) -> None:
    """锚点判定不依赖大模板：模板与随包回退均缺失时锚点分支仍然生效。"""
    monkeypatch.setattr(tm_module, "BUNDLED_TEMPLATE_DIR", tmp_path / "no_bundle")
    manager = TemplateManager(
        tmp_path / "missing.png",
        template_name="match_guide",
        anchor_manifest_path=anchor_env.manifest_path,
    )
    assert manager._template is None

    matched, _ = manager.match(anchor_env.frame(), threshold=0.8)

    assert matched
    assert manager.last_match_strategy == "anchors_local"

def test_anchor_stale_warning_after_consecutive_misses(anchor_env, caplog) -> None:
    """连续 50 轮未命中恰好打一条 WARNING（改版静默失效的显式出口），之后不再重复。"""
    manager = anchor_env.manager()
    frame = _noise_frame((360, 640))
    with caplog.at_level(logging.WARNING, logger="src.ocr.template_manager"):
        for _ in range(55):
            manager.match(frame, threshold=0.8)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "锚点连续" in r.message]
    assert len(warnings) == 1


def test_anchor_cached_local_revives_previous_scale(anchor_env) -> None:
    """分辨率变化但内容仍按上一轮比例渲染时，时序缓存复验优先命中。"""
    manager = anchor_env.manager()
    assert manager.match(anchor_env.frame(scale=0.5, shape=(720, 1280)), threshold=0.8)[0]

    # base_scale 变为 0.4583，但内容仍按 0.5 比例渲染（模拟分辨率切换后布局未重排）
    matched, confidence = manager.match(anchor_env.frame(scale=0.5, shape=(660, 1180)), threshold=0.8)

    assert matched
    assert confidence >= 0.85
    assert manager.last_match_strategy == "anchors_cached_local"
    assert manager.last_match_scale == 0.5


def test_anchor_ref_local_matches_reference_scale_rendering(anchor_env) -> None:
    """高分屏（base_scale=1.5）上内容按参考原尺寸渲染时由 1.0 参考档命中。"""
    manager = anchor_env.manager()
    matched, _ = manager.match(anchor_env.frame(scale=1.0, shape=(2160, 3840)), threshold=0.8)

    assert matched
    assert manager.last_match_strategy == "anchors_ref_local"
    assert manager.last_match_scale == 1.0


def test_anchor_threshold_floor_rejects_weak_patches(anchor_env) -> None:
    """外部阈值 0.6 低于分数下限 0.85：弱相似贴片（分数居中）仍被拒识。"""
    manager = anchor_env.manager()
    frame = np.zeros((1440, 2560, 3), dtype=np.uint8)
    rng = np.random.default_rng(9)
    for i, (x, y) in enumerate(ANCHOR_SLOTS):
        weak = cv2.addWeighted(anchor_env.textures[i], 0.6, rng.integers(0, 256, (50, 70), dtype=np.uint8), 0.4, 0)
        frame[y:y + 50, x:x + 70] = weak[:, :, np.newaxis]

    matched, _ = manager.match(frame, threshold=0.6)

    assert not matched


def test_anchor_survives_small_frame_without_error(anchor_env) -> None:
    """小图上锚点期望窗口越界（1.0 参考档 y=810/1230 超出 720 高度）不抛异常。"""
    manager = anchor_env.manager()
    matched, _ = manager.match(_noise_frame((720, 1280)), threshold=0.8)

    assert not matched
    assert manager.last_match_strategy == "anchors_miss"


@pytest.mark.skipif(not SHOT_DIR.exists(), reason="本地截图集不入库，存在时才回归")
def test_anchor_regression_on_real_screenshots() -> None:
    """30 张真机截图回归：26 张按钮栏可见全部命中，4 张回放画面全部拒识。"""
    manager = TemplateManager(template_name="match_guide")
    checked = 0
    for path in sorted(SHOT_DIR.glob("*.png")):
        image = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        if image is None or image.shape[:2] != (1440, 2560):
            continue
        checked += 1
        matched, _ = manager.match(image, threshold=0.6)
        if path.name in REAL_NEGATIVE:
            assert not matched, path.name
        else:
            assert matched, path.name
    assert checked >= 30


def test_corrupt_user_template_falls_back_to_bundled(tmp_path):
    """半截 PNG（写入中断遗留）与缺失同待遇回退随包模板，不再 match() 恒 False。"""
    user = tmp_path / "user_template.png"
    user.write_bytes(b"\x89PNG\r\n\x1a\n")  # 只有签名头，imdecode 必失败
    manager = TemplateManager(template_path=user, template_name="hero_selection")
    assert manager.is_loaded
    assert manager._loaded_template_path.name == "wujiang_select.png"


def test_set_template_write_failure_keeps_original(tmp_path, monkeypatch):
    """写盘失败（磁盘满/文件被锁）时原模板保持完整，且不残留 .tmp。"""
    import os

    user = tmp_path / "user_template.png"
    ok, buf = cv2.imencode(".png", np.zeros((60, 60), dtype=np.uint8))
    assert ok
    user.write_bytes(buf.tobytes())
    original = user.read_bytes()
    manager = TemplateManager(template_path=user, template_name="hero_selection")

    def _disk_full(fd):
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", _disk_full)
    with pytest.raises(OSError):
        manager.set_template(
            np.zeros((200, 200, 3), dtype=np.uint8), (10, 10, 50, 50))
    assert user.read_bytes() == original
    assert list(tmp_path.glob(".*.tmp")) == []


def test_anchor_failure_warning_deduped_per_manifest_version(tmp_path, caplog) -> None:
    """锚点清单损坏的回退态：轮询每任务新建实例只告警一次，清单更新后再告警。"""
    import logging as logging_module

    tm_module._ANCHOR_FAIL_WARNED.clear()
    assets = tmp_path / "match_guide"
    assets.mkdir()
    manifest = {"reference_width": 2560, "reference_height": 1440, "anchors": "not-a-list"}
    manifest_path = assets / "anchors.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with caplog.at_level(logging_module.WARNING, logger="src.ocr.template_manager"):
        for _ in range(5):  # 模拟 ocr_worker 每任务新建实例（每拍重跑 _load_anchors）
            TemplateManager(template_name="match_guide", anchor_manifest_path=manifest_path)
        warnings = [r for r in caplog.records if "锚点加载失败" in r.message]
        assert len(warnings) == 1  # 同一清单版本只告警一次，不再每拍刷屏

        manifest_path.write_text(json.dumps({
            "reference_width": 2560, "reference_height": 1440,
            "anchors": {"still": "broken"},
        }), encoding="utf-8")
        TemplateManager(template_name="match_guide", anchor_manifest_path=manifest_path)
        warnings = [r for r in caplog.records if "锚点加载失败" in r.message]
        assert len(warnings) == 2  # 清单变化（mtime）后重新告警，信号不失活
