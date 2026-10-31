# I2 Agent接入共享IAM

标准OpenSpec apply实施前，除生成的contextFiles外还必须读取本包[共同契约](contract.md)、[公开验收矩阵](acceptance.md)和[冻结基线](baseline.json)。进度统一维护tasks.md；不使用goal模式、不依赖另一个长运行目标管理器。匿名实施完整说明见[实施入口](../../../docs/handoffs/agent-remote-iam-apply.md)。

## Why
将Agent当前的本地身份PG裁决替换为可显式启用的IAM远程裁决，同时保留已验收的业务隔离、台账、Skill/MCP和观测。原抽取蓝图的“同表直接接管”假设不符合当前Java/Python结构，本包改为先完成隔离环境可验收的消费方适配，不把真实存量接管混进一次交付。

## What Changes
- 建立严格HTTP客户端及人类/执行引用适配，分开IAM与业务API路由；local保留原行为，remote失败关闭。
- 通过服务端票据摘要绑定桥接当前rt-v1与真实verification_ref，不改IAM票据格式、不发明按session_id核验。
- 接入原Auth、同步/异步外呼、重试、主/辅助模型、DeepAgents子任务、MCP以及受保护业务回读。
- 保留业务GUC/RLS/FK，以同UUID的隔离业务投影完成实证；输出历史映射dry-run和IAM业务权限注册清单，不迁真实用户、不物理拆库。
- 两个现有前端保留业务页面，接入服务端会话/SSO；身份CRUD归IAM，业务CRUD不搬。
- 分阶段检查点和固定正反例，范围与证据等级在实施前公开，不针对匿名模型调整要求。

## Capabilities
### New Capabilities
- agent-remote-iam：Agent消费远程IAM、执行复查、业务隔离兼容和隔离切换验收。

### Modified Capabilities
无。本包新增远程部署能力并继承既有台账、反馈、观测和运行性能约束，不用新规格覆盖原有未完成项。

## Impact
主要涉及src/common/identity、admin/api/app/services/identity.py和deps.py、auth路由、frontend/lib/admin-api.ts、两个前端的会话接线及相应测试。IAM与RAG项目只读。上游固定8da8ba1，Agent产品基线319dab4；实际启动漂移按baseline.json处理。交付状态“待独立验收”；正式身份切换、IAM业务权限发布、I4拆库另行放行。
