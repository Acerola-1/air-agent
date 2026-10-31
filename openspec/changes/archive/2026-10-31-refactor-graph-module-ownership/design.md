# 图实现归位与公共层职责设计

## Context

动机与授权边界见 [proposal.md](proposal.md)。2026-10-29 的只读调查基于 `dev`、HEAD `98ce4ff33aecdcdcec6ef87a9c190ee1e14eefe1` 及当前未提交工作区；这些是源码事实，不是已部署状态或本轮运行验收。模块级盘点见 [source-inventory.json](source-inventory.json)，包括源文件摘要、静态导入线索、拟定归属和处理理由。

### 已核实现状

- `langgraph.json` 注册三个独立入口；`basic_qa/graph.py` 调用 `common.business_graph.build_business_graph`，Basic QA 的大部分实现在公共目录。
- 数据分析已经在 `data_analysis/graph.py` 自建 `StateGraph(DataAnalysisState)`，有自己的 `nodes.py`、`state.py` 和 `prompt_builder.py`。它没有调用同一构图器，但仍依赖 `business_graph/tool_wrappers.py`、`tool_recovery.py`；不能将整个公共目录直接改名为 Basic QA。
- `common/capabilities.py` 内含数据分析菜单匹配、标题及选择校验，也有 Basic QA 的材料依赖处理；`common/prompts.py` 混有通用片段、图专属提示组装和旧流程残余。
- 两图都调用 `common/context_preparation.py`；它进一步使用 `context_guard.py`、`conversation_summarizer.py`、`turn_tool_governance.py`，完成摘要控制、消息投影、资料治理及一次超窗恢复。这是需要拆分的完整控制流程，不是一组纯计量工具。
- Basic QA 新增的 `basic_qa_integration.py` 还负责当前规则／schema投影、能力休眠与恢复；它也属于 Basic QA 的上下文实现。
- 溯源图通过 `intelligent_tracing/guards.py` 包装 Deep Agents 摘要中间件，不直接使用上述手写控制流程；共享容量计算、Token 计量、观测和身份复验。
- `common/mcp_scoped.py` 反向使用 BQA 工具目录的 schema 摘要；租户守卫中间件和工具治理从 `common/business_graph` 导入公共执行／摘要函数。迁移必须反转这些依赖，不能绕过实际发送边界。
- `tool_wrappers.py` 当前有两层BQA作用域校验：`basic_qa_tool_wrapper` 在包装器入口调用 `_basic_qa_scope_denial`，`execute_mcp_tool_request` 在每次发送尝试再次调用。代码级重试重新进入后者，不重跑外层包装器；两层均须保留。底层 `mcp_scoped.execute_mcp_tool`／工厂另有通用授权及有条件schema比对，不能把它们当成已验证BQA当前能力与当次模型披露的替代品。
- `admin/api/app/services/ledger.py:443-506` 独立实现Skill材料读取、版本、数据分析菜单匹配和BQA依赖映射，并在显式受理／恢复等路径使用；其后才另调公共目录。迁移必须覆盖这份重复策略，而非只改公共目录调用。

### 与已有规格及计划的关系

- `revise-basic-qa-session-capabilities` 决定当前 BQA v2 行为；其任务 5.1、7.6 在调查时仍开放。旧 `add-basic-qa-conversational-skill-loop` 保留历史状态，不恢复逐轮清空或单 Skill 策略。
- `simplify-model-window-compaction-trigger` 决定现有容量、水位、摘要覆盖与恢复语义；结构拆分不改变这些算法的初始行为。它没有要求两图必须调用同一个 Python 控制器。
- `fix-data-analysis-react-loop` 的菜单及工具后循环是封存业务基线。2026-10-27 的 `7d79526` 是封存说明提交；本次不回滚到该提交或更早版本，以当前工作区保留的业务行为为准。
- 主规格 `composite-backend-routing`、`skill-three-layer-system` 等仍含早期通用 Deep Agents／文件工具假设，部分是历史 delta 外壳；BQA v2 的活动计划已澄清实际实现。本次不按旧文重建 CompositeBackend，不更改或归档其他 change，不借重构修正所有历史主规格。
- 这是纯结构重构，声明 `skip_specs: true`；不创建功能 delta。下文的架构不变量与验收矩阵是工程约束，不声称新增用户功能或旧未完成项已通过。

## Goals / Non-Goals

**Goals:**

- 每个图可从本目录读懂构图、节点、状态、提示、策略和上下文流程；Basic QA 与数据分析之间没有直接或经公共包装隐藏的实现依赖。
- 数据分析完成结构归位后继续封存；Basic QA 的节点、能力或上下文策略变化不自动改变它的业务流程。
- 公共模块的输入是明确的数据或接口，不读取某图私有 state 字段来决定流程，不导入图实现；通用领域规则仍可共享。
- 覆盖整个 `src/` 的归属盘点，逐项说明迁移／拆分／保留／残余处理；实际修改围绕已确认边界，不机械搬动所有单消费者工具。

