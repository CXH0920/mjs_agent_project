# 数据模型规范

> 长期设计规则与决策依据，覆盖 Pydantic 模型、Manager 层与增量更新。

## 一、Pydantic 模型定位

### 规则 1.1：模型即契约

Pydantic `BaseModel` 是**项目唯一的 JSON 格式契约**。所有数据写入（爬虫输出、AI 生成结果）都必须通过 `model_validate` + `model_dump(mode="json")` 校验后落盘。

**为什么：** 项目有 3 个数据来源（官网爬虫、AI API、AI 浏览器），输出格式不一致。Pydantic 作为唯一校验关口保证了数据文件 (`heroes.json` / `synergies.json` / `guides.json`) 的结构稳定性。任何一个数据源写入不合规数据，都会在校验阶段暴露，而非在 UI 展示时崩溃。

**怎么做：** 不要用裸 `json.dump()` 写数据文件，所有写操作走 `validate → dump` 链路。

### 规则 1.2：校验容忍非关键字段失败

`field_validator` 对关键字段（`id`、`name`）使用严格校验，校验失败跳过整条数据；但对辅助字段（`max_hp` 转型失败、`skill_name` 为空）使用宽松处理，以默认值替代。

**为什么：** 官网数据源有偶发的不一致性（如某武将缺失 `p_blood_max` 字段），如果因此跳过整个武将会造成数据缺失。容忍小缺陷比数据缺失更可取。

**怎么做：** 区分"关键校验"和"宽松校验"——关键字段用 `raise ValueError`，辅助字段用 `try/except` 赋默认值。

### 规则 1.3：validation_alias 用于官网字段映射

`Hero` 模型中对官网直接抓取的字段使用 `validation_alias` 映射中文字段名（例如 `Field(..., validation_alias="角色ID")`），AI 生成的结果直接使用英文字段名。

**为什么：** 官网 JS chunk 使用中文 key（`角色ID`、`名称`、`势力`），AI prompt 返回英文 key（`hero_id`、`name`、`faction`）。Pydantic 的 `validation_alias` 天然支持两种来源在同一模型中表达。

## 二、Manager 层设计原则

### 规则 2.1：Manager 是数据文件的同步映射

每个 Manager（`HeroManager` / `SynergyManager` / `GuideManager`）在 `load()` 时将 JSON 文件全量读入内存，在 `save()` 时全量写回。不维护独立的脏标记或增量变更跟踪。

**为什么：** 数据量很小（165 武将 / 若干相性对 / ~42 攻略），全量操作无性能问题。使用脏标记会引入状态不一致风险且无收益。

### 规则 2.2：相性的双向归一

`SynergyManager._make_key(a_id, b_id) → tuple(sorted([a_id, b_id]))` 保证 `(A=114, B=115)` 与 `(B=115, A=114)` 映射到同一存储 key。

**为什么：** 相性是双向无向关系。如果允许 `(A→B)` 和 `(B→A)` 并存，会导致相性数据重复、查询结果异常。归一化在存储层就解决了这个问题，无需调用方关心排序。

### 规则 2.3：重复添加抛 ValueError

`add_hero()` / `add_synergy()` / `add_guide()` 在检测到重复时抛 `ValueError`，而非静默跳过或覆盖。

**为什么：** 调用方需要区分"第一次添加"和"重复写入"以决定后续逻辑（如增量更新中的计数统计）。`update_*()` 方法（无抛异常）用于覆盖场景。

## 三、DataFacade 门面

### 规则 3.1：DataFacade 是 UI 层唯一入口

`MainWindow` 通过 `DataFacade` 持有三个 Manager 引用，不直接创建 Manager 实例。（测试除外，测试可独立创建 Manager。）

**为什么：** 避免 `MainWindow` 中散落多份数据引用（如 `self._hero_mgr` / `self._synergy_mgr` / `self._guide_mgr` 各 3 处），统一入口确保重新加载时所有 Manager 同步刷新。

