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
            elif top == "config" and dst != "config":
                # config 为最底层：除 stdlib 外只准 import 本包（原子写盘等
                # 公用工具住 src.config.atomic_write；曾以函数内导入反向依赖
                # data.json_repository，本遍历含函数体内 import，后门同样拦截）
                offenders.append(f"{py.relative_to(ROOT)} -> src.{dst}")
    assert not offenders, (
        "底层包不得 import ui（唯一入口是 main.py）；"
        "config 为最底层，不得 import 其他项目包：\n" + "\n".join(offenders)
    )


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
    # MumuConfigCoordinator 40 → 42：官方 PC 客户端直连（2026-10-25）——
    # is_pc_client property 与 refresh_pc_windows 枚举封装两个薄方法，职责
    # 仍为配置协调（UI 不直连 capture 层的架构转出口），无拆分必要
    # 42 → 43：连接类别单选切换（2026-10-26）——switch_connection_mode
    # 品牌记忆/恢复编排单方法
    # 43 → 45：探测结果品牌校验（2026-10-26）——_on_device_refresh_failed
    # 槽与 _discard_stale_refresh 补发两方法堵跨品牌切换竞态（过期结果
    # 覆盖当前品牌列表），接收后台结果的编排职责内聚
    "business/emulator/mumu_config_coordinator.py:MumuConfigCoordinator": 45,
    # MumuDevicePage 41：连接类别单选切换（2026-10-26）新增 _on_mode_activated
    # 入册（40 基线 + 双形态/单选接线），页面装配职责内聚
    "ui/configuration/mumu_device_page.py:MumuDevicePage": 41,
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
    # 35 → 36：_last_run_ok 标志封堵脚本崩溃时业务回调把空输出渲染成
    # 假结论（audit 崩溃显示「文档校验通过」，第 15 批审查 P2-1）
    "ui/maintenance/rule_doc_panel.py:RuleDocPanel": 36,
    "ui/match/match_guide_panel.py:MatchGuidePanel": 34,
    # index_refinement_dialog 34 → 12：清单/编辑控件拆入两个 pane（审计 G4 切片 4.3），
    # 12 低于默认预算 30，条目出表
    # MainWindow 33 → 34：对话框开启器编排以 self._dialogs 组合字段接入（审计 G1 切片
    # 4.2a）——以 1 个组合字段置换 16 个对话框/采集方法（方法数 49 → 33 出表），
    # 属结构性放宽，后续拆分状态条 chips 时可回落；
    # 34 → 35：状态栏数据新鲜度芯片挂载（self._freshness_chip，2026-10）
    "ui/app/main_window.py:MainWindow": 35,
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
PERSISTENCE_ROOTS = {"DataManager", "JsonRepository", "DataFacade"}


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
    # 相性落后判定（synergy_manager.py）：无状态纯函数，与 SynergyScore/
    # Hero 时间戳语义内聚，SynergyManager.list_outdated_synergies 与相性
    # 指定配对计算对话框共用同一判定事实源，搬出 data 会造成 manager 反向依赖。
    "is_synergy_outdated",
    # 原子写盘基元（json_repository.py）：无状态底层 IO 工具、非仓储/读函数，
    # 值对象同地位；UI 写配置文件（白名单覆盖、势力配色等）直接使用，
    # 注入反而造转发壳。atomic_write_text：faction_color_dialog 配色文件
    # 原子化接入（config/ 无备份链，半截 JSON 会静默丢用户配色）。
    "atomic_write_json",
    "atomic_write_text",
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
    # 557 → 561：启动 2 分钟后自动公告检查接线（P1-4，2026-10）；
    # 561 → 575：状态栏第三枚数据新鲜度芯片挂载接线（芯片本体在
    # data_freshness_chip.py 自足，此处仅构造与两条信号回接）
    # 575 → 584：closeEvent 中止三个抓取子进程（VUL-B2-002）——QProcess 随父
    # 对象析构不杀子进程树，browser 模式会孤儿化 Edge/node 并锁住 edge_profile
    # 590 → 592：closeEvent 末尾补挂向导修复子进程终止（P2-2，第 12 批审查
    # 修复——修复子进程游离于 fetch 退出协议外，退出后会孤儿化继续写 data/）
    # 592 → 599：三面板装配共享捕获单飞锁（第 13 批审查 P1-1）——跨面板重叠
    # 请求曾因广播回调错配清空对局阵容，共享锁 + owner 校验封住该路径
    "ui/app/main_window.py": 599,
    # 首启向导主体（spec_onboarding T4–T6 三段体检 + T9 一键修复接线）。
    # 546 为复核修复后实测值（EnvCheckWorker 引用 GC 窗口修复 + 深链协调器
    # 整只重建防端口倒退）；三段渲染/协调器/预热接线为单一对话框内聚职责，
    # 拆分只会制造参数众多的小模块，登记预算限制继续膨胀
    # 547 → 551：段 2 分辨率体检的 ROI 截图回调补 finish 收尾（VUL-B4-004）——
    # 向导不进入 ROI 编辑态，不释放槽位会让"连接并体检"第二次起静默失效
    # 551 → 552：多品牌连接——零实例文案按所选品牌展示（EMULATOR_LABELS
    # 经协调器转出口，UI 不直连 src.capture），单行替换
    # 552 → 560：shutdown_fix_task——应用退出时终止仍在运行的一键修复子进程
    # （P2-2，第 12 批审查修复；关向导不杀任务、退出才杀，语义与维护面板一致）
    "ui/app/onboarding_dialog.py": 560,
    # mumu_config_dialog.py（原 771）出表：拆出 mumu_device_page.py 与
    # mumu_recognition_page.py 后仅余 224 行页装配（审计 G2 切片 4.1a/4.1b）
    "ui/recommendation/recommendation_panel.py": 860,
    # 940 → 955：崩溃/启动失败码不再进业务回调——_on_finished 标记
    # _last_run_ok，audit/sync 回调侧短路（第 15 批审查 P2-1 修复）
    "ui/maintenance/rule_doc_panel.py": 956,
    # 953 → 781：清单/编辑构建与纯渲染拆入 refinement_list_pane / refinement_editor_pane
    # （审计 G4 切片 4.3；字段 34 → 12，方法数经 property 桥说明见 CLASS_BUDGETS 注释）
    "ui/maintenance/index_refinement_dialog.py": 790,
    # 911 → 492：两刀绞杀——4.6a 名称证据解析与页面消歧出仓 name_resolution.py，
    # 4.6b 批量画布三函数出仓 batch_canvas.py（审计 G3）
    # 492 → 498：B1 修复批——team 徽记归一化失败后的主/复核引擎链式单条重试
    # （修批量乱码绕过单槽回退的旁路 + v6 书法体徽记弱项，重放实测救回 14/18 槽）
    # 498 → 519：B1 收尾批 d1a55e1（白名单简繁变体退役+预热残留修复）与
    # 6ea1866（兜底批次零产出短路）+21 行未同步预算，补登记恢复绿灯；
    # 519 → 493：P1-6 模板方法合并——recognize/_recognize_match_guide 双路径
    # 收口为 _recognize_page(image, plan)，批次 0 借出的 21 行净偿还
    "ocr/recognizer.py": 493,
    # 2026-10-26 对局攻略页锚点判定（437 → 700，超 500 行 tripwire 入册）：
    # 双锚点 AND + 期望位置校验三层漏斗（缓存复验/基准+参考缩放局部/全图粗扫
    # 兜底），动态背景漏检收口；锚点加载失败自动回退原有大模板漏斗
    # 700 → 716：模板写盘原子化（mkstemp+fsync+replace）+ 用户模板损坏
    # 回退随包模板（第 2 批审查 P2-1 修复，封堵写入中断致页面判定持续失效）
    # 716 → 734：锚点加载失败告警负面缓存（同一清单版本只告警一次，
    # 防回退态轮询每拍刷 WARNING 挤爆日志轮转，第 13 批审查 P2-1）
    "ocr/template_manager.py": 734,
    # 2026-10-05 修复批：补齐 load_issues 出口与工作区同步检查（491 → 568，
    # 补提交说明承诺而未实现的两项检查），超 500 行 tripwire 入册
    # 2026-10-05 运维收尾：AI_LOG_PATTERNS "401"→"HTTP 401" 精确匹配注释（568 → 570）
    # 2026-10-06 B1 引擎切换：paddle 环境检查重写为 onnxruntime/rapidocr 双套件
    # 版本+模型就位检查，新增「白名单」冲突重检组（570 → 610，设计文档 §十四）
    # 2026-10-11 ONNX 迁移 S4：新增「RAG索引」检查组（体积/孤立段/编码器三件套，
    # embedding_onnx_migration.md §S4）
    # 2026-10-13 首启向导 T1：check_environment 拆分 check_dev_toolchain/check_runtime_env
    # （向导只跑运行时子集）+ run_checks 增 checks 形参（spec_onboarding §3.3），675 → 682
    # 2026-10-24 多品牌连接：check_devices 读 EMULATOR_* 键（双键回退）+ 序列号
    # 正则纳入 emulator-<n> 形态 + 新增 check_adb_versions 版本一致性诊断
    # （PATH adb 与配置 adb 混版是连接漂移高发根因），682 → 716
    "scripts/doctor.py": 716,
    # 2026-09-30 审计补种（种子 = 当前实测值，只许收紧）
    # 877 → 900：版本调整编辑对话框补未保存关闭确认（快照比对 + reject
    # 拦截，第 14 批审查 P2-1），长文本效果草稿不再被 Esc 静默丢弃
    "ui/library/card_management_panel.py": 900,
    # hero_classification_panel.py（原 827）出表：三页签拆入
    # library/classification/ 后仅余 181 行装配壳（审计 G6 切片 4.5）
    # 699 → 742：公告翻页补拉（第一轮审查 P2-5 修复）——两次检查间新增
    # 超过单页 5 条的公告靠翻页补回，known_keys 整页已知即停 + 上限防失控
    "scraper/official_source/announcement.py": 742,
    # 670 → 570：官方导入网关 official_import_gateway、图像保存调度
    # image_save_scheduler、OCR 协调器 ocr_task_coordinator 依次出仓（审计 G8）
    # 570 → 575：截图存盘失败补 warning 日志（修复批 2026-10-05，+1 行，留余量）
    # 575 → 479：P1-8 绞杀第三刀——连接域（配置热更/会话状态/连接编排）出仓
    # capture_connection.py；关停守卫 _closed 改用共享 OneShotToken（P0-3）
    # 479 → 499：emulator 域修复批——_reconnect_if_needed 统一手动/轮询
    # 重连编排（TD-A2-001：轮询自愈重连同步状态+热更 is_current 校验）与
    # capture_for_poll 等待上界 _POLL_CAPTURE_TIMEOUT_S（VUL-B3-002）
    # 499 → 506：_handle_capture_result 补终止信号保证（TD-A5-001）——目录
    # 不可写/执行器已关停/协调器已退役时改走 capture_failed，避免在途锁
    # 永不释放导致界面永久停在"正在截图"
    # 506 → 511：官方 PC 客户端直连（2026-10-25）——采集会话注解放宽为
    # AdbCapture | PcClientCapture 双形态 + 连接失败前缀按形态分立（PC 无 ADB 字样）
    "business/emulator/capture_service.py": 511,
    # 官方 PC 客户端直连采集模块（2026-10-25 首次入册，544 行）：窗口枚举
    # （EnumWindows ctypes 签名声明块）+ WGC 会话 + BitBlt 兜底 + 黑帧检测
    # + 客户区裁剪单文件内聚——接口对齐 adb_screen.py（431 行）惯例，Win32
    # 签名声明与降级链拆出去只会制造跨文件状态传递，登记预算限制膨胀
    # 544 → 568：WGC 错误 attach 修复批（2026-10-26）——window_hwnd 定向
    #（标题子串匹配命中"名将杀 Agent"工具自身窗口）+ 帧尺寸对账防线
    # 568 → 577：颜色与裁剪修复（2026-10-26）——BGRA→RGB 通道反转（帧实为
    # Direct3D B8G8R8A8，此前按 RGBA 读致红蓝互换）+ 回退路径左阴影自校准
    # 577 → 599：Linux CI 兼容（2026-10-25）——模块级 WinDLL 加载加
    # sys.platform 守卫与 _NullWinDLL 占位（capture_connection 顶层 import
    # 要求跨平台可导入；占位哑函数按 Win32 失败语义返回 0，调用方既有
    # 容错分支照常生效）
    "capture/pc_client.py": 599,
    # 641 → 363：API 档案域拆出 profiles.py（审计 G7，2026-09）；
    # 363 → 366：get_mumu_config 补回截图模式键（T1 运维加固，2026-10）；
    # 366 → 368：MJS_DATA_REPO 私有仓位置映射（R3 生命线加固，2026-10）
    # 368 → 364：键元表单源化（P1-4/P1-7，2026-10）——key_mapping/三张类型
    # 清单/两个 getter 默认值四处登记收口为 _ENV_KEY_SPECS 单表；
    # 364 → 366：数据新鲜度透传键 DATA_FRESHNESS_TIMELINE_STALE_DAYS（2026-10）
    # 366 → 370：档位阈值可配置（TD-A6-001）——新增 DATA_FRESHNESS_STALE_GREEN_DAYS
    # / DATA_FRESHNESS_STALE_RED_DAYS 两键 + 说明注释；官方榜单约每半月更新，
    # 7/14 写死会让榜单长期停在黄/红并盖掉武将信号
    # 370 → 379：多品牌连接（2026-10-24）——新增 EMULATOR_TYPE + 三连接键
    # （default_from 回退旧 MUMU_* 实配值，老 config.env 平滑升级）
    # 379 → 382：官方 PC 客户端直连（2026-10-25）——新增 PC_WINDOW_TITLE 键
    #（真机取证定稿默认"名将杀"，窗口识别主键为进程名+类名）
    # 382 → 385：连接类别单选改造（2026-10-26）——新增 EMULATOR_BRAND 品牌
    # 记忆键（PC 客户端期间记住上次模拟器品牌，切回时恢复）
    # 385 → 408：连接键回退收口（2026-10-26）——LEGACY_CONNECTION_KEYS 与
    # connection_key 落位 config 层（连接域/OCR/doctor/UI 五处零散 or 回退
    # 统一调用，"空值也回退"语义钉死），capture_connection 删除本地副本
    # 408 → 410：BOM 容错（2026-10-27）——两处读侧 utf-8-sig 剥可选 BOM，
    # 防记事本「带 BOM 的 UTF-8」保存污染首键
    # 410 → 421：新增 runtime_root/runtime_data_dir 调用时求值函数——
    # 路径快照常量的根治出口，守护测试不再依赖 importlib.reload
    "config/env.py": 421,
    # 638 → 642：罕见字兜底改计数熔断（TD-A5-001）——单次瞬时异常只损失本格
    # 兜底，连续 3 次才熔断（熔断本体在 official_ocr_engines.py，含 60s 冷却），
    # 降级期间经 status_callback 明示
    # 642 → 664：20261022 拼接长图切分接入——读图后纵横比 >= 4 先按静默带切
    # 成补边虚拟分页（切分本体在 official_board_parser.split_stitched_image），
    # 页单元展开、状态提示与段 y 偏移注入复核原图坐标
    # 664 → 710：单格识别快通道（2x 单候选 + 质量门早退，未过门回退 4x 双
    # 候选原路径）——det 输入面积主导单次调用耗时，快通道对七种版式语料
    # 逐格结果零差异、整板约 2.5-3x 提速
    "business/recognition/official_data_import_service.py": 710,
    # 首次入册（417 → 524 越过 500 绊线）：split_stitched_image 拼接长图切分
    # （20261022 版式兼容：静默带定位 + 补边 + 分页结构校验）；固定版式解析
    # 算法族（LAYOUTS/PAGED_LAYOUTS + 切行/切格/数字模板）高内聚且仅依赖
    # cv2/numpy，拆分只会制造间接层
    "ocr/official_board_parser.py": 524,
    # 610 → 639：四个编辑/删除入口补 AI 生成忙碌守卫（T1 运维加固，2026-10）
    # 639 → 643：编辑/删除武将两个入口补齐同款守卫（VUL-B3-001——删除级联
    # 写 3 文件，会被生成子进程下一批全量提交静默冲掉，2026-10）
    "ui/library/hero_browser.py": 643,
    # 601 → 605：基线置空/缺源显式报错 + 终局"基线不完整"标注（P1-5，2026-10）
    "scripts/sync_rule_stats.py": 605,
    # 2026-10 tripwire 落地补种（种子 = 当前实测值，只许收紧）。此前名单靠
    # 人工抄录，match_guide_panel 在名单外由 778 涨至 840 无拦截（0007fc4）
    # 840 → 848：接入共享捕获单飞锁与忙提示（第 13 批审查 P1-1）——构造参数
    # 注入共享锁、begin/finish 携带 owner、被占时状态栏提示
    "ui/match/match_guide_panel.py": 848,
    # 584 → 593：跨供应商空 model 保存硬校验（2026-10-27）——无预设默认模型
    # 的供应商（openai/ollama/openai-compatible）model 必填拦截 + 占位文案按供应商同步
    "ui/configuration/settings_dialog.py": 593,
    # P0-1 会话状态收口（2026-10）：SessionGuard 替换裸 _session 比对，
    # _clear_session_state 统一 start/stop/牌面退出三处手抄重置清单（567 → 566）
    # 566 → 593：候选期卡数回升防抖——过场坏帧（模拟器旧帧/撕裂帧）扣住一拍
    # 签名复现再发布（2026-10-09 桑弘羊跳变事故：旧 9 张帧把已选走的牌打回面板）
    # 593 → 655：扣住只留回升方向（回落立即发布，禁将 14→11→9 不再各延迟一拍）；
    # 新增漏检补齐（存活卡原位一致时按上一拍卡位补齐，2026-10-12 11→9 面板跳变）
    # 与排布校验（5+4 非法排布不发）；退出阈值 2→4 拍（该局转场误退出 3 次）
    # 655 → 667：_publish_pool 锁内会话世代校验（VUL-B3-001）——旧拍/GUI 确认/
    # 图片导入在 stop 后不再回写牌面，堵住待确认复活与幽灵确认污染频次文件；
    # OCR 超时任务置位 cancelled（VUL-B3-002 配套），worker 取出时短路
    # 667 → 669：超时弃单改调 task.abandon() 同步释放整帧引用（审查缺口 1）；
    # 世代失配拦截补 debug 留痕（审查问题 4）
    # 669 → 677：图片导入回写确认表前加会话世代守卫（44837e3）——导入在途
    # 停止/重启换代后旧图校验结果直接丢弃，不再删除或错位新会话刚做的
    # 人工确认，与 _publish_pool 同一守卫口径
    "business/recognition/peak_select_watcher.py": 677,
    # 561 → 565：截图未落盘时状态栏改真实提示（修复批 2026-10-05）
    # 565 → 567：接入共享捕获单飞锁（第 13 批审查 P1-1）——构造注入共享锁、
    # begin/finish 携带 owner，被占时状态栏提示
    "ui/match/peak_select_panel.py": 567,
    # 546 → 551：6ea1866（兜底批次零产出短路）+5 行未同步预算，补登记恢复绿灯
    # 551 → 518：P2-13a 五处 _log_timing 手抄 kwargs 收口为 stage_timing 字典展开
    # 518 → 520：reset_recognizer_cache 同步清 _page_cache——白名单变更后同指纹
    # 页面不再复用旧纠错结果（TD-A8-001，2026-10-14）
    # 520 → 529：OcrTask.cancelled 标志 + run() 取出短路（VUL-B3-002）——等待方
    # 超时放弃的任务不再执行过期 OCR/写 latest.json/记错法频次，队列与图像引用
    # 随出队释放；_execute 中 ocr_results 前移 + 调试产物落盘降级 try（VUL-B4-001）
    # 529 → 542：OcrTask.abandon()（置取消并即时释放整帧引用，等待方超时调用）
    # 与 _execute 开头 image 局部快照（审查缺口 1/2）——释放必须发生在弃单时刻，
    # worker 被官方导入占住期间滞留队列的任务到取出才释放的话峰值仍是 O(占用时长)
    # 542 → 556：弃单的预热任务出队时复位 _warmup_queued + reset_warmup_state()
    # 跨线程复位入口（VUL-B4-002，2026-10-22）——弃单走 cancelled 短路分支
    # 到不了 warmup_failed 复位点，标志悬挂会让重新预热永久无效
    # 556 → 564：整批官方导入部分失败时 detail 明示已生效组（第 8 批
    # 审查 P2-1），消除「弹窗报失败、数据已半生效」的认知矛盾
    "business/recognition/ocr_worker.py": 564,
    # 首次入册（498 → 508 越过 500 绊线）：do_poll_work 补 except 兜底，未预期
    # 异常也回写 complete_poll，避免在途忙碌闸永久占用、轮询静默卡死（VUL-B3-001）
    # 508 → 511：wait_for_ocr_task 超时即弃单（VUL-B3-002 审查缺口 2）——置取消
    # 并释放整帧引用，滞留队列的任务被 worker 短路，不再执行过期识别
    "ui/app/poll_coordinator.py": 511,
    # 545 → 640：新增 robots.txt 解析与抓取前校验（VUL-B6-001 合规修复，
    # 2026-10-18）。不拆独立模块：6 个测试 monkeypatch 锚点（ROBOTS_CACHE_DIR /
    # _robots_rules_by_host / crawler.urllib.request）均在 crawler 命名空间，
    # 拆分需参数化薄包装层且拆后仍约 576 行，净收益为负。
    # 660 → 636：clean_html 的实体解码剥离与 carry_over_local_fields 迁出到
    # hero_fields.py（64 行纯函数无 IO，便于单测），本文件只保留重新导出。
    # 636 → 640：robots 抓取与图片下载超时从主数据 TIMEOUT 拆出专用短常量
    # （ROBOTS_FETCH_TIMEOUT / IMAGE_DOWNLOAD_TIMEOUT），CDN 或 robots 异常时
    # 快速失败降级，不再每个子进程白等满额 30 秒。
    # 640 → 532：清洗三函数 split_skill_desc/transform/validate_heroes 补齐
    # 迁往 hero_fields.py（兑现「专注抓取链路」承诺）+ fetch 重定向逐跳
    # robots 校验与 robots 通配符/$ 语法告警（第 5 批审查遗留 P2 修复）
    "scraper/official_source/crawler.py": 532,
    "ui/library/hero_detail_views.py": 538,
    "ui/recommendation/hero_card_widget.py": 531,
    # 首次入册（实测 503 越过 500 绊线）：榜单路径聚合 RANKING_CSV_PATHS 与
    # WATCH_DIR 改 ranking_csv_paths()/watch_dir() 调用时求值（reload 根治
    # 工单），判定逻辑未变
    "business/freshness.py": 503,
    # 526 → 544：七路径常量函数化（调用时求值）+ 公开函数默认参数哨兵化
    # （reload 根治工单：榜单路径不再依赖 import 时快照）
    "data/recommendation_index_repository.py": 544,
    # 517 → 542：专属牌编辑对话框补未保存关闭确认（第 14 批审查 P2-1）
    "ui/library/special_cards_panel.py": 542,
    # 514 → 547：指定模式（pair/single）武将条目字段校验——坏条目记
    # 失败项跳过不再裸 KeyError 中断整任务，同 id 去重防自配对（第 6 批
    # 审查 P2-3）
    "scraper/ai/generation.py": 547,
    # 502 → 568：审计收集函数行级结构守卫 + audit_summary 自身失败兜底
    # （TD-A5-001 修复，2026-10-21：坏数据降级为提示条目而非穿透崩溃）
    # 568 → 571：collect_timeline_risk_messages 显式传 root 下时间轴路径给
    # load_timeline（44837e3）——修复 root != PROJECT_ROOT（测试 tmp_path）时
    # 存在性检查与实际加载文件不一致导致的审计误报
    "business/rag/audit_service.py": 571,
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
