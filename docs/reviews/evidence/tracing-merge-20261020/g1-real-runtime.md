# G1 真实运行证据（IAM + MCP + 模型）

日期：2026-10-20（Asia/Shanghai）
分支：`feature/intelligent-tracing-dev-merge`
运行时：官方 `langchain/langgraph-api:3.13` 镜像构建的
`air-agent/langgraph-api:tracing-merge-20261020`，API 0.16.2 / Python 3.13，
挂载当前 `src/`。

## 1. 真实身份

- IAM Java `127.0.0.1:19100`，业务库 `127.0.0.1:15432/air_agent_business`。
- 使用既有企业账号 `root` 登录 IAM，选择 `alpha-env`；该企业角色包含
  `agent:business:intelligent-tracing:use`。
- 会话 cookie 只保存在本机临时文件（0600），未写入仓库。
- BFF 每次运行重新签发 runtime ticket；LangGraph custom auth 实时调用 IAM
  introspect 复查会话、企业成员与四项修订。

## 2. 真实 MCP 与模型运行

- 请求：郑州市北区建设指挥部，2026-10-18 00:00–23:00，PM2.5，
  基线 2026-10-11 00:00–2026-10-18 00:00，半径 20 km。
- thread：`b33b2f82-2806-4075-97b4-32e4360f4c82`
- run：`01a157d7-7261-7213-a9f7-62bdbe3be5bf`
- attempt：`b04d71aa-525b-4b80-87ec-f82843e2d1b2`
- request：`36391090-5ecf-47bd-b1b6-a8108bc9001f`
- 报告：`rep_618e1b4b1c6d460b9e5f5019dfa61176`
- 台账终态：`succeeded`，`state_revision=3`；
  `business_code=intelligent-tracing`，`business_policy_version=2026-10-19.1`。
- 事件：`run.admission_confirmed`、`answer.confirmed`、`run.state_observed`
  各 1 条。
- 模型：`hy3`（OpenAI 兼容网关）。真实模型完成 freeze、并行 task 调度与报告
  尝试；模型侧工具调用参数为空的问题通过
  `ChatOpenAI(disable_streaming="tool_calling")` 规避。
- MCP：真实目录查询定位 `41fe79bb1`（北区建设指挥部）；站点小时序列返回
  23/23 有效小时，覆盖率 100%，24 小时滑动均值 16.0 µg/m³。该数值仅为
  观测统计，不是源贡献率。

最终报告由确定性 fallback 渲染并过 gate 发布，正文包含数据覆盖、评价口径和
固定限制；本次网格证据未形成异常结论，气象抓取失败，报告明确写为限制而不是
补零或推断。完整 SSE 在 `g1-pm25-attempt11.sse.txt`（本目录副本）。

```text
## PM2.5 污染过程与来向线索分析
分析对象：北区建设指挥部（41fe79bb1）
时间范围：2026年10月18日00时至2026年10月18日23时
分析策略：analysis-policy-v2

### 数据覆盖
数据完整率 100.0%（有效 23 / 应有 23 小时，最大连续缺口 0 小时）。

### 异常过程
当前数据覆盖或基线不足，无法判断是否存在异常；未把未知当作无异常。

### 评价口径
24小时滑动均值：16.0 微克/立方米，有效小时 23/24；该指标仅作补充统计，
不是自然日均或法规评价。
```

## 3. 本轮修复（由真实运行暴露）

1. `TracingBudgetMiddleware` 的时钟和计数原先绑定在单例图上，容器启动 180 秒
   后所有新运行立即“预算耗尽”。改为按 run 键控并在 `abefore_agent` 重置；
   新增共享实例复用回归测试。
2. `hy3` 在“长系统提示 + 多工具”流式响应中丢失 tool_call 参数，导致
   `freeze_tracing_request` 收到空对象。主模型带工具时禁用流式。
3. 用户把“郑州市”并入站点名时，目录精确匹配失败。冻结前拆分行政区前缀，
   并保留“北区建设指挥部”原名匹配。
4. 气象专员原有两个 `ToolRuntime` 工具在声明式子代理中未注入 runtime。
   改为确定性中间件先取数、计算，再把摘要注入模型系统消息；子代理不再持有
   数据工具，数值仍只来自确定性代码。
5. 无参 `get_analysis_policy_summary` 被调用时收到意外 `args` 关键字，补齐空
   args schema。
6. 气象质量标志 `pblh_sentinel` 被当成用户限制，gate 以
   `unknown_limitation` 拒绝整份报告。只保留可解释的 `pblh_unavailable`。

## 4. 环境恢复记录

运行前 Apple `container` 网络在宿主/容器间数据面失效（TCP 可连、PostgreSQL
握手失败，新容器无法访问旧容器与 DNS）。已执行一次 `container system
stop/start`，按依赖顺序重启 IAM PG/Redis、业务 PG/Redis、LightRAG 存储和
LangGraph 容器；host 服务用 `deployment/run_separated_local.py start` 恢复。
为适配新 vmnet 网关（`192.168.64.1` → `192.168.65.1`），
`local_host_bridge` 改为允许回环/私有地址绑定，启动脚本动态探测当前网关。
这是环境修复，不是产品功能变更；IAM 未重建、未 seed、未改账号。

## 5. 真实身份负例（BFF + admin + IAM）

同源 BFF `http://127.0.0.1:3000`，真实会话 cookie：

| 场景 | 请求 | 结果 |
|---|---|---|
| 未登录 | `POST /api/threads` 无 cookie | 401 `{"error":"未登录"}` |
| 缺少页面企业上下文 | 有 cookie，无 `x-tenant-id` / `x-tenant-context-version` | 403 `context_missing` |
| 企业上下文不一致 | 有 cookie，`x-tenant-id` 为其他企业 | 409 `context_changed` |

以上分别证明入口身份缺失、业务上下文缺失和跨企业上下文篡改在受理前被拒绝。
身份后端完全不可用时的 503 尚未在本轮执行（需要短暂停止 IAM；为不影响已恢复
的共享栈，未在本证据中伪造该状态）。

## 6. 未覆盖

- 其余五种污染物（PM10/O3/NO2/SO2/CO）的 PM10 实跑在工具调用阶段超过 3 分钟
  仍未收敛，已主动取消；其余污染物未执行。六因子覆盖结论不能据 PM2.5 单次
  运行外推。
- 授权 MCP 的拒绝/撤销、真实浏览器 G2、六因子成本汇总尚未完成。
- 本证据只说明 PM2.5 单条真实链路可用，不证明全部污染物或全部 G2 场景通过。
