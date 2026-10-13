# 事件台账协调者接管验收

日期：2026-10-13；版本：混合工作树，HEAD仍为7b82c04。**本轮发现的阻断问题已由协调者补修，本地组合验收通过；未提交、未归档、未宣称生产验收。** 匿名02最终未独立完成，模型评价保留在`flash-blind-02-repair-02-review.md`。

## 修复后的实际行为

- 服务rs-v1只访问`GET /internal/ledger/threads/{id}/runs`及`.../{run_id}`。响应白名单为run/thread/status/时间及可信ledger关联，移除kwargs/input/自由metadata。原生运行宽读、state/history、写入口对普通服务票据403。网关内部委托绑定票据和完整读路径，回环地址受限；不依赖或恢复原成员权限。
- 原会话撤销后，服务网关仍能定位同一真实运行，补齐映射并校准终态。同一提交原生重放409携`run_already_confirmed`，BFF复用200且run_id相同，平台运行数保持1。空列表、分页不完整或多个匹配均不重开启动权。
- 新增**0010**，按submission保存不可漂移的平台run映射。0007/0008/0009不改写。仅对无恢复历史、唯一确认提交回填，歧义保留NULL。历史原操作可读取其原run，恢复保持原request/attempt并追加映射；旧回调不覆盖当前运行。终态重放返回旧结果，不创建运行。
- 同attempt的恢复裁决持有一致顺序的行锁，不同恢复提交不能同时启动。校准按具体pending submission关联，避免找到旧恢复运行；时间戳+attempt_id游标防同时间记录漏扫，CLI返回next_cursor与真实批次数。
- 原问题快照每个request只创建一次，重新生成不重复插入、不覆盖原文或延长原期限。请求摘要保留原调用意图，实际执行摘要与服务端归一化kind一致；BFF采用受理返回的kind并透传可选来源ID，跨线程来源拒绝，无来源resume拒绝。
- `LEDGER_CONTENT_RETENTION_DAYS`服务端默认90，`LEDGER_CONTENT_RETENTION_DAYS_BY_TENANT`支持可信租户覆盖。请求只能缩短期限，不能延长策略；当前策略同样限制旧NULL期限快照回读。配置非法拒绝启动。此期提供部署配置，后台编辑入口属于后续治理change。
- 作者已修好的事实/event/outbox同事务、receipt前置、复合关联约束、永久删除及受限worker成果保留。

## 独立验证结果

| 范围 | 结果 | 来源 |
|---|---|---|
| 最终全新PG迁移 | 0001→0010，三套独立库 | fresh-migrations-final.json；API/RLS/ledger隔离，非owner调度角色 |
| 管理端 | **160通过** | admin-complete.txt，包括新增快照、归一化、租户保留与来源测试 |
| 台账专项 | **84通过** | ledger-final-clean.txt，包括权限、并发、稳定映射、游标与空列表回归 |
| 根单元 | **314通过/75跳过/5既有失败** | root-final.txt；checkpoint关闭1、knowledge路由4仍单列 |
| 完整集成（真实联调开启） | **31通过/7跳过/1既有失败** | integration-complete.txt；仅既有500k monkeypatch |
| 前端契约 | **31通过** | contract-final.txt |
| 干净生产构建 | **通过** | build-accepted.txt；独立目录，无旧.next与环境秘密，含类型检查 |
| 服务权限/受理歧义 | **真实API通过** | service-boundary-accepted.json：正文拒读、撤销后找回映射、BFF完成复用、运行恒为1 |
| 备份后删除/恢复旧备份 | **通过** | restore-drill.json：随机独立库head0010，重施永久删除/权限，新写保留，RLS生效，自建库已删除 |
| 浏览器 | **普通提交/重生成/刷新回读通过** | CUA IAB，真实主模型。browser-counts.json：请求1、尝试2、回答2、原文快照1；browser-regenerate.jpg |
| 格式与规格 | Ruff/严格OpenSpec/diff检查通过 | verification.txt |

测试耗时不作为应用P95或模型实施效率。真实企业MCP仍安全禁用；浏览器仅操作专用acc_alice和本轮合成会话。其余停止、导航、切换语义保留原UX契约，并有现有契约/API回归；不将这些等同全六图两前端长运行浏览器矩阵。

## 交付边界与下一步

change1的真实企业MCP材料与完整矩阵仍是生产门禁，未解除。作者记录的保守停止状态交互限制未改为假确认，仍须后续完整矩阵验证。v1契约继续为冻结候选；当前混合工作区未提交，实际冻结提交尚未填写，**不能以HEAD规划提交启动第3/5项并行**。

本轮补修不再交匿名02返工；完成同一基线的提交冻结后，后续change由另一个匿名模型实施。评估表始终区分匿名模型输入、协调者补修和最终本地验收。测试专用数据库/角色仅清理本轮创建的资源，不重置业务卷或其他作者环境。