**Non-Goals:**

- 不重写 BQA 功能、不开发数据分析功能，不统一两套上下文引擎，不重构 Deep Agents 内部。
- 不调摘要比例、保留轮数、模型配置、Skill 正文、工具排序、检索策略、权限模型或最终回答要求；不恢复结果字段门禁。
- 不新增迁移、改变业务策略版本、清空 checkpoint／索引／线程，不把模块移动当作强制重载材料或重建 embedding 的理由。
- 不重构前端和管理后台界面、不修改 IAM／RAG 外部仓库；不在本提案阶段运行产品验收或改正式服务。

## Decisions

### D1. 各图拥有流程，公共层拥有机制

最终依赖约束：`basic_qa -> common`、`data_analysis -> common`、`intelligent_tracing -> common`。Basic QA 与数据分析不得互相导入；`common` 不得直接、延迟、动态或经另一个公共文件反向导入任一图实现。宿主应用的装配层可以按当前请求选择图策略，但不得因此让 Basic QA 的独立导入需要数据分析包。

“公共”按职责判断：时间规范、空气质量单位、Token 计量、受保护连接、存储等可以共享；不能因两个图曾共同调用，就让节点流、菜单策略或会话状态控制留在公共层。公共登记中的 Graph ID、已审核工具白名单、不可变协议元数据是合法跨业务数据，不要求把所有图名字符串清零；专属执行分支和业务流程必须归位。

考虑并放弃：只改文件夹名称会保留耦合；把所有代码复制到两图会复制认证与安全机制；继续以参数化大构图器承载两图不满足独立演进要求。

### D2. 目标布局与命名

以下是拟定布局，现有未涉及模块保持原名；具体符号迁移需与清单对应，不通过空壳声明伪装完整实现。

```text
src/
  basic_qa/
    graph.py                    # 保留公开工厂
    builder.py                  # 本图节点和条件边
    nodes.py
    state.py
    prompts.py
    intent.py                   # 仅保留本图仍有效的入口判定
    capability_policy.py        # 本图目录元数据、依赖选择
    input_policy.py             # 仅消息／合法resume输入约束
    tool_policy.py              # 披露、激活、控制批次及本图纠错
    capabilities/
      runtime.py
      projection.py
      state.py
      observation.py
      skill_materials.py
      skill_search.py
      mcp_catalog.py
      mcp_index.py
      mcp_search.py
    context/
      preparation.py
      projection.py
      summarization.py
      tool_results.py
      recovery.py
    skills/                     # 原材料及相对路径不变
  data_analysis/
    graph.py                    # 保留公开工厂和本图构图
    nodes.py
    state.py
    prompts.py                  # 原prompt_builder及专属组装归位
    menu_skill_mapping.py
    capability_policy.py        # 菜单过滤、标题、选择校验
    tool_policy.py              # 封存的本图编排与纠错策略
    context/                    # 同名职责、独立实现，不导入BQA
      preparation.py
      projection.py
      summarization.py
      tool_results.py
      recovery.py
    skills/
  common/
    tool_execution/
      mcp.py                    # 通用发送、核权、版本比对机制
      results.py                # 通用归档和结果处理机制
      protocol.py               # 通用调用／回执配对辅助
    skills/
      materials.py              # 纯材料读取／解析
    tool_output_summary.py      # 原subagent_summarizer的通用归纳
    runtime_context.py          # 原context.py，避免与会话流程混称
    model_capacity.py
    model_budget.py
    model_errors.py
    text_reduction.py
    prompts.py                  # 仅通用片段／共享领域规则
    ...                         # 身份、台账、观测、连接等保留
  application/
    business_policies.py         # 可信、按需的图策略装配
    langgraph_auth.py            # 原生入口装配，再委托公共认证机制
    http.py                     # HTTP入口装配，再委托现有公共应用
  intelligent_tracing/          # 内部结构保持现状
```

`BusinessGraphNodes`、`BusinessGraphState`、`build_business_graph` 分别改为语义准确的 BQA 名称。若旧 checkpoint 序列化依赖类型名，在所属目录内保留必要的序列化适配，不在 `common` 留旧业务包。新增 `__init__.py` 只组织本包，不能自动导入其他图或启动预热。

### D3. 具体源到目标清单

