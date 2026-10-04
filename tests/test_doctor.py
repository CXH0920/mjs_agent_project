"""doctor 自检核心检查函数回归：坏数据必须 FAIL，输出格式稳定。"""

from __future__ import annotations

from src.scripts import doctor


def test_check_data_flags_broken_json(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "broken.json").write_text('{"截断": ', encoding="utf-8")
    (tmp_path / "data" / "good.json").write_text("[]", encoding="utf-8")

    findings = doctor.check_data()

    broken = next(f for f in findings if f.name == "data/*.json 可解析")
    assert broken.status == doctor.FAIL
    assert "broken.json" in broken.detail
    assert "从 data/backups 恢复" in broken.fix


def test_check_data_flags_empty_key_files(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "heroes.json").write_text("[]", encoding="utf-8")

    findings = doctor.check_data()

    heroes = next(f for f in findings if f.name == "data/heroes.json")
    assert heroes.status == doctor.FAIL
    assert "不可逆丢失" in heroes.detail


def test_check_data_flags_stale_baseline(tmp_path, monkeypatch):
    import os
    import time

    monkeypatch.setattr(doctor, "ROOT", tmp_path)
    backup_dir = tmp_path / "data" / "backups"
    backup_dir.mkdir(parents=True)
    old = backup_dir / "heroes-20260101-000000-000000.json"
    old.write_text("[]", encoding="utf-8")
    stale = time.time() - 40 * 86400
    os.utime(old, (stale, stale))

    findings = doctor.check_data()

    heroes = next(f for f in findings if f.name == "backups/heroes")
    assert heroes.status == doctor.WARN
    assert "40 天" in heroes.detail


def test_check_config_flags_dead_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "ROOT", tmp_path)
    (tmp_path / "config.env").write_text(
        "MUMU_ADB_PORT=16448\nRAG_PROJECT_DIR=legacy\n", encoding="utf-8")
    (tmp_path / "config.env.example").write_text("", encoding="utf-8")

    findings = doctor.check_config()

    dead = next(f for f in findings if f.name == "死键")
    assert dead.status == doctor.WARN
    assert "RAG_PROJECT_DIR" in dead.detail
    assert "从 config.env 删除" in dead.fix


def test_check_security_flags_tracked_secret(tmp_path, monkeypatch):
    """敏感文件被 git 跟踪 = 会随仓库分发，必须 FAIL"""
    monkeypatch.setattr(doctor, "ROOT", tmp_path)
    (tmp_path / "config.env").write_text("DEEPSEEK_API_KEY=sk-x", encoding="utf-8")

    def fake_run(cmd, **_kwargs):
        import subprocess

        if cmd[:2] == ["git", "check-ignore"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="config.env", stderr="")

    monkeypatch.setattr(doctor.subprocess, "run", fake_run)

    findings = doctor.check_security()

    finding = next(f for f in findings if f.name == "config.env")
    assert finding.status == doctor.FAIL
    assert "被 git 跟踪" in finding.detail


def test_run_checks_survives_checker_exception(monkeypatch):
    """单组检查器异常不得拖垮整份报告：以 FAIL 呈现而非崩溃"""
    def boom():
        raise RuntimeError("模拟检查器崩溃")

    monkeypatch.setattr(doctor, "CHECK_GROUPS", [("测试组", boom)])
    findings = doctor.run_checks(offline=True)

    assert len(findings) == 1
    assert findings[0].status == doctor.FAIL
    assert "模拟检查器崩溃" in findings[0].detail


def test_render_contains_tally_line():
    findings = [
        doctor.Finding("组", "项A", doctor.PASS, "ok"),
        doctor.Finding("组", "项B", doctor.WARN, "注意", fix="做点什么"),
    ]
    text = doctor.render(findings, 1.2)

    assert "[PASS] 组/项A: ok" in text
    assert "↳ 修复: 做点什么" in text
    assert "1 PASS / 1 WARN / 0 FAIL / 0 SKIP" in text
