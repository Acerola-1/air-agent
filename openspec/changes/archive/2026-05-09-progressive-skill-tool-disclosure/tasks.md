## 1. Skill 元数据与状态

- [x] 1.1 扩展 `src/common/skill_router.py` 元数据模型和解析以包含 `allowed_tools`。
- [x] 1.2 添加或暴露所选 Skill 元数据的状态模式，包括名称、路径、描述、允许的工具和分数。
- [x] 1.3 添加 Skill 元数据解析的单元测试，包括空格分隔的 `allowed-tools` 和缺失的 `allowed-tools`。

## 2. Skill 发现工具

- [x] 2.1 实现使用 `SkillSemanticRouter` 并返回结构化匹配结果的图本地 `find_skill` Tool 工厂。
- [x] 2.2 确保 `find_skill` 使用 `Command(update=...)` 将匹配的 Skill 元数据写入 Agent 状态。
- [x] 2.3 使用存根路由器添加匹配和未匹配 `find_skill` 行为的单元测试。

## 3. 动态工具披露中间件

- [x] 3.1 实现通过 `middleware.tools` 预注册所有图业务工具的中间件。
- [x] 3.2 实现根据所选 Skill 的 allowed-tools 从 `request.tools` 过滤模型可见业务工具的中间件。
- [x] 3.3 在过滤期间保留 `find_skill`、显式配置的系统工具和 DeepAgents 内置工具。
- [x] 3.4 添加选择前、选择后以及未列出业务工具的过滤单元测试。

## 4. Basic QA 试点集成

- [x] 4.1 将 `basic-qa` 配置为声明 `find_skill` 为业务入口工具，并通过新的注册中间件注册现有业务工具。
- [x] 4.2 更新 `basic-qa` 系统提示词，使业务空气质量请求必须在使用业务数据工具前调用 `find_skill`。
- [x] 4.3 保留现有的通用中间件行为，用于权限检查、时间上下文、遗留图表数据、问题扩展和模式路由。

## 5. 验证

- [x] 5.1 运行 Skill 路由、Tool 发现和中间件过滤的针对性单元测试。
- [x] 5.2 运行 `python -m compileall src tests` 或仓库的等效编译检查。
- [x] 5.3 如果依赖项和外部服务要求允许，运行 `make test`。