| 当前来源 | 拟定处理 |
| --- | --- |
| `common/business_graph/{builder,nodes,state,prompting,intent}.py` | 迁入 BQA 对应文件；去除没有生产消费者的通用构图分支前，记录静态引用和实际注册入口；仍有效行为保留，不恢复旧意图路由 |
| `business_graph/basic_qa_runtime.py`、`basic_qa_integration.py`、`session_capabilities.py`、`capability_observation.py` | 分别归入 `basic_qa/capabilities/{runtime,projection,state,observation}.py` |
| `business_graph/{skill_materials,skill_search,mcp_tool_catalog,mcp_tool_index,mcp_tool_search}.py` | 归入 BQA capabilities，去掉重复的 `basic_qa_` 文件名前缀；schema纯标准化／摘要计算下沉公共机制 |
| `business_graph/tool_wrappers.py` | 通用发送、核权、归档提取到 `common/tool_execution/`；BQA专属校验、披露元数据和控制批次归 `basic_qa/tool_policy.py`；数据分析包装编排归本图 `tool_policy.py` |
| `business_graph/tool_recovery.py` | 本轮重试与控制工具策略分别归两图 `tool_policy.py`；只保留无图策略的协议辅助为公共函数，不保留共享可变恢复控制器 |
| `business_graph/skill_content.py`、`common/skill_discovery.py` | 通用文件读取归 `common/skills/materials.py`；旧单选／内联／find_skill流程查明无生产调用后退役，保留历史计划证据及仍有效测试语义，不能作为v2流程迁回 |
| `business_graph/subagent_summarizer.py` | 纯输出归纳迁为 `common/tool_output_summary.py`；它不是Deep Agents图，名称不再暗示子代理编排 |
| `common/context_preparation.py` | 两图各自拥有 preparation／recovery；不留转发入口或相同公共控制器 |
| `common/context_guard.py` | 摘要覆盖水位和输入投影分别归两图 context/projection；纯工具协议辅助可归公共 protocol |
| `common/conversation_summarizer.py` | 按轮切分、待归纳历史、水位与会话摘要组装分别归两图 context/summarization；底层纯文本归纳仍用公共 text_reduction |
| `common/turn_tool_governance.py` | 当前轮资料治理选择、写回与容量重试编排分别归两图 context/tool_results；通用存储、完整资料归纳保留公共机制 |
| `common/capabilities.py` | 目录DTO、材料版本计算、受理范围交集及授权发现保留；菜单、图专属标题／依赖策略下沉各图，由可信策略接口接入 |
| `common/prompts.py`、`data_analysis/prompt_builder.py` | 共享原子规则保留；图专属组装归各图 prompts；未参与运行的旧常量不能迁移时顺手启用 |
| `common/identity/langgraph_auth.py` 的BQA输入约束 | 策略归BQA input_policy；可信入口仍在受理前调用，不得只靠图内节点保护 |
| `admin/api/app/services/ledger.py` 的validate_skill_selection及其受理／恢复调用方 | 通用事务和幂等留管理API，材料读取与图策略改由同一可信接口提供；去除独立维护的菜单／标题／依赖映射副本，保持错误语义与材料版本一致 |
| `common/mcp_scoped.py` 的BQA schema反向依赖 | 使用公共schema标准化／摘要计算接口和可信披露元数据；不得导入BQA目录或扩大调用范围 |
| `common/middleware/tenant_runtime_guard_middleware.py` | 改用公共材料读取和公共MCP执行接口；不引入图内运行时，主子任务守卫继续生效 |
| `common/context.py` | 改名runtime_context，保留模式解析／消息读取等通用职责并同步全部引用，不复制会话控制流程 |
| `common/model_capacity.py`、`model_budget.py`、`text_reduction.py` | 保留纯容量、计量、schema处理及完整文本归纳机制；图专属参数选取、触发流程和写回归各图 |
| 其他common模块 | 逐文件归属见inventory；身份／租户／台账／观测／知识／存储／计算工具及领域纯规则保持公共职责，不因只有一个当前消费者而强制下沉 |

清单仅列当前源模块及设计目标，不是已执行迁移。完成实施后，`common/business_graph/`、上述四个旧公共上下文控制文件及 `common/skill_discovery.py` 不得继续作为生产入口，也不得留下导入别名壳。

### D4. 图策略通过可信装配接入，不从公共层反向导入

公共目录与身份机制定义窄接口：读取Skill元数据、校验规范选择、约束允许输入字段。两图在自己的 policy 模块实现策略；策略仅接收服务端已验证的请求与不可变目录，不接收浏览器提供的函数、模块路径或凭据。

`application/business_policies.py` 是装配根，使用代码中固定的图标识映射，只按可信实际业务装配对应策略。原生运行必须先由平台助手注册表把 `assistant_id` 解析为实际执行图；客户端metadata、config、业务代码和自报Graph均不能决定策略。保持现有受理顺序：解析actual graph及声明冲突检查，在规定位置执行本图输入约束、业务准入、预留归属／正文摘要／操作类型／选择核对和启动权原子消费；所有检查仍在任何业务节点前完成，不能因装配而提前消费或重复消费启动权。普通、流式和resume路径都覆盖。

