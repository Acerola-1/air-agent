## 新增需求

### 需求：ModeRoutingMiddleware 将 module/mode 注入 Agent 上下文
ModeRoutingMiddleware SHALL 使用 `@dynamic_prompt` 装饰器将 module 和 mode 参数动态注入 DeepAgent 系统提示词。它 MUST 从 `runtime.config["configurable"]` 中读取 `mode` 和 `module`，并格式化为 `[module:XXX][mode:XXX]` 路由标记。

#### 场景：fast 模式配合 basic 模块
- **WHEN** configurable 参数包含 `mode="fast"` 和 `module="basic"`
- **THEN** 中间件将 `[module:basic][mode:fast]` 注入提示词上下文，Main Agent 路由到 basic-agent SubAgent 并附带 fast 模式指令

#### 场景：expert 模式配合 analysis 模块
- **WHEN** configurable 参数包含 `mode="expert"` 和 `module="analysis"`
- **THEN** 中间件注入 `[module:analysis][mode:expert]`，Main Agent 路由到 analysis-agent SubAgent 并附带 expert 模式指令

#### 场景：未指定模块，Main Agent 自动路由
- **WHEN** configurable 参数包含 `module=None` 或未提供 module
- **THEN** 中间件注入 `[module:auto][mode:fast]`，Main Agent 自行判断选择合适的 SubAgent

### 需求：ModeRoutingMiddleware 默认值
当 configurable 参数中未指定 `mode` 时，ModeRoutingMiddleware SHALL 默认为 `"fast"`。当未指定 `module` 时，SHALL 使用 `"auto"` 以允许 Main Agent 智能路由。

#### 场景：默认 mode 和 module
- **WHEN** 未提供任何 configurable 参数
- **THEN** 中间件使用默认值 `mode="fast"` 和 `module="auto"`，格式化为 `[module:auto][mode:fast]`