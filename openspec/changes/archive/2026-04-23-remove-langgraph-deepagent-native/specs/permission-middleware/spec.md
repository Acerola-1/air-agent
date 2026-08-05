## 新增需求

### 需求：PermissionMiddleware 在 Agent 执行前拦截请求
PermissionMiddleware SHALL 在 DeepAgent 的 `abefore_agent` 钩子中执行，在任何模型或工具调用之前执行权限校验。它 MUST 支持三种分类路径：`no_check`（完全跳过）、`need_check`（简化 LLM 参数提取）和 `uncertain`（带上下文补全的完整 LLM 判断）。

#### 场景：规则引擎判定无需权限校验
- **WHEN** 用户问题包含通用知识关键词且无时间/站点引用
- **THEN** 中间件返回 None，允许 DeepAgent 继续执行，不进行任何 LLM 或 MCP 调用

#### 场景：规则引擎确认需要权限校验
- **WHEN** 用户问题包含时间或站点关键词
- **THEN** 中间件调用轻量级 LLM 提取权限参数，然后调用 `permission_vaild` MCP 工具

#### 场景：不确定分类需要完整 LLM 判断
- **WHEN** 问题较短（≤15 字符）或内容模糊
- **THEN** 中间件带完整上下文（对话历史 + 当前时间 + 用户 ID）调用 LLM，判断是否需要权限校验并提取参数

### 需求：PermissionMiddleware 支持登录/升级中断
当权限校验失败且类型为 `need_login` 或 `need_permission` 时，PermissionMiddleware SHALL 调用 `langgraph.types.interrupt()` 暂停执行，等待用户操作（登录或升级）。恢复后，它 MUST 使用新的用户 ID 重新进行权限校验。

#### 场景：访客用户触发 need_login 中断
- **WHEN** 访客用户查询时间相关数据，权限校验返回 `need_login`
- **THEN** 中间件调用 `interrupt({"type": "need_login", "message": "..."})`，前端显示登录提示

#### 场景：用户使用已登录的用户 ID 恢复
- **WHEN** 用户完成登录并发送 `Command(resume={"user_id": "xxx"})`
- **THEN** 中间件使用新的用户 ID 重新运行权限校验，若有效则继续执行

### 需求：PermissionMiddleware 在首次请求时初始化 MCP 工具
PermissionMiddleware SHALL 在 `abefore_agent` 开始时调用 `await ensure_mcp_tools()` 来延迟初始化 MCP 工具列表。这 MUST 替代模块级的 `asyncio.run()` 初始化，以避免事件循环冲突。

#### 场景：首次请求触发 MCP 初始化
- **WHEN** DeepAgent 收到首次请求且 MCP 工具尚未初始化
- **THEN** 中间件异步从 MCP 服务器获取工具列表，填充模块级变量，然后继续进行权限校验

#### 场景：后续请求跳过初始化
- **WHEN** MCP 工具已经初始化
- **THEN** 中间件跳过初始化，直接继续进行权限校验

### 需求：PermissionMiddleware 通过 stream_writer 推送进度消息
PermissionMiddleware SHALL 使用 `runtime.stream_writer` 在权限校验期间推送进度消息，匹配现有格式 `{"type": "progress", "message": "权限校验..."}`。

#### 场景：权限校验进度通知
- **WHEN** 中间件开始权限校验
- **THEN** stream_writer 发送 `{"type": "progress", "message": "权限校验..."}`

#### 场景：权限校验失败通知
- **WHEN** 权限校验失败
- **THEN** stream_writer 发送 `{"node": "permission_eval", "type": "permission_error", "message": error_message}`