公共函数显式接收策略或服务端不可变策略引用；不得自行 `importlib` 解析图实现，也不得在缺少策略时跳过菜单或输入检查。每个宿主进程独立初始化固定映射，不以可变全局“当前图”存状态。三项映射均必须存在：BQA按需加载自身policy；数据分析按需加载自身policy；`intelligent-tracing` 使用应用层兼容适配器复用其现有目录／输入声明，不改图内实现、不套用BQA输入或数据分析菜单策略。不静态导入全部图，不允许请求传任意模块路径。

原生认证入口和HTTP入口由 `application` 装配后委托现有公共认证／应用；`langgraph.json` 的 auth/http 路径及 Dockerfile／编排副本同步调整，三个公开 Graph ID 与 `src/<graph>/graph.py:graph` 保持不变。管理API在现有应用启动／请求边界接入同一装配，明确迁移 `validate_skill_selection` 及其独立材料／菜单／依赖规则，目录和台账受理使用同一数据源；按服务端准入所得graph选择策略，原生入口再次以实际助手图核对，不改变事务及幂等顺序。

管理API当前菜单匹配对非ImportError／CapabilityValidationError异常存在吞错路径。必须移除 `except Exception: pass` 放行：策略加载／查找、菜单匹配、结果属性／类型解析出现异常时转换为受控拒绝，不继续受理、消费启动权或外呼；正常的“未匹配到菜单”按当前业务规则单独处理，不与校验器故障混淆。以AttributeError、TypeError和策略不可用故障注入验证拒绝及有效正控。该项按既有 `agent-request-capability-selection` 的服务端选择验证要求做最小失败关闭修复，单列原有缺陷、异常行为差异和正控／反例，不声称这条异常行为逐字不变。这不新增规格要求或恢复人员授权；本change仍无功能delta。`data_analysis/menu_skill_mapping.py` 本身继续保留，不预设必须移动或改名该文件。

独立导入 BQA 图只接入自身策略和公共机制，不经一个会导入全部图的聚合包。宿主目录列举多个业务是应用级行为，不等于让BQA依赖数据分析包。架构测试须覆盖这一差别，不能只检查 import 文本。

#### MCP执行策略必须挂在真实发送边界

工具执行策略与上述菜单／输入策略是不同的接口。公共 `common/tool_execution/` 定义强制的 `ToolExecutionPolicy` 协议，BQA在 `basic_qa/tool_policy.py` 实现现有 `_basic_qa_scope_denial` 的语义；其他图使用固定登记的自身兼容策略。公共层仅调用注入的接口，不导入图实现。策略选择依据服务端实际图／业务绑定，不依据调用方传入一个可选回调或客户端自报 `ledger_graph_id`。

迁移时明确保留以下调用关系，而非仅保留“逐发送核权”措辞：

```text
basic_qa_tool_wrapper
  -> BQA入口作用域检查                         # 第一层，保留
  -> 通用执行／重试循环
     -> execute_mcp_tool_request               # 每次重试重新进入
        -> execute_mcp_tool
           -> ScopedMCPFactory.call
              -> 身份／可信上下文／策略存在性检查
              -> 通用业务／连接／受理／schema准备和检查
              -> 强制执行策略authorize_send   # 第二层，紧邻每次真实尝试
              -> tool.ainvoke
```

第二层的共同落点是工厂实际 `tool.ainvoke` 前的强制预检；不能只放在request helper，防止直接使用 `execute_mcp_tool` 或工厂发送绕过。各入口可以提前做检查，但最终发送点必须经过同一个不可跳过的预检。缺可信上下文或策略先在方法入口拒绝，不进行目录发现等网络请求；有上下文的请求完成发现／schema准备、异步观测等步骤后，在实际invoke回调内再次确认当前身份和作用域，不能使用外层旧成功结果跨越异步等待或复用于下一次尝试。现有HTTP身份／连接凭据复验继续独立保留；若SDK／传输层存在不经过预检的自动工具重试，须纳入同一边界或关闭该内层自动重试。

`authorize_send` 的上下文由服务端运行路径生成，绑定可信身份、企业、actual graph、业务、submission／attempt、服务及稳定tool ID；BQA还必须携带服务端state／当前能力引用和本次模型消息／披露记录。策略读取当前材料，核对工具已激活、本次模型消息ID与披露工具集合、schema版本及当前受理范围。BQA当前state仍由本图解释，公共层只传递不透明可信执行上下文；不在公共函数写 `state['bqa_*']` 分支。

缺策略、解析不出实际图、图／业务冲突、BQA缺可信state或披露证明、策略异常均在外呼前拒绝；禁止 `policy is None -> allow`，禁止把缺少 `bqa_disclosed_schema` 解释为免检。直接调用低层入口但没有可信上下文的BQA请求必须拒绝；其他图与测试业务也需显式可信登记策略，不通过假装非BQA获得默认放行。配置／metadata中伪造的策略、回调、图名或披露对象不构成执行凭据。

