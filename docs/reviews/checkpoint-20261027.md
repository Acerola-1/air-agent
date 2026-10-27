# basic QA 改造前检查点（2026-10-27）

本次按用户要求保存现有进度并同步 GitHub `main`，尚未实施 basic QA 的多轮路由或 Skill 选择改造。检查点包含已有的溯源会话与数据证据、登录恢复、Langfuse 观测、性能测试流程，以及数据分析图暂时封存的说明。

## 开发范围

- 后续开发聚焦 `basic-qa` 与 `intelligent-tracing`。
- `data-analysis` 暂时封存，保留代码、注册、约束和必要兼容回归；不表示运行入口已下线。
- 当前运行态以 [正式环境说明](../CURRENT-RUNTIME.md) 为准，历史地址及探针不作为启动依据。

## 本次定向检查

- Python：溯源单元测试及登录恢复、票据绑定、并发投影、性能监督与观测恢复等相关测试，首轮 276 项通过、2 项因无法读取 macOS `vm.swapusage` 失败；获得该系统访问后，原两项复跑均通过。不同测试共 278 项通过，保留 2 条既有图节点配置类型警告。
- 前端：`npm --prefix frontend run test:contracts`，94 项通过。
- Ruff：62 个新增或修改的 Python 源码/测试文件检查通过。
- `git diff --check` 通过。

以上是定向回归，未在本次重新执行完整构建、真实浏览器、正式业务数据/MCP、模型负载、数据库升级或恢复验收；既有报告的局限和未完成项保持原状态。

## 公开提交与本地证据

公开提交包含可复用源码、测试、计划、流程及以下验收摘要：

- [Langfuse 用户与会话](../testing/langfuse-users-sessions-20261022/report.md)
- [登录过期恢复](../testing/sso-session-recovery-20261023/acceptance.md)
- [溯源会话基线](../testing/tracing-conversation-20261023/baseline.md)与[验收](../testing/tracing-conversation-20261023/acceptance.md)
- [溯源数据证据验收](../testing/tracing-data-evidence-20261025/acceptance.md)

这些摘要引用的原始日志、截图、探针配置与运行结果保留在本地，不随公开仓库提交；公开版本中的相应相对路径可能不可访问。两份未确认可公开的业务报告样例及生成式设计审计输出也不纳入本次公开提交。私有环境文件、凭据与会话不提交。

工作区原有的 85 项文档/证据删除在提交前已从原 HEAD `8a4ccc3bca53d8592bcec5e5cf379b8479a19347` 另存到本地 `.local/checkpoints/20261027-before-basic-qa/deleted-from-head/`，逐文件 SHA-256 记录在同目录上级清单中；Git 历史亦保留原版本。本次不将删除解释为已验收或历史失败已消除。
