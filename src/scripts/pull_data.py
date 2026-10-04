# -*- coding: utf-8 -*-
"""私有数据仓同步（pull_data.py）

抓取数据自 2026-10-03 起不入版本库（见 LICENSE「第三方内容说明」与 data/SOURCES.md），
经本脚本与私有数据仓（Gitee mjs_data_private）同步。
guides/synergies 为 AI 生成物，不入仓。

私有仓位置解析：环境变量 MJS_DATA_REPO > config.env 的 MJS_DATA_REPO > 项目同级默认。

用法：
    python -m src.scripts.pull_data pull                # 拉取 + sha256 校验 + 原子落位工作区
    python -m src.scripts.pull_data push                # 守卫校验后回推工作区数据 → 私有仓
    python -m src.scripts.pull_data push --dry-run      # 只打印将发生的动作，不执行
    python -m src.scripts.pull_data verify              # 仅校验私有仓内容与 manifest 一致
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from src.config.env import PROJECT_ROOT as ROOT
from src.config.env import load_env_config
from src.scripts.rag_common import install_crash_logger

DEFAULT_DATA_REPO = ROOT.parent / "mjs_data_private"
# 与 .gitignore「抓取数据出库」段保持同步
PRIVATE_PATHS = [
    "data/heroes.json", "data/combos.json", "data/cards.json",
    "data/card_annotations.json", "data/special_cards.json",
    "data/card_points.json", "data/equip_attrs.json",
    "data/hero_classification.json", "data/mjs_adjustments.json",
    "data/announcements.json", "data/baike_snapshot.json",
    "data/raw_guides", "data/rag_corpus", "images",
]
# push 有效性守卫的 heroes 下限：当前 186 武将，低于此值视为截断
# （对齐 full.py 写入守卫的思路——verify 只保证指纹一致，不保证数据质量）
MIN_HERO_COUNT = 100


def resolve_data_repo() -> Path:
    """私有仓根目录：环境变量 MJS_DATA_REPO > config.env 的 MJS_DATA_REPO > 同级默认。"""
    override = os.environ.get("MJS_DATA_REPO")
    if override:
        return Path(override)
    configured = load_env_config().get("mjs_data_repo")
    if configured:
        return Path(str(configured))
    return DEFAULT_DATA_REPO


def _repo() -> Path:
    repo = resolve_data_repo()
    if not (repo / ".git").is_dir():
        sys.exit(
            f"FAIL: 私有数据仓不存在或未初始化: {repo}\n"
            "  新机初始化：git clone https://gitee.com/chen-xianghao920/mjs_data_private.git"
            " 到项目同级目录；位置不同时设置 MJS_DATA_REPO 指向仓根目录。\n"
            "  详见 README「数据与合规」章节。"
        )
    return repo


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args],
                            capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"FAIL: git {' '.join(args)}\n{result.stderr}")
    return result.stdout.strip()


def _manifest(repo: Path) -> dict:
    manifest = repo / "manifest.json"
    if not manifest.exists():
        sys.exit(f"FAIL: 找不到 {manifest}——先在私有仓运行 scripts/gen_manifest.py")
    try:
        return json.loads(manifest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        sys.exit(
            f"FAIL: manifest 损坏不可解析: {manifest}\n  {error}\n"
            "  修复：在私有仓重跑 scripts/gen_manifest.py"
        )


def verify(repo: Path | None = None) -> int:
    repo = repo or resolve_data_repo()
    entries = _manifest(repo)["files"]
    bad = [f["path"] for f in entries
           if not (repo / f["path"]).exists()
           or _sha256(repo / f["path"]) != f["sha256"]]
    if bad:
        print("FAIL: " + ", ".join(bad))
        return 1
    print(f"PASS: {len(entries)} 个文件与 manifest 一致")
    return 0


def pull(repo: Path | None = None, dry_run: bool = False) -> int:
    repo = repo or _repo()
    if not dry_run:
        _git(repo, "pull", "--ff-only")
        if verify(repo):
            return 1
    entries = _manifest(repo)["files"]
    if dry_run:
        for item in entries:
            print(f"[dry-run] 将落位: {item['path']}")
        return 0

    # 原子落位：全部先拷进同盘 staging 再逐文件 replace——单个文件要么完整旧
    # 要么完整新；copy2 直写中途失败会留下截断文件污染工作区
    staging_root = ROOT / ".tmp_test"
    staging_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".pull_staging-", dir=staging_root))
    try:
        for item in entries:
            tmp = staging / item["path"]
            tmp.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(repo / item["path"], tmp)
        count = 0
        for item in entries:
            dst = ROOT / item["path"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            (staging / item["path"]).replace(dst)
            count += 1
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    print(f"OK: 已原子落位 {count} 个文件")
    return 0


def _validate_for_push(rel: str, src: Path) -> str | None:
    """push 前数据有效性守卫；返回拒绝原因，None 表示放行。

    verify 只保证 manifest 与文件一致，不保证数据质量：损坏/截断的 json
    会带着合法指纹覆盖私有仓好副本——push 方向没有旧文件兜底，必须前置拦截。
    """
    if src.is_dir():
        if not any(src.iterdir()):
            return "目录为空"
        return None
    if src.suffix != ".json":
        return None
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return f"JSON 解析失败: {error}"
    if not data:
        return "数据为空"
    if rel == "data/heroes.json" and isinstance(data, list) and len(data) < MIN_HERO_COUNT:
        return f"武将数 {len(data)} 低于下限 {MIN_HERO_COUNT}（疑似截断）"
    return None


def push(repo: Path | None = None, dry_run: bool = False) -> int:
    repo = repo or _repo()

    # 1) 数据有效性守卫：先全量校验，任一 FAIL 不碰私有仓
    failures = []
    for rel in PRIVATE_PATHS:
        src = ROOT / rel
        if not src.exists():
            continue
        reason = _validate_for_push(rel, src)
        if reason:
            failures.append(f"{rel}: {reason}")
    if failures:
        print("FAIL: 数据有效性守卫拦截，未写入私有仓：")
        for line in failures:
            print(f"  - {line}")
        return 1

    # 2) 脏工作区拒绝：残留会被下方 add -A 无声带入提交
    if not dry_run:
        dirty = _git(repo, "status", "--porcelain")
        if dirty:
            print("FAIL: 私有仓工作区不干净，先处理以下残留再 push：")
            print(dirty)
            return 1

    # 3) 拷贝
    for rel in PRIVATE_PATHS:
        src, dst = ROOT / rel, repo / rel
        if not src.exists():
            print(f"跳过（工作区不存在）: {rel}")
            continue
        if dry_run:
            print(f"[dry-run] 将复制: {rel}")
            continue
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    if dry_run:
        print("[dry-run] 后续将：刷新 manifest → 校验 → add -A → commit → push（未执行）")
        return 0

    # 4) 先对齐远端再提交，避免 non-ff 被拒时留下半完成状态
    _git(repo, "pull", "--ff-only")
    subprocess.run([sys.executable, str(repo / "scripts" / "gen_manifest.py")], check=True)
    if verify(repo):
        return 1
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", f"data sync {datetime.date.today().isoformat()}", "--allow-empty")
    _git(repo, "push", "origin", "HEAD")
    print("OK: 私有仓已更新")
    return 0


def main() -> None:
    install_crash_logger("pull_data")
    parser = argparse.ArgumentParser(description="私有数据仓同步", )
    parser.add_argument("command", choices=["pull", "push", "verify"])
    parser.add_argument("--dry-run", action="store_true", help="只打印将发生的动作，不执行")
    args = parser.parse_args()

    from src.business.common.task_ledger import record_task
    started = time.monotonic()
    if args.command == "verify":
        code = verify()
    else:
        code = {"pull": pull, "push": push}[args.command](dry_run=args.dry_run)
    record_task(f"pull_data_{args.command}", ok=code == 0, exit_code=code,
                duration_s=time.monotonic() - started,
                reason="dry-run" if args.dry_run else "")
    sys.exit(code)


if __name__ == "__main__":
    main()
