# Change6/7 联合验证与真实案例验收

## Why

用户于2026-10-21明确把业务代码交付与真实服务/真实案例/规模性能验证分开。Change6/7的功能先通过合成数据和隔离PG回归保存到dev，后续用独立计划验证实际使用效果，避免把未跑证据误报通过，也避免每次开发都提前压测。

## What Changes

- 建立三个独立执行阶段：A为隔离环境中的真实服务全链与恢复演练（合成输入）；B为用户批准的脱敏真实案例质量验证；C为明确启动后的隔离规模性能验证。
- 固定每阶段的版本、输入、预期、指标、资源所有权、原始证据和报告模板；失败指向代码修复，不通过改断言、删样本或改分母过关。
- 收录现有Change6/7尚缺的证据映射；业务阶段可提交不等于生产已启用，原有未完成项不改为通过。
- 与`docs/testing/basic-qa-performance-regression.md`共用隔离资源、固定题库、发送器和采集器；同一批合法请求同时核对后台统计与需求归类。执行方式见`docs/handoffs/console-insights-performance-unified-goal.md`，不另建第二套性能标准。
- 当前只交计划，不启动A/B/C、不访问用户历史、不调用真实IAM/模型、不压测、不部署。

## Capabilities

### New Capabilities

无。本项是现有功能的验收工具、固定测试输入和证据交付，不新增业务能力或另建评测平台；使用`skip_specs: true`。

### Modified Capabilities

无。继承Change6/7已定义的授权、计数、保留期、分类与性能要求，不修改产品协议，不把未验证项从要求中删除。

## Impact

- 计划执行时新增本仓库专用验收脚本、合成输入清单、脱敏案例标签和小型验收报告；运行证据进入忽略提交的`docs/reviews/evidence/run/`新目录。
- A涉及现有`deployment/run_separated_local.py`、Java IAM接口、官方LangGraph API、三个公开业务图、后台BFF/页面、洞察worker及恢复脚本；只用隔离空资源，不默认操作正式栈。
- IAM管理码`agent:insights:manage`的目录补充由协调者单独核对并提供窄迁移；原实现要求登记权限但禁止实施者跨仓修改，本计划明确这项交接，不要求用户自行解决技术依赖。
- 不重开已取消的Change8评测平台，不重做MCP服务端；已有Langfuse可承载相应评测记录，统计和权限依据仍为本地受控事实。
