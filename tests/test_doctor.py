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


def test_run_checks_survives_system_exit(monkeypatch):
    """回归：pull_data._manifest 缺 manifest 时 sys.exit——SystemExit 不是
    Exception 子类，原 `except Exception` 兜不住会杀死整份报告"""
    def boom():
        import sys
        sys.exit("FAIL: 找不到 manifest.json")

    monkeypatch.setattr(doctor, "CHECK_GROUPS", [("测试组", boom)])
    findings = doctor.run_checks(offline=True)

    assert len(findings) == 1
    assert findings[0].status == doctor.FAIL
    assert "manifest.json" in findings[0].detail


def test_check_private_repo_flags_missing_manifest(tmp_path, monkeypatch):
    """回归：私有仓存在但无 manifest 时应得 FAIL finding（带修复指引），
    而不是被 pull_data._manifest 的 sys.exit 穿透"""
    repo = tmp_path / "mjs_data_private"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.setenv("MJS_DATA_REPO", str(repo))

    findings = doctor.check_private_repo()

    manifest = next(f for f in findings if f.name == "manifest 一致性")
    assert manifest.status == doctor.FAIL
    assert "gen_manifest" in manifest.fix


def test_render_contains_tally_line():
    findings = [
        doctor.Finding("组", "项A", doctor.PASS, "ok"),
        doctor.Finding("组", "项B", doctor.WARN, "注意", fix="做点什么"),
    ]
    text = doctor.render(findings, 1.2)

    assert "[PASS] 组/项A: ok" in text
    assert "↳ 修复: 做点什么" in text
    assert "1 PASS / 1 WARN / 0 FAIL / 0 SKIP" in text


