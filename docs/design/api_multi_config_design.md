# 多 API 配置需求与设计文档

> 状态：已确认（四项决策点均已拍板，见 §十 决策记录）
> 关联模块：`src/config/env.py`、`src/ui/configuration/settings_dialog.py`、`src/scraper/ai/api_generator.py`、`src/ui/generation/backend_choose_dialog.py`、`src/business/rag/refinement_service.py`
> 关联规范：`docs/spec/spec_config.md`（本设计需修订其中规则 1.1，见 §5.3 修订说明）

> **修订说明（2026-08）**：FR-3「任务发起时选择 API」已撤销——API 选用/切换只在配置栏完成，任务发起处不再出现 API 选择器（`ApiComboBox` 组件已删除，两处挂载移除）。`is_default`/默认档案概念移除，改为**同时只允许一个启用**（enabled 互斥）：启用即当前使用的 API，切换=启用新档+停用旧档；任务发起用唯一启用档案（`get_api_config()` 取第一个 enabled）。以下 FR-3、§6.2、§十决策1 等涉及"任务选择器/默认档案"的段落据此理解为已过时。

---

## 一、背景与现状

### 1.1 现状链路

当前项目仅支持**单套** API 配置，链路如下：

```
config.env                      # DEEPSEEK_API_KEY / DEEPSEEK_API_URL / DEEPSEEK_MODEL
  └─ parse_env_file()           # 解析为 dict[str, str]
      └─ load_env_config()      # key_mapping 转为小写内部键
          └─ get_api_config()   # 返回单份 {api_key, api_url, model}
              ├─ AIBatchGenerator        （批量生成攻略/相性，api_generator.py）
              ├─ refinement_service      （索引精修 LLM 建议）
              └─ run_synergy_drift.py 等脚本
```

UI 侧 `SettingsDialog`（settings_dialog.py）以固定表单编辑三个字段；`BackendChooseDialog` 的「API 方式」Tab 展示成本估算，无 API 选择能力。

### 1.2 痛点

1. **单一供应商绑定**：只能配 DeepSeek 官方端点，用户无法使用中转站、OpenAI、通义、本地 Ollama 等其他服务。
2. **单一账号**：无法同时配置多个 Key（如主备账号、多人分摊）。
3. **切换成本高**：换 API 必须手改 config.env 或重开配置对话框，且每次只存在一份，改动有破坏风险。
4. **无选择语义**：任务发起时无法按需指定"本次用哪个 API"。

### 1.3 目标（已与需求方确认）

- 支持配置**多个 API 档案**（多供应商混合：DeepSeek / OpenAI / 通义 / 本地 Ollama / OpenAI 兼容中转站，每家可配多个账号）。
- 任务发起时由用户**手动下拉选择**本次使用的 API 档案。
- 首次升级时**自动迁移**旧 `DEEPSEEK_*` 三件套为默认档案，用户无感知。
- 现有消费方代码保持零改动可运行（向后兼容）。

### 1.4 非目标（本期不做，避免过度设计）

- 默认 API 失败后的自动故障切换（fallback）——后续如需，在档案模型上扩展 `fallback` 字段即可，不影响本期数据模型。
- 批量任务自动轮换/分摊请求。
- 多用户/权限管理、费用统计报表、Key 自动续期。

---

## 二、术语

| 术语 | 含义 |
| --- | --- |
| API 档案（ApiProfile） | 一套独立可用的 API 配置组合：名称 + 供应商 + Key + URL + 模型 + 启用状态 |
| 默认档案 | 全局唯一标记，任务发起时选择器预选该项；无档案时任务不可用 API 方式 |
| 挂载点 | 发起 AI 任务、需要选择 API 的 UI 位置 |

---

## 三、功能需求

### FR-1 多 API 档案管理（配置板块）

1. **新增**：在配置对话框中创建新档案，填写字段（见 FR-2），保存后立即生效。
2. **编辑**：修改任意已存档案；Key 字段不回显明文，留空表示"保持原值"。
3. **删除**：删除档案需确认；删除默认档案时自动提升第一个启用档案为新默认；无启用档案则无默认项。
4. **启用/停用**：停用后不出现在任务选择器中，但档案与 Key 保留，可随时恢复。
5. **设为默认**：仅允许一个默认档案；设为默认时自动清除其他档案的默认标记。
6. **保存时格式校验（本期）**：见 FR-2 校验列；「测试连接」按钮**延期至 P2**（真实链路验证由首次任务失败日志闭环：HTTP 401=Key 错、404=URL 错、400=模型名错）。

