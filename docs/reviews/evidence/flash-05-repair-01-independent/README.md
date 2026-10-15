# 首轮修复独立验收证据

正式时长：用户131+83=214分钟，作者约45分钟不采用。输入85文件/sha见anonymous-input.json；作者源码和既有审计文件审计前后未改变。

- live-probes.json / live-extended.txt：真实管理ASGI、PG、非owner运行库和实际native hook；无模型外呼。默认/关闭开关和恢复正例成功；真实目录/幂等反例失败。首轮基础live探针结果被本轮扩展probe替代，扩展脚本完整保留。
- client-probes.json：带受控真实上下文的实际TS编译探针，BFF上游fetch受控返回，不冒充浏览器。旧probe未模拟新引入context helper的403不是最终结论。
- accepted-readback.json：编译实际chat-store，发送S1、变更draft、延后确认，accepted被写S2。
- module-probes.json / modules-extended.json：真实临时文件系统、真实SDK；普通代理改善、特殊代理仍撞名、glob签名失败、Skill来源与参数采集。
- dynamic-graph-objects.json：真实导入生产三图后注册工具，固定列表仍空；非实际模型运行。
- browser-*.txt/png：新production构建，真实浏览器→BFF签票→artifact_http目录路由→PG。实际7项、选择、Escape关闭；未运行官方平台，所以线程列表错误是隔离环境预期，不作为产品问题。
- empty-to-old-head.txt / old-head-to-new-head.txt：独立临时库，0001到旧head再到新head成功，选择表FORCE RLS=true；库已删除。
- core.txt / admin-ledger.txt / frontend-contracts.txt / root-unit.txt：最终允许本地网络的复验，28通过、44通过1既有失败、46通过、361通过75跳过6既有失败。基线失败身份对照上一轮原HEAD同环境证据。
- core-sandbox-denied.txt / frontend-build.txt：初始沙箱阻止loopback/进程通信的环境尝试，非模型缺陷。frontend-build-permitted.txt是成功新构建，包含TS检查。
- ruff.txt：新迁移9个E501。openspec-validate.txt：Change strict通过。
- ocr-preview/rules/review-coverage.json：72项逐一记账，53作者文件审查（33首交同指纹沿用+20本轮），19旧审计产物跳过产品审查、仅核指纹。文件审查不等于行为通过。

未完成：官方API24格、三SDK实际子任务/动态工具全矩阵、真实interrupt恢复、备份恢复重施墓碑、完整窄屏/IME/生产模型/Langfuse及负载验收。无commit/push/归档，未补修产品。备份仅在私有tmp，不入库；只清本轮自建服务/标签/临时迁移库。
