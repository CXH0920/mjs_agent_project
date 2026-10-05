# CI 与本地环境差异说明（P2-3，2026-10-04）

## 结论先行

**CI 绿 ≠ 本地绿**。CI（ubuntu-latest + 精简环境）跑的是纯逻辑子集，Windows 特有故障
（ACL 损坏、ADB/MuMu 设备链路、路径分隔符、中文编码）结构性测不到。发版与周更的
质量门禁以**本地全量 pytest + `release.py` 烟雾测试**为准。

## CI 环境（.github/workflows/verify.yml）

| 项 | CI | 本地 |
|---|---|---|
| 系统 | ubuntu-latest | Windows 10 |
| 环境 | environment-ci.yml（无 paddle/CUDA，测试全程 mock） | environment.yml（paddlepaddle-gpu 2.6.2 全家） |
| 门禁 | ruff ✅ 阻断、import-linter ✅ 阻断、pytest ✅ 阻断 | 同左（pre-commit 前移 ruff） |
| report-only | vulture、pyright（continue-on-error，不阻断） | 同左 |
| pytest | `-n auto --timeout=60 --timeout-method=thread` | 同左（另配 basetemp） |

## CI 侧会跳过的用例（本地不跳）

| 用例 | 跳过原因 | 证据 |
|---|---|---|
| `test_rag_integration.py`（真实语料加载回归） | rag_corpus 语料未入公开仓（数据出库，经私有仓同步） | 该文件 L86 `pytest.skip("rag_corpus 语料未入库（CI）…")` |
| `test_rag_block_ids.py`（block_id 唯一性校验） | 同上 | 该文件 L31 |
| `test_card_grid_detector.py`（牌面识别网格检测） | 缺少本地真图样本（screenshots/test_2v2_top 等，不入库） | 该文件 L194、L290 |
| `test_ocr_components.py`（静态字库覆盖武将名） | 真实武将数据已出库（data/heroes.json 走私有仓同步，pull 后可运行） | 该文件 L255 `pytest.skip("真实武将数据已出库…")` |

**推论**：RAG 语料质量回归、牌面识别几何回归与 OCR 静态字库覆盖只在开发机
生效——语料/样本/数据的变更必须在本机跑全量 pytest 确认后才能 push。

## 后续：OCR 回归样本库纳入 CI 的路径（P1-7）

现状：`tests/ocr_baseline_cases/` 仅 `labels.json`（cases 为空），OCR 链路改动
只能靠 `python -m src.scripts.ocr_baseline run` 人工对比，无法量化是否退化。

补齐步骤（样本采集为人工动作，需模拟器在线）：

1. 采集：选将页/巅峰赛页各状态下执行
   `python -m src.scripts.ocr_baseline collect --page-type hero_selection`
   （工具自动截图并存 case；目标 50~100 张，覆盖满员/空槽/特殊字体状态）
2. 标注：编辑 `tests/ocr_baseline_cases/labels.json`，把 case 的 slots 填上
   真实武将名并将 `annotated` 改为 `true`
3. 基线：`python -m src.scripts.ocr_baseline run` 输出逐槽对比报告
   （`tests/ocr_baseline_cases/report.json`），确认当前准确率作为基线
4. 纳入 CI：样本标注完成后，在 verify.yml 的 Test 步骤后追加
   `python -m src.scripts.ocr_baseline run --ci`（工具需相应支持无头比对、
   只比对 annotated case 并以准确率阈值退出）——样本就绪前不实施

## 运行期资源验证备忘（P1-8 关联）

retrospective 183 号（AI 查询后内存持续增长）的代码级验证结论见该条目更新；
残余怀疑为浏览器模式（Playwright+Edge）进程内存，实测命令：

```bash
# 应用运行中每 60s 采样一次进程内存（PID 换成实际值）：
for i in $(seq 1 120); do tasklist /FI "PID eq <PID>" /FO CSV | tail -1 >> logs/mem_trace.csv; sleep 60; done
```
