/goal 在 /Users/acerola/Dev/Python/air_agent 中，持续完成 OpenSpec change add-tenant-identity-access 的实现、测试和本地联调，达到该 change 的全部验收条件，交付可供独立审查的结果。

你是本次实施 AI。先阅读 AGENTS.md、frontend/AGENTS.md、docs/enterprise-agent-roadmap.md、docs/reviews/2026-10-12-enterprise-planning-baseline.md、docs/flash-blind-evaluation.md，以及 openspec/changes/add-tenant-identity-access/ 下的 proposal.md、design.md、全部 specs、tasks.md 和 handoff.md。使用项目已有 OpenSpec apply 工作流；总体路线图用于背景，本次只实施这个 change。

已确认：第一版支持多企业严格隔离；企业共用一个业务/MCP 平台但有独立授权身份；复用现有 admin/api 和 admin/web，将聊天与后台统一到 PostgreSQL 身份源。实现企业/部门/成员/角色、可撤销会话、企业切换、六图/线程/运行/归档隔离、MCP 身份授权与并发隔离，以及显式历史迁移。保留现有消息展示、停止、预算、完整数据计算和归档体验。

按 tasks.md 顺序自主推进，每个任务完成实现与验证后再勾选，出现失败自行诊断和修复，不需要逐任务向我确认。可以自行决定契约内的工程细节。安全边界必须在服务端、数据库和真实工具执行处落实，不能只改界面或提示词；不要使用 noop、匿名/全局凭据或客户端自报身份作为企业授权兜底。

验证必须覆盖 A/B 企业、同一用户加入两企业、跨用户/跨企业访问、撤销、并发与失败重试；执行相关测试、真实 PostgreSQL/RLS 和六图 API 联调、两个前端的实际浏览器验收，以及迁移幂等与恢复演练。真实 MCP 授权须依据已核实协议和 A/B 授权样本；模拟与实测分开记录。缺外部契约、凭据、历史归属清单或必要权限时，先完成其他独立任务，保留受影响任务未完成并一次说明具体缺失项，不伪造通过证据。

保护现有工作与业务数据，不重建/删除现有数据库卷，不输出或提交凭据。完整测试对照基线报告，不通过删除或跳过测试掩盖新回归；合理迁移被本次契约替代的旧测试。不要扩展到其他 change，不自动提交、推送、合并或归档。

完成后将报告写到 docs/reviews/tenant-identity-access-acceptance.md，列出实现改动、配置和迁移影响、可复跑验证命令、spec 场景到测试/证据的映射、真实与模拟验证范围、遗留问题和未完成任务。报告不写模型名或供应商，只使用我提供的匿名轮次编号；未提供编号则写“本轮实现”。交付状态为“待独立验收”，保留 diff 供后续盲评。