### FR-2 档案字段与校验

| 字段 | 必填 | 说明与校验 |
| --- | --- | --- |
| 名称 name | 是 | 全局唯一（保存时校验），默认自动生成 `{provider}-{序号}`；用于选择器展示 |
| 供应商 provider | 是 | 下拉枚举：`deepseek` / `openai` / `ollama` / `openai-compatible`；选择后自动填入对应默认 URL 与模型（可改） |
| API Key | 视供应商 | Password 输入框；`ollama` 等本地服务可不填；`deepseek`/`openai`/`openai-compatible` 必填；保存时去除首尾空格；**不做前缀格式强制**（智谱 `{id}.{secret}`、中转站自定义/无前缀均为合法异构格式，强校验会误伤，见 §十 决策 4） |
| API URL | 是 | 必填，保存时校验 `http(s)://` 前缀；选择供应商时自动预填（见 §4.2 预设表） |
| 模型 model | 否 | 留空表示使用服务默认模型；成本估算按实际档案模型读取价格表（未知模型显示"无法自动估算"，沿用现有行为） |
| 启用 enabled | - | 布尔，默认 true |
| 备注 note | 否 | 自由文本，如"主备账号"、"限流低时用" |

规则：**任何档案不得出现 Key 明文写入日志、UI 状态文本、错误消息或版本控制文件**（延续 spec_config.md 规则 5.1）。

### FR-3 API 选用（配置栏完成，已修订）

**任务发起处不出现 API 选择**（已撤销原"下拉选择本次 API"设计）。API 的选用/切换统一在配置栏「参数配置」Tab 完成：

1. 配置栏档案列表通过**启用/停用**控制：同时只允许一个 `enabled=true`（互斥）；启用即当前使用的 API，切换=启用新档+停用旧档。
2. 任务发起（批量生成、索引精化）用 `get_api_config()` 取唯一启用档案；无启用档案时任务入口拦截并引导去配置（`BackendChooseDialog._on_accept` / `build_generator` 返回 None）。
3. 成本估算按启用档案模型读取（首次即用启用档案，无"切换后取值源不统一"问题；B1/B5 随选择器移除而消失）。
4. 无任何启用档案时：任务确认处给出"未配置可用 API"提示并拦截（对齐现有"未配置 DEEPSEEK_API_KEY"的交互模式）。

### FR-4 旧配置自动迁移

首次启动（`api_profiles.json` 不存在时）执行一次：

1. 读取 config.env 中 `DEEPSEEK_API_KEY / DEEPSEEK_API_URL / DEEPSEEK_MODEL`。
2. 三者任一非空 → 生成第一个档案：`name="deepseek-main"`、`provider="deepseek"`、`enabled=true`（唯一启用），URL/模型缺省填默认值，写入 `api_profiles.json`。
3. config.env 旧键**保留不动**（避免破坏用户手编习惯；`get_api_config()` 兼容路径仍可读，见 §5.1）。
4. 三件套全空（含环境变量兜底场景）→ 不生成档案，等待用户在界面配置。

> 环境变量（`DEEPSEEK_API_KEY` / `OPENAI_API_KEY`）**不参与迁移、长期保留为"无默认档案时的最后兜底"**（已确认：脚本/CI 路径依赖环境变量注入 Key，废弃会回归；档案体系优先、兜底最后，两者不冲突，见 §十 决策 3）。

---

## 四、数据模型与存储

### 4.1 存储方案选型（决策记录）

| 方案 | 描述 | 结论 |
| --- | --- | --- |
| A. config.env 索引键 | `API_1_KEY`、`API_1_URL`... 扁平 KV 扩展 | ✗ 字段 8 个 × N 档案键爆炸；`key_mapping` 是静态表无法动态映射；列表语义（顺序/默认/启停）在扁平 KV 中表达别扭；每次结构变更都要改解析代码 |
| B. 新增结构化 JSON 文件 | `config/api_profiles.json`，与 `model_pricing.json` 同目录同风格 | ✓ **采用**。项目已有 JSON 配置先例；天然支持列表/嵌套/可选字段；扩展档案字段（如未来加 fallback、轮换权重）无需改解析器 |
| C. config.env 内嵌 JSON 值 | `API_PROFILES={"..."}` 单键 | ✗ 破坏 `parse_env_file`"所有值都是字符串"的语义（规则 2.1）；单行 JSON 可读性差、易手编损坏 |

