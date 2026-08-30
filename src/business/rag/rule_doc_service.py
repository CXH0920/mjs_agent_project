# -*- coding: utf-8 -*-
"""元规则 T0 文档维护服务（rule_doc_service.py）

为「知识库维护 → 元规则维护」页签提供纯函数：audit 输出解析、数据段差异解析、
提案读写、疑难登记（本地待办文件）、疑难转提案。命令执行由 UI 层用 QProcess 完成。
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from src.config.env import PROJECT_ROOT
from src.data.json_repository import atomic_write_json

DEFAULT_DOC = PROJECT_ROOT / "docs" / "元规则整理-完整版.md"
PROPOSAL_DIR = PROJECT_ROOT / "docs" / "archive" / "proposals"
PENDING_FILE = PROJECT_ROOT / "docs" / "rule_doc_pending.json"
RAG_EVALS_DIR = PROJECT_ROOT / "data" / "rag_evals"

# ---------------------------------------------------------------------------
# audit 输出解析
# ---------------------------------------------------------------------------

ISSUE_RE = re.compile(r"^\s*\[(ERROR|WARN|INFO)\]\s*(.*)$")


def parse_audit_output(text: str) -> list[dict]:
    """解析 audit_rule_doc.py 输出，返回 [{level, message}]（含汇总行）。"""
    issues = []
    for line in text.splitlines():
        m = ISSUE_RE.match(line)
        if m:
            issues.append({"level": m.group(1), "message": m.group(2).strip()})
        elif "汇总：" in line:
            issues.append({"level": "SUMMARY", "message": line.strip()})
    return issues


def audit_issue_counts(issues: list[dict]) -> dict:
    counts = {"ERROR": 0, "WARN": 0, "INFO": 0}
    for it in issues:
        if it["level"] in counts:
            counts[it["level"]] += 1
    return counts


# ---------------------------------------------------------------------------
# 数据段差异解析
# ---------------------------------------------------------------------------

def parse_sync_diff(path: Path | str) -> list[dict]:
    """解析 sync_rule_stats.py --json 输出的差异项。"""
    path = Path(path)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        return []
    return [
        {"section": d.get("section", ""), "line_no": d.get("line_no", 0),
         "kind": d.get("kind", ""), "old": d.get("old"), "new": d.get("new"),
         "message": d.get("message", "")}
        for d in data if isinstance(d, dict)
    ]


def sync_json_path(root: Path) -> Path:
    """差异报告路径（sync_rule_stats.py --json 的输出）。

    落在 src/scripts/（运行时隐藏文件的既有落点，.gitignore 已覆盖）；
    脚本迁移 src/scripts/ 时本路径曾被遗漏，指向从未存在的顶层 scripts/ 导致写报告崩溃。
    """
    return root / "src" / "scripts" / ".sync_rule_stats_report.json"


def confirmed_diff_path(root: Path) -> Path:
    """B2 确认清单路径（sync_rule_stats.py --apply-json 的输入）。"""
    return root / "src" / "scripts" / ".sync_confirmed_diffs.json"


# ---------------------------------------------------------------------------
# 提案读写
# ---------------------------------------------------------------------------

def list_proposals(root: Path) -> list[dict]:
    """列出 docs/archive/proposals 下 CP-*.json（按编号倒序）。"""
    d = root / "docs" / "archive" / "proposals"
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("CP-*.json"), reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        items = data.get("items", [])
        out.append({
            "path": str(p),
            "proposal_id": data.get("proposal_id", p.stem),
            "created_at": data.get("created_at", ""),
            "total": len(items),
            "approved": sum(1 for i in items if i.get("status") in ("approved", "revised")),
            "rejected": sum(1 for i in items if i.get("status") == "rejected"),
        })
    return out


def parse_proposal(path: Path | str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# 文档行缓存：path -> (mtime, lines)；文档未变化时不重复整文件读取
_DOC_LINES_CACHE: dict[str, tuple[float, list[str]]] = {}


def _doc_lines(doc_path: Path) -> list[str] | None:
    """读取文档行列表（带 mtime 缓存）；文件缺失/不可读返回 None。"""
    try:
        stat = doc_path.stat()
    except OSError:
        return None
    cached = _DOC_LINES_CACHE.get(str(doc_path))
    if cached is not None and cached[0] == stat.st_mtime:
        return cached[1]
    try:
        lines = doc_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    _DOC_LINES_CACHE[str(doc_path)] = (stat.st_mtime, lines)
    return lines


def doc_target_line(doc_path: Path | str, item: dict) -> str | None:
    """返回提案项在文档中的当前目标行（用于 diff 的 local 侧）。

    faq_revise → target=faq_编号 定位 `| N |` 行；row_revise → old_text 精确匹配。
    找不到返回 None。
    """
    doc_path = Path(doc_path)
    lines = _doc_lines(doc_path)
    if lines is None:
        return None
    m = re.match(r"faq_(\d+)", str(item.get("target", "")))
    if m:
        no = m.group(1)
        for ln in lines:
            if re.match(r"^\|\s*%s\s*\|" % no, ln):
                return ln
        return None
    if item.get("type") == "row_revise":
        old = str(item.get("old_text") or "").strip()
        for ln in lines:
            if ln.strip() == old:
                return ln
    return None


def doc_section_context(doc_path: Path | str, target: str, radius: int = 3) -> str | None:
    """返回目标位置附近的文档上下文。

    faq_编号 → 该行 ±radius；小节号（如 5.2）→ 标题后若干行；找不到返回 None。
    """
    doc_path = Path(doc_path)
    lines = _doc_lines(doc_path)
    if lines is None:
        return None
    m = re.match(r"faq_(\d+)", str(target))
    if m:
        no = m.group(1)
        idx = next((i for i, ln in enumerate(lines) if re.match(r"^\|\s*%s\s*\|" % no, ln)), None)
        if idx is None:
            return None
        start, end = max(0, idx - radius), min(len(lines), idx + radius + 1)
        return "\n".join(lines[start:end])
    for prefix in ("### " + str(target), "## " + str(target)):
        idx = next((i for i, ln in enumerate(lines) if ln.startswith(prefix)), None)
        if idx is not None:
            end = min(len(lines), idx + 1 + radius * 4)
            return "\n".join(lines[idx:end])
    return None


def doc_line_at(doc_path: Path | str, line_no: int) -> str | None:
    """按 0 基行号读文档行；越界/文件缺失返回 None（差异查看用）。"""
    doc_path = Path(doc_path)
    lines = _doc_lines(doc_path)
    if lines is None:
        return None
    if not isinstance(line_no, int) or line_no < 0 or line_no >= len(lines):
        return None
    return lines[line_no]


def doc_context_around(doc_path: Path | str, line_no: int, radius: int = 3) -> str | None:
    """目标行 ±radius 的上下文文本（越界自动裁剪）；文件缺失返回 None。"""
    doc_path = Path(doc_path)
    lines = _doc_lines(doc_path)
    if lines is None:
        return None
    if not isinstance(line_no, int) or line_no < 0 or line_no >= len(lines):
        return None
    start, end = max(0, line_no - radius), min(len(lines), line_no + radius + 1)
    return "\n".join(lines[start:end])


VALID_PROPOSAL_STATUSES = {"pending", "approved", "revised", "rejected"}


def update_proposal_item(root: Path, proposal_path: str, item_id: str,
                         status: str, edited_text: str | None = None) -> dict:
    """原位更新提案 JSON 中指定条目的 status/edited_text（临时文件 + os.replace 原子写）。

    非法 status / 未知 item_id → ValueError；写失败抛 OSError（原文件不变）。
    """
    if status not in VALID_PROPOSAL_STATUSES:
        raise ValueError("非法提案状态：%s（可选 %s）" % (status, "、".join(sorted(VALID_PROPOSAL_STATUSES))))
    path = Path(proposal_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    for item in data.get("items", []):
        if item.get("id") == item_id:
            item["status"] = status
            if edited_text is not None:
                item["edited_text"] = edited_text
            break
    else:
        raise ValueError("找不到提案项 %s（%s）" % (item_id, path.name))
    atomic_write_json(path, data)
    return data


# ---------------------------------------------------------------------------
# 疑难登记（本地待办文件 docs/rule_doc_pending.json）
# ---------------------------------------------------------------------------

PENDING_SCHEMA = {"items": [{"id": int, "date": str, "description": str,
                             "involved": str, "source": str, "status": str}]}


def load_pending(root: Path = PROJECT_ROOT) -> list[dict]:
    path = root / "docs" / "rule_doc_pending.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data.get("items", []) if isinstance(data, dict) else []


def add_pending(root: Path, description: str, involved: str = "",
                source: str = "实战/测试") -> list[dict]:
    """登记一条疑难（追加到本地待办文件），返回最新清单。"""
    items = load_pending(root)
    next_id = max((i.get("id", 0) for i in items), default=0) + 1
    items.append({
        "id": next_id,
        "date": date.today().isoformat(),
        "description": description.strip(),
        "involved": involved.strip(),
        "source": source.strip() or "实战/测试",
        "status": "open",
    })
    path = root / "docs" / "rule_doc_pending.json"
    atomic_write_json(path, {"items": items})
    return items


def pending_to_proposal(root: Path, pending_id: int) -> Path:
    """把一条 open 疑难转成 FAQ 新增提案（status=pending），返回提案 JSON 路径。"""
    items = load_pending(root)
    item = next((i for i in items if i.get("id") == pending_id), None)
    if item is None:
        raise ValueError("找不到疑难登记 id=%d" % pending_id)
    proposal_id = "CP-%s-P%03d" % (date.today().isoformat(), pending_id)
    proposal = {
        "proposal_id": proposal_id,
        "created_at": date.today().isoformat(),
        "source": "疑难登记转提案",
        "items": [{
            "id": "P-01",
            "type": "faq_new",
            "target": "5.2",
            "suggested_text": item["description"],
            "source": item.get("source", "实战/测试"),
            "basis": "疑难登记 #%d（%s，涉及 %s）" % (item["id"], item["date"], item.get("involved", "")),
            "suggested_status": "待确认",
            "rationale": "组合结算盲点消化为 FAQ",
            "status": "pending",
            "edited_text": None,
        }],
    }
    out = (root / "docs" / "archive" / "proposals") / (proposal_id + ".json")
    atomic_write_json(out, proposal)
    # 标记疑难为已转提案
    for i in items:
        if i.get("id") == pending_id:
            i["status"] = "proposed"
    atomic_write_json(
        root / "docs" / "rule_doc_pending.json",
        {"items": items},
    )
    return out


# ---------------------------------------------------------------------------
# 文档第 7 章疑难解析（只读展示）
# ---------------------------------------------------------------------------

def parse_doc_chapter7(doc_path: Path | str = DEFAULT_DOC) -> list[dict]:
    """解析完整版第 7 章疑难登记表，返回 [{date, description, involved, source, status}]。"""
    doc_path = Path(doc_path)
    if not doc_path.exists():
        return []
    lines = doc_path.read_text(encoding="utf-8").splitlines()
    start = None
    for i, ln in enumerate(lines):
        if re.match(r"^###?\s*7\.1", ln):
            start = i
            break
    if start is None:
        return []
    rows = []
    for ln in lines[start:]:
        if ln.startswith("## "):
            break
        if not ln.startswith("|") or re.match(r"^\|\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|$", ln):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) < 5 or cells[0] in ("登记日期", "#"):
            continue
        rows.append({"date": cells[0], "description": cells[1], "involved": cells[2],
                     "source": cells[3], "status": cells[4] if len(cells) > 4 else ""})
    return rows