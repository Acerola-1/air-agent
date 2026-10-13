# 匿名运行03：反馈实施交接

只实施本change。读取顺序：AGENTS.md→docs/handoffs/parallel-03-05.md→docs/contracts/agent-events-v1/freeze.json→proposal/design/全部specs/tasks/contract-extension→本文件。源码基线9a1781e；实际工作区由协调者创建，主工作区不是实施目录。记录启动SHA和初始diff，不reset/clean，不删除历史证据，不改另一个change任务。

## 最小完整链路与完成条件

第一条纵向链：本项隔离服务中真实提问产生确认answer→message精确映射→点赞→PG当前评价+历史+事件/outbox→刷新同一评价。完成后再扩复制、负面意见/CAS、计时、停止阶段和统计。不要先堆按钮再发现后台无法关联answer；不要只启用browser类型就认为受理完成。

所有已授权任务持续执行并处理新回归。真实PG/API和浏览器是实际证明，fixture/mock用于故障/统计边界且必须标注。任务只有有证据才勾选；外部条件不足的项保留未完成。不以“当前环境没准备”为由只写测试清单。用户授权本域实现、隔离环境创建/测试和可逆修复；不授权重置主共享卷、打开未知企业MCP、改配置模型、push/合并/归档。

## 验收矩阵

| 规格 | 正向 | 必须反例 | 证据 |
|---|---|---|---|
| 评价版本/状态 | 首答、重生成两版、正负切换、撤销、刷新 | 部分回答、临时/错误ID、无匹配、旧callback | 真实浏览器截图+answer/request/attempt无正文计数 |
| 评价事务/并发 | 同操作重投复用、修订递增 | 响应丢失、同ID异载荷、双页面CAS、outbox故障 | 真实PG并发/故障注入前后计数 |
| 权限/删除 | 本人、运营、统计角色分别正常 | A/B、同用户多企业、无GUC、意见无原文权、永久删除/旧备份 | 非owner PG和真实HTTP |
| 复制 | clipboard成功才记、重复复制次数/distinct | clipboard拒绝、空答、无answer映射、事件重投 | 实际浏览器+受控拒绝测试+PG |
| 浏览器受理 | 混合批次逐项、合法迟到 | 浏览器终态/usage伪造、非法关联、超限/未来漂移、身份重绑 | 双语言样本+HTTP/PG |
| 计时/停止 | 首正文DOM、完成、三停止阶段 | 进度提前、刷新跨时钟、断流判取消、上次终态解锁 | 客户端回归+真实运行/浏览器 |
| 指标 | 2/3满意、3/4覆盖、3次复制/1回答、窗口 | 无分母、历史伪零、删除/重投、多企业cursor、平均P95 | fixture与明细回读相符 |

建议检查：根`uv sync --all-extras`、管理端目录`uv sync --extra dev`使用各自锁；前端`npm ci`使用自己的node_modules。迭代先受影响pytest/前端测试，接线完成再跑基础身份/台账专项、必要完整管理与集成；前端执行`npm run test:contracts`、`npx tsc --noEmit`、干净`npm run build`。严格规格`openspec validate add-chat-feedback-interactions --strict`。真实live变量、运行角色和ARCHIVE_SCOPE_KEYS需来自本项安全环境，不把“缺配置401/403”报告成产品正向验证；500k旧失败保留。

公共修改只在责任表范围内做最小接线，逐文件列用途。旧预留/冻结检测因授权扩展而变更时，新增精确集合与保留非法来源断言，不删除或skip原测试文件。规范生成副本保持一套事实源。管理和Agent各自Python venv不能相互导入应用包。锁文件及OpenTelemetry当前版本遵从冻结源码，不按旧说明降级。

交付`docs/reviews/chat-feedback-interactions-acceptance.md`和安全证据flash-03，包含每个spec场景→测试/真实证据、接口与迁移、共享diff、开关/启用时间、未完成项、实际用时/返工/环境等待、Token未采集或工具实测。保留diff，不自动commit/push/合并/归档，最终状态“待独立验收”。不需要等待04完成才交本分支；组合迁移及反馈+usage/Trace由协调者完成。
