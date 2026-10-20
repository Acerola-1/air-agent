# Change 6 独立验收矩阵

所有安全反例有合法正向控制；报告区分纯函数、真实自有PG、合成IAM/模型、真实Java IAM、官方API与真实浏览器。任务全勾/HTTP200/图加载均不单独构成通过。

| ID | 场景 | 必须证明 |
|---|---|---|
| A01 | 统计/运营/正文/处理四类角色 | stats可看聚合；ops无正文；双权限可读同企业他人；只读写403 |
| A02 | A/B同用户、无上下文、平台root | API与实际运行角色SQL均隔离；平台名称无旁路；有效各方正对照 |
| A03 | 请求/重生成/恢复/内部重试 | 1request、2attempt、1resume等样本期望逐项可复算，不乘join |
| A04 | 跨午夜、七日、回答/调用cohort | 窗口与分母独立，不平均P95，无分母null |
| A05 | 正负改评/撤销/复制 | 当前满意率/覆盖/复制次数及回答数不同，未评价非满意 |
| A06 | usage缺失、价格缺失、多币种/重算 | Decimal小计和完整性，旧修订不重复计，未归因单列 |
| A07 | Skill/工具>200项 | 选择/加载/执行分开，传输与业务失败分开，全量聚合不是首屏相加 |
| A08 | 运行与来源正文 | 新三图确认正文可读且版本准确；旧未采集明确，无checkpoint/共享Trace兜底 |
| A09 | 终答采集失败/草稿/撤权/超长 | 答案不变业务失败；失败可见，推理/工具块不落库，受控截断不冒充全文 |
| A10 | 反馈处理CAS/幂等/审计失败 | 并发至多一成功，同ID异payload409，整体回滚；新反馈needs_review |
| A11 | 新管理权限真实IAM授予/回收 | 正常API配码/读回/合法写；回收下一请求403，非SQL seed/假令牌 |
| A12 | Trace与依赖故障 | 跨企业Trace调用外部0；共享项目无直链；Langfuse局部失败/IAM整体503 |
| A13 | 删除、晚到与到期 | 请求/回答/评论/处理内容立即不可读；物理清理、旧备份重施墓碑后不复活 |
| A14 | UI A→B→A、ctx变更/普通409 | 所有旧响应丢弃；业务修订冲突不报企业切换；正文缓存清空 |
| A15 | 实际运营完整浏览器链 | 总览下钻→运行→正文/Trace→反馈处理→刷新权威读回，ID与SQL一致 |
| A16 | 中文IME/键盘/窄屏/慢网 | 实际操作与截图，焦点恢复，搜索不提前发送，无伪零/布局跳动 |
| A17 | 基准和越界 | design规模/样本/P95与SQL计划；limit/window/维度超界明确拒绝，不全表拉前端 |
| A18 | 最终溯源/锁版本组合 | 三图运行、ValidatedReport最终正文同摘要、内部专员不扩问题量，删除/撤权无泄漏 |

建议新测试域：`admin/api/tests/test_console_{access,queries,metrics,content,feedback}.py`，`tests/unit_tests/test_answer_content_snapshot.py`，`admin/web/tests/operations-*`。需补admin/web实际测试脚本，不把当前`lint=tsc --noEmit`描述为组件测试。既有聊天`npm --prefix frontend run test:contracts`作为接线兼容回归，不是后台浏览器验收。

自有空资源；迁移head现查。结果写新的`docs/reviews/evidence/run/console-<时间>/`，可提交小型脱敏摘要放非run目录。性能失败/生产依赖不可用保留未完成，不通过减少断言、全局skip或假数据关闭门禁。
