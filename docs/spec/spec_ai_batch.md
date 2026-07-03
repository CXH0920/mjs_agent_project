# AI 批量生成规范

> 长期设计规则与决策依据，覆盖生成器选择、API 调用、JSON 提取、浏览器自动化。

## 一、生成器选择

### 规则 1.1：两个生成器满足统一接口

`AIBatchGenerator`（API 模式）和 `PlaywrightGenerator`（浏览器模式）均实现 `generate_guide(hero) → (dict|None, usage|None)` 和 `generate_synergy(a, b) → (dict|None, usage|None)` 接口。所有生成模式（全量、增量、指定、配对）共用同一套 generate 接口。

**为什么：** 接口统一性允许 `ai_batch.py` 和 UI 层的 `BackendChooseDialog` 以相同的循环结构驱动四种生成模式（全量攻略、全量相性、指定配对、选定武将），无需为每个模式单独写一套调度逻辑。

### 规则 1.2：浏览器模式的 usage 返回 None

`PlaywrightGenerator` 的生成方法返回 `(result, None)`。

**为什么：** 浏览器模式无法获取 token 统计数据。如果 mock 一个 usage 值，会导致后续的"预估 vs 实际"对比报表误判。返回 None 让调用方自然忽略统计。

## 二、API 调用（AIBatchGenerator）

### 规则 2.1：同步 httpx 客户端

使用 `httpx.Client()`（同步），不使用 `httpx.AsyncClient()` 或 `requests`。

**为什么：** 项目整体是同步的（PySide6 GUI + CLI 脚本）。使用异步客户端需要额外的事件循环管理，引入 `asyncio.run()` 在已有线程模型中容易冲突。`httpx` 优于 `requests` 的原因：原生支持 timeout、连接池复用、响应式 API 更现代。

### 规则 2.2：指数退避重试上限 3 次

```python
for attempt in range(1, self.max_retries + 1):
    time.sleep(2 ** attempt)  # 2s, 4s, 8s
```

**为什么：** 3 次重试 + 指数退避的设计平衡了恢复概率和等待时间。如果 API 临时限流，2 秒通常不够恢复，但 8 秒足够。超过 3 次（累计最长等 14 秒）仍未恢复说明是持续性问题，继续重试只是浪费时间和 tokens。

### 规则 2.3：限速控制基于 RPM

`_min_interval = 60.0 / rpm` 控制每次请求之间的最小间隔。以 `_last_request_time` 为基准，不足时 sleep 补齐。

**为什么：** DeepSeek API 对 RPM（每分钟请求数）有硬限制。在 UI 层（攻略生成、相性生成）中多个生成任务串行执行，如果没有限速控制，可能瞬间触发大量请求导致 429 限流。RPM 可配置让用户根据自己购买的 API 套餐调整。

## 三、JSON 提取（_extract_json）

### 规则 3.1：四阶段尝试

1. 全文 `raw_decode`（最快，无冗余字符时一次成功）
2. 从 ```json 代码块提取（AI 常返回 Markdown 包裹）
3. 从 `---` 分隔线后提取（DeepSeek 网页版有时在最终 JSON 前加摘要）
4. 第一个 `{` 到最后一个 `}` 区间截取（兜底）

**为什么：** AI 的回复格式高度不可控。这四种模式覆盖了实际项目中遇到的所有 6 种变体。阶段 1 最快且精确，后续逐级放宽匹配条件直至能解析。没有银弹——必须容忍 AI 回复的不规范性。

### 规则 3.2：_repair_strings 状态机修复字面换行

`_repair_strings()` 按 `in_string` 状态跟踪 JSON 字符串上下文，仅修复字符串值内的字面 `\r` / `\n` 为 `\\n`。

**为什么：** AI 回复中技能描述字段经常包含真实的换行符，导致 `json.loads()` 在字符串值内遇到换行直接报错。全局替换 `\n` 会破坏键名中的合法字符。只有状态机跟踪 `in_string` 才能精确命中需要修复的位置。

### 规则 3.3：_convert_ids_to_int 解决类型漂移

```python
data["counters"] = [int(v) for v in data["counters"]]
```

**为什么：** AI 回复中的 ID 有时是字符串（`"114"`），有时是数字（`114`），但 Pydantic 模型中 `hero_id: int` 要求 int。少数情况下 AI 甚至混用两种类型。显式转换消除了这个隐式类型问题。

## 四、浏览器自动化（PlaywrightGenerator）

### 规则 4.1：复用 Edge 持久化上下文

使用 `chromium.launch_persistent_context(channel="msedge", user_data_dir="...")`。

**为什么：** `user_data_dir` 保存登录态的 cookie 和 session 信息。用户只需在首次运行时手动登录 DeepSeek 一次，后续启动自动复用登录态，无需每次都扫码或输密码。

### 规则 4.2：system prompt 只发送一次

`_guide_system_sent` / `_synergy_system_sent` 标志控制首次发送完整的 `system_prompt + 武将数据`，后续只发送武将数据。

**为什么：** DeepSeek 网页版的上下文窗口有限。如果每次生成都重发 system prompt + 全部历史，很快会超出上下文限制。在同一会话中持续追加新请求，让 AI 保持已建立的生成模式。

### 规则 4.3：流式回复双阶段等待

Phase 1（检测回复开始）：每 500ms 检查 assistant 元素数量是否增加，超时 180s。
Phase 2（等待内容稳定）：每 2s 检查回复文本长度，连续 3 次不变视为生成完成。

**为什么：** AI 生成回复不是瞬间完成的——存在延迟后爆发的特点。如果只等固定时间，可能内容尚未生成完全。两阶段设计分别解决了"何时开始生成"和"何时生成完毕"两个问题。

## 五、相性配对（ai_synergy_pair）

### 规则 5.1：支持 2~8 武将的多配对组合

传入的武将数量范围 2~8，使用 `itertools.combinations(pair_heroes, 2)` 遍历所有 C(N,2) 配对。输出进度 `[i/total]` 与实际配对数同步。

**为什么：** 游戏对局中通常 2~8 名玩家，用户需要同时计算某几个武将间的两两相性关系。固定 2 武将的限制过于严格，但超过 8 个会带来过多 API 调用（C(8,2)=28 对，约需 2 分钟）。8 个上限是实际需求与等待时间的平衡点。

### 规则 5.2：逐对失败不阻断

```python
for idx, (ha, hb) in enumerate(itertools.combinations(pair_heroes, 2), start=1):
    result, usage = generator.generate_synergy(ha, hb)
    if result:
        # 保存
    else:
        print(f"FAIL")
```

`FAIL` 对不中断整体流程，仅打印失败信息。其他配对继续执行。

**为什么：** 选定的 8 个武将中有个别配对因 API 限流或网络波动失败时，中断整个流程会让用户等待后一无所获。继续执行剩余配对，只生成成功的那部分，比全部失败好。用户可后续单独补生成缺失的配对。

### 规则 5.3：进度输出格式兼容正则解析

输出格式 `[idx/total] A <-> B OK/FAIL`，可与 UI 层 `GuideProgressDialog` 的正则匹配 `r"\[(\d+)/(\d+)\]"`。

**为什么：** UI 层通过解析子进程 stdout 的正则来更新进度条。"选定武将"模式中也使用相同格式。保持进度输出格式统一让 UI 层复用同一套进度更新逻辑。
