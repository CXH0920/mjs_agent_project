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
# 循环依赖：运行期 import 图中不允许出现任何强连通分量（SCC）。
#
# 历史：审计 F3 的循环簇（manager <-> json_repository/三个 manager，根因是
# DataIssue/DataFacade 放置在 manager.py）已随 issues.py / facade.py 拆分修复；
# scripts.sync_rule_stats <-> scripts.audit_rule_doc 互引环已随快照读写下沉
# snapshot_common.py 消除。白名单保持为空：发现任何循环一律直接修复。
# ---------------------------------------------------------------------------
ALLOWED_CYCLES: list[frozenset[str]] = []


def _module_modules() -> dict[str, Path]:
    """dotted 模块名 -> 文件路径（含包 __init__.py）。

    键与 import 语句同命名空间（"src.x.y"，相对项目根）——此前误用
    ROOT.parent 前缀导致绝对导入永远解析不到，SCC 检测在空图上空转。
    """
    modules: dict[str, Path] = {}
    for py in _iter_py_files():
        rel = py.relative_to(ROOT).with_suffix("")
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
                    pkg_parts = list(path.parent.relative_to(ROOT).parts)
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


# ---------------------------------------------------------------------------
# UI 禁止构造数据层仓储/Manager：持久化对象的装配只属于组合根（app_services）
# 与业务层工厂（maintenance_repositories.build 等）。名单由 src/data(+business)
# 对持久化基类的继承闭包动态生成，不依赖命名约定——值对象（pydantic/enum/
# dataclass）与模块级函数天然不在列。守卫落地前 UI 命中 7 处，落地后应为 0。
# ---------------------------------------------------------------------------
COMPOSITION_ROOTS = {"src/ui/app/app_services.py"}
PERSISTENCE_ROOTS = {"DataManager", "JsonRepository", "DataFacade", "_JsonRepository"}


def _base_names(node: ast.ClassDef) -> list[str]:
    """提取基类名；泛型下标（DataManager[Hero]，5 个 Manager 均此写法）解包为 DataManager。"""
    names: list[str] = []
    for base in node.bases:
        if isinstance(base, ast.Name):
            names.append(base.id)
        elif isinstance(base, ast.Attribute):
            names.append(base.attr)
        elif isinstance(base, ast.Subscript):
            value = base.value
            if isinstance(value, ast.Name):
                names.append(value.id)
            elif isinstance(value, ast.Attribute):
                names.append(value.attr)
    return names


def _class_table() -> dict[str, dict]:
    """src/data 与 src/business 的类 -> {file, bases, dataclass}。"""
    table: dict[str, dict] = {}
    for pkg in ("data", "business"):
        for py in (SRC / pkg).rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            tree = ast.parse(py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    table[node.name] = {
                        "file": py.relative_to(ROOT).as_posix(),
                        "bases": _base_names(node),
                        "dataclass": any(
                            (isinstance(d, ast.Name) and d.id == "dataclass")
                            or (isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == "dataclass")
                            or (isinstance(d, ast.Attribute) and d.attr == "dataclass")
                            for d in node.decorator_list
                        ),
                    }
    return table


def _persistence_classes(table: dict[str, dict]) -> set[str]:
    """继承闭包：所有（传递地）可达持久化根的类名。"""
    resolved: set[str] = set()

    def reaches(name: str, seen: frozenset) -> bool:
        if name in resolved:
            return True
        if name in seen or name not in table:
            return False
        if name in PERSISTENCE_ROOTS:
            resolved.add(name)
            return True
        if any(reaches(base, seen | {name}) for base in table[name]["bases"]):
            resolved.add(name)
            return True
        return False

    for name in table:
        reaches(name, frozenset())
    return resolved


def test_ui_must_not_construct_data_layer() -> None:
    table = _class_table()
    repos = _persistence_classes(table)
    offenders = []
    for py in (SRC / "ui").rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        rel = py.relative_to(ROOT).as_posix()
        if rel in COMPOSITION_ROOTS:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        local_classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else None)
            if name in repos and name not in local_classes:
                offenders.append(f"{rel}:{node.lineno}  {name}()")
    assert not offenders, (
        "UI 直接构造数据层仓储/Manager（装配只属于组合根 app_services 或业务层工厂）：\n"
        + "\n".join(f"  {o}" for o in offenders)
    )


def test_data_layer_classes_are_classified() -> None:
    """tripwire：src/data 新类必须归类为持久化闭包后代 / pydantic / enum / dataclass。

    从零手写、不继承任何现有基类的新存储类会让本测试变红——此时应继承
    DataManager/JsonRepository 复用原子写与文件锁，或在该 PR 中说明理由并
    显式扩充 PERSISTENCE_ROOTS。
    """
    table = _class_table()
    repos = _persistence_classes(table)
    unclassified = []
    for name, info in table.items():
        if not info["file"].startswith("src/data/"):
            continue
        if name in repos:
            continue
        bases = info["bases"]
        if "BaseModel" in bases or any("Enum" in b for b in bases) or info["dataclass"]:
            continue
        unclassified.append(f"{info['file']}  {name}")
    assert not unclassified, (
        "src/data 出现既非仓储后代也非值对象（pydantic/enum/dataclass）的新类：\n"
        + "\n".join(f"  {u}" for u in unclassified)
        + "\n新持久化类应继承 DataManager/JsonRepository；确需新根请在 PR 说明并扩充 PERSISTENCE_ROOTS"
    )
