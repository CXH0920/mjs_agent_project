# -*- coding: utf-8 -*-
"""架构守护测试：分层禁令 / 循环依赖 / 类规模棘轮。

全部基于 stdlib AST 静态分析，不 import 项目代码，无 Qt 依赖，可无头运行。
预算/白名单只许收紧（缩小），放宽必须在 PR 说明里给出理由。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

# ---------------------------------------------------------------------------
# 底层包禁止向上依赖：ui 是最顶层，任何非 ui/main/scripts 的包不得反向 import ui。
# （main.py 是入口；scripts 允许 import ui：capture_ui_baselines 等基线工具驱动真实窗口。）
# ---------------------------------------------------------------------------
UI_IMPORTERS_ALLOWED = {"ui", "scripts", "main.py"}


def _iter_py_files() -> list[Path]:
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts]


def _internal_imports(path: Path) -> set[str]:
    """解析文件运行期依赖的内部顶级包（src.data / src.ui / ...）。

    - 跳过 ``if TYPE_CHECKING:`` 块（仅类型检查用，非运行期依赖）；
    - 相对导入按文件所在包解析；
    - 函数内延迟导入也计入（运行期真实加载）。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()

    def is_type_checking(node: ast.If) -> bool:
        test = node.test
        target = test.attr if isinstance(test, ast.Attribute) else (
            test.id if isinstance(test, ast.Name) else None)
        return target == "TYPE_CHECKING"

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.If) and is_type_checking(node):
            return
        candidates: list[str] = []
        if isinstance(node, ast.Import):
            candidates = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = (node.module or "").split(".") if node.module else []
            else:
                pkg_parts = list(path.parent.relative_to(ROOT).parts)
                base = pkg_parts[: len(pkg_parts) - (node.level - 1)]
                base += (node.module or "").split(".") if node.module else []
            candidates = [".".join(base)] if base else []
            candidates += [".".join(base + [alias.name]) for alias in node.names]
        for cand in candidates:
            parts = cand.split(".")
            if len(parts) >= 2 and parts[0] == "src":
                found.add(parts[1])
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return found


def test_no_upward_layer_dependency() -> None:
    offenders = []
    for py in _iter_py_files():
        top = py.relative_to(SRC).parts[0]
        if top in UI_IMPORTERS_ALLOWED:
            continue
        for dst in _internal_imports(py):
            if dst == "ui":
                offenders.append(f"{py.relative_to(ROOT)} -> src.ui")
    assert not offenders, "底层包不得 import ui（唯一入口是 main.py）：\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# 循环依赖：运行期 import 图中不允许出现白名单之外的强连通分量（SCC）。
#
# 现存唯一已知循环簇（审计 F3，根因：DataIssue/DataFacade 放置在 manager.py）：
#   src.data.manager <-> {json_repository, hero_manager, synergy_manager, guide_manager}
# F3 修复（移动 DataIssue / DataFacade 出 manager.py）后应删除整张白名单。
# ---------------------------------------------------------------------------
ALLOWED_CYCLES: list[frozenset[str]] = [
    frozenset({
        "src.data.manager",
        "src.data.json_repository",
        "src.data.hero_manager",
        "src.data.synergy_manager",
        "src.data.guide_manager",
    }),
]


def _module_modules() -> dict[str, Path]:
    """dotted 模块名 -> 文件路径（含包 __init__.py）。"""
    modules: dict[str, Path] = {}
    for py in _iter_py_files():
        rel = py.relative_to(ROOT.parent).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        modules[".".join(parts)] = py
    return modules


def _module_edges() -> dict[str, set[str]]:
    """模块级运行期 import 边（含函数内延迟导入，跳过 TYPE_CHECKING 块）。"""
    modules = _module_modules()
    edges: dict[str, set[str]] = {m: set() for m in modules}

    def deepest_module(dotted: str) -> str | None:
        parts = dotted.split(".")
        while parts:
            if ".".join(parts) in modules:
                return ".".join(parts)
            parts.pop()
        return None

    for mod, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))

        def is_type_checking(node: ast.If) -> bool:
            test = node.test
            target = test.attr if isinstance(test, ast.Attribute) else (
                test.id if isinstance(test, ast.Name) else None)
            return target == "TYPE_CHECKING"

        def visit(node: ast.AST) -> None:
            if isinstance(node, ast.If) and is_type_checking(node):
                return
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0:
                    base = (node.module or "").split(".") if node.module else []
                else:
                    pkg_parts = list(path.parent.relative_to(ROOT.parent).parts)
                    base = pkg_parts[: len(pkg_parts) - (node.level - 1)]
                    base += (node.module or "").split(".") if node.module else []
                targets = [".".join(base)] if base else []
                targets += [".".join(base + [alias.name]) for alias in node.names]
            for cand in targets:
                resolved = deepest_module(cand)
                if resolved and resolved != mod:
                    edges[mod].add(resolved)
            for child in ast.iter_child_nodes(node):
                visit(child)

        visit(tree)
    return edges


