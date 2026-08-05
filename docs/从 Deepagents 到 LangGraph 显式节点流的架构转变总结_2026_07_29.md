# 从 Deepagents 到 LangGraph 显式节点流的架构转变总结_2026_07_29

> 本文记录 basic_qa 与 intelligent_analysis 两个业务图从 `create_deep_agent + 12 中间件` 架构迁移为
> LangGraph 显式 StateGraph 节点流的完整设计，含迁移动因、图结构、逐节点职责、并行机制、
> 工具可见性模型与 skill 文件职责划分。可与《在 Langgraph 中单 Graph 架构设计总结_2026_03_17》
> 《在 Deepagents 中 多图结构的设计总结_2026_04_30》对照阅读，是该系列的第三次架构演进记录。

## 一、迁移动因

deepagents 是为**开放域自主规划**设计的运行时（虚拟文件系统、渐进披露、SubAgent），而本项目是
**封闭业务域**（技能集固定、工具集固定、输出契约固定）。旧架构一次典型数据查询需要约 **7 次串行
LLM 调用**：权限预分类 → 槽位抽取 → 主模型决定调 find_skill → 主模型决定 read_file → 主模型执行
→ 清洗重生成 → 推荐追问，端到端 20–35s。其中"找技能、读规则、洗输出"三类调用对封闭域是纯开销。

迁移后关键路径压缩为 **3 次串行 LLM**（槽位抽取 1 + 主模型 2），典型数据查询 6–10s；
问候类问题走快车道仅 1 次 LLM。Java 端 SSE 事件契约零改动。

## 二、目标图结构（basic-qa / intelligent-analysis 共享）

两图入口各自只有 3 行实例化代码，全部逻辑在共享构建器 `src/common/business_graph/`：

```
START
  │
  ▼
classify_intent ──── chitchat / knowledge ────────────────┐
  │ data_query                                            │
  ▼                                                       │
check_permission ──硬拒绝──────────────────────┐          │
  │ 通过/修正                                  │          │
  ▼                                            │          │
resolve_skill                                  │          │
  │                                            ▼          ▼
  ▼                                     finalize_output ◀─ prepare_model（快车道汇入）
prepare_model ──▶ call_model ◀──▶ execute_tools
                    │ 无 tool_calls
                    ▼
              finalize_output ──▶ END
```

## 三、逐节点职责

### 1. classify_intent —— 意图车道分流（纯正则，0 LLM）

看最新用户消息判进三条车道，写入 `state.intent`：

| 车道 | 判定依据 | 例子 |
|---|---|---|
| chitchat | 整句锚定问候白名单 | 你好 / 谢谢 / 你是谁 |
| knowledge | 知识问答句式且不含时间/地点信号 | 什么是AQI |
| data_query | 其余一切（fail-open 兜底） | 查保定市今天空气质量 |

设计原则是**错误代价不对称**：数据查询误判成闲聊 = 用户拿不到数据（严重）；闲聊误判成数据
查询 = 只是慢几秒（可接受）。不确定一律走 data_query 全流程。

### 2. check_permission —— 权限审查（仅 data_query，全图第 1 次 LLM）

复用权限中间件 `abefore_agent`，内部时序：

1. 正则预分类（纯规则，LLM 语义判定已合并进槽位抽取的 `need_check` 字段）；
2. **并行**执行：拉用户画像（MCP，TTL 缓存且只缓存有效画像）+ LLM 槽位抽取；
3. 多区域/站点归属解析（`asyncio.gather` 并发 + 缓存）；
4. 规则引擎 `check_permission()`（纯代码）产出 permitted / 豁免窗口 / 修正指令；
5. 硬拒绝时直接短路到 finalize_output，拒绝话术作为最终答案。

推 `progress`、`permission_status` 事件。

### 3. resolve_skill —— 技能路由（0 LLM，至多 1 次 embedding）

