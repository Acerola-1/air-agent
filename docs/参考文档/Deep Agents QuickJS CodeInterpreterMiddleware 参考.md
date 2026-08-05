# Deep Agents QuickJS CodeInterpreterMiddleware 参考

## 一、定位

`CodeInterpreterMiddleware` 是 Deep Agents 在 v0.6 系列新增的实验性中间件，用于把一个轻量级 QuickJS JavaScript/TypeScript 解释器接入 agent loop。启用后，agent 会获得一个默认名为 `eval` 的工具，可以在受控的内存运行时里执行小段代码。

它的核心价值不是替代 Python、shell 或完整沙箱，而是让 agent 在一次或多次工具调用之间拥有一个可编程工作区，用代码完成循环、分支、聚合、排序、过滤、校验、去重、重试和结果压缩等确定性任务。

官方文档入口：

- Deep Agents Interpreters: https://docs.langchain.com/oss/python/deepagents/interpreters
- Deep Agents changelog: https://docs.langchain.com/oss/python/releases/changelog

## 二、为什么需要解释器

普通 agent 工作流通常是：

```
LLM 决定下一步 -> 调用一个工具 -> 工具结果回到上下文 -> LLM 再决定下一步
```

这个模式适合简单任务，但遇到下面场景会变重：

- 需要对很多输入重复调用同一个工具。
- 需要根据中间结果做循环、分支、重试或提前终止。
- 需要对结构化数据做确定性处理，例如排序、分组、聚合、打分、字段映射。
- 中间结果很多，但最终只需要把少量证据或汇总结果交回模型。
- 需要协调多个 subagent，并把子任务结果拼成最终结论。

解释器把其中的“机械控制流”和“结构化数据处理”交给代码完成，减少模型轮次和上下文污染。

## 三、工作原理

### 3.1 中间件注入 eval 工具

安装 `deepagents[quickjs]` 后，可以从 `langchain_quickjs` 引入中间件：

```python
from deepagents import create_deep_agent
from langchain_quickjs import CodeInterpreterMiddleware

agent = create_deep_agent(
    model="openai:gpt-5.4",
    middleware=[CodeInterpreterMiddleware()],
)
```

中间件会向 agent 暴露一个 `eval` 工具。模型调用这个工具时，传入 JavaScript/TypeScript 代码，QuickJS 在内存上下文中执行代码，并把结果、错误和可捕获的 `console.log` 输出返回给 agent。

### 3.2 QuickJS 是执行边界

QuickJS 是一个适合嵌入式执行的轻量 JavaScript 运行时。Deep Agents 的解释器代码运行在嵌入式 QuickJS context 中，默认不直接拥有宿主机能力。

默认不开放：

- 文件系统
- 网络
- shell 命令
- 包安装
- 操作系统级执行
- 时间/时钟 API
- agent 工具调用

这意味着解释器默认更像一个“受限的内存计算环境”，不是完整 OS 沙箱。

### 3.3 PTC 暴露工具能力

解释器默认不能调用 agent 工具。如果希望解释器代码调用工具，需要通过 PTC（programmatic tool calling）显式 allowlist：

```python
from deepagents import create_deep_agent
from langchain_quickjs import CodeInterpreterMiddleware

agent = create_deep_agent(
    model="openai:gpt-5.4",
    middleware=[CodeInterpreterMiddleware(ptc=["task"])],
)
```

启用后，解释器代码可以通过 `tools.<tool_name>()` 调用 allowlist 中的工具。例如允许 `task` 后，解释器可以用代码批量调用 subagent，再聚合结果。

PTC allowlist 是关键权限边界。每加入一个工具，就等于给解释器代码开放一项外部能力。不要把高风险、宽权限或会产生费用/写入/删除/外网访问的工具随意暴露给解释器。

## 四、状态快照与多轮对话

`CodeInterpreterMiddleware` 默认会在每次 agent run 结束时快照解释器状态，并在下一轮恢复。

可以保留的状态包括：

- 全局变量
- 函数
- 导入的解释器 skill module
- 代码执行后仍存在于 QuickJS context 中的内存状态

生命周期可以理解为：

```
一轮开始 -> 恢复上次快照 -> eval 执行代码并读写变量 -> agent run 结束 -> 保存新快照
```

在同一轮 agent run 内，多次 `eval` 调用共享当前 live context；中间件不会在每次 `eval` 之间做快照，而是在 run 完成后保存。

这对数据分析类任务很有用：agent 可以先把查询结果整理成中间变量，再继续基于这些变量做筛选和汇总。

## 五、适合本项目的使用场景

### 5.1 批量空气质量查询

例如用户要求比较多个城市、多个站点或多个时间段：

```
统计 2026 年 1 月以来，北京、上海、广州每天 PM2.5 超过良等级的天数，并列出每个城市最高的 5 天。
```

