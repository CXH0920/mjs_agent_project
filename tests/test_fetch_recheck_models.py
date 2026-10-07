# -*- coding: utf-8 -*-
"""v4 复核模型预取脚本（fetch_recheck_models）幂等分支单元测试。

mock urlopen 与目标目录（绝不触网、绝不写入真实 rapidocr models/），
覆盖 fetch() 四分支：缺失下载就位、已就位跳过、哈希不符修复重下、
下载件哈希不符删 .part 且不污染原文件。
"""

import hashlib
import urllib.request

import pytest
import src.scripts.fetch_recheck_models as fetch_mod


class _FakeResponse:
    """模拟 urlopen 响应：read 一次取空，触发 _download 的流式终止。"""

    def __init__(self, payload: bytes):
        self._payload = payload
        self.headers = {"Content-Length": str(len(payload))}

    def read(self, size: int = -1) -> bytes:
        out, self._payload = self._payload, b""
        return out

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


@pytest.fixture
def fetch_env(tmp_path, monkeypatch):
    """隔离目标目录与假模型清单，mock urlopen 并记录调用。"""
    target = tmp_path / "models"
    target.mkdir()
    monkeypatch.setattr(fetch_mod, "_models_dir", lambda: target)
    payload = b"fake-v4-model-bytes"
    monkeypatch.setattr(
        fetch_mod,
        "V4_MODEL_FILES",
        [
            (
                "fake_model.onnx",
                "https://example.invalid/fake.onnx",
                hashlib.sha256(payload).hexdigest(),
            )
        ],
    )
    state = {"payload": payload, "calls": []}

    def fake_urlopen(request, timeout=60):
        state["calls"].append(request.full_url)
        return _FakeResponse(state["payload"])

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return target, state


def test_fetch_downloads_missing_model(fetch_env) -> None:
    target, state = fetch_env
    assert fetch_mod.fetch() == 0
    assert state["calls"] == ["https://example.invalid/fake.onnx"]
    assert (target / "fake_model.onnx").read_bytes() == state["payload"]
    assert not list(target.glob("*.part"))


def test_fetch_second_run_skips_download(fetch_env) -> None:
    target, state = fetch_env
    assert fetch_mod.fetch() == 0
    state["calls"].clear()
    assert fetch_mod.fetch() == 0
    # 幂等：已就位且哈希匹配，第二次不再发起任何下载
    assert state["calls"] == []
    assert (target / "fake_model.onnx").read_bytes() == state["payload"]


def test_fetch_repairs_hash_mismatch(fetch_env) -> None:
    target, state = fetch_env
    dest = target / "fake_model.onnx"
    dest.write_bytes(b"corrupted-on-disk")
    assert fetch_mod.fetch() == 0
    assert state["calls"] != []
    assert dest.read_bytes() == state["payload"]


def test_fetch_failure_removes_part_and_keeps_dest(fetch_env) -> None:
    target, state = fetch_env
    dest = target / "fake_model.onnx"
    dest.write_bytes(b"previous-content")
    state["payload"] = b"corrupted-download"
    assert fetch_mod.fetch() == 1
    assert not list(target.glob("*.part"))
    # 坏下载件不得覆盖原文件
    assert dest.read_bytes() == b"previous-content"
