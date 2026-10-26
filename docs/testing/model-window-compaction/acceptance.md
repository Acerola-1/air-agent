# 模型窗口比例压缩修复验收

日期：2026-10-26，Asia/Shanghai。验证基线为dev分支、HEAD `5193d214d4bb5aed39db6349f5f975bd36f839a4` 的未提交工作区；保留其他任务修改。本轮修复已复现的四项缺陷，未提交、部署或归档。

## 修复结果

1. 基础问答和数据分析的超窗恢复耗尽转为终止异常，不再被外层三次通用重试重启；受控服务持续超窗时，两图均只有首次和一次恢复发送，摘要一次。
2. 溯源摘要器显式绑定有效输入边界及统一请求计量，不依赖SDK是否认识模型别名；缺失或不匹配profile时，已知超限请求在发送前被阻止。
3. 恢复从本次已准备的摘要、覆盖位置和工具治理结果继续，要求输入严格减小；失败终态携带应持久化的状态，不再原样重发已被拒绝的投影。
4. 输入配置绑定用户、企业、context_version、单调会话代次及业务图；切换立即失效、取消旧请求。实际Composer受控生命周期验证覆盖A→B→A、同账号会话更新、草稿、码点上限和组合输入。

回归用例已经纳入正式测试目录，临时复现脚本、重复日志、XML快照及此前两轮测试目录已清理。移除本次涉及的过时节点说明、脚手架注释代码和旧模型实测日期注释；保留有用途的配置迁移提示、OpenSpec计划和必要接口注释。

## 本轮实测

- Python相关回归：**295 passed**，3条既有节点参数类型警告。包含新增 `tests/unit_tests/test_compaction_boundaries.py`，并覆盖容量、摘要、显式节点、工具治理、输入限制、输出、逐调用守卫及溯源测试。
- PostgreSQL检查点：**1 passed**。本轮新建回环55487端口的独立空库，通过真实 `AsyncPostgresSaver` 执行首次整理、关闭重开、一次模型恢复及再次重开回读；摘要与覆盖位置正确、原始历史保留。模型与摘要输出为受控替身，不等于真实模型质量验收。
- 临时容器 `compaction-check-20261026` 使用已有PostgreSQL镜像、独立测试账号和空库，测试后停止并自动删除，检查无残留；未启动或改动共享数据库/卷。
- 前端契约：**94 passed**。新增行为测试执行真实Composer代码，校验迟到配置、取消、发送与草稿，不仅检查源码字符串。
- TypeScript、受影响文件Ruff及格式检查：通过。
- 前端设计静态审计：strict模式0错误、0警告。
- 生产构建：`npm --prefix frontend run build -- --webpack` 通过。原Turbopack构建超过6分钟无进展后仅终止本轮构建进程，改用兼容构建；不将原构建记为成功。仍有Next.js middleware约定弃用及框架Edge Runtime的 `process.cwd` 警告。
- OpenSpec严格结构校验：通过；它不代表尚未执行的真实系统验收通过。

## 复跑入口

在仓库根目录执行相关域回归：

```bash
.venv/bin/python -m pytest tests/unit_tests/test_model_capacity.py tests/unit_tests/test_context_guard.py tests/unit_tests/test_conversation_summarizer.py tests/unit_tests/test_context_recovery.py tests/unit_tests/test_compaction_boundaries.py tests/unit_tests/test_input_limits.py tests/unit_tests/test_turn_tool_governance.py tests/unit_tests/test_stream_overflow.py tests/unit_tests/test_business_graph_nodes.py tests/unit_tests/test_deep_explicit_skill.py tests/unit_tests/test_per_call_outbound_guards.py tests/unit_tests/tracing -q
npm --prefix frontend run test:contracts
frontend/node_modules/.bin/tsc --noEmit --incremental false -p frontend/tsconfig.json
npm --prefix frontend run build -- --webpack
openspec validate simplify-model-window-compaction-trigger --strict
```

PostgreSQL测试为 `tests/integration_tests/test_window_compaction_checkpoint.py`，仅接受显式的 `COMPACTION_TEST_DATABASE_URI`，要求回环地址及 `compaction_check` 前缀的自建库。无此变量时跳过，不读取正式DSN或自动连接共享库；操作者负责创建空资源并在结束后回收。单独运行时设置 `PYTHONPATH=src`。

## 规格覆盖与仍未完成项

- 窗口比例、旧参数停用和真实边界：容量/上下文单测及真实SDK middleware边界通过；百万窗口公式与主子容量隔离属于受控证据。
- 完整请求、工具结果突增、近期轮次收缩、覆盖一致性：相关节点/治理回归通过，另有真实PostgreSQL覆盖与恢复回读；尚未完成所有工具突增与多水位组合的完整持久化运行矩阵。
- 一次恢复、不重放工具、无进展停止、守卫和输出：相关受控回归通过；尚未采集当前服务商真实超窗错误样本。
- 输入配置、码点、迟到结果、草稿和组合输入：前端行为与后端测试通过；不是实际浏览器、系统输入法或物理剪贴板证明。
- 独立摘要窗口、分块和失败不推进：现有相关回归通过；真实百万模型长会话及其摘要成本、耗时、事实保留尚未验证。

真实环境检查时，当前IAM和业务后端未就绪；浏览器只能到达登录页，不能完成当前修复的登录后页面、窄屏、实际企业切换和物理输入验证。共享容器均保持原有停止状态，没有为测试重启正式栈。OpenSpec对应未完成项保留未勾选；不能据此报告产品已完成全部验收或已经上线。

## 单独提交前复核（2026-10-27）

按用户授权，仅暂存本次窗口比例压缩、输入限制、修复及配套文档；同文件中的其他任务改动保留在工作区。从暂存区单独导出源码，使用已有依赖重新运行相同相关域：Python **215 passed**（1条既有节点参数类型警告），前端契约 **76 passed**；TypeScript、受影响实现的Ruff和OpenSpec严格校验通过，暂存差异无空白错误。此次数量对应独立提交快照，不包含其他任务新增的用例；不替换上文完整工作区的历史结果。

未重复启动数据库、执行真实模型或浏览器验收，生产构建沿用上文已记录的完整工作区证据，不把它改记为本次独立快照的构建结果。本次仅本地提交，不push、部署或归档；未完成项继续保留。
