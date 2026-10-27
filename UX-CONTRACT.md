# UX Contract

## Product context

中文空气质量聊天与分析工作台，日期遵循业务资料的本地日期／小时；本次没有新增时间选择器。可访问性目标 WCAG 2.2 AA。视觉约定见 DESIGN.md，实施范围见 `openspec/changes/fix-langgraph-streaming-context-governance/`。

## Business-context sources

| 范围 | 来源 | 类型 | 日期 |
|---|---|---|---|
| 原文回读／生命周期 | tool-artifact-lifecycle/spec.md | 本轮已确认规格 | 2026-10-11 |
| 消息与取消 | langgraph-chat-streaming/spec.md | 本轮已确认规格 | 2026-10-11 |
| 历史整理与容量 | model-context-governance/spec.md | 本轮已确认规格 | 2026-10-11 |
| 计算完整性／资源 | bounded-data-computation/spec.md | 本轮已确认规格 | 2026-10-11 |

上述路径均位于本轮变更 specs 下。保留期与删除决定由这些规格负责；本文只记录可观察交互。

## Visual contract

CSS 变量／Tailwind 运行时为 tokens 所有者，DESIGN.md 记录语义与映射，保留既有明暗主题与中文语气。当前聊天流程是新增状态和资料回读的唯一所有者，不新增全局通知系统。

## Canonical UI Map

| 能力 | 所有者 | 约定 | 验证 |
|---|---|---|---|
| 按钮与说明 | components/ui/button、tooltip | 复用 shared primitive | 类型检查／实际键盘 |
| 正文输入 | ChatPanel Composer | Enter 提交；输入法组合期间不提交；读取受信字符上限后才允许发送，超限保留草稿 | 实际浏览器 |
| 输入长度提示 | ChatPanel Composer | 显示 Unicode 码点计数／服务端上限；超限或配置未就绪禁止发送；按用户、企业、会话代次及图丢弃旧配置 | 契约／实际浏览器 |
| 滚动 | globals.css、ChatPanel 消息区 | 独立阅读滚动条；上翻暂停跟随 | 实际浏览器 |
| 整理反馈 | ProcessingNotice | 开始／完成／失败，共享组件 | 流事件回归／实际运行 |
| 归档资料 | ArchiveReader | 显式加载、上一段／下一段，8000 字符每页 | API 授权／实际回读 |
| 会话操作 | chat-store、ThreadList | generation 隔离，删除失败不移除列表 | 回归／实际删除测试线程 |
| 图／模式选择 | GraphSwitcher、ModeSwitcher | 沿用现有共享控制 | 浏览器 |

本次没有表格选择、日期输入或新的下拉组件，不能为这些未使用能力虚构 canonical owner。

## Dataset navigation

会话列表延续后端按用户和图搜索的 50 项分页上限；本次不改变它的导航产品。原文资料采用显式上一段／下一段，不预加载全量文件。会话 ID／资料 ID 是内部导航标识，原始资料、诊断和运行状态不放 URL 可分享参数。

## Flow ledger

| 操作 | 等待 | 成功 | 失败与恢复 |
|---|---|---|---|
| 提问 | 显示处理中与停止按钮，保存已发送文本 | 唯一终答；刷新后仍为同一终答 | 已确认内容保留，重新发送提供新的一轮请求 |
| 停止 | 发送服务端取消，结束界面等待 | 读取已确认状态 | 显示停止未确认或刷新失败，可重开会话 |
| 历史整理 | 状态条进行中 | 用量变化／保留轮数，不显示内部摘要 | 未覆盖历史保留；不宣称已成功整理 |
| 工具失败 | 卡片显示可读失败 | 无错误 JSON 正文 | 用户主动查看诊断／调整条件 |
| 原文回读 | 按钮禁用、正在读取 | 当前页字符范围／总量 | 明确过期、无权限或暂时失败；可重试 |
| 删除会话 | 沿用现有产品确认 | 关联归档一起清理，更新列表 | 会话保留，显示失败，不把失败当成功 |

## Async and recovery

