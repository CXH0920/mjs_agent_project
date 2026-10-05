# -*- coding: utf-8 -*-
"""架构守护测试：分层禁令 / 循环依赖 / 类规模棘轮 / UI 数据 import 白名单 /
跨层直连禁令 / 文件行数棘轮。

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
# 2026-09 审计 C1：manager.py 的 TYPE_CHECKING 反向导入三个子 Manager 构成
# 类型级环（运行期不可见，上面的运行期 SCC 检测漏掉），随
# apply_incremental_update 迁 facade.py 消解；守护同步扩展到类型边
# （test_no_type_level_import_cycles）。
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


def _module_edges(include_type_checking: bool = False) -> dict[str, set[str]]:
    """模块级 import 边（含函数内延迟导入）。

    默认跳过 ``if TYPE_CHECKING:`` 块（仅类型检查用，非运行期依赖）；
    include_type_checking=True 时计入——类型级依赖同样不得成环
    （基类模块不得反向知晓子类），见 test_no_type_level_import_cycles。
    """
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
            if isinstance(node, ast.If) and is_type_checking(node) and not include_type_checking:
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


def test_no_type_level_import_cycles() -> None:
    edges = _module_edges(include_type_checking=True)
    cycles = [c for c in _strongly_connected_components(edges) if len(c) > 1]
    assert not cycles, (
        "TYPE_CHECKING 类型级循环依赖（解法：搬函数/提取协议，而非加白名单）：\n"
        + "\n".join("  {" + ", ".join(sorted(c)) + "}" for c in cycles)
    )


# ---------------------------------------------------------------------------
# 类规模棘轮：单类直接方法数不得超过预算。预算 = 当前全仓最差值，
# 重构缩小后应下调预算；新增超过 DEFAULT_MAX_METHODS 的类会被拦下。
# ---------------------------------------------------------------------------
DEFAULT_MAX_METHODS = 40
CLASS_BUDGETS: dict[str, int] = {
    # IndexRefinementDialog 47 → 62：清单/编辑构建与纯渲染拆入
    # refinement_list_pane / refinement_editor_pane（审计 G4 切片 4.3，字段 34 → 12、
    # 文件 953 → 781 行）；方法数上涨全部来自 ~20 个单行 property 控件桥——
    # 为保持 151+ 处既有测试锚点名（dialog._table 等）不改动，编排留对话框经
    # property 读写 pane 控件。属机械膨胀非职责增加，后续测试锚点迁移后可回落。
    "ui/maintenance/index_refinement_dialog.py:IndexRefinementDialog": 62,
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
# 类字段数棘轮：类体直属方法内 self.X 赋值的去重属性名数不得超过预算。
# 口径与 _method_counts 一致（仅直属成员，不含嵌套作用域）——pydantic/enum/
# dataclass 值对象走类级注解，天然计 0，无需豁免。2026-09-30 实测 236 类：
# 9 类超 30，次高 25，阈值切在干净带上。种子 = 当前实测值，只许收紧；
# 字段归组为状态对象是首选解法。
# ---------------------------------------------------------------------------
DEFAULT_MAX_FIELDS = 30
CLASS_FIELD_BUDGETS: dict[str, int] = {
    # RecommendationPanel 44 → 35：配队横条拆 combo_strip.py、捕获流抽
    # shared/capture_flow.py（审计 G5 切片 4.4a/4.4b）
    "ui/recommendation/recommendation_panel.py:RecommendationPanel": 35,
    "ui/match/peak_select_panel.py:PeakSelectPanel": 41,
    "ui/maintenance/rule_doc_panel.py:RuleDocPanel": 35,
    "ui/match/match_guide_panel.py:MatchGuidePanel": 34,
    # index_refinement_dialog 34 → 12：清单/编辑控件拆入两个 pane（审计 G4 切片 4.3），
    # 12 低于默认预算 30，条目出表
    # MainWindow 33 → 34：对话框开启器编排以 self._dialogs 组合字段接入（审计 G1 切片
    # 4.2a）——以 1 个组合字段置换 16 个对话框/采集方法（方法数 49 → 33 出表），
    # 属结构性放宽，后续拆分状态条 chips 时可回落。
    "ui/app/main_window.py:MainWindow": 34,
    "ui/recommendation/hero_card_widget.py:HeroCardWidget": 32,
}


def _field_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for py in _iter_py_files():
        rel = py.relative_to(SRC).as_posix()
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            fields: set[str] = set()
            for child in node.body:  # 仅直属成员，与 _method_counts 同口径
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for st in ast.walk(child):
                        targets = st.targets if isinstance(st, ast.Assign) else (
                            [st.target] if isinstance(st, ast.AnnAssign) else [])
                        for t in targets:
                            if (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                                    and t.value.id == "self"):
                                fields.add(t.attr)
            counts[f"{rel}:{node.name}"] = len(fields)
    return counts


def test_class_field_count_within_budget() -> None:
    offenders = []
    for key, count in _field_counts().items():
        budget = CLASS_FIELD_BUDGETS.get(key, DEFAULT_MAX_FIELDS)
        if count > budget:
            offenders.append(f"{key}: {count} > 预算 {budget}")
    assert not offenders, (
        "类字段数超出预算（字段归组为状态对象是首选解法；预算只许收紧）：\n"
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


# ---------------------------------------------------------------------------
# UI 对数据层的 import 白名单：运行期只许消费值对象（pydantic/enum/dataclass）
# 与下列显式白名单符号；仓储/Manager 类一律由组合根（app_services）注入——
# 构造禁令见 test_ui_must_not_construct_data_layer，本条管住 import 层面。
# 棘轮哲学同上：白名单只许收紧，放宽必须在 PR 说明理由。
# ---------------------------------------------------------------------------
ALLOWED_DATA_CONSTANTS = {
    "EFFECT_STATUSES",        # 卡牌图鉴：效果状态词汇（UI 表单选项与入库校验同源）
    "FIELD_TYPES",            # 卡牌图鉴：字段类型词汇
    "SPECIAL_CATEGORIES",     # 专属牌：类别词汇
    "VALID_POINTS",           # 牌点维护：合法点数
    "VALID_SUITS",            # 牌点维护：合法花色
    "VALID_SUBTYPES",         # 装备属性：合法子类型
    "MAX_GUIDE_TEXT_LENGTH",  # 攻略文本长度上限（与模型约束同源）
}
ALLOWED_DATA_FUNCTIONS = {
    # 评分→相性评级映射：SynergyScore 模型校验器同源（data/models.py），
    # 搬出 data 会制造反向依赖，故白名单放行 UI 展示共用。
    "synergy_rating_for_score",
    # 原子写盘基元（json_repository.py）：无状态底层 IO 工具、非仓储/读函数，
    # 值对象同地位；UI 写配置文件（白名单覆盖等）直接使用，注入反而造转发壳。
    "atomic_write_json",
}


def _runtime_imports_from(path: Path, prefixes: tuple[str, ...]) -> list[tuple[str, str, int]]:
    """ui 文件运行期对指定前缀模块的 (模块, 符号, 行号) 清单。

    - 跳过 ``if TYPE_CHECKING:`` 块（与 _internal_imports 同语义）；
    - 函数内延迟导入计入（运行期真实加载）；
    - ``import a.b`` 形式以空符号记录（无符号可用，按模块判定）。
    """

    def is_type_checking(node: ast.If) -> bool:
        test = node.test
        target = test.attr if isinstance(test, ast.Attribute) else (
            test.id if isinstance(test, ast.Name) else None)
        return target == "TYPE_CHECKING"

    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[str, str, int]] = []

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.If) and is_type_checking(node):
            return
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module.startswith(prefixes):
                for alias in node.names:
                    found.append((node.module, alias.name, node.lineno))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(prefixes):
                    found.append((alias.name, "", node.lineno))
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return found


def _is_value_object(table: dict[str, dict], symbol: str) -> bool:
    info = table.get(symbol)
    if not info or not info["file"].startswith("src/data/"):
        return False
    bases = info["bases"]
    return "BaseModel" in bases or any("Enum" in b for b in bases) or info["dataclass"]


def test_ui_data_import_allowlist() -> None:
    table = _class_table()
    repos = _persistence_classes(table)
    offenders = []
    for py in (SRC / "ui").rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        rel = py.relative_to(ROOT).as_posix()
        if rel in COMPOSITION_ROOTS:
            continue
        for module, symbol, lineno in _runtime_imports_from(py, ("src.data",)):
            if symbol in repos:
                offenders.append(
                    f"{rel}:{lineno}  {module} :: {symbol}（仓储/Manager 由组合根注入，不 import）")
                continue
            if _is_value_object(table, symbol):
                continue
            if symbol in ALLOWED_DATA_CONSTANTS:
                continue
            if symbol and symbol in ALLOWED_DATA_FUNCTIONS:
                continue
            offenders.append(f"{rel}:{lineno}  {module} :: {symbol or '(模块级 import)'}")
    assert not offenders, (
        "UI 运行期 import 了数据层非白名单符号（值对象/词汇常量除外；"
        "仓储与读函数经组合根或业务服务注入）：\n"
        + "\n".join(f"  {o}" for o in offenders)
    )


# ---------------------------------------------------------------------------
# UI 禁止直连基础设施层（ocr/scraper/capture）：跨层能力经 business 服务或
# 组合根注入。豁免须逐条写明理由；新豁免默认拒绝。
# ---------------------------------------------------------------------------
INFRA_IMPORT_EXCEPTIONS = {
    # ROI 版位定义是 UI 编辑、OCR 消费的同一份共享契约（拆出反而造两处真相）
    ("src/ui/configuration/roi_selector.py", "src.ocr.roi_config"),
}


def test_ui_must_not_import_infra() -> None:
    offenders = []
    for py in (SRC / "ui").rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        rel = py.relative_to(ROOT).as_posix()
        for module, _symbol, lineno in _runtime_imports_from(py, ("src.ocr", "src.scraper", "src.capture")):
            if (rel, module) in INFRA_IMPORT_EXCEPTIONS:
                continue
            offenders.append(f"{rel}:{lineno}  {module}")
    assert not offenders, (
        "UI 直连基础设施层（经 business 服务或组合根注入；豁免须在 INFRA_IMPORT_EXCEPTIONS 说明理由）：\n"
        + "\n".join(f"  {o}" for o in offenders)
    )


# ---------------------------------------------------------------------------
# scraper 直接构造 data 持久化类的棘轮：scraper/ai 是无组合根的 CLI 批处理
# 进程入口，断点续传读侧直接构造 Manager 属成文豁免——注入式改造对独立进程
# 收益不成立（2026-09 审计 C2 决策）。清单只许收紧；新增第 4 处构造必须先
# 在本清单写明理由。
# ---------------------------------------------------------------------------
SCRAPER_MANAGER_CONSTRUCTIONS: dict[tuple[str, str], int] = {
    ("src/scraper/ai/batch.py", "SynergyManager"): 1,
    ("src/scraper/ai/batch.py", "GuideManager"): 1,
    ("src/scraper/ai/utils.py", "HeroManager"): 1,
}


def test_scraper_manager_construction_ratchet() -> None:
    repos = _persistence_classes(_class_table())
    found: dict[tuple[str, str], int] = {}
    for py in (SRC / "scraper").rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        rel = py.relative_to(ROOT).as_posix()
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id in repos:
                found[(rel, node.func.id)] = found.get((rel, node.func.id), 0) + 1
    added = {k: v for k, v in found.items() if k not in SCRAPER_MANAGER_CONSTRUCTIONS}
    assert not added, (
        "scraper 新增直接构造 data 持久化类（CLI 入口豁免清单之外，须注入或在此成文理由）：\n"
        + "\n".join(f"  {k[0]}  {k[1]} x{v}" for k, v in added.items())
    )
    stale = {k for k in SCRAPER_MANAGER_CONSTRUCTIONS if k not in found}
    assert not stale, (
        "豁免清单存在已消失的构造点（很好，说明解耦有进展）——请同步收缩清单：\n"
        + "\n".join(f"  {k[0]}  {k[1]}" for k in stale)
    )


# ---------------------------------------------------------------------------
# 文件行数棘轮：观察名单内单文件行数不得超过预算（种子 = 当前实测值）。
# 与类方法数棘轮互补——拦住"往大文件继续堆代码"；重构缩小后应下调种子。
# recognizer.py 条目即绞杀者规则的机械化：只减不增，新特征一律进新模块。
# ---------------------------------------------------------------------------
FILE_LINE_BUDGETS: dict[str, int] = {
    # 819 → 552：对话框开启器与菜单构建拆出 dialog_coordinator.py / menu_builder.py
    # （审计 G1 切片 4.2a/4.2b，方法数 49 → 33）；552 → 557：资料库编辑入口
    # 接入 AI 生成忙碌守卫闭包（T1 运维加固，2026-10）；
    # 557 → 561：启动 2 分钟后自动公告检查接线（P1-4，2026-10）
    "ui/app/main_window.py": 561,
    # mumu_config_dialog.py（原 771）出表：拆出 mumu_device_page.py 与
    # mumu_recognition_page.py 后仅余 224 行页装配（审计 G2 切片 4.1a/4.1b）
    "ui/recommendation/recommendation_panel.py": 860,
    "ui/maintenance/rule_doc_panel.py": 928,
    # 953 → 781：清单/编辑构建与纯渲染拆入 refinement_list_pane / refinement_editor_pane
    # （审计 G4 切片 4.3；字段 34 → 12，方法数经 property 桥说明见 CLASS_BUDGETS 注释）
    "ui/maintenance/index_refinement_dialog.py": 781,
    # 911 → 492：两刀绞杀——4.6a 名称证据解析与页面消歧出仓 name_resolution.py，
    # 4.6b 批量画布三函数出仓 batch_canvas.py（审计 G3）
    "ocr/recognizer.py": 492,
    # 2026-10-05 修复批：补齐 load_issues 出口与工作区同步检查（491 → 568，
    # 补提交说明承诺而未实现的两项检查），超 500 行 tripwire 入册
    # 2026-10-05 运维收尾：AI_LOG_PATTERNS "401"→"HTTP 401" 精确匹配注释（568 → 570）
    "scripts/doctor.py": 570,
    # 2026-09-30 审计补种（种子 = 当前实测值，只许收紧）
    "ui/library/card_management_panel.py": 877,
    # hero_classification_panel.py（原 827）出表：三页签拆入
    # library/classification/ 后仅余 181 行装配壳（审计 G6 切片 4.5）
    "scraper/official_source/announcement.py": 699,
    # 670 → 570：官方导入网关 official_import_gateway、图像保存调度
    # image_save_scheduler、OCR 协调器 ocr_task_coordinator 依次出仓（审计 G8）
    # 570 → 575：截图存盘失败补 warning 日志（修复批 2026-10-05，+1 行，留余量）
    "business/emulator/capture_service.py": 575,
    # 641 → 363：API 档案域拆出 profiles.py（审计 G7，2026-09）；
    # 363 → 366：get_mumu_config 补回截图模式键（T1 运维加固，2026-10）；
    # 366 → 368：MJS_DATA_REPO 私有仓位置映射（R3 生命线加固，2026-10）
    "config/env.py": 368,
    "business/recognition/official_data_import_service.py": 638,
    # 610 → 639：四个编辑/删除入口补 AI 生成忙碌守卫（T1 运维加固，2026-10）
    "ui/library/hero_browser.py": 639,
    # 601 → 605：基线置空/缺源显式报错 + 终局"基线不完整"标注（P1-5，2026-10）
    "scripts/sync_rule_stats.py": 605,
    # 2026-10 tripwire 落地补种（种子 = 当前实测值，只许收紧）。此前名单靠
    # 人工抄录，match_guide_panel 在名单外由 778 涨至 840 无拦截（0007fc4）
    "ui/match/match_guide_panel.py": 840,
    "ui/configuration/settings_dialog.py": 584,
    "business/recognition/peak_select_watcher.py": 567,
    # 561 → 565：截图未落盘时状态栏改真实提示（修复批 2026-10-05）
    "ui/match/peak_select_panel.py": 565,
    "business/recognition/ocr_worker.py": 546,
    "scraper/official_source/crawler.py": 541,
    "ui/library/hero_detail_views.py": 538,
    "ui/recommendation/hero_card_widget.py": 531,
    "data/recommendation_index_repository.py": 526,
    "ui/library/special_cards_panel.py": 517,
    "scraper/ai/generation.py": 514,
    "business/rag/audit_service.py": 502,
    # ui/shared/style.py（1286 行）不入表：纯设计 token 与 QSS 常量字符串，
    # 无行为逻辑，拆分只会制造间接层（tripwire 豁免见 LINE_TRIPWIRE_EXEMPTS）。
}


def test_file_line_count_within_budget() -> None:
    offenders = []
    for rel, budget in FILE_LINE_BUDGETS.items():
        count = len((SRC / rel).read_text(encoding="utf-8").splitlines())
        if count > budget:
            offenders.append(f"src/{rel}: {count} > 预算 {budget}")
    assert not offenders, (
        "文件行数超出预算（重构缩小后请同步下调预算；放宽预算需在 PR 说明理由）：\n"
        + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# 行数 tripwire：超过默认行数线的文件必须显式登记（入册或豁免），堵住
# "棘轮只保护名单上的文件"的盲区——match_guide_panel 曾在名单外
# 778 → 840 行无拦截。种子补种见 FILE_LINE_BUDGETS 尾部，豁免须写明理由。
# ---------------------------------------------------------------------------
LINE_TRIPWIRE = 500
LINE_TRIPWIRE_EXEMPTS = {
    # ui/shared/style.py：纯设计 token 与 QSS 常量字符串，无行为逻辑，
    # 拆分只会制造间接层（同 FILE_LINE_BUDGETS 注释）
    "ui/shared/style.py",
}


def test_files_over_line_tripwire_are_budgeted() -> None:
    missing = []
    for py in _iter_py_files():
        rel = py.relative_to(SRC).as_posix()
        if rel in FILE_LINE_BUDGETS or rel in LINE_TRIPWIRE_EXEMPTS:
            continue
        count = len(py.read_text(encoding="utf-8").splitlines())
        if count > LINE_TRIPWIRE:
            missing.append(f"src/{rel}: {count} 行")
    assert not missing, (
        "超过 500 行的文件既未入册 FILE_LINE_BUDGETS 也未豁免"
        "（入册种子 = 实测值只许收紧；豁免须写明理由）：\n" + "\n".join(missing)
    )