实施补充（2026-10-30）：异步策略返回本图的同步最终校验器；工厂将通用发送守卫绑定到实际 HTTP auth 流，在其身份／连接异步复验之后回读台账选择（含成员、线程、attempt／submission 及删除墓碑），随即同步校验不透明请求绑定再发送。这样策略等待中的删除与最后回读中的能力变化都不能使用旧成功结果。BQA 要求同步校验器存在，其他图使用明确登记的兼容策略；公共层不解释 BQA state。可选 schema 声明统一名为 `mcp_disclosed_schema`，不替代强制策略。真实计数 MCP 与独立 PG 正反例见实施记录。

每次真实尝试都重新调用策略；重试等待期间撤权、能力休眠、材料／schema或披露失效，后续发送停止。策略拒绝／异常不是瞬时网络错误，不进入可重试异常兜底，记录 `network_attempted=false` 和真实拒绝类别；若已经发送过一次，后续拒绝不能把前一次实际发送抹成0。

专门的S01–S07反例列入A10，必须走真实发送实现及可计数回环MCP，不能mock掉执行策略或只断言包装器返回值：

| ID | 输入路径 | 必须结果 |
| --- | --- | --- |
| S01 | 可信实际图为basic-qa，绕过ToolNode直接调 `execute_mcp_tool_request`；缺披露、错模型消息ID、未激活、schema失效分别输入 | 冻结schema并完成受控预热后，各反例新增业务请求0；发现请求另计，同等有效身份、材料、披露的正控恰1次 |
| S02 | 直接调 `execute_mcp_tool` 及工厂实际发送入口，缺可信执行上下文／执行策略 | BQA一律拒绝且请求0；不能靠省略metadata或伪造其他图绕过 |
| S03 | 策略抛异常或注册缺失 | 请求0、不降级到通用业务白名单放行，不重试策略错误 |
| S04 | 首次请求返回受控可重试错误，在重试前撤权／休眠／改变材料或schema | 每次重试预检重新运行；总业务请求停在首次1次，后续新增0，拒绝不再重试 |
| S05 | 入口检查通过后、发送前的异步准备期间改变合法范围 | 第二层拒绝且本次业务请求0；证明不是只依赖入口那次检查 |
| S06 | 数据分析、tracing主子任务及显式测试业务走自己的可信策略 | 有效正控通过，不被强加BQA能力加载条件，原通用授权反例继续拒绝 |
| S07 | 在自有测试副本中仅禁用最终第二层策略调用，保留入口、注册存在性和通用授权；使用S05中入口已通过、异步窗口内能力休眠或模型消息绑定失效且schema未变的样本 | 原实现新增业务请求0，移除最终预检后对应断言必须失败；S02仍应被存在性检查拒绝，不用于证明此变异。缺策略与缺执行调用是两种不同变异，不要求以“全量单测仍绿”为前提 |

当前覆盖有BQA实际编译图＋真实MCP SDK用例和显式陈旧schema的底层反例；直接request helper用例仍使用 `test-fixture`，未覆盖上述BQA直接发送缺披露路径。没有执行第二层移除的变异测试，不能断言其移除后全部现有测试必然通过。

S01–S07使用冻结、已知schema的计数fixture，分别记录发现与业务请求，不把schema准备当作业务执行成功；缺策略／可信上下文的反例两类新增请求均为0，其他作用域拒绝至少保证新增业务请求0，原测试若要求更严格的零网络则不得降低。

### D5. 两套上下文控制器，初始行为一致但后续独立

从当前工作区提取同一已存在行为，分别放入两图；不回退到封存日期以前，也不在公共目录增加 `BaseContextManager` 或另一个新名称的完整控制器。可以共享纯函数；不能让两图通过继承／回调外壳调用同一个摘要生命周期、重试循环或state写回控制器。

两边分别保留：完整请求计量；当前配置的比例与容量边界；成功摘要水位；旧摘要加未覆盖历史；默认近期三轮及容量压力下3→2→1；摘要失败不推进覆盖；当前轮大结果归档／归纳；工具调用配对；明确超窗只恢复失败模型调用一次；无进展停止；不重放工具、受理、整图或已发布正文；摘要和恢复每次外呼前实时复验身份。

BQA额外拥有活动能力投影、完整规则／schema一致性、历史加载回执精简、休眠／重激活及本轮必要组合保护。数据分析不引入这些v2策略；继续菜单确定性流程。共享部署容量及模型配置可以共同读取，保持现有默认值；之后BQA的算法修改无需修改封存图的控制代码。

原 `context.py` 仅为运行模式及消息读取辅助，不是上述会话管理；改名用于消除混淆。Deep Agents继续由自身guards与SDK摘要管理历史，不改用这两套流程。