**结论：采用方案 B。** 这是最贴合现有架构（`model_pricing.json` 先例）且长期可维护的方案，代价是需修订 spec_config.md 规则 1.1（见 §5.3）。

### 4.2 JSON Schema

文件路径：`config/api_profiles.json`（需加入 `.gitignore`，见 §5.2）

```json
{
  "version": 1,
  "profiles": [
    {
      "name": "deepseek-main",
      "provider": "deepseek",
      "api_key": "sk-xxxx",
      "api_url": "https://api.deepseek.com/v1/chat/completions",
      "model": "deepseek-v4-pro",
      "enabled": true,
      "note": ""
    },
    {
      "name": "relay-2",
      "provider": "openai-compatible",
      "api_key": "sk-yyyy",
      "api_url": "https://my-relay.example.com/v1/chat/completions",
      "model": "gpt-4o-mini",
      "enabled": false,
      "note": "中转站备用"
    }
  ]
}
```

约束（保存时强制，加载时容错）：

- `is_default` 字段已废弃（启用互斥语义下不再需要）；旧文件中的 `is_default` 加载时静默丢弃，不参与逻辑。
- `name` 唯一且非空；重复时后者加序号去重并告警。
- `api_key` 允许为空（本地服务），但 provider 为 `deepseek/openai/openai-compatible` 且为空时，任务入口给出未配置提示（不中断启动）。

供应商预设表（选择 provider 时自动填入，用户可覆盖）：

| provider | 默认 URL | 默认模型 | Key 必填 |
| --- | --- | --- | --- |
| deepseek | `https://api.deepseek.com/v1/chat/completions` | `deepseek-v4-pro` | 是 |
| openai | `https://api.openai.com/v1/chat/completions` | （留空） | 是 |
| ollama | `http://localhost:11434/v1/chat/completions` | （留空） | 否 |
| openai-compatible | （留空，需手填中转地址） | （留空） | 是 |

### 4.3 加载与保存规则（对齐现有规范）

- 文件不存在 → 空档案列表，不告警（首次启动场景）。
- 文件损坏（JSONDecodeError / 结构非法）→ warning 日志 + 空列表兜底，**不阻断启动**（对齐 spec_config.md 规则 2.2 的容错哲学，且任何情况不得用损坏文件覆盖用户数据）。
- 保存沿用"先写 tmp 再 replace"原子写入（对齐规则 3.1），UTF-8 无 BOM、`indent=2` 风格（对齐 `save_pricing_config`）。

---

## 五、接口与安全设计

### 5.1 兼容策略（零改动优先）

`get_api_config()` **签名与返回值保持不变**：内部改为"读取默认档案三件套"；无默认档案时回退现有 `config.env → 环境变量 → 默认值` 链。

```python
def get_api_config():
    """返回 {api_key, api_url, model}。
    优先取默认 API 档案；无档案时回退 config.env / 环境变量 / 默认值（旧行为）。"""
```

⇒ 现有消费方（`AIBatchGenerator` 构造、`refinement_service`、`run_synergy_drift.py`）**零改动**，未选档案时天然使用默认档案。

### 5.2 新增接口与敏感信息防护

| 接口 | 职责 |
| --- | --- |
| `load_api_profiles() -> dict` | 读取 `api_profiles.json`，容错（见 §4.3），返回 `{"version", "profiles": [...]}` |
| `save_api_profiles(data) -> None` | 原子写入，含 `is_default` 唯一性等约束修正 |
| `list_api_profiles() -> list[dict]` | 供 UI 列表展示（剔除 `api_key` 明文，返回掩码或已配置标记） |
| `get_api_profile(name) -> dict | None` | 按名称取单档案（含 Key，仅供任务解析路径使用） |
| `resolve_api_config(name=None) -> {api_key, api_url, model}` | **任务侧唯一入口**：name 为空时等价于 `get_api_config()`；name 指向的档案不存在或停用时 warning + 回退默认档案 |
| `migrate_legacy_api_config() -> None` | 首次启动迁移（FR-4），幂等（文件已存在即跳过） |