def test_check_data_reports_load_issues(tmp_path, monkeypatch):
    """load_issues 出口：模型级加载问题（invalid_json 等）必须可见。"""
    monkeypatch.setattr(doctor, "ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "heroes.json").write_text('{"截断": ', encoding="utf-8")

    findings = doctor.check_data()

    load_issues = next(f for f in findings if f.name == "load_issues")
    assert load_issues.status == doctor.FAIL
    assert "invalid_json" in load_issues.detail


def test_check_data_load_issues_pass_when_clean(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    for name in ("heroes.json", "guides.json", "synergies.json"):
        (tmp_path / "data" / name).write_text("[]", encoding="utf-8")

    findings = doctor.check_data()

    load_issues = next(f for f in findings if f.name == "load_issues")
    assert load_issues.status == doctor.PASS


def test_check_private_repo_flags_worktree_divergence(tmp_path, monkeypatch):
    """工作区同步：本地文件与私有仓 manifest 指纹不同 = 未 push 的修改，须 WARN。"""
    import hashlib
    import json

    repo = tmp_path / "mjs_data_private"
    (repo / ".git").mkdir(parents=True)
    (repo / "data").mkdir()
    (repo / "data" / "cards.json").write_text('[{"id": 1}]', encoding="utf-8")
    sha = hashlib.sha256((repo / "data" / "cards.json").read_bytes()).hexdigest()
    (repo / "manifest.json").write_text(
        json.dumps({"files": [{"path": "data/cards.json", "sha256": sha, "bytes": 10}]}),
        encoding="utf-8")
    monkeypatch.setenv("MJS_DATA_REPO", str(repo))
    monkeypatch.setattr(doctor, "ROOT", tmp_path / "project")
    # manifest 预检通过但 verify 走 _manifest——此处直接验证 _check_worktree_sync
    (tmp_path / "project" / "data").mkdir(parents=True)
    (tmp_path / "project" / "data" / "cards.json").write_text('[{"id": 1, "local": true}]', encoding="utf-8")

    finding = doctor._check_worktree_sync(repo)

    assert finding.status == doctor.WARN
    assert "data/cards.json" in finding.detail

    # 本地与私有仓一致 → PASS
    (tmp_path / "project" / "data" / "cards.json").write_text('[{"id": 1}]', encoding="utf-8")
    finding = doctor._check_worktree_sync(repo)
    assert finding.status == doctor.PASS


def test_check_ai_link_ignores_token_counts(tmp_path, monkeypatch):
    """HTTP 401 精确匹配故障主行；token 统计行（completion=4012）不得误计。"""
    import time

    monkeypatch.setattr(doctor, "ROOT", tmp_path)
    log_dir = tmp_path / "logs" / "scraper"
    log_dir.mkdir(parents=True)
    today = time.strftime("%Y-%m-%d")
    (log_dir / "ai_generation.log").write_text(
        f"{today} 10:00:00 [INFO] subprocess.ai.stdout: [刘备] token: prompt=4327 completion=4012 (reasoning=0, content=4012)\n"
        f"{today} 10:00:05 [ERROR] src.scraper.ai.api_generator: API 请求不可重试: HTTP 401（不再重试）\n",
        encoding="utf-8",
    )

    findings = doctor.check_ai_link()

    assert len(findings) == 1
    assert findings[0].status == doctor.WARN
    # 仅故障主行命中一次；token 行若被误计会出现 "401×2"
    assert findings[0].detail == "HTTP 401×1"


def _patch_rag_paths(monkeypatch, tmp_path):
    from src.rag import config

    monkeypatch.setattr(config, "CHROMA_DIR", tmp_path / "rag_index" / "chroma")
    monkeypatch.setattr(config, "RAG_INDEX_DIR", tmp_path / "rag_index")
    monkeypatch.setattr(config, "ONNX_MODEL_DIR", tmp_path / "onnx")
    monkeypatch.setattr(config, "EMBEDDING_BACKEND", "onnx")
    return config


def test_check_rag_index_missing_dir_warns(tmp_path, monkeypatch):
    _patch_rag_paths(monkeypatch, tmp_path)

    findings = doctor.check_rag_index()

    assert len(findings) == 1
    assert findings[0].status == doctor.WARN
    assert "src.rag.indexer" in findings[0].fix


def test_check_rag_index_pass_when_small_and_single_segment(tmp_path, monkeypatch):
    config = _patch_rag_paths(monkeypatch, tmp_path)
    chroma = config.CHROMA_DIR
    (chroma / "seg-1").mkdir(parents=True)
    (chroma / "chroma.sqlite3").write_bytes(b"x" * 1024)
    # 合法 ONNX 三件套
    import hashlib
    import json as jsonlib

    onnx_dir = config.ONNX_MODEL_DIR
    onnx_dir.mkdir()
    (onnx_dir / "model.onnx").write_bytes(b"m")
    (onnx_dir / "tokenizer.json").write_text("{}", encoding="utf-8")
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    (onnx_dir / "sha256.json").write_text(jsonlib.dumps({
        "model.onnx": {"sha256": sha(onnx_dir / "model.onnx")},
        "tokenizer.json": {"sha256": sha(onnx_dir / "tokenizer.json")},
        "input_names": ["input_ids", "attention_mask", "token_type_ids"],
    }), encoding="utf-8")

    findings = doctor.check_rag_index()

    by_name = {f.name: f for f in findings}
    assert by_name["索引体积"].status == doctor.PASS
    assert by_name["集合段目录"].status == doctor.PASS
    assert by_name["ONNX 编码器"].status == doctor.PASS


def test_check_rag_index_flags_bloat_orphans_and_missing_onnx(tmp_path, monkeypatch):
    config = _patch_rag_paths(monkeypatch, tmp_path)
    chroma = config.CHROMA_DIR
    (chroma / "seg-1").mkdir(parents=True)
    (chroma / "seg-2").mkdir(parents=True)
    (chroma / "s").mkdir()
    (chroma / "s" / "big.bin").write_bytes(b"x" * (30 * 1048576))  # 30MB → 超标

    findings = doctor.check_rag_index()

    by_name = {f.name: f for f in findings}
    assert by_name["索引体积"].status == doctor.WARN
    assert by_name["集合段目录"].status == doctor.WARN
    assert by_name["ONNX 编码器"].status == doctor.FAIL
    assert "export_embedding_onnx" in by_name["ONNX 编码器"].fix


def test_check_rag_index_skips_onnx_when_st_backend(tmp_path, monkeypatch):
    config = _patch_rag_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "EMBEDDING_BACKEND", "st")
    (config.CHROMA_DIR / "seg-1").mkdir(parents=True)

    findings = doctor.check_rag_index()

    onnx = next(f for f in findings if f.name == "ONNX 编码器")
    assert onnx.status == doctor.SKIP
    assert "st" in onnx.detail


# ---------------------------------------------------------------------------
# 首启向导 T1：check_environment 拆分与 run_checks(checks=…) 子集（spec_onboarding §3.3）
# ---------------------------------------------------------------------------

def test_check_environment_is_concat_of_split_parts():
    """拆分等价性：环境组输出 = 工具链 + 运行时（顺序与条目逐项一致，CLI 不变）。"""
    assert doctor.check_environment() == (
        doctor.check_dev_toolchain() + doctor.check_runtime_env())


def test_check_dev_toolchain_only_reports_ruff(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "ROOT", tmp_path)
    (tmp_path / ".pre-commit-config.yaml").write_text(
        "- repo: ruff\n  hooks:\n    - id: ruff\n      args: [check, src, tests, (1.1.1)]\n",
        encoding="utf-8")

    findings = doctor.check_dev_toolchain()

    assert [f.name for f in findings] == ["ruff 版本三处一致"]
    assert findings[0].status == doctor.FAIL  # environment.yml 两处缺失 → None


def test_check_runtime_env_flags_missing_dependency(monkeypatch):
    import importlib.util

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)

    findings = doctor.check_runtime_env()

    imports = {f.name: f for f in findings if f.name.endswith("可导入")}
    assert imports["onnxruntime 可导入"].status == doctor.FAIL
    assert "environment.yml" in imports["onnxruntime 可导入"].fix


def test_check_runtime_env_flags_missing_models(monkeypatch):
    from src.scripts import fetch_recheck_models

    monkeypatch.setattr(fetch_recheck_models, "verify_v4_models",
                        lambda: ["v4 rec 模型缺失"])

    findings = doctor.check_runtime_env()

    models = next(f for f in findings if f.name == "OCR 模型（v6 内置 + v4 预取）")
    assert models.status == doctor.WARN
    assert "v4 rec 模型缺失" in models.detail
    assert "fetch_recheck_models" in models.fix


def test_run_checks_accepts_check_subset():
    def only_one():
        return [doctor.Finding("子集", "单项", doctor.PASS, "ok")]

    findings = doctor.run_checks(offline=True, checks=[("子集", only_one)])

    assert len(findings) == 1
    assert findings[0].group == "子集"


def test_run_checks_default_reads_patched_groups(monkeypatch):
    """守护默认值语义：不传 checks 时必须读运行时模块属性（形参默认值绑定旧引用
    会让 monkeypatch 静默失效，spec_onboarding §3.3 实现注意）。"""
    def boom():
        raise RuntimeError("子集检查器崩溃")

    monkeypatch.setattr(doctor, "CHECK_GROUPS", [("守护组", boom)])

    findings = doctor.run_checks(offline=True)

    assert len(findings) == 1
    assert findings[0].group == "守护组"
    assert findings[0].status == doctor.FAIL