### D6. 持久化、版本与可信边界保持兼容

保留 `messages`、`summary`、`summary_covered_message_id`、`bqa_capabilities`、`bqa_turn_key/bqa_turn_digest`、`bqa_disclosure` 的语义、版本及图节点／checkpoint通道标识；类和模块可以改名，但必须用旧实现产生的真实序列化样本证明新实现可恢复，不能只测新会话。

Skill版本继续依据内容和相对材料名，不含新的绝对源码路径；MCP稳定ID、schema版本、embedding索引内容、表名及代次不因搬家改变。不新增迁移、不递增全局业务策略版本来规避旧记录；如果测试发现未预见的序列化迁移需求，应先记录阻断并修订方案，不能静默删除数据或增加公共业务转发包。

公共发送机制始终复查身份／企业业务许可／连接与受理范围；BQA先生成可信披露记录，再由公共机制做通用版本比对。客户端不能通过输入policy对象或schema摘要扩大许可。所有模型、摘要、MCP重试继续同一守卫、台账和观测口径，删除／撤权优先。

### D7. 旧残余、测试与文档随归属调整

不保留名为通用、实为单图的非BQA构图分支。清理前用注册表、仓库导入、动态工具注册及探针确认无生产消费者；测试模拟分支不是继续保留死运行时的充分理由，但其中仍有效的安全与行为断言必须迁入真实入口测试。

受影响单测按 `tests/unit_tests/basic_qa/`、`data_analysis/`、公共计量／协议域整理；共享行为用参数化契约分别绑定实际两图实现，BQA专属断言单独保留。`test_business_graph_config.py` 中旧公共路径／源码字符串断言替换为实际入口、拓扑和行为断言，不能删除业务保护断言来“通过重构”。测试辅助fixture可复用，生产控制器不共享。

同步当前维护文档和探针；历史验收报告、历史OpenSpec、旧失败证据保留原文，不通过全仓替换路径改写历史。旧change尚未完成项继续开放，新的迁移报告明确指向新路径。

### D8. 验收矩阵与证据

实施证据使用新的 `docs/testing/graph-module-ownership/<run-id>/`，记录源码基线、命令、模型／工具类型、资源所有权、前后指标及失败身份。以下均为待实施验收，不是本轮通过项。

| ID | 验收内容 | 必须满足的结果与证据 |
| --- | --- | --- |
| A01 | 全量模块与旧入口清理 | inventory全部源模块有归属；旧公共业务包／控制文件／转发壳不存在；当前生产及测试／脚本引用无漏迁 |
| A02 | 依赖方向 | AST覆盖普通、相对、TYPE_CHECKING、局部导入，并核对动态导入／注册；阻止加载data_analysis包的独立进程仍可导入及运行BQA受控编译图，反向亦然；真实宿主只选择单图的路径也不能提前加载另一图 |
| A03 | 实际拓扑与导入副作用 | 两图独立节点、state和条件边，公开ID／图路径／checkpointer=None语义不变；图／宿主装配导入时模型、embedding、MCP请求计数为0 |
| A04 | BQA v2行为 | 多Skill、独立工具、跨轮复用、复述零查询、schema披露与执行一致；复用现有固定A01–D06用例和九Skill材料，不恢复旧门禁 |
| A05 | 封存数据分析 | 有菜单、无菜单、菜单与显式Skill冲突、正常工具后循环、终答及错误路径与迁移前一致；BQA控制工具／状态不进入此图 |
| A06 | 两套上下文独立 | 相同行为契约分别调用两图实际preparation/recovery；只替换BQA策略／摘要器或注入BQA状态变化不影响数据分析，反向同样；类型、模块与控制器不是公共别名 |
| A07 | 摘要与容量 | 水位上下、已有摘要、水位无效、旧线程无水位、3→2→1、工具突增、摘要失败／取消、schema与规则完整计量；检查两图所有实际发送点及失败状态写回 |
| A08 | 有界恢复 | 真实编译图注入明确超窗错误：仅失败模型最多2次发送，已完成业务工具恰1次；无进展、二次失败和非容量错误有界停止，已发布正文不重复 |
| A09 | 持久化升级 | 在自建PostgreSQL及实际LangGraph SDK中，旧实现生成两图checkpoint并跨进程由新图恢复；覆盖水位、v2能力、无水位旧线程、interrupt/resume、已有工具结果和终答，不清空历史 |
| A10 | 安全正反例及可信装配 | 在自有官方LangGraph容器和独立依赖上用实际resource hooks／HTTP入口覆盖普通、流式、resume：伪造assistant／预留图／声明业务、内部输入、缺策略均在外呼前拒绝且启动权不被误消费，有效三图正控成功；计数MCP／模型继续覆盖撤权、IAM不可用、跨租户、空集合、schema变化、删除。管理API与原生服务两个进程对目录、显式选择、菜单、材料版本、重试／恢复结果一致；故障后新增实际请求0 |
| A11 | Deep Agents兼容 | 主任务及真实SDK子任务的守卫、摘要、预算、报告／正文事件与checkpoint暂停恢复保持；不将确定性任务变成模型任务 |
| A12 | 版本、索引与外部契约 | 材料版本、schema摘要、稳定ID、同内容索引缓存复用、Graph和API／事件快照保持；有差异必须解释并阻断无授权的功能变更 |
| A13 | 实际消费链冒烟 | 自有持久化环境下用实际SDK和当前配置模型验证BQA一条连续追问与一条触发摘要后追问；数据分析用冻结输入／受控工具验证菜单链，缺真实业务系统明确受控局限，不伪造生产数据 |
| A14 | 原有失败与最终复核 | 对照相同基线逐例记录新增／既有失败；独立评审逐项核对A01–A13及范围，分别报告单测、受控图、持久化SDK、真实模型、浏览器、Git与部署状态 |

