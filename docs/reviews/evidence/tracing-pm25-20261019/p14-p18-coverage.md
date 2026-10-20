# P14–P18 覆盖状态（不得当作全部真实正向控制）

本文件替代此前测试中的 no-op `assert True`。P14–P18 的真实 IAM/MCP/浏览器/持久化验收未执行，因此不能把 G0 契约测试算作完整正向控制，也不能据此勾选 5.4/5.6/5.7。

| 案例 | G0 已覆盖 | 仍未执行/不足以宣称通过 |
|---|---|---|
| P14 引用与画布攻击 | 伪造 claim ID、reviewer 新增 claim、跨 scope/旧 revision 证据、存在但不支持命题、缺固定限制、画布只收报告 ID 的 schema 负例 | 真实画布发布/持久化、事件、浏览器重开一致性 |
| P15 范围隔离 | EvidenceRegistry 跨 scope 拒绝；ScopedMCPDataSource 排除依赖时假 executor 零调用；非 allowlist 模式 fail-closed | 真实 MCP 请求计数、真实租户/线程/企业隔离 |
| P16 身份与摘要 | `GuardedSummarizationMiddleware` 在摘要外呼前调用可信身份复查；lead/meteo/reviewer 接 `TenantRuntimeGuardMiddleware` | 真实 IAM 撤销、IAM 不可用、缓存命中失败关闭的真实服务证据 |
| P17 追问与乱序 | request/findings reducer 保留新 revision；发布前 findings/allowlist digest 不一致会重建；旧报告不发布 | 同线程真实持久化修订、A-B-A、跨进程恢复 |
| P18 停止删除恢复 | 发布中间件按 epoch/tombstone/cancel 抑制；报告回读校验 scope/digest | 真实取消事实、墓碑、备份恢复与晚到写入 |

已勾选项仅表示 G0 契约或代码接线完成；P14–P18 不允许用总体准确率或 fixture 成功抵消真实边界未验收。
