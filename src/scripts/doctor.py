# -*- coding: utf-8 -*-
"""doctor.py — 一键自检（只读）

把环境/设备/数据/配置/日志/合规问题压缩成 10 秒、一页 PASS/WARN/FAIL 报告，
FAIL/WARN 均附修复动作。检查全部只读；网络检查失败标记 SKIP 不阻塞报告。

用法：
    python -m src.scripts.doctor              # 全量检查
    python -m src.scripts.doctor --offline    # 跳过网络检查（合规触发器）
    python -m src.scripts.doctor --json       # 机器可读输出（ops.py 复用）
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.metadata
import importlib.util
import io
import json
import re
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from src.config.env import PROJECT_ROOT as ROOT
from src.scripts.rag_common import install_crash_logger

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"

REPO_SLUG = "CXH0920/mjs_agent_project"
# retrospective.md「升级触发器」：满足任一立即转全量历史重写预案
COMPLIANCE_TRIGGERS = "fork > 0 ｜ stars > 50 ｜ 权利人联系 ｜ takedown ｜ 商业化"
DEAD_CONFIG_KEYS = ("RAG_PROJECT_DIR", "MUMU_MATCH_GUIDE_COOLDOWN")
OPTIONAL_CONFIG_KEYS = (
    "RECOMMENDATION_P_FLOOR", "RECOMMENDATION_BAN_WEIGHT",
    "RECOMMENDATION_SIGMOID_K", "RECOMMENDATION_LOW_WIN_RATE_GAP",
    "LOG_LEVEL", "LOG_TO_FILE",
)
DIVERGING_DEFAULT_KEYS = ("MUMU_OCR_RECHECK_ENABLED", "MUMU_OCR_AUTO_SWITCH_TAB")
BACKUP_STEMS = ("heroes", "guides", "synergies")
KEY_DATA_FILES = ("data/heroes.json", "data/guides.json", "data/synergies.json")
AI_LOG_PATTERNS = ("401", "思考过程耗尽", "超过最大重试", "10061")


@dataclass
class Finding:
    group: str
    name: str
    status: str
    detail: str = ""
    fix: str = ""


def _env_file_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def _env_keys(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in _env_file_lines(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    return out


# ── 环境 ──────────────────────────────────────────────────────────────

def check_environment() -> list[Finding]:
    findings = []
    versions = {}
    for label, path, pattern in (
        ("pre-commit", ROOT / ".pre-commit-config.yaml", r"ruff check src tests \((\d+\.\d+\.\d+)"),
        ("environment.yml", ROOT / "environment.yml", r"ruff==(\d+\.\d+\.\d+)"),
        ("environment-ci.yml", ROOT / "environment-ci.yml", r"ruff==(\d+\.\d+\.\d+)"),
    ):
        m = re.search(pattern, "\n".join(_env_file_lines(path)))
        versions[label] = m.group(1) if m else None
    distinct = {v for v in versions.values() if v}
    if len(distinct) == 1 and None not in versions.values():
        findings.append(Finding("环境", "ruff 版本三处一致", PASS, f"ruff=={distinct.pop()}"))
    else:
        findings.append(Finding(
            "环境", "ruff 版本三处一致", FAIL, f"各处={versions}",
            fix="同步 .pre-commit-config.yaml / environment.yml / environment-ci.yml 的 ruff 版本，否则钩子与 CI 判定分叉"))

    spec = importlib.util.find_spec("paddle")
    if spec is None:
        findings.append(Finding("环境", "paddle 可导入", FAIL, "未安装",
                                fix="conda env 按 environment.yml 安装 paddlepaddle-gpu"))
    else:
        version = None
        for dist in ("paddlepaddle-gpu", "paddlepaddle"):
            with contextlib.suppress(importlib.metadata.PackageNotFoundError):
                version = importlib.metadata.version(dist)
                break
        findings.append(Finding("环境", "paddle 可导入", PASS, version or "已安装（版本未知）"))

    ocr_model = Path.home() / ".paddleocr" / "whl" / "det" / "ch" / "ch_PP-OCRv4_det_infer"
    if ocr_model.is_dir():
        findings.append(Finding("环境", "OCR 检测模型", PASS, str(ocr_model)))
    else:
        findings.append(Finding("环境", "OCR 检测模型", WARN, f"缺少 {ocr_model}",
                                fix="首次运行 OCR 功能会自动下载，或从备份环境复制"))
    return findings


# ── 设备 ──────────────────────────────────────────────────────────────

def check_devices() -> list[Finding]:
    findings = []
    from src.config.env import get_mumu_config

    adb_path = get_mumu_config().get("mumu_adb_path")
    if not adb_path or not Path(adb_path).exists():
        findings.append(Finding(
            "设备", "ADB 路径", FAIL, f"配置的 ADB 不存在: {adb_path}",
            fix="检查 config.env 的 MUMU_ADB_PATH，或运行模拟器后由 prober 自动探测"))
        return findings
    findings.append(Finding("设备", "ADB 路径", PASS, str(adb_path)))

    try:
        result = subprocess.run([str(adb_path), "devices"], capture_output=True,
                                text=True, timeout=10)
        serials = re.findall(r"^(127\.0\.0\.1:\d+)\s+device\b", result.stdout, re.M)
    except (OSError, subprocess.SubprocessError) as error:
        findings.append(Finding("设备", "adb devices", WARN, f"执行失败: {error}",
                                fix="确认模拟器已启动且 ADB 服务正常"))
        return findings
    if not serials:
        findings.append(Finding("设备", "模拟器在线", WARN, "无 127.0.0.1:<port> 在线设备",
                                fix="启动 MuMu 模拟器后重试"))
    elif len(serials) > 1:
        findings.append(Finding(
            "设备", "模拟器在线", WARN, f"检测到 {len(serials)} 台设备 {serials}",
            fix="多设备冲突会触发 more-than-one-device 错误，只保留一个模拟器实例或在配置中固定端口"))
    else:
        findings.append(Finding("设备", "模拟器在线", PASS, serials[0]))
    return findings


# ── 私有数据仓 ────────────────────────────────────────────────────────

def check_private_repo() -> list[Finding]:
    from src.scripts import pull_data

    findings = []
    try:
        repo = pull_data.resolve_data_repo()
    except Exception as error:  # noqa: BLE001 — 检查器不得因单组失败中断
        findings.append(Finding("私有仓", "路径解析", FAIL, str(error)))
        return findings
    if not (repo / ".git").is_dir():
        findings.append(Finding(
            "私有仓", "存在性", FAIL, f"{repo} 不存在或未初始化",
            fix="git clone https://gitee.com/chen-xianghao920/mjs_data_private.git 到项目同级；"
                "位置不同时设置 MJS_DATA_REPO"))
        return findings
    findings.append(Finding("私有仓", "存在性", PASS, str(repo)))

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = pull_data.verify(repo)
    if code == 0:
        findings.append(Finding("私有仓", "manifest 一致性", PASS, buffer.getvalue().strip()))
    else:
        findings.append(Finding(
            "私有仓", "manifest 一致性", FAIL, buffer.getvalue().strip(),
            fix="在私有仓重跑 scripts/gen_manifest.py；若工作区文件损坏，从远端重新 clone"))
    return findings


# ── 数据 ──────────────────────────────────────────────────────────────

def check_data() -> list[Finding]:
    findings = []
    broken = []
    for path in sorted((ROOT / "data").glob("*.json")):
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            broken.append(f"{path.name}: {error}")
    if broken:
        findings.append(Finding(
            "数据", "data/*.json 可解析", FAIL, "；".join(broken),
            fix="从 data/backups 恢复对应文件，或重跑 pull_data pull"))
    else:
        count = len(list((ROOT / "data").glob("*.json")))
        findings.append(Finding("数据", "data/*.json 可解析", PASS, f"{count} 个文件"))

    for rel in KEY_DATA_FILES:
        path = ROOT / rel
        if not path.exists():
            findings.append(Finding(
                "数据", rel, FAIL, "文件缺失",
                fix="运行 python -m src.scripts.pull_data pull 从私有仓恢复"))
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue  # 上一条已报
        if not isinstance(data, list) or not data:
            findings.append(Finding(
                "数据", rel, FAIL, "内容为空或顶层非数组（置空保存会不可逆丢失）",
                fix="勿保存；从 data/backups 或私有仓恢复"))

    backup_dir = ROOT / "data" / "backups"
    for stem in BACKUP_STEMS:
        snapshots = sorted(backup_dir.glob(f"{stem}-*.json"))
        if not snapshots:
            findings.append(Finding(
                "数据", f"backups/{stem}", WARN, "无任何基线",
                fix="在数据管理界面做一次修改触发备份，或跑一次周更采集"))
            continue
        age_days = (time.time() - snapshots[-1].stat().st_mtime) / 86400
        if age_days > 30:
            findings.append(Finding(
                "数据", f"backups/{stem}", WARN, f"最新基线已滞后 {age_days:.0f} 天",
                fix=f"重跑一次 {stem} 的生成/采集流程刷新基线"))
        else:
            findings.append(Finding(
                "数据", f"backups/{stem}", PASS, f"最新基线 {age_days:.0f} 天前"))
    return findings


# ── 配置 ──────────────────────────────────────────────────────────────

def check_config() -> list[Finding]:
    findings = []
    user_env = _env_keys(ROOT / "config.env")
    example_env = _env_keys(ROOT / "config.env.example")

    dead = [key for key in DEAD_CONFIG_KEYS if key in user_env]
    if dead:
        findings.append(Finding(
            "配置", "死键", WARN, f"{'、'.join(dead)} 已无代码消费者",
            fix="从 config.env 删除，避免误以为配置生效"))
    else:
        findings.append(Finding("配置", "死键", PASS, "无"))

    missing = [key for key in OPTIONAL_CONFIG_KEYS if key not in user_env]
    if missing:
        findings.append(Finding(
            "配置", "可选键未配置", WARN, f"{'、'.join(missing)} 走代码默认值",
            fix="需要调整时按 config.env.example 注释添加"))
    else:
        findings.append(Finding("配置", "可选键未配置", PASS, "无"))

    diverging = [key for key in DIVERGING_DEFAULT_KEYS
                 if key in user_env and key in example_env
                 and user_env[key].lower() != example_env[key].lower()]
    if diverging:
        findings.append(Finding(
            "配置", "与 example 默认相反", WARN,
            f"{'、'.join(diverging)}（本地与模板默认相反，属有意覆盖请忽略；否则核对）"))

    try:
        from src.config.profiles import get_api_config

        model = get_api_config().get("model")
    except Exception as error:  # noqa: BLE001
        findings.append(Finding("配置", "生效模型", SKIP, f"读取失败: {error}"))
        return findings
    from src.config.env import get_model_pricing

    if model and get_model_pricing(model) is None:
        findings.append(Finding(
            "配置", "定价覆盖", WARN, f"生效模型 {model} 不在 model_pricing.json",
            fix="补充价格条目，消除每次调用的'价格配置不可用'告警并保证费用估算准确"))
    else:
        findings.append(Finding("配置", "定价覆盖", PASS, f"生效模型 {model} 已有价格"))
    return findings


# ── AI 链路 ───────────────────────────────────────────────────────────

def check_ai_link() -> list[Finding]:
    findings = []
    cutoff = time.time() - 7 * 86400
    date_prefix_floor = time.strftime("%Y-%m-%d", time.localtime(cutoff))
    counts: dict[str, int] = {}
    log_files = [ROOT / "logs" / "app.log", ROOT / "logs" / "scraper" / "ai_generation.log"]
    for path in log_files:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            if line[:10] < date_prefix_floor:
                continue
            for pattern in AI_LOG_PATTERNS:
                if pattern in line:
                    counts[pattern] = counts.get(pattern, 0) + 1
    hits = {k: v for k, v in counts.items() if v}
    if hits:
        findings.append(Finding(
            "AI链路", "近 7 天故障信号", WARN,
            "；".join(f"{k}×{v}" for k, v in sorted(hits.items())),
            fix="401=Key 失效；额度耗尽=调大 MAX_OUTPUT_TOKENS 或换模型；详见对应日志"))
    else:
        findings.append(Finding("AI链路", "近 7 天故障信号", PASS, "无 401/额度耗尽/重试耗尽记录"))
    return findings


# ── 日志健康 ──────────────────────────────────────────────────────────

def check_logs() -> list[Finding]:
    findings = []
    stale_dumps = list((ROOT / "logs").glob("pytest-timeout-*.log"))
    if stale_dumps:
        findings.append(Finding(
            "日志", "timeout 转储位置", WARN,
            f"logs/ 下残留 {len(stale_dumps)} 个 pytest-timeout 转储（现应落 .tmp_test/timeout_dumps）",
            fix="删除 logs/pytest-timeout-*.log"))
    else:
        findings.append(Finding("日志", "timeout 转储位置", PASS, "logs/ 干净"))

    rag_logs = list((ROOT / "logs" / "rag").glob("*.log"))
    empty = [p.name for p in rag_logs if p.stat().st_size == 0]
    if rag_logs and len(empty) == len(rag_logs):
        findings.append(Finding(
            "日志", "logs/rag 落盘", WARN, f"{len(rag_logs)} 个日志全部 0 字节（维护脚本未跑过或钩子未装）",
            fix="跑一次 python -m src.scripts.maintain_rag --check 验证落盘"))
    else:
        findings.append(Finding(
            "日志", "logs/rag 落盘", PASS,
            f"{len(rag_logs) - len(empty)}/{len(rag_logs)} 个有内容"))
    return findings


# ── 磁盘 ──────────────────────────────────────────────────────────────

def check_disk() -> list[Finding]:
    findings = []
    targets = ["dist", "build", "build_deps", "env_backup", "screenshots", ".tmp_test", "logs"]
    sizes: dict[str, float] = {}
    try:
        result = subprocess.run(["du", "-sm", *(ROOT / t for t in targets)],
                                capture_output=True, text=True, timeout=60, cwd=ROOT)
        for line in result.stdout.splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2:
                sizes[Path(parts[1].strip()).name] = float(parts[0])
    except (OSError, subprocess.SubprocessError):
        findings.append(Finding("磁盘", "目录占用", SKIP, "du 不可用"))
        return findings
    heavy = {name: mb for name, mb in sizes.items() if mb >= 500}
    if heavy:
        findings.append(Finding(
            "磁盘", "大目录", WARN,
            "；".join(f"{name} {mb:.0f}MB" for name, mb in sorted(heavy.items())),
            fix="env_backup 可归档；dist/build 可清理；screenshots 见回收任务"))
    else:
        findings.append(Finding(
            "磁盘", "目录占用", PASS, "；".join(f"{k} {v:.0f}MB" for k, v in sorted(sizes.items()))))

    shots = list((ROOT / "screenshots").rglob("*.png"))
    if shots:
        oldest = min(p.stat().st_mtime for p in shots)
        age_days = (time.time() - oldest) / 86400
        total_mb = sum(p.stat().st_size for p in shots) / 1048576
        if age_days > 30 or total_mb > 300:
            findings.append(Finding(
                "磁盘", "screenshots 堆积", WARN,
                f"{len(shots)} 个文件 / {total_mb:.0f}MB / 最早 {age_days:.0f} 天前（零回收机制）",
                fix="等 P1-6 磁盘回收任务，或手动清理"))
        else:
            findings.append(Finding("磁盘", "screenshots", PASS, f"{len(shots)} 个文件"))
    return findings


# ── 合规触发器 ────────────────────────────────────────────────────────

def check_compliance() -> list[Finding]:
    url = f"https://api.github.com/repos/{REPO_SLUG}"
    request = urllib.request.Request(url, headers={"User-Agent": "mjs-doctor"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as error:  # noqa: BLE001 — 离线/限流时降级 SKIP
        return [Finding("合规", "GitHub 触发器", SKIP, f"查询失败: {error}")]

    forks, stars = payload.get("forks_count", 0), payload.get("stargazers_count", 0)
    if forks > 0 or stars > 50:
        return [Finding(
            "合规", "GitHub 触发器", WARN, f"forks={forks}, stars={stars}",
            fix=f"触发升级预案（{COMPLIANCE_TRIGGERS}）→ 立即执行全量历史重写，见 retrospective.md")]
    return [Finding("合规", "GitHub 触发器", PASS, f"forks={forks}, stars={stars}（阈值内）")]


# ── 安全 ──────────────────────────────────────────────────────────────

def check_security() -> list[Finding]:
    findings = []
    sensitive = ["config.env", "config/api_profiles.json"]
    for rel in sensitive:
        path = ROOT / rel
        if not path.exists():
            findings.append(Finding("安全", rel, PASS, "不存在（无需检查）"))
            continue
        result = subprocess.run(["git", "check-ignore", rel], cwd=ROOT,
                                capture_output=True, text=True)
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", rel],
                                 cwd=ROOT, capture_output=True, text=True)
        if tracked.returncode == 0:
            findings.append(Finding(
                "安全", rel, FAIL, "被 git 跟踪（含敏感内容却会随仓库分发）",
                fix="git rm --cached 并加入 .gitignore"))
        elif result.returncode == 0:
            findings.append(Finding("安全", rel, PASS, "已被 .gitignore 忽略，不会入库"))
        else:
            findings.append(Finding(
                "安全", rel, WARN, "未被 ignore 也未被跟踪（git add -A 会带入）",
                fix=f"把 {rel} 加入 .gitignore"))
    return findings


CHECK_GROUPS = [
    ("环境", check_environment),
    ("设备", check_devices),
    ("私有仓", check_private_repo),
    ("数据", check_data),
    ("配置", check_config),
    ("AI链路", check_ai_link),
    ("日志", check_logs),
    ("磁盘", check_disk),
    ("合规", check_compliance),
    ("安全", check_security),
]


def run_checks(offline: bool = False) -> list[Finding]:
    findings: list[Finding] = []
    for group, checker in CHECK_GROUPS:
        if offline and group == "合规":
            findings.append(Finding(group, "GitHub 触发器", SKIP, "--offline 跳过"))
            continue
        try:
            findings.extend(checker())
        except Exception as error:  # noqa: BLE001 — 单组失败不得拖垮整份报告
            findings.append(Finding(group, checker.__name__, FAIL,
                                    f"检查器异常: {error!r}"))
    return findings


def render(findings: list[Finding], elapsed: float) -> str:
    lines = ["═" * 30, "名将杀 doctor 自检报告", "═" * 30]
    for f in findings:
        line = f"[{f.status}] {f.group}/{f.name}: {f.detail}" if f.detail else f"[{f.status}] {f.group}/{f.name}"
        lines.append(line)
        if f.fix:
            lines.append(f"         ↳ 修复: {f.fix}")
    tally = {status: sum(1 for f in findings if f.status == status)
             for status in (PASS, WARN, FAIL, SKIP)}
    lines.append("─" * 30)
    lines.append(
        f"结论：{tally[PASS]} PASS / {tally[WARN]} WARN / {tally[FAIL]} FAIL / {tally[SKIP]} SKIP"
        f"（耗时 {elapsed:.1f}s；WARN/FAIL 的修复动作见上方 ↳ 行）")
    return "\n".join(lines)


def main() -> None:
    install_crash_logger("doctor")
    parser = argparse.ArgumentParser(description="一键自检（只读）")
    parser.add_argument("--offline", action="store_true", help="跳过网络检查（合规触发器）")
    parser.add_argument("--json", action="store_true", help="机器可读输出（ops.py 复用）")
    args = parser.parse_args()

    started = time.perf_counter()
    findings = run_checks(offline=args.offline)
    elapsed = time.perf_counter() - started

    if args.json:
        print(json.dumps(
            [{"group": f.group, "name": f.name, "status": f.status,
              "detail": f.detail, "fix": f.fix} for f in findings],
            ensure_ascii=False, indent=2))
    else:
        print(render(findings, elapsed))
    sys.exit(0 if not any(f.status == FAIL for f in findings) else 1)


if __name__ == "__main__":
    main()