已有测试资产：`test_context_guard.py`、`test_conversation_summarizer.py`、`test_context_recovery.py`、`test_compaction_boundaries.py`、`test_turn_tool_governance.py`；BQA的 `test_basic_qa_*`、`test_business_graph_nodes.py`；数据分析的 `test_data_analysis_output_guard.py` 与菜单选择相关测试；`test_mcp_connection_execution.py`、`test_per_call_outbound_guards.py`；`tests/unit_tests/tracing/` 中子代理、安全门禁、预算、会话及流式测试；`tests/integration_tests/test_window_compaction_checkpoint.py` 与BQA SDK脚本。迁移时保留这些用例的有效断言并扩充缺口，不以文件改名代替覆盖。

A10同时必须交付D4的S01–S07逐发送策略反例与正控，以及菜单策略加载／匹配／属性类型异常的拒绝受理证据；这些是独立的阻断项，不能由图级未披露测试、拓扑检查或普通业务授权测试替代。

原报告已有局限须保留：BQA v2 5.1／7.6开放，存在真实模型专业范围／终答失败记录；窗口压缩仍缺完整持久化矩阵与真实提供商超窗样本。它们不是本重构自动承担的功能修复，但会阻止把既有功能“全量通过”写入本报告。若出现新回归，必须修复；同身份既有失败保留，不删断言、放宽gold或全局跳过。

实施先跑定向Python与架构检查；受影响Python执行Ruff及格式检查。仅有后端路径修改时不机械构建前端；如果公共契约或消费代码发生实际变化，再运行对应契约／构建检查并先读前端指南。不要求新做性能容量测试；对固定受控输入比对模型、embedding及业务请求数，避免迁移额外外呼。真实模型输出质量不以逐字相同为通过条件。

## Risks / Trade-offs

- [独立控制器存在初始重复代码] → 接受两图独立生命周期的必要重复；仅抽取无图策略的叶子机制，以双实现契约防止迁移遗漏，不再次合并控制器。
- [混合包装器拆分后只剩入口校验] → 按D4保留第一层入口检查及工厂实际发送前强制策略，S01–S07验证直接入口／逐次重试／缺策略／发送前变化与反例敏感性；不能只保留通用业务授权或mock第二层。
- [相对路径或类型移动改变版本／恢复] → 基线记录材料及schema摘要，旧实现真实序列化后跨版本恢复；失败阻断发布，不清历史。
- [装配层形成隐蔽跨图导入或并发串图] → 固定且按当前业务选择的可信策略，独立进程禁用另一图包测试，并行A/B上下文隔离；无可变全局当前策略或公共动态业务加载。
- [助手图选择或多进程材料规则不一致] → 策略依据平台actual graph和管理端可信准入，目录／受理复用相同策略；官方容器入口及两个宿主进程的一致性正反例覆盖，不仅测函数调用。
- [未提交工作区被覆盖或混入提交] → 开始apply前记录当时HEAD、源码摘要、diff与untracked；只改迁移清单，提交另按授权，不使用reset／clean覆盖其他工作。
- [旧测试绑定源码文字] → 改为实际拓扑与行为断言，保留保护语义及新旧路径映射；历史证据不改写。
- [数据分析缺少真实系统输入] → 使用明确冻结的菜单／载荷和自有工具做兼容验收，报告不声称恢复原业务环境；不为重构重建该系统。

## Migration Plan

