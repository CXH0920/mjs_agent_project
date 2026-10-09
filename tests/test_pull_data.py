"""pull_data 私有数据仓同步回归：有效性守卫 / 原子落位 / 脏工作区 / 真实 push 链路。

私有仓是抓取/维护数据的唯一异地副本：push 方向没有旧文件兜底，
坏数据一旦入仓即覆盖好副本——守卫与脏工作区拒绝是生命线的前置闸门。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from src.scripts import pull_data

HEROES = [
    {"id": i, "name": f"武将{i}", "faction": "魏",
     "skills": [{"name": "技能", "description": "描述"}]}
    for i in range(1, 151)
]

# 测试私有仓用的 manifest 生成器：与私有仓 scripts/gen_manifest.py 同语义
_GEN_MANIFEST_SRC = '''"""测试用 manifest 生成器（与私有仓 scripts/gen_manifest.py 同语义）。"""
import datetime, hashlib, json, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
EXCLUDE = {"manifest.json", "README.md", "scripts"}
files = [
    {"path": p.relative_to(ROOT).as_posix(),
     "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
     "bytes": p.stat().st_size}
    for p in sorted(ROOT.rglob("*"))
    if p.is_file() and p.relative_to(ROOT).parts[0] not in EXCLUDE
    and ".git" not in p.relative_to(ROOT).parts
]
(ROOT / "manifest.json").write_text(
    json.dumps({"generated_at": str(datetime.datetime.now()), "files": files}, ensure_ascii=False),
    encoding="utf-8")
'''


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def sync_env(tmp_path, monkeypatch):
    """项目侧工作区 ROOT + 真实 git 私有仓（init → 种子提交 → bare 远端 → upstream）。

    push 流程内的自动 pull --ff-only 与 push origin HEAD 走真实 git，零 mock。
    """
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(pull_data, "ROOT", root)

    repo = tmp_path / "mjs_data_private"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "gen_manifest.py").write_text(_GEN_MANIFEST_SRC, encoding="utf-8")
    (repo / "README.md").write_text("private", encoding="utf-8")
    _git("init", "-q", cwd=repo)
    _git("config", "user.email", "t@example.com", cwd=repo)
    _git("config", "user.name", "tester", cwd=repo)
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True, capture_output=True)
    _git("remote", "add", "origin", str(bare), cwd=repo)
    (repo / "data").mkdir()
    (repo / "data" / "cards.json").write_text('[{"id": 1}]', encoding="utf-8")
    subprocess.run([sys.executable, str(repo / "scripts" / "gen_manifest.py")],
                   check=True, capture_output=True)
    _git("add", "-A", cwd=repo)
    _git("commit", "-m", "seed", cwd=repo)
    _git("push", "-u", "origin", "HEAD", cwd=repo)

    monkeypatch.setenv("MJS_DATA_REPO", str(repo))
    return root, repo


def test_verify_pass_and_fail(sync_env):
    _, repo = sync_env
    assert pull_data.verify(repo) == 0

    (repo / "data" / "cards.json").write_text('{"tampered": true}', encoding="utf-8")
    assert pull_data.verify(repo) == 1


def test_pull_places_files_atomically(sync_env):
    root, repo = sync_env
    (root / "data").mkdir()
    (root / "data" / "cards.json").write_text("[]", encoding="utf-8")

    assert pull_data.pull(repo) == 0
    assert json.loads((root / "data" / "cards.json").read_text(encoding="utf-8")) == [{"id": 1}]
    # staging 用完即清
    assert not list((root / ".tmp_test").glob(".pull_staging-*"))


def test_pull_backs_up_dirty_local_file(sync_env, capsys):
    """回归：未 push 的本地修改被 pull 覆盖前必须先留备份（否则无处找回）。"""
    root, repo = sync_env
    (root / "data").mkdir()
    (root / "data" / "cards.json").write_text('[{"id": 1, "本地修改": true}]', encoding="utf-8")

    assert pull_data.pull(repo) == 0
    # 覆盖后为私有仓版本
    assert json.loads((root / "data" / "cards.json").read_text(encoding="utf-8")) == [{"id": 1}]
    # 被覆盖的本地修改在 data/backups 留有扁平化命名的副本
    backups = sorted((root / "data" / "backups").glob("data__cards-*.json"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == '[{"id": 1, "本地修改": true}]'
    assert "未 push 的修改" in capsys.readouterr().out


def test_pull_clean_worktree_creates_no_backup(sync_env):
    """工作区与私有仓一致时落位不产生备份（正常周更路径零额外产物）。"""
    root, repo = sync_env
    assert pull_data.pull(repo) == 0
    assert not (root / "data" / "backups").exists()


def test_pull_dry_run_touches_nothing(sync_env):
    root, repo = sync_env
    assert pull_data.pull(repo, dry_run=True) == 0
    assert not (root / "data" / "cards.json").exists()


def test_pull_refuses_when_repo_tampered(sync_env):
    root, repo = sync_env
    (repo / "data" / "cards.json").write_text('{"tampered": true}', encoding="utf-8")

    assert pull_data.pull(repo) == 1
    assert not (root / "data" / "cards.json").exists()


def test_push_blocked_by_malformed_json(sync_env, capsys):
    root, repo = sync_env
    (root / "data").mkdir()
    (root / "data" / "heroes.json").write_text('{"截断": ', encoding="utf-8")
    (root / "data" / "cards.json").write_text('[{"id": 1}]', encoding="utf-8")

    assert pull_data.push(repo) == 1
    assert "数据有效性守卫拦截" in capsys.readouterr().out
    assert not (repo / "data" / "heroes.json").exists()


def test_push_blocked_by_truncated_heroes(sync_env):
    root, repo = sync_env
    (root / "data").mkdir()
    (root / "data" / "heroes.json").write_text(json.dumps(HEROES[:5]), encoding="utf-8")

    assert pull_data.push(repo) == 1
    assert not (repo / "data" / "heroes.json").exists()


def test_push_blocked_by_dirty_repo(sync_env, capsys):
    root, repo = sync_env
    (repo / "data" / "cards.json").write_text('{"残留": true}', encoding="utf-8")
    (root / "data").mkdir()
    (root / "data" / "heroes.json").write_text(json.dumps(HEROES), encoding="utf-8")
    (root / "data" / "cards.json").write_text('[{"id": 1}]', encoding="utf-8")

    assert pull_data.push(repo) == 1
    assert "工作区不干净" in capsys.readouterr().out


def test_push_dry_run_touches_nothing(sync_env, capsys):
    root, repo = sync_env
    (root / "data").mkdir()
    (root / "data" / "heroes.json").write_text(json.dumps(HEROES), encoding="utf-8")
    (root / "data" / "cards.json").write_text('[{"id": 1}]', encoding="utf-8")

    assert pull_data.push(repo, dry_run=True) == 0
    assert "将复制" in capsys.readouterr().out
    assert not (repo / "data" / "heroes.json").exists()


def test_git_output_decoded_as_utf8(sync_env, monkeypatch):
    """回归：git 输出恒为 UTF-8，_git 不显式指定编码时按进程默认编码解码——
    中文 Windows 控制台（无 PYTHONUTF8）为 GBK，撞上中文提交摘要/远端消息时
    reader 线程抛 UnicodeDecodeError，git 已执行成功但输出读取崩溃丢失
    （2026-10-09 push 实际撞上）。进程默认编码随 PYTHONUTF8 漂移，无法在本测试
    进程内确定性还原 GBK 场景，故以透明 spy 钉死显式 encoding="utf-8" 这一契约，
    并验证中文提交主题经 _git 读回无损。"""
    _, repo = sync_env
    (repo / "data" / "cards.json").write_text('[{"id": 2}]', encoding="utf-8")
    _git("add", "-A", cwd=repo)
    _git("commit", "-m", "同步：中文提交信息验证", cwd=repo)

    real_run = subprocess.run
    kwargs_seen: dict = {}

    def run_spy(cmd, **kwargs):
        kwargs_seen.update(kwargs)
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(pull_data.subprocess, "run", run_spy)

    assert pull_data._git(repo, "log", "--format=%s", "-1") == "同步：中文提交信息验证"
    assert kwargs_seen.get("encoding") == "utf-8"


def test_push_full_cycle(sync_env):
    """守卫 → 脏检查 → 拷贝 → 对齐远端 → 刷新 manifest → 提交推送：全链真实 git"""
    root, repo = sync_env
    (root / "data").mkdir()
    (root / "data" / "heroes.json").write_text(json.dumps(HEROES), encoding="utf-8")
    (root / "data" / "cards.json").write_text('[{"id": 1}]', encoding="utf-8")

    assert pull_data.push(repo) == 0

    assert json.loads((repo / "data" / "heroes.json").read_text(encoding="utf-8"))[0]["id"] == 1
    manifest = json.loads((repo / "manifest.json").read_text(encoding="utf-8"))
    assert "data/heroes.json" in {f["path"] for f in manifest["files"]}
    # 远端 bare 已收到本次提交
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    branch = subprocess.run(["git", "-C", str(repo), "branch", "--show-current"],
                            capture_output=True, text=True, check=True).stdout.strip()
    remote_head = subprocess.run(["git", "-C", str(repo.parent / "origin.git"), "rev-parse", branch],
                                 capture_output=True, text=True, check=True).stdout.strip()
    assert remote_head == head
