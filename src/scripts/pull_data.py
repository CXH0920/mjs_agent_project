# -*- coding: utf-8 -*-
"""私有数据仓同步（pull_data.py）

抓取数据自 2026-10-03 起不入版本库（见 LICENSE「第三方内容说明」与 data/SOURCES.md），
经本脚本与私有数据仓（Gitee mjs_data_private）同步。
guides/synergies 为 AI 生成物，不入仓。

用法：
    python -m src.scripts.pull_data pull     # 拉取 + sha256 校验 + 落位工作区
    python -m src.scripts.pull_data push     # 回推工作区数据 → 私有仓（刷新 manifest 后提交推送）
    python -m src.scripts.pull_data verify   # 仅校验私有仓内容与 manifest 一致
"""
import argparse
import datetime
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from src.config.env import PROJECT_ROOT as ROOT

DATA_REPO = ROOT.parent / "mjs_data_private"
# 与 .gitignore「抓取数据出库」段保持同步
PRIVATE_PATHS = [
    "data/heroes.json", "data/combos.json", "data/cards.json",
    "data/card_annotations.json", "data/special_cards.json",
    "data/announcements.json", "data/baike_snapshot.json",
    "data/raw_guides", "data/rag_corpus", "images",
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*args: str) -> str:
    result = subprocess.run(["git", "-C", str(DATA_REPO), *args],
                            capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"FAIL: git {' '.join(args)}\n{result.stderr}")
    return result.stdout.strip()


def _manifest() -> dict:
    manifest = DATA_REPO / "manifest.json"
    if not manifest.exists():
        sys.exit(f"FAIL: 找不到 {manifest}——先在私有仓运行 scripts/gen_manifest.py")
    return json.loads(manifest.read_text(encoding="utf-8"))


def verify() -> int:
    bad = [f["path"] for f in _manifest()["files"]
           if not (DATA_REPO / f["path"]).exists()
           or _sha256(DATA_REPO / f["path"]) != f["sha256"]]
    if bad:
        print("FAIL: " + ", ".join(bad))
        return 1
    print(f"PASS: {len(_manifest()['files'])} 个文件与 manifest 一致")
    return 0


def pull() -> int:
    _git("pull", "--ff-only")
    if verify():
        return 1
    count = 0
    for item in _manifest()["files"]:
        src, dst = DATA_REPO / item["path"], ROOT / item["path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        count += 1
    print(f"OK: 已落位 {count} 个文件")
    return 0


def push() -> int:
    for rel in PRIVATE_PATHS:
        src, dst = ROOT / rel, DATA_REPO / rel
        if not src.exists():
            print(f"跳过（工作区不存在）: {rel}")
            continue
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    subprocess.run([sys.executable, str(DATA_REPO / "scripts" / "gen_manifest.py")], check=True)
    if verify():
        return 1
    _git("add", "-A")
    _git("commit", "-m", f"data sync {datetime.date.today().isoformat()}", "--allow-empty")
    _git("push", "origin", "HEAD")
    print("OK: 私有仓已更新")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="私有数据仓同步", )
    parser.add_argument("command", choices=["pull", "push", "verify"])
    args = parser.parse_args()
    sys.exit({"pull": pull, "push": push, "verify": verify}[args.command]())
