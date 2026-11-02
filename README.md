# 空间科学智能就绪数据集质量评价智能体

2026 怀柔科学城 AI4S · 揭榜挂帅赛道。批量读取空间科学智能就绪数据集，按声明式评价规范执行检查，
完成「批量检查 → 问题定位 → 程度与质量预测 → 数据集排序 → 可复核报告」。

| 交付物 | 位置 |
|---|---|
| 可运行原型（Docker 镜像 + Dockerfile + 运行说明） | `Dockerfile`、本文 |
| 数据集质量评价规范（固定版） | [docs/评价规范.md](docs/评价规范.md)（由 `spec/quality_spec.yaml` 生成，与实现逐条一致） |
| 方案规划（DEL-1） | [docs/方案规划.md](docs/方案规划.md) |
| 部署与使用说明（DEL-3） | [docs/部署与使用说明.md](docs/部署与使用说明.md) |
| 设计说明 | [docs/DESIGN.md](docs/DESIGN.md) |
| 智能体构件登记（工具 / 技能 / 规则 / 记忆 / 插件 / 编排与护栏） | [agent/](agent/)（由 `tools/gen_agent_registry.py` 生成，与实现逐条一致） |
| 公开集实测材料 | 每次运行自动生成于输出目录（见下文「输出」），目录结构按《交付材料清单》§三 |

## 目录

```
spec/quality_spec.yaml   判定标准：维度、指标、检查项、缺陷代码、容差、四态、等级、规则版本
agent/                   智能体构件登记 manifest / tools / skills / rules / memory / plugins
src/sqa/
  cli.py                 命令行入口与退出码
  spec.py                规格加载与校验（维度、指标、检查项引用一致）
  registry.py            检查器注册；启动时校验「规格 ↔ 实现」逐条对应
  dataset.py             数据集发现、标签与数据配对、共享判定状态
  runner.py              调度：适用性 → 依赖 → 执行（重试/降级）→ 四态汇总；质量变体组检查
  variants.py            质量变体对照引擎：分组、多数参考、元素匹配、程度计量
  codes.py               缺陷代码（对齐附件一污染项目）与不可重推项弃报
  report.py              覆盖率不变量、缺陷程度、维度分、综合分、等级、全局与组内排序
  deliver.py             按交付材料清单输出评分卡、排序表、问题清单、证据索引、运行日志、MANIFEST
  materials.py           文件清单与未完成事项说明
  llm.py                 模型服务客户端（OpenAI 兼容，可选，默认不调用）
  assist.py              模型子代理：说明文档声明抽取、整改建议改写
  checks/                检查器实现，一个检查编号一个函数
tools/                   服务器流水线、规范文档与构件登记生成、标定统计、档位标签检验（tier_eval.py）
tests/                   合成数据上的回归测试（含规范文档同步检查）
```

## 本地运行

```bash
pip install -r requirements-dev.txt
```

```bash
PYTHONPATH=src python -m sqa run --input /path/to/publish --output ./out
```

```bash
PYTHONPATH=src python -m sqa validate-spec
```

```bash
python -m pytest -q tests
```

`--workers N` 指定并行进程数（默认自动：容器可用 CPU 数与质量变体组数中较小者）；`--purpose training` 按模型训练用途评价；`--use-llm` 启用模型子代理（需 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`，缺失直接报错退出）：读说明文档供 D04 比对、改写整改建议；两者都不参与判定与计分，默认关闭。

开发辅助（不进入评测流程，可接 DeepSeek 等 OpenAI 兼容服务，`LLM_BASE_URL=https://api.deepseek.com`、`LLM_MODEL=deepseek-chat`）：

- `python tools/gen_profile.py <数据集目录> [--llm]`：为新数据类型生成标注族草稿并逐项实测命中率，人工审核后写入规格。`--llm` 会把样本发给模型服务：**比赛数据受保密承诺约束，不得发给外部模型**，须另加 `--i-confirm-data-may-leave` 才会外发；
- `python tools/llm_review.py docs/评价规范.md --out 审阅意见.md`：让模型对照评分要求审阅交付文档。
- `python tools/show_evidence.py <输出目录> <数据根目录> <结论编号>`：按回查链生成一页抽查 HTML（原图标出证据区域、文件校验值核对），答辩现场用；
- `python tools/make_demo_dataset.py demo_data [--cme]`：生成录屏用的合成演示数据集（含一份故意写错的说明文档；`--cme` 生成三帧一组、带文件清单的 CME 演示数据），可放心接外部模型。
修改规格后重新生成规范文档：