## 四、增量更新

### 规则 4.1：删除武将必须级联清理关联数据

`apply_incremental_update()` 在 `removed_heroes` 中同时调用 `synergy_mgr.delete_synergies_for_hero()` 和 `guide_mgr.delete_guide()`。

**为什么：** 如果只删除武将而保留相性和攻略数据，UI 中会出现"幽灵 ID"导致 `get_hero(partner_id)` 返回 None，相性卡片显示 `#114` 而非武将名。

### 规则 4.2：IncrementalUpdate 是单一变更描述体

增/删/改三种操作在一个 `IncrementalUpdate` 模型中表达，而非拆成三个独立 API。`apply_incremental_update()` 按**新增→修改→删除**的顺序执行。

**为什么：** 批量变更需要确定性顺序。先新增再修改保证 `update_hero()` 的对象一定存在；最后删除避免误删刚修改的数据。

## 五、官方榜单与推荐指数需求

### 5.1 目标、范围与数据边界

官方榜单数据用于提供历史单将胜率、出场排名、禁用排名及其派生的推荐指数快照。正式输入为 `2v2胜率排行.csv`、`2v2出场排行.csv`、`武将放逐.csv`；推荐页仅读取 `武将推荐指数.csv`，不得在页面打开、OCR 导入或轮询时自动重算。

- 胜率是单将历史数据；不得推导阵容胜率或单局胜负预测。
- 出场、禁用仅有相对排名，不得在 UI 中表述为真实次数或概率。
- 只有同时具备三项有效数据且可关联到 `Hero.id` 的武将，才参与推荐分、评级和排序。
- 导入完成后状态文件标记“待重建”；用户确认后手动重建，成功才清除该标记。正式 CSV 被占用时保留旧快照并提示重试。

### 5.2 推荐指数计算契约

设当前胜率 CSV 的实际数据行数为 `N`，出场/禁用排名为 `R_pick`、`R_ban`，胜率为 `WR`。名称去重仅用于报告重复数据，不得缩小 `N` 并连带制造排名越界：

```text
P = 1 - (R_pick - 1) / (N - 1)
B = 1 - (R_ban - 1) / (N - 1)
P_base = P_floor + (1 - P_floor) × P
Pref = P_base × (1 + λ × B)
Sigmoid(WR) = 1 / (1 + exp(-k × (WR - offset)))
RI = WR × Pref × Sigmoid(WR)
```

默认 `P_floor=0.2`、`λ=0.5`、`k=10`，`offset` 为有效角色胜率中位数加 2%。`P_floor` 必须在 0.1～0.5，`λ` 必须在 0～0.5；失效配置回退默认值并记录告警。`WR < offset - 0.05` 的有效武将仍计算和展示分数，但在自动推荐排序中排在其他有效武将之后。

使用有效角色 RI 的最近秩 p5/p95 分位数映射 0～100 分，裁剪后按 0.5 向上取整，评级为 S(80～100)/A(60～79)/B(40～59)/C(20～39)/D(0～19)。当 `N=1` 或 p5=p95，展示 50 分、B 级。相同输入下排序须稳定：先低胜率降级分组，再按未取整 RI 降序、Hero.id 升序。

### 5.3 异常与验收

- 缺失、越界、重复排名、英雄集合不一致或名称无法关联时，记录“数据不足”，不参与分位数、评级和自动排序。
- CSV 和快照写入使用临时文件替换；官方导入存在未确认名称、重复名称或同规模榜单集合不一致时，只输出复核证据并保留旧正式 CSV。名称完整性通过后，其他异常行仍保留期望排名写入正式行序并进入待复核。
- 固定测试样本必须逐项验证 P、B、P_base、Pref、Sigmoid、RI、展示分、评级与稳定排序；相同输入重复计算结果完全一致。
- 页面必须显示“基于当前版本全服汇总数据计算，仅供参考”，且不使用“最优阵容”“胜率预测”等文案。
