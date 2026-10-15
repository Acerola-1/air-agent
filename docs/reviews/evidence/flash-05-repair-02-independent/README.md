# 匿名05第二轮修复独立证据

用户时长131+83+60=274分钟；外部等待0.0s为用户/作者报告；Token未采集。102份作者输入冻结见anonymous-input.json。作者产品与原报告/证据均不被审计改写。

- production-sdk-graphs.json/txt：真实生产三图SDK ainvoke，受控模型，隔离身份guard为放行。候选3/3报metadata None；仅换原HEAD guard重编译作正向对照3/3完成。非官方API或商业模型矩阵。
- process-boundary.json/txt：独立管理/运行时custom app HTTP进程，真实签票/身份库/受控MCP发现，目录200含5工具，合法选择管理422；自建进程/fixture已关闭。
- live-probes.json / live-extended.txt：真实ASGI+非owner运行库+native hook；默认正例通过，未知工具/协议和改Auto反例改善，合法Skill+MCP空集+伪造目录仍200。live.txt是此前基础重跑，扩展版多一个隔离目录校验探针。
- live-replay.json/txt：省略选择的resume同键重放首次200、第二次409。合成行保留在专用库复验。
- accepted-readback.json：原探针现可保持S1，但没有验证权威来源。accepted-vs-server.json/txt：实际TS输出与真实受理DTO相比，ID/digest均不相同。
- modules-extended.json：glob/根ls/manual同提交回调已修好。new-regressions.json：一般名称碰撞、别名分类、Auto全局披露、metadata None、显式关闭fixture却开启。
- empty-to-old-head.txt / old-head-to-new-head.txt：自建临时库原head成功，新revision缺selection_source失败。临时库已删除。probe-migrations.py在子命令失败时仍可能自身exit0，判定以子命令退出码和原始SQL错误为准。
- core.txt：28通过；admin-ledger.txt：44通过/1同环境既有失败；frontend-contracts.txt：46通过；root-unit.txt：361通过/75跳过/6原失败身份。原测试绿不能覆盖上述独立真实反例。
- frontend-build.txt：生产构建及类型通过，未删除旧构建缓存。ruff.txt：抽查8修复文件通过；作者旧ruff_check.txt里的56项不是当前8文件检查结果。openspec-validate.txt：Change strict通过。
- review-coverage.json：OCR101项，60作者文件（42同指纹沿用+18本轮）、41历史协调者审计产物核指纹保留。另读tasks/报告。文件审查不等于验收全通过。

未复跑完整浏览器390px/IME、官方API24格、真实商业模型/Langfuse、备份恢复矩阵或完整保护性能；不把环境缺项伪填通过。本轮没有补修、提交、推送或归档。仅用15434和自建回环端口，未连接15432共享栈、未操作IAM/KB；仅关闭自建进程、删除自建临时迁移库；作者PG/Redis保留。审计前数据库备份在私有tmp，权限600、不入库。