```bash
python tools/gen_spec_doc.py > docs/评价规范.md
```

```bash
python tools/gen_agent_registry.py
```

## Docker

```bash
docker build -t sqa-agent:0.9.0 .
```

```bash
docker run --rm --network none -v /path/to/publish:/data:ro -v $(pwd)/out:/output sqa-agent:0.9.0
```

纯规则引擎，不需要加速卡，DCU 与 A10 服务器均可运行；镜像约 133 MB；运行期不联网、单次运行、无状态。

## 输入约定（依据组委会公开数据 OPC-V34）

- `--input` 可以是单个数据集目录，也可以是包含多个数据集的目录（如解包后的 `publish/`，6 类 × 5 个质量版本）。
  **同时输入同一基础数据的多个质量版本时，会自动启用变体对照**，可检出类别错标、漏标、虚标、位置偏移等内容性缺陷。
- 两种布局都支持：
  - 分目录：`<数据集>/label/<数据文件名>.json` + `<数据集>/data/<数据文件名>`（HMI、AIA、CME）
  - 平铺：`<数据集>/<id>.json` + `<数据集>/<id>.png`（DMSP、EPB），或 `<数据文件名>.json` + 数据文件（ULF）
- 标注族按标签内容识别，不依赖目录名。

## 输出

| 路径 | 内容（交付物编号） |
|---|---|
| `00_MANIFEST.yaml` | 本次输出的全部文件、字节数与 SHA-256 |
| `03_评价规范.yaml` | 本次运行所依据的规格 |
| `04_评分卡/<数据集>_<版本>.json` / `.html` | 评分卡（DEL-5）：维度得分、覆盖率、四类状态计数、主要缺陷（回指结论编号）、关键缺陷、质量预测、证据编号 |
| `05_排序表/ranking_global.csv`、`ranking_explanation.md` | 全局排序（DEL-6），名次 1..n 连续不并列，写明主要差异来源 |
| `06_问题清单/findings.jsonl`、`findings_report.md` | 问题清单（DEL-7）：通过 / 不通过 / 待判定 / 不适用四类全量输出，结论以此为准 |
| `07_证据索引/evidence_index.csv`、`evidence/` | 证据索引与证据文件（DEL-8），全量保留 |
| `08_运行日志/run.log.jsonl`、`system_env.txt`、`seeds.yaml`、`run_summary.json` | 运行日志（DEL-9，一行一事件，UTC）、系统版本与随机种子（DEL-10）、耗时与资源 |
| `09_未完成事项说明.md` | 未实现检查、未覆盖维度、不可计算项、不可重推检查项声明、已知局限（DEL-11） |
| `details/<数据集>/report.json`、`inventory.json` | 完整判定记录（含全部证据与中间量，供复算）与文件清单 |
| `summary.json` | 质量变体分组、全局与组内排序 |

每条结论带检查编号、规则版本、证据位置与缺陷代码（对齐发榜方附件一的污染项目）。不可重推类缺陷（标量数值扰动、结构参数失配、
角度抖动、CME 参数失配、时序断裂）一律落「待判定」并写明需补充的基准材料，不输出「不通过」。
除 `run.log.jsonl`、`run_summary.json`、`00_MANIFEST.yaml` 外，同一输入与规则版本重跑逐字节一致。

退出码：`0` 完成 · `2` 规格错误或规格与实现不对应 · `3` 输入错误 · `4` 覆盖率不变量破坏 · `5` 大模型环境变量缺失。
「拒绝入库」是正常的判定结果，退出码仍为 0。单个检查器异常会重试并降级为「待判定」，不中断整次评估。

## 新增一条检查

1. 在 `spec/quality_spec.yaml` 的 `checks` 中声明编号（类别字母 + 两位序号）、名称、缺陷代码、适用范围与受限边界、维度、整改建议、类别、证据类型、严重度、适用标注族与依赖。
2. 在 `src/sqa/checks/` 中用 `@check("编号")` 实现，返回 `ok / defect / pending / not_applicable`。
3. 运行 `tools/gen_spec_doc.py` 与 `tools/gen_agent_registry.py` 重新生成规范文档与构件登记。规格与实现、文档、登记不一致时，校验与测试都会失败。