当前运行使用线程、generation 与 run 身份，旧结果不能修改新会话。SSE error 视为失败。取消与切换使用服务端 cancel 和断流取消；流内仅消费已确认终答和公开状态，messages 原始增量不作为正文。

## 正文流式状态（agent-output-stream-v1）

溯源的内部本轮意图提交由 `chat-store.parseMessages` 在实时与历史统一过滤，不显示为用户工具操作，也不作为正文；业务数据工具仍保留原有可回看记录。来源：`openspec/changes/add-tracing-conversation-routing/`。

- 正文只消费服务端 `final_output_start/delta/done/aborted` 生命周期；`messages` 原始增量、工具参数、内部摘要和 DeepAgents 专员草稿永不作正文。
- 处理中显示独立状态条；收到已校验 delta 后正文立即追加，标“正在生成 · 内容尚未最终确认”。`done` 才表示完整终答；`aborted` 或流结束但无 done 时保留已显示内容并标“未完成”，不开放反馈/复制为完整答案。已有正文后失败不得另开完成输出；仅零正文的失败允许由 single_shot 固定失败文案替代。
- 客户端按 `thread_id/run_id/output_id` 隔离输出，按 `seq` 连续追加；重复帧忽略，冲突/缺口回读可信快照。DeepAgents 按逐节检查点续发，不重发已确认章节；传输重放不得重复追加正文。
- 旧 done-only 事件仍显示完整答案，但计时与来源标为 legacy，不计新协议流式。
- 停止待确认、刷新、切换企业与 A→B→A 的旧输出不得复活或写入当前正文。

普通说明生成中遇到尚未公开的具体条款时，服务端可停止模型并追加已核验原文或明确的缺依据拒答，以受限答复结束；不得公开该模型断言，不把拒答算成业务问题已解决。未知引用、已公开内容校验冲突、撤权、取消、超时仍按未完成处理。来源：`openspec/changes/unify-user-visible-answer-streaming/specs/agent-visible-output-streaming/spec.md`。

数据分析的“分析项目”由 `AnalysisMenuSelector` 使用原生 select 提供，菜单定义与 `src/data_analysis/menu_skill_mapping.py` 同步并以契约回归校验。选项弹层由系统负责；选择传入本轮 `configurable.menu_name`，运行及停止待确认时锁定。切换项目重置显式技能，技能选择列表只展示该菜单映射的技能。会话搜索仅过滤已加载的最多 50 条，显示标题与搜索标题共用 `threadTitle`，切图或企业时清除旧搜索词。

## Accessibility and layout

详情切换为原生 button，aria-expanded 与视图一致。公开状态用 live region，错误说明用 alert。只在接近底部时跟随回答增长，用户上翻时保留阅读位置。输入法保护沿用现有 Composer，动态区域不改动输入框宽度。归档原文有局部滚动容器，保持滚动条可见并可操作。

## Verification policy

自动化回归检查中间帧、旧回调、错误事件和权限。真实浏览器、LangGraph API、PG 验收须另留证据；模拟通过不能替代真实运行。用户名可写本地验收说明，密码与 AUTH_SECRET 不进入文档、快照或日志。

## Context measurement

未知模型的输入近似量显示 ≈，提示明确说明系统规则、聊天与工具结果、工具定义及请求开销。回答完成后仪表优先显示最近主模型实际输入，准备阶段显示估算；提供商最近实际 usage 独立展示，估算不是聊天历史字符数。普通城市查询不能因字节上界被错误拒绝。

## 企业切换与停止确认

业务来源：`openspec/changes/add-tenant-identity-access/specs/unified-revocable-session/spec.md`。

企业切换由 TenantBadge 管理，复用 shared Dialog / Button，与 GraphSwitcher 使用相同弹层语义；原生 select 保留系统键盘和选择行为。活动回答或停止待确认时先打开应用内确认框，初始焦点在“取消切换”，Escape 退出后焦点回到企业选择。后台确认等待期间禁用重复操作。

