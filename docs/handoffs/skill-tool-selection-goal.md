/goal 完成当前项目的 OpenSpec change：add-skill-tool-selection（匿名运行05），交付可独立验收的实现。

请使用你所在工具支持的 goal/持续执行模式；若没有 /goal 命令，将以上目标视为持续任务直接执行。本提示词不依赖某一厂商或模型，不猜测或主动透露模型身份。

起点与工作区：在 /Users/acerola/Dev/Python/air_agent 的 dev 分支找到包含本提示词和完整 Change4 计划的最新提交，先记录实际HEAD和干净状态。从该提交建立独立worktree/checkout，分支使用codex/前缀；不要只取2d6f7c8而漏掉规划包，也不要与其他对话同时编辑同一工作区。若现有目录有未知未提交改动，先保留，不覆盖或清理。产品基线为2d6f7c8，IAM协调提交06b411f。

阅读：AGENTS.md、frontend/AGENTS.md、DESIGN.md、UX-CONTRACT.md，以及 openspec/changes/add-skill-tool-selection/ 下 proposal.md、specs/**、design.md、contract-extension.md、tasks.md、acceptance.md、handoff.md；再读docs/reviews/agent-interactions-observability-takeover-acceptance.md与docs/flash-blind-evaluation.md。使用项目OpenSpec实施流程，逐项实现并留真实证据；中文更新文档。

目标：为六张Graph增加按当前企业授权的Skill/MCP目录，以及聊天输入区的Skill自动/单选、MCP自动/指定集合（空集为不用MCP）。用户选择经服务端实时授权校验，随受理台账/event/outbox同事务冻结；原生直连、BFF、Skill文件/加载器、真实MCP dispatch、子Agent、重试和恢复执行同一不可变约束。data-analysis必须保持菜单确定性。选择不会扩大权限，旧客户端默认自动兼容。观测区分选择与真实执行，选而未用不算使用量或费用。

先做Gate0：独立PG/Redis/端口、两企业双成员与双MCP服务同名工具；验证真实SDK编译后动态发现/调度、DeepAgents的/skills和子任务边界，再正式接线。安全要求必须同时有成功正例和真实网络零调用的拒绝反例，不能用只过滤schema、模型提示、HTTP200或mock返回代替。

边界：仅本Change4及必要接线。当前身份接口继续使用，不重写登录/RBAC、不切Java IAM、不改知识库、不做Change6，不修改另外两个仓库。保持租户隔离、即时撤销、失败关闭、回环绑定、旧幂等/删除/价格计费及已有停止反馈体验。不要放宽事件validator或删测试过关；allowed-tools不是必需工具清单。依赖与模型配置沿用当前锁和私有配置，不擅自换模型。真实企业MCP生产接入未验证须单列，受控MCP测试仍必须做。

环境：此前3/5专用测试容器已清理，应新建自有测试库/容器，建议8104/3104/2032且先查占用；Apple container语法依AGENTS，非Docker Desktop。不要启动默认共享栈后清库，不做全局prune，绝不删知识库/正式Agent/Langfuse共享数据。凭据仅本地注入，不入日志/报告/Git。对自己的临时服务记录所有权并收尾。

验收：完成tasks全部实际可验证任务及acceptance的A01–A24，覆盖六图native/BFF、stream/wait、显式选择、恢复、RLS、并发/旧幂等、删除、撤销、浏览器scope/窄屏/键盘/IME，以及至少35样本×2轮的本地开销。复跑受影响单测/契约/集成、迁移、Ruff、tsc、干净生产构建和本change strict校验；共享契约全量回归对照原失败集合。旧基线根5/集成1失败及9历史spec格式失败需逐项确认，不把任何新失败统称基线。Change3用户豁免的物理剪贴板检查不重新作为阻塞。

交付：实现留在独立工作区diff，未经新的授权不要commit/push/合并/归档。写docs/reviews/skill-tool-selection-acceptance.md和docs/reviews/evidence/flash-05/，逐项给出测试/真实运行、成功和失败证据、模型/MCP环境、起始/最终源码指纹、实际耗时与外部等待、未完成项及资源清单。未实测项保留未勾选；模拟、受控服务、真实配置路径必须分别标注。Token无法取得写“未采集”，不猜测，不预先打分。遇到外部阻塞，继续无依赖工作并汇报具体缺口，不伪造完成。

请直接持续执行到上述交付可供独立验收；不用反复询问本提示词已经授权的实现与验证步骤。