1. 问候门控 `is_conversational` 命中则直接降级（防"你好"被 embedding 相似度误命中）；
2. `router.amatch(question)` 语义匹配（索引启动后台预热 + 问题级 LRU 缓存）；
3. 单命中：内联 SKILL.md 正文 + 当前 mode 的 references 全文（按 mtime 缓存）；
   多候选：top1 完整规则 + 其余摘要 + 披露 `load_skill` 工具；未命中：降级通用能力。

替代旧架构"主模型调 find_skill → 主模型调 read_file"的两轮往返。

### 4. prepare_model —— 系统提示词组装（0 LLM，按车道差异化）

- chitchat：小提示词、零工具、不刷 MCP；
- knowledge：知识问答提示词 + 本地知识工具；
- data_query：`ensure_mcp_tools()` + 完整拼装（基础角色 + 北京时间 + fast/expert mode +
  `<permission_context>` + skill 规则 + 并行调用指引）。

替代 TimeContext / ModeRouting / 权限 wrap_model_call 等中间件"每轮反复注入"，现在只算一次。

### 5. call_model —— 模型决策（每轮 1 次 LLM）

`_available_tools(state)` 算出本轮可见工具子集 → `bind_tools` → 喂
`[SystemMessage] + state.messages`；3 次重试 + 空回复 nudge。模型二选一：发 tool_calls
（可一次发多个 = 并行组）或直接产出答案文本。

### 6. execute_tools —— 工具执行（0 LLM，纯 IO 并发）

`ToolNode(tools=[*get_business_tools(), load_skill_tool], awrap_tool_call=composed_tool_wrapper)`。
对最后一条 AIMessage 的全部 tool_calls 用 `asyncio.gather` **真并发**执行，每个调用各自穿过
组合包装器五段流水：

```
推 progress 事件 → 绑定工具实例 → 执行
  ├─ MCP 远程工具失败 → 2s 退避重试 1 次 → 仍败 → 503 降级 ToolMessage
  └─ 本地工具失败 → 500 降级（不重试）
→ rich_output 推送（图表数据直发前端）+ ToolMessage 压缩（省 token）
```

失败隔离：单个工具挂掉不影响同批其他结果。

### 7. finalize_output —— 唯一输出出口（常态 0 LLM）

Java 端只消费本节点事件，是全图契约出口：

1. **确定性清洗**最终答案（正则去思考标签/工具痕迹/内部上下文块；仅检测到明显残留且
   环境开关 `FINAL_OUTPUT_LLM_CLEANUP_FALLBACK` 打开才走 LLM 兜底，默认关闭）；
2. 并发启动推荐追问 LLM 任务（不占关键路径）；
3. 分片推 `final_output_delta` → 推 `final_output_done`（Java 落库全文）；
4. 短超时 await 追问结果，就绪则推 `expanded_questions`，失败静默。

替代旧 FinalOutputCleanupMiddleware 的"答案重生成"整轮 LLM。

## 四、路由边（纯函数，无 LLM）

- call_model 之后：最后一条 AIMessage 有 tool_calls → execute_tools；无 → finalize_output；
- execute_tools 之后：最后一条是 ToolMessage → 回 call_model；否则 → finalize_output。

这就是显式写出的 ReAct 循环，替代 deepagents 内部隐藏的 agent loop。

## 五、并行机制：谁决定、谁执行

**并行不是框架配置，而是模型在一次回复里同时发出多个 tool_call。** 分工：

- **决定方（模型，call_model）**：由两层提示词驱动——全局 `<use_parallel_tool_calls>` 指引 +
  skill 步骤里的 `[并行组]` 标注（技能级具体化，告诉模型哪些调用无依赖）。`[串行]` 步骤则让
  模型单发调用等结果，下一轮再发并行组。**因此 skill 文件中的 [串行]/[并行组] 标注在新架构
  下是真正生效的调度指令，清洗时必须保留。**
- **执行方（ToolNode，execute_tools）**：内部对每个 tool_call 建协程后 `asyncio.gather`，
  3 个各 1.5s 的 MCP 调用总耗时 ≈1.5s 而非 4.5s。

## 六、工具模型：注册全量、绑定子集