调用方约定：**业务代码只调 `resolve_api_config(name)`**，不直接读 `load_api_profiles()`，保证"任务选择档案"与"默认档案"两条路径行为一致并避免 Key 泄漏面。

敏感信息防护：

- `api_profiles.json` 加入 `.gitignore`（`config.env` 已有先例，位于 line 40）。
- `list_api_profiles()` 默认掩码 Key，仅 `get_api_profile`/`resolve_api_config` 可取明文，且这些值不得进入 logger/UI 状态文本/异常消息。
- 配置对话框中的 Key 控件始终 `Password` 模式。

### 5.3 spec_config.md 规则 1.1 修订（本设计的依赖项）

现规则："config.env 是唯一持久配置存储，不使用 settings.json 或数据库。"

修订建议（待评审后更新 spec_config.md）：

> **规则 1.1（修订）：config.env 存储标量键值配置；结构化列表配置（模型价格 `model_pricing.json`、API 档案 `api_profiles.json`）存放于 `config/` 目录下的 JSON 文件。** config.env 不得存放嵌套/列表结构，JSON 文件不得混入标量运行参数。
>
> **为什么：** 扁平 KV 表达不定长列表（多 API 档案）会带来键爆炸与静态映射失效；项目已有 `model_pricing.json` 的 JSON 先例，结构化数据用结构化文件存储是最简实现。两处职责边界清晰，`parse_env_file` 保持纯字符串语义不破坏。

---

## 六、UI 设计

### 6.1 配置对话框改造（settings_dialog.py）

「参数配置」Tab 由"固定三行表单"改为**档案列表 + 编辑面板**（沿用现有 `QTableWidget` + 行内按钮风格，与「价格配置」Tab 视觉一致）：

```
┌──────────────────────────────────────────────┐
│ 参数配置（Tab）                                │
│ ┌─────────────────────┬────────────────────┐ │
│ │ 档案列表 (QTableWidget)│ 编辑面板            │ │
│ │ 名称 | 供应商 | 状态    │ 名称: [____]        │ │
│ │ deepseek-main | DS | ● │ 供应商: [DS ▾]     │ │
│ │ relay-2 | 中转 | ○    │ API Key: [******]   │ │
│ │                      │ URL: [____________] │ │
│ │ [新增] [删除] [设默认]  │ 模型: [____________] │ │
│ │ [启用/停用]            │ 备注: [____________] │ │
│ └─────────────────────┴────────────────────┘ │
└──────────────────────────────────────────────┘
```

交互要点：

- 列表行显示：名称、供应商缩写、默认标记（★）、启用状态（●/○）、Key 已配置指示（"已配置/未配置"，**不回显明文**）。
- 选中行 → 右侧面板回填；Key 框始终空白，placeholder 显示"已配置（留空保持不变）"或"未配置"。
- 「设默认」按钮：单选语义，点击后行标记刷新。
- 删除默认档案时弹确认并提示将自动提升新默认。
- 保存校验失败（重名、空 URL 等）时保留当前草稿并提示（对齐现有 `_collect_pricing_config` 的校验交互）。

### 6.2 配置栏选用（已修订，无任务发起选择器）

**已撤销**原 `ApiComboBox` 组件与两处挂载点（`BackendChooseDialog`、`IndexRefinementDialog`）。API 选用统一在配置栏「参数配置」Tab 的档案列表完成（见 §6.1），通过启用/停用互斥控制当前使用的档案。任务发起处（`BackendChooseDialog`）只保留成本估算行（按启用档案模型读取）与"无可用档案"拦截，不显示任何 API 档案信息；`IndexRefinementDialog` 的 `build_generator` 直接传 `None` 走 `get_api_config()`。

---

## 七、异常与边界处理汇总

| 场景 | 行为 |
| --- | --- |
| profiles.json 不存在 | 空列表 + 触发迁移检查（FR-4） |
| profiles.json 损坏 | warning + 空列表兜底，启动不阻断，不覆盖损坏文件 |
| 多个 is_default | 保留第一个，其余清 false + warning |
| 删除默认档案 | 自动提升第一个 enabled 档案为新默认 |
| 全部停用/无档案 | 任务入口下拉禁用，给出配置引导提示；`get_api_config()` 回退环境变量链 |
| 选中档案被删除/停用（并发场景） | `resolve_api_config` warning + 回退默认档案 |
| 重名档案 | 保存校验拒绝，提示改名（不自动改名，行为可预期） |
| API 调用失败 | 沿用现有 `AIBatchGenerator` 重试/日志，不引入切换逻辑（非目标） |

