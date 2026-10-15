# 授权能力目录与用户选择

## Why

当前Agent能自动匹配Skill、调用工具并记录实际观测，但用户没有查看和选择能力的入口；部分图的工具集还固定在导入时，不能可靠反映当前企业授权。需要把用户选择、服务端许可和实际执行串成可验证链路，为后续能力分析提供可信数据。

## What Changes

- 增加按当前企业/成员/Graph过滤的Skill与MCP工具目录，明确可用、未配置及暂不可用；不返回凭据、内部路径或完整规则。
- 聊天输入区支持Skill自动/显式单选、MCP自动/指定工具集合（空集合表示不用MCP）；默认保持现有自动体验，不新增管理后台或任意MCP接入。
- 提交时将选择校验、绑定并冻结到可信submission/attempt；原生直连、BFF、重试、重新生成与恢复共享约束，客户端metadata不能覆盖受理快照。
- 六图均执行同一选择边界；data-analysis保持菜单确定性，显式选择只能与当前菜单映射一致。工具披露和实际执行都受限；图导入不做远端发现，后续授权发现的工具不必重建图即可使用。
- 复用Change5观测，区分用户意图、受理计划和实际Skill/工具使用；被选择但没执行不能计使用量。契约扩展、迁移、RLS、删除和版本回读随实现交付。
- 与独立IAM并行：继续消费现有可信RuntimeIdentity/会话接口，将身份解析封装为可替换依赖，不在本change切IAM、改用户系统或实现entitlement。
- **BREAKING（受控安全收紧）**：声明未知、过期、不匹配或越权选择的请求在模型/工具调用前拒绝；同幂等键不同选择返回409。旧客户端未声明选择仍按自动模式受理，旧记录标not_collected。

## Capabilities

### New Capabilities

- `agent-authorized-capability-catalog`：有界、按企业/成员/Graph授权的能力目录、稳定身份及可用状态。
- `agent-request-capability-selection`：用户选择UI、服务端受理冻结、六图披露/执行、恢复与回读的完整生命周期。

### Modified Capabilities

- `agent-capability-observability`：为既有Skill阶段补充用户/自动选择来源与受理选择关联，保持实际执行、工具重试、隐私和计费去重语义。

## Impact

产品基线为已推送的3/5组合提交`2d6f7c8`；完整规划包提交会在handoff标明。3/5已归档，Change3最后剪贴板文本实机项由用户豁免，不重新作为本项阻塞。

影响`frontend`聊天输入/状态/BFF、运行时Skill加载和MCP作用域工厂、显式图及DeepAgents工具接线、admin台账受理/迁移、事件契约与观测。复用六图、身份、台账和3/5，保持依赖锁与实际模型配置。匿名运行05领取本项；实施工作区/端口/验收矩阵/只读边界见handoff与acceptance。