`ToolNode` 的 `tools=` 是**执行注册表**（编译期固定、一图服务所有请求，必须认识所有可能被调
用的工具）；模型每轮实际可见的工具由 `_available_tools` 按 state 动态过滤后 `bind_tools`：

| 车道/状态 | 模型可见工具 |
|---|---|
| chitchat | 零工具（不发 schema，省 token） |
| knowledge | 仅始终可见本地工具（时间 + 知识检索） |
| data_query 命中 skill | allowed-tools ∩ 业务池 + 始终可见（多候选加 load_skill） |
| data_query 未命中 | 全量业务工具（native 降级语义） |

skill 本身**不携带工具**：SKILL.md frontmatter 的 `allowed-tools` 只是引用共享业务池
（2 个本地工具 + 运行时发现的全部 MCP 工具）的名单。模型发不出未绑定工具的调用，
所以注册全量不构成越权面。此模型等价于旧 SkillToolRegistryMiddleware + SkillToolFilterMiddleware。

## 七、Skill 文件职责划分（清洗后）

```
SKILL.md    = 路由名片（description，语义路由 embedding 语料）
            + 工具清单（allowed-tools）
            + 两模式共用铁律（执行边界 / 通用质量校验）
fast.md     = 快速模式完整执行手册（按 chat_mode 二选一内联）
expert.md   = 专家模式完整执行手册（按 chat_mode 二选一内联）
```

2026-07-29 集体清洗（49 个文件）：

1. **删除陈旧导航指令**（31 个 SKILL.md）："下一步先读取 references 文件"——新架构由
   resolve_skill 代码内联规则，模型无 read_file 工具，该指令只会造成困惑或幻觉调用；
2. **重试指令下沉到代码**（54 处提示词删除）：原"工具调用失败时，自动重试 1 次，间隔 2 秒"
   靠 LLM 执行 = 每次重试多烧一轮完整 LLM 且无法真正等待；现由 composed_tool_wrapper
   对 MCP 远程工具代码级退避重试（本地工具失败多为确定性错误，不重试）；
   提示词仅保留降级语义（"失败则基于已有数据降级回答"）；
3. **[串行]/[并行组] 标注保留**（理由见第五节）。

## 八、Java 端 SSE 契约（迁移不变量）

Java 侧 `stream_mode=["custom","updates"]`，messages 事件全部丢弃（隐藏中间推理步骤）。
必须保持的 custom 事件形状：`progress` / `permission_status` / `final_output_delta`（正文唯一
来源）/ `final_output_done`（落库全文）/ `rich_output` / `expanded_questions`；updates 仅处理
`__interrupt__`。regenerate 依赖 history 中 human 消息匹配，故 state.messages 保留原文。

## 九、成本对比（典型数据查询："查询保定市今天的空气质量"）

| 阶段 | 旧 deepagents | 新节点流 |
|---|---|---|
| 意图/权限前置 | 预分类 LLM + 槽位 LLM 串行 | 槽位 LLM 1 次（与画像 MCP 并行） |
| 技能定位 | find_skill 往返 + read_file 往返（2 轮主模型） | embedding 匹配 + 代码内联（0 LLM） |
| 执行 | 主模型 N 轮 | 主模型 2 轮（决策 + 组织答案） |
| 输出 | 清洗重生成 1 轮 + 追问串行 1 轮 | 确定性清洗 0 LLM + 追问并行 |
| **串行 LLM 合计** | **~7 次 / 20–35s** | **3 次 / 6–10s** |

问候类（chitchat 车道）：1 次 LLM，2–3s。

## 十、边界与保留

- data_analysis 图先于本次迁移已是节点流范式，本次将其 tool_wrappers 上移共享；
- deep_research / intelligent_report / intelligent_tracing 仍用 deepagents（自主规划场景适配）；
- 旧中间件文件保留（上述三图仍在用），权限中间件被节点流直接复用；
- MCP 客户端、checkpointer（thread 级多轮记忆）、models、config 全部复用，
  上下文能力由 LangGraph 底座的 `add_messages` reducer + PostgresSaver 提供，与 deepagents 无关。
