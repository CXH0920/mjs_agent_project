#!/usr/bin/env python3
"""
名将杀 Agent 打包发版脚本

基于实际打包经验固化整个流程：预检 → 干净构建 → 纯净度校验 → 烟雾启动测试 → 可选打 zip。

用法（在 conda myenv 环境下运行）：
    conda run -n myenv python release.py            # 完整发版（构建+校验+烟雾测试）
    conda run -n myenv python release.py --no-smoke # 跳过 20s 启动烟雾测试
    conda run -n myenv python release.py --zip      # 额外产出 zip 分发包
    conda run -n myenv python release.py --skip-build  # 只对已有 dist 做校验

关键点（来自实际踩坑，详见 docs/打包发布指南.md）：
- build_deps/ 由 prepare_build_deps() 自动安装 CPU 版 paddlepaddle 2.6.2（独立目录、
  不碰 myenv GPU 版）；靠 PYTHONPATH 让 PyInstaller 优先收集它，spec 内也做了
  sys.path.insert 双保险。
- mjs_agent.spec 已处理 PyInstaller 6.x Tree 兼容、Cython/imageio/cnradical 数据、
  char_info_cache/wubi86 路径、torch 等 ppstructure 依赖排除、Qt 裁剪等；本脚本只
  负责编排，不改 spec 逻辑。
- 构建产物本身不含任何用户资料（config.env/edge_profile/logs/api key），首次启动由
  main._ensure_clean_runtime 生成。烟雾测试会在运行后清理它产生的运行时文件。
- 烟雾测试用 os.startfile 模拟真实双击（windowed exe 无控制台 → sys.stdout=None），
  暴露 Popen(PIPE) 启动会掩盖的双击场景崩溃。
- 中文路径支持：paddle 模型复制到 %TEMP%、cv2 用 imdecode/imencode 规避 ANSI fopen
  限制（详见指南"中文路径"一节）。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ── 路径常量 ─────────────────────────────────────────────────────
HERE = Path(__file__).resolve().parent  # 项目根（release.py 与 mjs_agent.spec 同级）
SPEC = HERE / "mjs_agent.spec"
BUILD_DEPS = HERE / "build_deps"               # CPU paddlepaddle 独立安装目录
DIST = HERE / "dist"
BUILD = HERE / "build"
EXE = DIST / "mjs_agent" / "mjs_agent.exe"
INTERNAL = DIST / "mjs_agent" / "_internal"

# OCR 模型加载需 ~15s，烟雾测试至少给 22s 才能覆盖加载阶段
SMOKE_TIMEOUT_S = 22

# 构建后由 exe 首次启动生成的运行时文件（不应出现在纯净交付物里）
RUNTIME_ARTIFACTS = [
    DIST / "mjs_agent" / "config.env",
    DIST / "mjs_agent" / "data",
    DIST / "mjs_agent" / "config",
    DIST / "mjs_agent" / "images",
    DIST / "mjs_agent" / "templates",
    DIST / "mjs_agent" / "mjs.ico",
    DIST / "mjs_agent" / "logs",
]

# ANSI 颜色（Git Bash / 现代终端支持）
_C = {"g": "\033[32m", "y": "\033[33m", "r": "\033[31m", "b": "\033[36m", "x": "\033[0m"}


def _ok(msg: str) -> None:
    print(f"{_C['g']}✓{_C['x']} {msg}")


def _info(msg: str) -> None:
    print(f"{_C['b']}▶{_C['x']} {msg}")


def _warn(msg: str) -> None:
    print(f"{_C['y']}!{_C['x']} {msg}")


def _die(msg: str) -> None:
    print(f"{_C['r']}✗ {_C['x']}{msg}")
    sys.exit(1)


# ── 预检 ───────────────────────────────────────────────────────
def preflight(full: bool = False) -> str:
    """发版前环境检查，返回 git 版本号。"""
    _info(f"预检中…（{'完整版' if full else '精简版'}）")
    if not SPEC.exists():
        _die(f"找不到 spec：{SPEC}")

    # PyInstaller 必须可用
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        _die("当前环境无 PyInstaller，请在 conda myenv 下运行")

    # 完整版需 playwright（用 channel=msedge 系统 Edge，不需额外下载浏览器二进制）
    if full:
        try:
            import playwright  # noqa: F401
        except ImportError:
            _die("完整版需 playwright：conda run -n myenv pip install playwright")
        _ok("playwright 已就绪")

    # PaddleOCR 模型必须存在（spec 从 ~/.paddleocr 收集 det/rec/cls 到包内离线用）
    ocr_home = Path(os.path.expanduser("~")) / ".paddleocr" / "whl"
    det_model = ocr_home / "det" / "ch" / "ch_PP-OCRv4_det_infer"
    if not det_model.is_dir():
        _die(f"找不到 PaddleOCR 模型：{det_model}\n"
             f"  请先在 myenv 运行 PaddleOCR 一次以下载模型，或从其他机器拷贝 ~/.paddleocr")

    # git 版本号（tag 或短 commit）
    ver = _git("describe", "--tags", "--always", fallback="0.0.0-unknown")
    _ok(f"版本号 {ver}")
    return ver


def _git(*args: str, fallback: str = "") -> str:
    try:
        out = subprocess.check_output(["git", *args], cwd=HERE, text=True, stderr=subprocess.DEVNULL)
        return out.strip()
    except Exception:
        return fallback


# ── CPU paddlepaddle 准备 ─────────────────────────────────────
def prepare_build_deps() -> None:
    """若 build_deps/paddle 不存在，独立安装 CPU 版 paddlepaddle 2.6.2。

    与 myenv 的 GPU 版隔离（--target + --no-deps，不碰全局环境），
    仅供 PyInstaller 收集 CPU 版 paddle，规避 GPU 版带 CUDA/cuDNN 的体积膨胀。
    """
    if (BUILD_DEPS / "paddle").is_dir():
        _ok("build_deps/ CPU paddle 已就绪")
        return
    _info("向 build_deps/ 安装 CPU 版 paddlepaddle 2.6.2（独立目录，不影响 myenv）…")
    BUILD_DEPS.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, "-m", "pip", "install",
        "--target", str(BUILD_DEPS),
        "--no-deps", "--no-cache-dir",
        "paddlepaddle==2.6.2",
    ]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        _die(f"安装 CPU paddlepaddle 失败（exit {e.returncode}）：请检查网络/pip 源")
    if not (BUILD_DEPS / "paddle").is_dir():
        _die("安装后 build_deps/paddle 仍不存在，CPU paddlepaddle 未就绪")
    _ok("CPU paddlepaddle 安装完成")


# ── 构建 ───────────────────────────────────────────────────────
def build(full: bool = False) -> None:
    """干净构建：清 build/dist → PyInstaller --clean --noconfirm。"""
    _info("清理旧的 build/ dist/ …")
    shutil.rmtree(BUILD, ignore_errors=True)
    shutil.rmtree(DIST, ignore_errors=True)

    _info("PyInstaller 构建中（约 2-3 分钟，CPU 版 PaddleOCR 收集较慢）…")
    # PYTHONPATH=build_deps 让 collect_all 优先命中 CPU 版 paddle（spec 内也有 sys.path.insert 双保险）
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BUILD_DEPS) + os.pathsep + env.get("PYTHONPATH", "")
    if full:
        env["MJS_FULL"] = "1"  # spec 顶部据此切换完整版 excludes/collect
    cmd = [sys.executable, "-m", "PyInstaller", str(SPEC), "--noconfirm", "--clean"]
    t0 = time.perf_counter()
    try:
        subprocess.run(cmd, env=env, check=True, cwd=HERE)
    except subprocess.CalledProcessError as e:
        _die(f"PyInstaller 构建失败（exit {e.returncode}）")
    elapsed = time.perf_counter() - t0
    _ok(f"构建完成，耗时 {elapsed:.0f}s")

    if not EXE.exists():
        _die(f"构建后未找到 exe：{EXE}")

    if full:
        # 完整版写 .full_build 标记；运行期 is_full_build() 读它决定保留知识库维护页。
        # spec 末尾 touch 会被 PyInstaller 构建覆盖 dist，故在 PyInstaller 完成后写入。
        (INTERNAL / ".full_build").touch()
        _ok(".full_build 标记已写入（完整版）")


# ── 完整版构建产物校验 ─────────────────────────────────────────
def verify_full_artifacts() -> None:
    """完整版构建后校验 playwright driver 与 .full_build 标记已收集进包。"""
    _info("完整版产物校验：playwright driver + .full_build 标记…")
    pw_driver = INTERNAL / "playwright" / "driver"
    if not pw_driver.is_dir():
        _die(f"未找到 playwright driver（{pw_driver}），collect_all 未收集 playwright")
    _ok("playwright driver 已收集")
    if not (INTERNAL / ".full_build").exists():
        _die(".full_build 标记未写入包内，运行期 is_full_build() 会误判为精简版")
    _ok(".full_build 标记已写入")


# ── 纯净度校验 ─────────────────────────────────────────────────
def purity_check() -> None:
    """确认交付物不含用户资料（config.env / edge_profile / logs / api key）。"""
    _info("纯净度校验（构建产物不应含用户资料）…")
    root = DIST / "mjs_agent"
    problems: list[str] = []

    for name in ("config.env",):
        if (root / name).exists():
            problems.append(f"发现 {name}（应由首次启动生成）")
    if (root / "data" / "edge_profile").exists():
        problems.append("发现 data/edge_profile（应由首次启动生成）")
    if (root / "logs").exists():
        problems.append("发现 logs/（应由首次启动生成）")

    if problems:
        _warn("纯净度问题（将自动清理运行时残留）：" + "；".join(problems))
        clean_runtime_artifacts()

    # api key 残留扫描（sk- 开头 20+ 位）
    _scan_apikey(INTERNAL)
    _ok("纯净度通过：无 config.env / edge_profile / logs / api key")


def _scan_apikey(root: Path) -> None:
    """递归扫描文本类文件内是否残留 api key 字面量（sk- 开头 20+ 位）。

    只扫源码/配置类文本文件；.dll/.pyd/.so 等二进制里的字节巧合命中不报。
    """
    import re

    text_exts = {".py", ".json", ".txt", ".env", ".cfg", ".toml", ".ini",
                 ".yaml", ".yml", ".md", ".js", ".ts"}
    pat = re.compile(r"sk-[A-Za-z0-9]{20,}")
    try:
        for p in root.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in text_exts:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if pat.search(text):
                _die(f"在 {p} 内发现疑似 api key 残留，请检查")
    except OSError:
        pass


def clean_runtime_artifacts() -> None:
    """删除 exe 首次启动产生的运行时文件，恢复纯净交付物。"""
    for p in RUNTIME_ARTIFACTS:
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.is_file():
            p.unlink(missing_ok=True)


# ── 烟雾启动测试 ───────────────────────────────────────────────
def smoke_test() -> None:
    """双击式启动 exe，确认不崩溃、无致命错误，随后清理运行时文件。

    用 os.startfile 模拟真实双击（explorer 启动，windowed exe 无控制台 → sys.stdout=None），
    暴露 Popen(PIPE) 启动会掩盖的 stdout=None 类崩溃（如 sys.stdout.reconfigure NoneType）。
    进程存活 + 日志无致命错误即通过。
    """
    import os
    import tempfile
    _info(f"烟雾启动测试（{SMOKE_TIMEOUT_S}s，双击模式，覆盖 OCR 模型加载）…")
    # 清掉 %TEMP% 的 OCR 模型缓存，强制重新从本次构建的包内复制，确保验证的是新模型
    _temp_ocr = Path(tempfile.gettempdir()) / "mjs_ocr_models"
    if _temp_ocr.is_dir():
        shutil.rmtree(_temp_ocr, ignore_errors=True)
    try:
        os.startfile(str(EXE))
    except OSError as e:
        _die(f"无法启动 exe：{e}")
    time.sleep(SMOKE_TIMEOUT_S)

    # startfile 不返回进程句柄，用 tasklist 查存活
    res = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq mjs_agent.exe", "/NH"],
        capture_output=True, text=True)
    alive = "mjs_agent.exe" in (res.stdout or "")

    # 双击模式无 stdout 捕获，改读 logs 判断致命错误
    log_text = ""
    logs_dir = DIST / "mjs_agent" / "logs"
    if logs_dir.is_dir():
        for p in logs_dir.rglob("*.log"):
            try:
                log_text += p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                pass
    fatal = any(k in log_text for k in ("No module named", "ModuleNotFoundError",
                                       "Traceback (most recent call last)",
                                       "模型加载失败"))
    # 清理 exe 进程（无论是否存活）
    subprocess.run(["taskkill", "/IM", "mjs_agent.exe", "/F"],
                   capture_output=True, text=True)

    if not alive:
        _warn("exe 未保持运行（疑似一闪而过）")
        if log_text:
            print(log_text[-500:])
        _die("烟雾测试失败：exe 未保持运行")
    if fatal:
        _warn("烟雾测试发现致命错误：")
        for line in log_text.splitlines():
            if any(k in line for k in ("No module", "ModuleNotFound", "Traceback", "模型加载失败")):
                print("  " + line)
        _die("烟雾测试失败：存在致命导入/加载错误")
    _ok("烟雾测试通过：GUI 保持运行，无致命错误")

    # 烟雾测试会产生 config.env/logs 等运行时文件，清理以保持交付物纯净
    _info("清理烟雾测试产生的运行时文件…")
    clean_runtime_artifacts()
    _ok("已恢复纯净交付物")


# ── zip 分发包 ──────────────────────────────────────────────────
def make_zip(version: str) -> Path:
    """把 dist/mjs_agent 打成 zip 分发包。"""
    import zipfile

    _info("打包 zip 分发包…")
    zip_path = DIST / f"mjs_agent-{version}.zip"
    if zip_path.exists():
        zip_path.unlink()
    src_root = DIST / "mjs_agent"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        for p in src_root.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(DIST))
    size_mb = zip_path.stat().st_size / 1024 / 1024
    _ok(f"zip 分发包：{zip_path}（{size_mb:.0f} MB）")
    return zip_path


# ── 汇总 ───────────────────────────────────────────────────────
def report(version: str, zip_path: Path | None, full: bool = False) -> None:
    root = DIST / "mjs_agent"
    total = sum(p.stat().st_size for p in root.rglob("*") if p.is_file()) / 1024 / 1024
    exe_size = EXE.stat().st_size / 1024 / 1024
    print()
    print("=" * 56)
    print(f"  名将杀 Agent {version}  {'完整版' if full else '精简版'}  发版完成")
    print("=" * 56)
    print(f"  交付目录 : {root}")
    print(f"  启动器   : mjs_agent.exe ({exe_size:.0f} MB)")
    print(f"  总体积   : {total:.0f} MB")
    if zip_path:
        print(f"  zip 分发 : {zip_path.name}")
    print()
    print("  双击 mjs_agent.exe 运行，首次启动自动在 exe 同级生成")
    print("  config.env（填 API Key）/ data/edge_profile/ / logs/")
    print()
    print("  注意：")
    print("  - 中文路径已支持（OCR 模型复制到 %TEMP%、cv2 用 imdecode/imencode）")
    print("  - 更新 paddleocr_models 后重新发版，需删 %TEMP%\\mjs_ocr_models 让其重新复制")
    print("  - 首启生成的 config.env 内 API Key 为空（核心对战辅助无需填）")
    print("=" * 56)


def main() -> None:
    # 强制 stdout/stderr 用 UTF-8，规避 Windows GBK locale 下中文/ANSI 输出报错
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="名将杀 Agent 打包发版")
    ap.add_argument("--full", action="store_true", help="构建完整版（含 RAG 知识库维护页 + Playwright 浏览器抓取）")
    ap.add_argument("--no-smoke", action="store_true", help="跳过启动烟雾测试")
    ap.add_argument("--zip", action="store_true", help="额外产出 zip 分发包")
    ap.add_argument("--skip-build", action="store_true", help="跳过构建，只校验已有 dist")
    args = ap.parse_args()

    if not args.skip_build:
        prepare_build_deps()
    version = preflight(args.full)

    if not args.skip_build:
        build(args.full)
    elif not EXE.exists():
        _die(f"--skip-build 但找不到已构建的 exe：{EXE}")

    purity_check()

    if args.full:
        verify_full_artifacts()

    if not args.no_smoke:
        smoke_test()
    else:
        _warn("已跳过烟雾测试")

    zip_path = make_zip(version) if args.zip else None
    report(version, zip_path, args.full)


if __name__ == "__main__":
    main()