解释器可以：

- 循环调用查询工具。
- 按城市和日期分组。
- 根据项目内污染等级边界判断超标。
- 排序并截取 Top N。
- 只把最终表格和关键证据返回给模型。

### 5.2 多工具结果聚合

例如一个问题需要同时查空气质量、气象条件、站点元数据和历史统计。解释器可以用代码把多个工具结果标准化成统一结构，再交给模型生成自然语言解释。

### 5.3 SubAgent 批量编排

如果 PTC allowlist 中开放 `task`，解释器可以用 `Promise.all` 并发调用多个 subagent：

- 一个 subagent 查数据。
- 一个 subagent 解释污染等级。
- 一个 subagent 做趋势分析。
- 最后由解释器聚合结构化结果，交给主模型输出。

### 5.4 减少上下文膨胀

查询工具可能返回大量行。解释器可以先在本地筛选、排序、聚合，只返回少量必要证据，避免把所有原始数据塞回模型上下文。

## 六、和其他执行路径的区别

| 需求 | 推荐方式 |
| --- | --- |
| 一两个简单工具调用 | 普通 tool calling |
| 循环、分支、重试、聚合、结构化数据处理 | QuickJS 解释器 |
| 从代码中批量调用工具或 subagent | QuickJS 解释器 + PTC allowlist |
| 需要跨线程复用确定性辅助函数 | 解释器 skills |
| 需要 shell、pip/npm 安装、测试、文件系统、完整 OS 执行 | sandbox/backend |

## 七、安全边界

解释器提供的是能力受限的代码执行层，不是完整生产沙箱。

需要注意：

- QuickJS 在嵌入式 context 中执行，不是天然隔离的独立虚拟机或独立进程。
- 默认隔离文件系统、网络、shell 和工具调用，但 PTC 暴露的工具会扩大能力边界。
- 对不可信或半可信任务，应把 agent 放进独立 worker 进程或容器，并收窄 PTC allowlist。
- 不要把能访问敏感系统、写数据库、删除数据、产生费用或无限制访问网络的工具直接暴露给解释器。
- 生产环境应设置内存、超时、最大 PTC 调用次数和返回字符数。

## 八、常用配置

`CodeInterpreterMiddleware` 的重要参数：

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| `memory_limit` | `64 * 1024 * 1024` | QuickJS heap 内存上限，单位 bytes |
| `timeout` | `5.0` | 单次 `eval` 超时时间，单位秒 |
| `max_ptc_calls` | `256` | 单次 `eval` 中最多允许的工具调用次数 |
| `tool_name` | `"eval"` | 暴露给模型的解释器工具名 |
| `max_result_chars` | `4000` | 返回结果和 stdout 的最大字符数 |
| `capture_console` | `True` | 是否捕获 `console.log`、`console.warn`、`console.error` |
| `ptc` | `None` | 允许解释器代码调用的工具列表 |
| `snapshot_between_turns` | `True` | 是否跨对话轮次保存解释器状态 |
| `max_snapshot_bytes` | `None` | 快照最大字节数，默认受 `memory_limit` 约束 |

本项目建议初始配置：

```python
CodeInterpreterMiddleware(
    timeout=5.0,
    memory_limit=64 * 1024 * 1024,
    max_ptc_calls=64,
    max_result_chars=4000,
    ptc=[],  # 先不开放工具，按业务场景逐步加入 allowlist
)
```

如果后续要让解释器批量调用安全的只读查询工具，可以逐个加入 allowlist，例如：

```python
CodeInterpreterMiddleware(
    ptc=[
        "query_air_quality",
        "query_station_metadata",
    ],
    max_ptc_calls=64,
)
```

## 九、依赖安装

只使用 Deep Agents v3 event streaming，不一定需要 QuickJS：

```txt
deepagents>=0.6.1
```

要启用 `CodeInterpreterMiddleware`，需要安装 QuickJS extra：

```txt
deepagents[quickjs]>=0.6.1
```

这个 extra 会安装解释器需要的 `langchain-quickjs` 相关依赖。项目使用 `uv` 管理依赖时，建议在输入约束文件中保持 `>=`，然后用锁文件固定解析后的精确版本。

## 十、落地建议

建议分阶段启用：

1. 第一阶段只安装 `deepagents[quickjs]`，确认依赖与现有 graph 能启动。
2. 第二阶段接入 `CodeInterpreterMiddleware`，但 `ptc=[]`，只允许模型做纯内存计算和结构化整理。
3. 第三阶段按只读、低风险、可审计原则开放少量查询工具。
4. 第四阶段再考虑开放 `task`，让解释器编排 subagent。

不要一开始把所有工具都开放给解释器。QuickJS 的价值在于把复杂控制流收敛到代码里，但权限仍应按最小必要原则逐步开放。
