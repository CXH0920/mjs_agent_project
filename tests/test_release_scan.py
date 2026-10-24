"""release.py 纯净度 api key 扫描的行为回归。

覆盖 2026-10-24 修复：目录级 OSError 不得静默截断扫描（否则敏感内容
最后防线失效），覆盖不完整必须终止发版而非放行。
"""

import os

import pytest
import release


def test_scan_apikey_passes_clean_tree(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.py").write_text("x = 1", encoding="utf-8")
    (tmp_path / "b.json").write_text("{}", encoding="utf-8")
    release._scan_apikey(tmp_path)  # 不抛即通过


def test_scan_apikey_detects_key(tmp_path, capsys):
    (tmp_path / "a.py").write_text(
        'key = "sk-abcdefghijklmnopqrstuvwxyz012345"', encoding="utf-8")
    with pytest.raises(SystemExit) as ei:
        release._scan_apikey(tmp_path)
    assert ei.value.code == 1
    assert "api key" in capsys.readouterr().out


def test_scan_apikey_dies_on_incomplete_scan(tmp_path, monkeypatch, capsys):
    (tmp_path / "a.py").write_text("x = 1", encoding="utf-8")
    real_walk = os.walk

    def walk_with_error(top, **kwargs):
        # 模拟遍历中途的目录级 OSError（长路径/权限），onerror 应被收集并终止
        onerror = kwargs.get("onerror")
        if onerror is not None:
            onerror(OSError(206, "文件名或扩展名太长", str(tmp_path / "deep")))
        yield from real_walk(top, **kwargs)

    monkeypatch.setattr(release.os, "walk", walk_with_error)
    with pytest.raises(SystemExit) as ei:
        release._scan_apikey(tmp_path)
    assert ei.value.code == 1
    assert "未完整覆盖" in capsys.readouterr().out