chat-store 的 stopPending 独立于本地流结束：未知状态保留运行引用和停止重试入口，确认前不接受新问题，也不允许第二次切换绕过停止。缺本次 run_id 时，上一轮终态不算本次停止；迟到元数据继续关联原运行。上下文冲突清除旧企业视图，旧异步确认不能覆盖新企业状态。

验证：聊天契约覆盖迟到ID、旧终态和重复操作；真实浏览器验证确认框、取消按钮焦点、停止后企业切换与列表清空；签发及资源授权另以真实HTTP/PG复验。

## 回答反馈、采集与窄屏（2026-10-14）

业务来源：`openspec/changes/archive/2026-10-15-add-chat-feedback-interactions/`、`openspec/changes/archive/2026-10-15-complete-agent-observability/`。评价所有者为`components/chat/feedback/FeedbackBar`，异步/队列所有者为`lib/feedback/client.ts`，实际DOM计时所有者为`ChatPanel`。点赞/点踩针对服务端确认的回答版本；草稿不改变已保存值，保存等待禁用，失败保留意见，冲突先回读修订。旧企业/用户/context_version/epoch的结果不得更新当前视图。

成功复制先等待浏览器剪贴板Promise，再显示提示并采集无正文事件；拒绝时显示可恢复失败，不记复制成功。系统剪贴板物理文本一致性未实测；2026-10-15用户明确豁免此最后抽查，归档关闭，不将其伪报实机通过。正文可见使用DOM布局与绘制；history、工具和推理不算首正文。平台run通过本人可信提交映射回读为attempt，未确认不猜关联。

停止使用本次run的权威状态；`interrupted`代表本次执行已停止，可解除stopPending，但用户停止意图、人工暂停/可恢复和业务cancelled仍独立。网络断开不等于取消。

小于md时会话列表由首页的共享Dialog承载，含标题/说明、焦点圈定、Escape关闭、触发器焦点恢复；选择会话关闭弹窗。md及以上保留侧栏。窄屏企业选择不隐藏，用户名称可隐藏腾出空间，反馈随正文自然换行。390×844与原视口已实看；原生select保留系统键盘语义。

## 能力与工具选择（Skill/MCP）（2026-10-15）

业务来源：`openspec/changes/archive/2026-10-15-add-skill-tool-selection/specs/**`。能力选择所有者为 `components/chat/CapabilitySelector.tsx`，状态所有者为 `lib/capability-store.ts`。

- **输入区顶栏控制**：在 Composer 顶栏常驻胶囊触发器，显示当前技能与 MCP 工具选择摘要（例如：“技能: 自动匹配 · 工具: 自动”或“技能: 基础查询 · 工具: 2项 / 不使用”）。
- **模态弹窗与切换**：点击打开共享 Dialog，包含“技能选择”与“MCP 工具”双 Tab。
  - **技能选择**：单选（Radio），首项为“自动匹配（默认推荐）”，其余为当前图可用的本地技能，按名称排序，附带材料版本及依赖工具指示。
  - **MCP 工具选择**：支持“自动（全部可用工具）”和“白名单模式”；白名单模式下勾选具体已授权工具，若勾选集合为空则明确表示“显式禁用 MCP 工具（零网络调用）”。针对当前不可用/故障服务提供告警徽标与禁用保护。
- **重置与草稿生命周期**：提供“恢复默认”操作。切换图（`switchGraph`）或租户时，自动清空草稿并递增 epoch，迟到的旧目录响应静默丢弃（防 A→B→A 时序竞争）。
- **运行锁定与不可变**：流式生成或等待停止期间禁用触发按钮，悬停提示锁定状态，保证已受理的选择在执行期间不可变。
- **重试与恢复**：`retry` 继承当前尝试的选择计划；`regenerate` 默认继承或使用显式新草稿；`resume` 严禁更改选择。
- **响应式与无障碍**：弹窗限高 `85vh` 并自带滚动；390×844 窄屏与桌面自适应；完整键盘焦点导航（Tab/Shift+Tab）、Escape 关闭、明确的 aria-label 以及中文输入法组合键保护。

## 业务入口与授权（2026-10-19）

