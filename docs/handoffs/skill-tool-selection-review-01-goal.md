/goal 修复同一OpenSpec change add-skill-tool-selection 的首次独立验收问题（匿名运行05·修复1）。

先读docs/reviews/flash-blind-05-add-skill-tool-selection.md、docs/reviews/evidence/flash-05-independent/findings.json及该change原proposal/design/contract-extension/acceptance/tasks/handoff。产品源码交付指纹仍为anonymous-input.json，HEAD8620cf7、分支codex/change-04-skill-tool；保留现有diff和原作者报告/证据，协调者没有帮你改产品。工具不支持/goal时按持续目标执行。

优先打通真实最小链：R01修v1/v2摘要在admin/runtime启动裁决的一致性与旧重放，不能把digest比对关掉；R02修前端上下文头、严格payload和真实目录DTO/渲染，选择来源仍由服务端生成；R03修空库完整迁移和已有head升级，不能重写旧已发布revision掩盖依赖错误。先用真实登录→目录→前端提交→受理→原生run启动/终态证明成功，保留负例与开关关闭的auto兼容，再扩六图。

依次闭合R04可信目录/工具/材料/菜单受理，R05所有SDK文件/搜索/下载/子任务与严格路径根，R06稳定ID/合法唯一别名/按scope披露/真实图动态调度，R07重试重生成和resume的不可变submission选择继承、dict访问及权限重查，R08user/tenant/graph/ctx/epoch清理及权威回读，R09无重漏分页/部分超时/大小裁剪。R10重做实际观测与证据，不能继续以导入hasattr、测试内自建wrapper、直接文件后端或局部helper测速代表六图/子Agent/全部运行性能。

把原tasks中未有真实证据的勾选恢复未完成，随实现核验后才勾。A01–A24逐项映射真实API/PG/Redis/SDK/MCP/浏览器证据，补native/BFF×stream/wait×6的run/submission/selection和至少每图显式成功、合法interrupt→resume、撤销、非owner、备份恢复与删除、manual观测来源/选择引用。测试要覆盖正向成功和真实网络零调用负例，不通过删除旧测试/弱化断言/放宽schema/授权缓存解决。先复跑已有test_event_ledger_api等直接受影响回归，必要配置共同基线失败注明，不给新增失败贴基线标签。

原始.log已核实存在，当前被Git忽略；修复日志按原始字节保留为可交付的.txt，勿将日志存在与真实场景完成混淆；修复结果写docs/reviews/skill-tool-selection-repair-01.md，新的证据写docs/reviews/evidence/flash-05-repair-01/，不覆盖作者首交/独立审计。区分真实配置模型、受控模型、直接模块与真实HTTP服务；缺外部条件保留任务，不伪报通过。局部文件helper性能与受理/调用保护全路径开销分开，故障上界/错误率实际采集，不填常量。按原35×2轮及相关检查执行，记录修复实际耗时与等待；Token无计量写未采集。

只修Change4，不动Java IAM、知识库或其他change；保留身份/租户/失败关闭/回环/立即撤销/删除/计费语义和实际模型配置。使用自己的测试库/端口，先查占用。原aa-sel05-pg/redis可继续用于本change；审计新建空库/8104/3104临时服务已由协调者收尾，原专用库保留合成审计行不要全库清空。不要启默认共享栈、停共享服务或全局prune。

保持diff供独立复验，不commit/push/合并/归档，不重新猜/标模型身份，不自行提高首交46分。不需要反复询问已授权的修复和验证步骤；完成全部必要工作后给可复跑交付，确有外部阻塞则明确未完成，不无止境重启掩盖错误。
