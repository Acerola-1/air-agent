## Why

`intelligent-tracing` 目前仍是通用 DeepAgents 图：持有全部 MCP 工具、技能目录为空，没有污染过程分析流程或证据约束。需要新增可复核的异常、空间格局、气象条件与风向关联分析，同时通过真实业务学习新版 DeepAgents 的委派、状态隔离和中间件装配。

2026-10-18 的前期探针记录称：站点/城市/网格空气数据、周边站点、历史小时气象、NWP 点位历史及热点工具可用；轨迹工具在已测参数组合下返回 `cl:不能为空`，PSCF 返回内容导致网关解析失败；当次发现的 109 个工具中没有污染源清单、企业排放或化学组分查询。以上是历史调查结果，不是本次实时复验，也不能据此确认服务端参数未透传。

用户已决定停用轨迹/PSCF，不再排查其服务端。本 change 的产品定位是“污染过程与来向线索辅助分析”：报告现象、关联、候选解释、反证与待核查事项，不确认具体企业、源类或贡献率。

## What Changes

- 固定 Python 3.13、deepagents 0.7.28 和 `upgrade-deepagents-0.7.28` 验收的完整依赖锁；不再兼容 0.7.5/0.7.28 双基线。升级锁、镜像、checkpoint 与回滚仍归升级 change。
- 新增经过校验并冻结的 `TracingRequest`，明确自然语言解析、站点消歧、时间口径、任务参数传递、追问修订和结果失效规则。
- 使用 DeepAgents lead 与四个专员：
  - `station-data`：确定性 `CompiledSubAgent`，站点/周边取数、质控和异常识别，零模型调用；
  - `grid-data`：确定性 `CompiledSubAgent`，网格格局、热点与空间覆盖，零模型调用；
  - `meteo-analyst`：隔离的声明式专员，选择补查气象/风场分析，数值由确定性工具计算；
  - `evidence-reviewer`：隔离的声明式专员，检查反证、遗漏和表达，不决定最高证据权限，不执行数据或文件工具。
- 删除轨迹专员；主图、专员和确定性工具均不装配或调用轨迹/PSCF。固定输出 `trajectory_unavailable`。
- 拆开 `spatial_pattern` 与 `process_assessment`；L1–L3 是描述性证据类别，L4 只保留为待核查源类假设，L5/L6 不可达。
- 用可信证据注册、结论 allowlist 和引用验证构建门禁；聊天、画布、持久化、历史回读共用已验证报告。禁词扫描只作辅助。
- 先完成 PM2.5 纵向闭环，再扩展 PM10/O3/NO2/SO2/CO；未实现污染物明确说明，不能静默套用 PM2.5。
- 采用“编排方式 × LLM 审查”的析因评测，分别评价业务可靠性、调查能力、运行成本和框架学习结果。
- `include_tools` 与 `mode="fork"` 仅作为独立可选实验，不参与业务主链、首阶段验收或上线依赖。

## Capabilities

### New Capabilities

- `tracing-multiagent-orchestration`：请求契约、四专员委派、状态与权限边界、输出发布及多轮隔离。
- `pollution-tracing-analysis`：污染过程、空间格局、风场关联、可信证据、结论限制与分阶段污染物支持。

### Modified Capabilities

无。不改动其他图、既有 Skill/MCP 授权语义或公共事件契约。既有 `agent-request-capability-selection` 约束继续覆盖复合分析工具、子任务与恢复。

## Impact

- 主要实现区域：`src/intelligent_tracing/`，以及相关域测试。
- 可按需增加共享观测/输出/画布的显式可选接线；默认行为及其他图必须保持一致，不将通用原始画布工具直接交给溯源 lead。
- 不新增 graph ID，不恢复已删除图，不改外部 IAM/RAG 仓库，不修复 MCP 服务，不引入外部轨迹或排放数据。
- 正式运行态以 `docs/CURRENT-RUNTIME.md` 指向的三仓清单为准。测试只用本任务自建临时资源，历史探针/升级报告不能作为账号、DSN 或启动入口。

## Dependencies and Delivery

- 2026-10-19 修订时基线：`dev`，HEAD `6ff95dd872300f8dfdb8dfcf4a4ab99e63cf5706`；当前 `langgraph.json` 已提交三图：basic-qa、data-analysis、intelligent-tracing。开工仍需复核实际 HEAD/diff。
- 当前主环境安装 0.7.5，独立 `/tmp/air-agent-upgrade` 环境安装 0.7.28；环境存在不代表升级验收完成。旧升级 change 的六图/旧网关/旧资源信息只作为历史记录，复验以现用三图和正式清单为准。
- G0：在独立 0.7.28 环境可立即开展纯函数、fixture、fake model 与并行状态 spike；不得因此替换正式环境。
- G1：正式配置联调/交付前，升级 change 提供新基线上的真实模型、观测与出网、迁移/回滚及镜像证据，并明确未完成项处置。不可把升级任务的勾选或历史 HTTP 200 当验收。
- G2：PM2.5 满足确定性、权限、发布门禁和真实闭环验收，再扩展五种污染物。独立框架实验不阻塞 G2。
- 详细设计见 [design.md](design.md)，验收见 [evaluation.md](evaluation.md)，实施入口见 [implementation-handoff.md](implementation-handoff.md)。