def _strongly_connected_components(edges: dict[str, set[str]]) -> list[set[str]]:
    """Kosaraju SCC。"""
    nodes = list(edges)
    visited: set[str] = set()
    order: list[str] = []

    def dfs1(u: str) -> None:
        stack = [(u, iter(edges[u]))]
        visited.add(u)
        while stack:
            node, it = stack[-1]
            advanced = False
            for v in it:
                if v not in visited:
                    visited.add(v)
                    stack.append((v, iter(edges[v])))
                    advanced = True
                    break
            if not advanced:
                order.append(stack.pop()[0])

    for n in nodes:
        if n not in visited:
            dfs1(n)

    reverse: dict[str, set[str]] = {n: set() for n in nodes}
    for u, vs in edges.items():
        for v in vs:
            reverse[v].add(u)

    components: list[set[str]] = []
    assigned: set[str] = set()

    def dfs2(root: str) -> set[str]:
        comp = {root}
        assigned.add(root)
        stack = [root]
        while stack:
            u = stack.pop()
            for v in reverse[u]:
                if v not in assigned:
                    assigned.add(v)
                    comp.add(v)
                    stack.append(v)
        return comp

    for n in reversed(order):
        if n not in assigned:
            components.append(dfs2(n))
    return components


def test_no_new_import_cycles() -> None:
    edges = _module_edges()
    cycles = [c for c in _strongly_connected_components(edges) if len(c) > 1]
    unexpected = [c for c in cycles if not any(c <= allowed for allowed in ALLOWED_CYCLES)]
    assert not unexpected, (
        "发现白名单之外的循环依赖（修复而非加白名单）：\n"
        + "\n".join("  {" + ", ".join(sorted(c)) + "}" for c in unexpected)
    )
    # 白名单簇若变小（成员退出循环），说明 F3 部分修复——同步收缩白名单
    shrunk = [c for c in cycles if any(c < allowed for allowed in ALLOWED_CYCLES)]
    assert not shrunk, (
        "已知循环簇缺少以下成员（很好，说明 F3 部分修复）——请同步收缩白名单：\n"
        + "\n".join("  {" + ", ".join(sorted(c)) + "}" for c in shrunk)
    )


# ---------------------------------------------------------------------------
# 类规模棘轮：单类直接方法数不得超过预算。预算 = 当前全仓最差值，
# 重构缩小后应下调预算；新增超过 DEFAULT_MAX_METHODS 的类会被拦下。
# ---------------------------------------------------------------------------
DEFAULT_MAX_METHODS = 40
CLASS_BUDGETS: dict[str, int] = {
    "ui/app/main_window.py:MainWindow": 47,
    "ui/configuration/mumu_config_dialog.py:MumuConfigDialog": 52,
    "ui/maintenance/index_refinement_dialog.py:IndexRefinementDialog": 47,
    "ui/recommendation/recommendation_panel.py:RecommendationPanel": 41,
}


def _method_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for py in _iter_py_files():
        rel = py.relative_to(SRC).as_posix()
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                methods = sum(
                    1 for child in node.body
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                )
                counts[f"{rel}:{node.name}"] = methods
    return counts


def test_class_method_count_within_budget() -> None:
    offenders = []
    for key, count in _method_counts().items():
        budget = CLASS_BUDGETS.get(key, DEFAULT_MAX_METHODS)
        if count > budget:
            offenders.append(f"{key}: {count} > 预算 {budget}")
    assert not offenders, (
        "类方法数超出预算（重构缩小后请同步下调预算；放宽预算需在 PR 说明理由）：\n"
        + "\n".join(offenders)
    )