---

## 八、验收标准（可测试）

1. **多档案管理**：新增 3 个档案（deepseek / 中转站 / ollama），保存后重启进程，列表与 Key 配置完整保留。
2. **默认唯一**：设置档案 B 为默认后，A 的默认标记自动清除；重启后默认项仍为 B。
3. **启用过滤**：停用档案 C 后，任务选择器不再出现 C，配置列表仍可见。
4. **任务选择生效**：在 `BackendChooseDialog` 选择档案 A（mock/fake URL 指向本地测试服务器），执行批量生成，捕获的请求 URL/Host 与 A 一致；再选 B 重复验证。
5. **成本估算联动**：切换不同模型档案时，估算行"模型"与费用随之刷新；未知模型显示"无法自动估算"。
6. **迁移**：仅含旧三件套的 config.env + 无 profiles.json → 启动后生成 `deepseek-main` 默认档案，旧键仍在文件中。
7. **兼容回归**：全部现有 AI 相关 pytest 用例通过（`get_api_config()` 语义不变）。
8. **安全**：grep 运行日志与 UI 状态文本，不出现任何 API Key 明文；`api_profiles.json` 已入 .gitignore。
9. **容错**：手写损坏 profiles.json → 启动成功、空列表、warning 日志。
10. **删除默认**：删除当前默认档案 → 自动提升新默认，下拉预选正确。

---

## 九、开发任务拆分（供排期，每步含验证方式）

| # | 任务 | 产出 | 验证 |
| --- | --- | --- | --- |
| 1 | `env.py`：`load/save/list/get/resolve_api_profiles` + `migrate_legacy_api_config` + `get_api_config` 改造 | 接口层 | pytest：迁移/容错/默认唯一/回退链用例 |
| 2 | `api_profiles.json` 落地 + `.gitignore` 补充 | 存储 | 手写损坏文件启动验证 |
| 3 | `settings_dialog.py`「参数配置」Tab 改造（列表+编辑面板；保存时格式校验：URL 合法性、provider 语义 Key 必填、Key 首尾空格清洗） | UI | 手动全流程：增删改/设默认/停用/Key 不回显；保存校验用例 |
| 4 | `ApiComboBox` 组件 + 挂载 `BackendChooseDialog`、`IndexRefinementDialog` | 选择器 | 验收用例 4/5 手动验证 |
| 5 | 修订 `spec_config.md` 规则 1.1、更新 README 配置说明 | 文档 | 评审通过 |
| 6 | 回归：全量 pytest + 冻结构建冒烟（精简版无 RAG 页不受影响） | 交付 | 全绿 |

---

## 十、决策记录（已确认）

| # | 决策点 | 结论 | 理由 |
| --- | --- | --- | --- |
| 1 | 记住"上次使用的档案" | **不记**，每次任务回到默认档案 | 批量任务"选一次跑很久"，显式确认是保险而非麻烦；记忆会弱化"默认档案"语义，并存在批量任务误用上次备用账号的风险 |
| 2 | 「测试连接」按钮 | **延期 P2**；本期只做保存时格式校验 | 真实链路由首次任务失败日志闭环（401=Key 错 / 404=URL 错 / 400=模型名错）；保存时本地校验拦截 URL 手误、Key 为空、复制带空格等高频手误 |
| 3 | 环境变量兜底 | **长期保留**，语义收窄为"无默认档案时的最后兜底" | 脚本/CI（run_synergy_drift、ai_batch）依赖环境变量注入 Key，废弃会回归；档案优先、兜底最后，两者不冲突 |
| 4 | Key 校验强度 | **provider 语义必填 + URL 格式校验 + 首尾空格清洗**，不做前缀强制 | 智谱 `{id}.{secret}`、中转站自定义/无前缀均为合法异构格式；强校验 `sk-` 与"多供应商"目标冲突；真实鉴权属「测试连接」（P2）职责 |