业务来源：`openspec/changes/simplify-agent-business-authorization/specs/agent-business-access/spec.md`。入口所有者为 `components/chat/GraphSwitcher.tsx`，过滤依据是 `/api/auth/me` 返回的 IAM 实时权限，不在前端推断或特判 root。

- **按实时可用业务生成**：只展示当前企业已开通且本人已获授的业务；不可用业务不出现在列表。无任何可用业务时显示禁用的“无可用业务”，并说明原因，不展示可启动入口。
- **两类失败分开表达**：业务无权（IAM 403/未授权）与外部连接故障（目录中的 `not_configured`/`auth_unavailable`/`temporarily_unavailable`）使用不同文案；不把连接故障写成业务未授权。
- **切换与隔离**：切换业务清空当前会话并按 user/tenant/context_version/epoch 丢弃迟到响应；A→B→A 同样隔离。运行中与 stopPending 时锁定切换。
- **旧授权页面退役**：企业“MCP 授权”页面与 `/mcp-bindings` 接口已删除，返回 404/405；连接状态如需要只作为平台部署说明展示，不接受浏览器写入地址或凭据。

## Change4协调者复验补充（2026-10-15）

弹窗内修改先保存在局部草稿，只有“完成”才提交；Escape、关闭和取消均丢弃本次编辑并恢复触发器焦点。单选支持方向键/Home/End，工具最多20项；目录分页须保持同一图、scope、epoch与目录版本，加载/错误期间不能提交。身份的用户、企业或context_version变化均清空视图与能力草稿，停止待确认期间选择器仍锁定。

“最近已受理”始终来自服务端本人提交的UUID与摘要，与下一轮意图分开显示；刷新后打开历史会话重新回读，无法确认时不拼造快照。加载规则或实际工具调用另看回答与工具记录。

Composer以composition生命周期、native isComposing和Safari keyCode=229保护中文选词Enter；Shift+Enter保留换行。真实事件处理测试通过，真实系统输入法候选窗仍待物理抽查。桌面和390×844、焦点/键盘、取消草稿、故障恢复、真实回答、点赞保存、历史与企业切换已实测，证据在docs/reviews/evidence/takeover-05-final/。

停止先记录意图，实际解除锁定仍依据本次平台运行的权威终态。首条metadata丢失时通过BFF覆盖写入的本次提交键匹配运行，不能把上一轮或其他提交的终态当成本次；未确认继续锁定并允许再次停止。停止和删除请求透传真实Origin供管理端校验，不伪装成固定开发地址。


## 远程IAM接入管理界面（2026-10-17）

身份与企业切换由admin/web的SessionProvider统一拥有；刷新先取得权威me，再登记声明并加载菜单，迟到刷新不能覆盖新企业。两个前端分别使用应用cookie/state，不按端口隔离。SSO发起显示等待且禁止重复点击。管理外壳复用AppSidebar/TenantSwitcher：md以上侧栏，390px下导航与内容上下排列，企业选择仍可操作。保持原tokens和中文文案，不新建一套导航或通知系统。


## 远程IAM聊天会话与退出（2026-10-18）

聊天会话由 `frontend/lib/session-client.ts` 统一拥有。刷新、切企业及退出递增sessionEpoch，A→B→A以及退出换账号后的旧结果不得回写。401清除上下文并回登录，503清除不可信上下文、显示可重试错误，不伪装成成功退出。退出等待禁止重复操作；只有服务端确认204/401才清理并导航，失败保留当前界面，显示“退出尚未完成，请重试”和重试入口。ChatPanel展示同一会话状态，不另设成功标准；BFF必须检查退出状态码。退出只清当前前端cookie，不影响另一前端独立应用会话。真实故障退出/恢复重试已操作，完整双端受控迟到响应浏览器矩阵尚未关闭。

## 知识库引用与原文