1. **冻结实施基线**：以apply当时工作区为准，比对计划inventory漂移；保存无秘密的源码／材料摘要、当前变更状态、两图受控行为和自有真实checkpoint样本。只记录本任务资源所有权。
2. **建立边界和通用叶子能力**：先确定公共执行／schema摘要／材料读取／输出归纳及可信策略接口，保留实时安全边界；宿主装配与所有消费方在同一迁移批次接线。
3. **完成两图归位**：BQA图／能力／上下文与数据分析专属策略／旧上下文分别迁移；同步测试、探针、入口声明和文档；移除旧公共业务目录与转发路径。分阶段修改不等于允许发布半迁移状态。
4. **独立验收**：完成A01–A14并提供逐项证据；未运行、环境不可用或既有失败分别记录。只有计划结构校验通过不允许报告产品验收通过。
5. **未来部署前检查**：本轮不部署。未来发布使用唯一启动入口；受影响图在途运行排空，入口、装配和所有源码作为同一版本切换，不把新旧节点混在同一运行。无需重建共享依赖、修改凭据或回退本地鉴权。
6. **回退**：代码与装配作为同一批回退至本次迁移前的精确工作区快照，不用裸HEAD替代含未提交实现的基线；先在隔离环境证明新写checkpoint可被回退版本读取。若双向不兼容则阻止发布并补设计，不downgrade／清空业务库，不依赖已移除的公共转发包。

实施、独立验收、用户产品判断、Git保存、部署及OpenSpec归档是不同状态。此设计准备好不代表其中任何实施／运行阶段已完成。

## 实施时的路径细化

两图的完整结果治理／重试循环分别放在本图 `tool_runtime.py`／`tool_recovery.py`，不保留 `common/tool_execution/results.py` 生命周期外壳；真正公共的归档存储沿用原模块。原 `intent.py` 及旧车道提示无现行公开消费者，退休而不新建空文件；保留 checkpoint 节点／字段兼容。原定 source-inventory 为规划时不可变盘点，实际去向和残余处理见 `docs/testing/graph-module-ownership/20261029-apply/ownership-audit.md`，实现及未通过验收见同目录 `implementation.md`。

## 2026-10-30 用户追加的权限门槛清理

结构迁移之后，用户明确授权移除BQA执行阶段对Skill归属／独立加载记录／激活状态的重复检查。此项行为边界以活动 `revise-basic-qa-session-capabilities` 的同期规格更新为准；原结构迁移和历史运行证据保留。S01的“未激活”以及S04／S05／S07仅改变激活状态的拒绝预期被此次指令取代：已有有效工具定义与真实授权时应放行。第二层仍必须检查本次工具定义、真实模型调用、schema、参数和授权，S05／S07以模型消息或定义绑定失效验证最终检查不能绕过。数据分析和DeepAgents不参与这次清理。

## 2026-10-30 用户追加的无效流程清理

用户审查后要求清理 Basic QA 的不可达遗留节点、恒真条件和误导命名。此次补修替代 D6 对所有注册节点一概保留的做法：迁移前精确工作区的 BQA v2 入口已经绕过 resolve_skill，故删除该空节点及绘图中的虚假入口边；不是恢复旧 Skill 路由，也不修改封存图。实际使用的 classify_intent 检查点标识保持，绑定方法改名 prepare_session。prepare_model 直连 call_model，不再读写 needs_planning；原字段继续保留为旧检查点通道。call_model 完成决策后仍进入 answer_model，普通交流同样先经过模型决策，不增加入口直达终答的捷径。

移除未使用的旧默认提示词及参数、不可执行的旧 Skill 观测块；实际会话版提示内容、搜索／加载／执行与流式终答行为不变。以旧有效拓扑和序列化检查点覆盖真实暂停位置、恢复后的工具与模型调用数，不把删除后新建会话当作恢复证据。早于迁移基线、实际暂停于旧 resolve_skill 的 v1 运行不在此兼容结论内，未来部署仍须排空在途旧运行；原 7.1／7.4 缺口保持开放。


## 2026-10-30 工程审查修正

本次修正由用户明确授权，以 f609510 为起点，保持公开图ID、节点ID、state通道、Skill正文、提示内容、业务策略版本和模型参数。Python实现按职责改名：prepare_prompt、plan_tools、compose_answer、run_tools；旧节点字符串仅作为持久化协议保留。构图不包含批次执行控制器，工具定义身份及能力异常为本图叶子模块，投影、执行和观测分别消费，避免互相反向依赖。

规划阶段拆出重试、空回复补救和状态生成；终答拆出发布中止、一次超窗恢复及结果写回。沿用原调用次数、上下文计量与失败关闭策略。路由以 control_code 或 output_aborted 判断服务端控制结果，不再用展示字段 ui_visible 代替阶段完成语义。

旧 intent、needs_planning、user_id、skill_rules_content 和共享 data_model_started_at 暂不更名或删通道；bqa_active_skill 的旧版本迁移读点明确保留。节点和字段的退役不混入本次修正。按用户最新指令，本轮只进行 Python 编译，测试代码仅迁移引用和断言；所有行为和持久化结论等待 DeepSeek 测试，不将先前81项通过结果归于修正后版本。
