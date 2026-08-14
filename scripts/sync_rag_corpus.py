"""一键 RAG 语料维护管道。

流程：test_project 官方数据（权威源） -> mjs_rag_project 语料重建/索引 -> 语料与索引复制回 test_project。
用法：
    python scripts/sync_rag_corpus.py             # 完整管道（import 前询问确认）
    python scripts/sync_rag_corpus.py --yes       # 跳过确认
    python scripts/sync_rag_corpus.py --dry-run   # 仅预览差异与复制计划
    python scripts/sync_rag_corpus.py --skip-import --skip-build   # 仅复制语料/索引
"""
from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config.env import PROJECT_ROOT, parse_env_file

DEFAULT_MJS_ROOT = r"G:\py_savepoint\mjs_rag_project"


def _mjs_root() -> str:
    env = parse_env_file()
    return env.get("RAG_PROJECT_DIR") or os.environ.get("RAG_PROJECT_DIR") or DEFAULT_MJS_ROOT


def _run(cmd, cwd):
    print(">>", " ".join(cmd))
    return subprocess.run(cmd, cwd=cwd)


def main():
    parser = argparse.ArgumentParser(description="一键 RAG 语料维护管道")
    parser.add_argument("--yes", action="store_true", help="跳过确认（含 import_from_test 确认）")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不执行")
    parser.add_argument("--skip-import", action="store_true", help="跳过官方数据同步")
    parser.add_argument("--skip-build", action="store_true", help="跳过语料重建与索引重建")
    parser.add_argument("--skip-copy", action="store_true", help="跳过语料/索引导回 test_project")
    args = parser.parse_args()

    mjs = _mjs_root()
    if not os.path.isdir(os.path.join(mjs, "scripts")):
        print("错误：RAG_PROJECT_DIR 无效或缺少 scripts: %s" % mjs)
        sys.exit(1)
    print("test_project :", PROJECT_ROOT)
    print("mjs_rag_project:", mjs)

    if args.dry_run:
        print("\n[--dry-run] 仅预览，不执行任何写操作")
        if not args.skip_import:
            r = _run([sys.executable, "-B", "scripts/import_from_test.py",
                      "--test-root", str(PROJECT_ROOT), "--dry-run"], cwd=mjs)
            if r.returncode != 0:
                sys.exit(r.returncode)
        if not args.skip_copy:
            print("\n[复制计划]")
            print("  mjs docs/*.json -> test data/rag_corpus/")
            print("  mjs rag/.cache/chroma -> test data/rag_index/chroma/")
        return

    if not args.skip_import:
        cmd = [sys.executable, "-B", "scripts/import_from_test.py", "--test-root", str(PROJECT_ROOT)]
        if args.yes:
            cmd.append("--yes")
        r = _run(cmd, cwd=mjs)
        if r.returncode != 0:
            print("import_from_test 失败，终止管道")
            sys.exit(r.returncode)

    if not args.skip_build:
        r = _run([sys.executable, "-B", "scripts/maintain_rag.py", "--build-index"], cwd=mjs)
        if r.returncode != 0:
            print("maintain_rag 失败，终止管道")
            sys.exit(r.returncode)

    if not args.skip_copy:
        # 1) 语料 json
        src_docs = os.path.join(mjs, "docs")
        dst_corpus = PROJECT_ROOT / "data" / "rag_corpus"
        dst_corpus.mkdir(parents=True, exist_ok=True)
        copied = 0
        for name in sorted(os.listdir(src_docs)):
            if name.endswith(".json"):
                shutil.copy2(os.path.join(src_docs, name), dst_corpus / name)
                copied += 1
        print("已同步语料文件: %d" % copied)

        # 2) Chroma 索引（先清空目标目录再复制，避免残留过期块）
        src_idx = os.path.join(mjs, "rag", ".cache", "chroma")
        dst_idx = PROJECT_ROOT / "data" / "rag_index" / "chroma"
        if os.path.isdir(src_idx):
            if os.path.isdir(dst_idx):
                shutil.rmtree(dst_idx)
            shutil.copytree(src_idx, dst_idx)
            print("已同步向量索引 -> %s" % dst_idx)
        else:
            print("警告：mjs 侧缺少向量索引 %s，跳过" % src_idx)

    print("\n管道完成 ✅")


if __name__ == "__main__":
    main()