来源入口只接受服务端登记的引用 ID。CitationViewer 的弹窗由 shared Dialog 管理焦点、Tab 与 Escape；关闭恢复入口焦点。每次打开、分页、重试都重新核权，使用 no-store；显示当前字符范围。用户、会话、企业、context_version、图或线程变化立即关闭并取消请求，A→B→A 也不复用旧结果。401/403/404/409/410 分别提示登录、无权、不存在、上下文变化、来源失效；503/504 与网络错误提供显式重试，不自动重发。

## Agent 运营后台（2026-10-20，Change 6）

业务来源：`openspec/changes/add-agent-admin-console/**`。页面所有者、共享组件与查询客户端见 DESIGN.md 同名章节；API 协议见该 change 的 `api-contract.md`。

- **权限分区**：统计聚合（`agent:metrics:read`）、企业运行/可识别用户/反馈明细（`agent:operations:read`）、正文（`agent:content:read`）、反馈写（`agent:feedback:manage`）分别判断。菜单隐藏不是授权；直接 API 访问同样返回 401/403/404/409/422/503。
- **窗口**：总览/用户/运行/反馈/能力/性能/成本共用 `today|last_7_days|last_30_days|custom`；自定义日期字段标注“包含结束日”，转 Asia/Shanghai 半开区间；结束日为今天时截到请求时刻。缺日期不静默回退，服务端明确 422。
- **分页与截止**：列表使用游标（绑定企业、context_version、过滤、排序与 as_of），翻页沿用原数据截止；跨企业/改过滤/坏游标明确 400。聚合由服务端全窗口计算，不把当前页相加或前 200 项当全集。
- **异步隔离**：用户、企业、context_version、权限、过滤或请求代次变化时清空旧数据并取消旧请求（AbortController）；A→B→A 不复用旧结果。普通处理 `409 revision_conflict` / `feedback_changed` 保留草稿并提示回读，不触发“企业切换”全局提示；只有会话 `context_changed` 才刷新会话。
- **反馈处理**：抽屉内保存草稿；同一次提交复用同一 `operation_id`，载荷变化生成新 ID；处理完成必须填写说明（≤1000 字符），有说明需正文权限。按钮按当前状态禁用非法动作；用户新负面修订显示“需要复核”，不自动改写用户评价。
- **正文**：原问题/回答/意见/说明按需分页（≤8000 字符），回答快照缺失显示未采集/采集失败/过期/受控截断，不从 Trace、checkpoint 或浏览器旧缓存补造。关闭阅读器/对话框恢复触发按钮焦点。
- **可访问与窄屏**：原生 button/select/input，表格有 caption 与数据表替代；390×844 导航在内容之上、企业选择常驻，表格横向滚动。中文 IME 组合期间搜索不提前提交，300ms 后生效，清除立即生效。

## 需求洞察（2026-10-21，Change 7）

需求洞察复用运营后台的QueryToolbar/DataTable/TrendBars与useOperationQuery。`operations/insights/layout.tsx`按现有useScopeKey及路由目标重建页面，清除旧企业的改名/合并/拆分草稿、选择项和操作反馈。拆分预览返回后再次确认页面仍有效；离开或切企业后不继续发送旧拆分提交。业务分类、来源正文及作业管理仍由服务端独立授权；本轮仅代码/类型验证，真实浏览器完整流程与系统IME在后续联合验收中验证。

## 登录过期恢复（2026-10-23）

统一身份的顶层授权导航在会话失效时，经原客户端与精确回调校验返回 `login_required/state`；Agent 后端仍比对发起浏览器的 HttpOnly state cookie，未通过不兑换、不建会话。聊天和管理端回调统一清理地址里的 ticket/state，并回到本端账号密码表单。过期、回调未完成和服务暂不可用分别使用固定提示，提示出现后聚焦账号输入框；不自动再次发起 SSO，避免循环。成功路径保留原认证和选企业步骤。普通 API 401、权限 403 与依赖 503 不被中央恢复重定向改写。

所有者：IAM `BrowserSessionInterceptor` / SSO 注册校验、Agent `iam_remote.sso_callback`、两端 `api/auth/iam/callback` 与现有登录表单。验收见 `docs/testing/sso-session-recovery-20261023/`；未改变会话期限、账号源或权限。
