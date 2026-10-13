# 匿名轮次 01：租户身份 change 独立验收

日期：2026-10-12。结论：**需修复，未通过验收；不进入第二项代码实施，不同步或归档第一项。**

轮次 01 是本次评审编号，原交付自称“本轮实现”；未提供或推测模型身份。评审由 Codex 完成，不采信实施者勾选作为通过证据。首次交付的观察与分数保留，后续修复另记轮次和时间。

## 首次交付记录

| 项目 | 记录 |
|---|---|
| Change | `add-tenant-identity-access` |
| 源码/规划基线 | `cb09a06` / `7b82c04`；本轮实现未提交 |
| 实施耗时 | **4 小时（240 分钟）**，来自用户报告；非工具计时 |
| 时间拆分 | 实际编码、等待、环境调试、Token/工具次数未采集；不由 Git 时间推算 |
| 交付声明 | 45/46 项已勾选，真实 MCP 授权 7.6 保持未完成 |
| 独立验收结果 | 多个租户/迁移/管理流程阻塞；45 个勾选不能视为45个已验收任务 |
| 实施效率 | 4 小时首次交付仍需返工；达到验收的总耗时尚未知，后续追加修复耗时 |
| 应用运行性能 | 未开展统一负载/延迟基准；未知，不把构建通过或4小时实施时间当运行性能 |
| 本轮改动范围 | 两前端、管理 API、原生 Auth、身份/RLS、MCP、归档及迁移；属于十项中复杂的基础 change |

## 诊断性暂评分

本轮未通过安全与正确性门槛，下面的分数仅记录交付表现，不作为“已通过模型排名”。采用既定五维权重，后续保持相同尺度。

| 维度 | 得分 | 依据 |
|---|---:|---|
| 契约正确性与业务可用 | 15/30 | 有完整基础模块，普通测试大部分通过；空库部署、迁移冲突、管理员配置流程及旧标签页行为失败 |
| 权限和数据边界 | 6/25 | 原生认证、部分真实 RLS、可撤销 session 已落地；存在实测跨租户线程认领、错误企业写入和数据库防护遗漏 |
| 测试与验证可信度 | 12/20 | root 结果可复现，真实/模拟及 MCP 缺失区分诚实；admin 全绿无法从声明迁移复现，关键负向场景未覆盖 |
| 可维护性与项目契合 | 11/15 | 复用既有服务，模块大体清楚；票据 key 轮换、重复授权逻辑和私有平台表迁移存在问题 |
| 范围控制与交付完整 | 8/10 | 保留 diff、未归档，真实 MCP 外部任务未假勾选；存在部分过度完成声明和少量无关格式变化 |
| 合计 | **52/100（暂评）** | 首次安全门槛失败，需修复后复验 |

速度记录独立于上述100分。用户认为4小时偏慢，已纳入效率观察；只有一个样本，暂不据此断言比其他模型慢。后续比较“首次用时、首次通过、修复轮数、达到验收总用时”，并注明 change 难度差异。

## 必须修复的发现

| 编号/级别 | 位置 | 复验与影响 | 必需修复及回归 |
|---|---|---|---|
| R01 / P1 安全阻塞 | `src/common/identity/langgraph_auth.py:147`、`identity/db.py` | 原生 API：无 ownership 的合成 B 用户线程，A 读取404；A 对同 ID 创建且 `if_exists=do_nothing` 返回200；随后 A 读取200，取得 B 的合成私有标记。只需知道旧线程 ID，就可认领未知历史归属 | 创建前不能把既存未知资源登记为本人；真实创建确认、归属冲突、并发/失败补偿有明确协议。未知历史资源仅限受控显式迁移 |
| R02 / P1 数据边界 | 聊天代理 `frontend/app/api/[..._path]/route.ts:18`；后台 `deps.py`、`admin/web/lib/api.ts` | 实际聊天服务中，A旧页面共享 cookie切至B后提交新线程返回200，落入B；独立 PG/ASGI 中旧页面创建部门返回201，落入B。只有 switch 接口检查版本，普通操作没有旧页面上下文约束 | 两前端在业务请求携带目标企业/版本并由服务端校验，缺失/冲突有明确结果；旧标签页回调、切换和回读同步处理 |
| R03 / P1 数据库防护 | `src/common/storage/artifact_db.py:75`、`0004_tenant_artifact.py` | 活体 PG 的 `tool_artifact_storage` 与 `artifact_computations` 均 RLS=false；Agent 归档仍连接 DATABASE_URI owner，计算表无租户列。显式 WHERE 不替代已承诺的数据库隔离 | 归档/计算改为非 owner 租户事务、RLS与同租户关联；迁移/过期清理另用受限路径；补真实 PG 漏条件/连接池/计算越权测试 |
| R04 / P1 撤权 | `src/common/identity/runtime.py:81` | 单元反例：session context/user/tenant/member 修订变化但均 active，revalidate 正常返回；RuntimeIdentity 根本未保留可比较的修订值。已开始运行不能据此撤销角色或旧上下文 | 可信非秘密上下文携带必要版本；每次模型/工具/重试/辅助调用实时比较身份与权限，覆盖切A→B→A及撤权后仍active的场景 |
| R05 / P1 部署 | `admin/api/migrations/versions/0004_tenant_artifact.py:72` | 全新 PG 空库 upgrade head 退出1，SQL 拼成 `ONtool_artifact_storage`；既有归档表分支恰好避开缺陷 | 修复空库路径并验证“纯迁移从零”和“旧库升级”两条路径，不靠手工先建表绕过。任务2.1原完成声明需纠正 |
| R06 / P1 身份迁移 | `admin/api/scripts/migrate_identity.py:249`、`:369` | 独立 PG：同名、不同UUID的 JSON/PG 用户未提供 canonical，preflight仍clean=true，apply静默复用PG用户。另源码直接 UPDATE 原生 thread 私有表，违背已选的受限 API 迁移路径 | 显式源UUID/canonical/冲突核验与原哈希保全；用支持的受限平台接口迁移 metadata，保留消息内容/台账与计数证明；错误或重复映射不得静默吞并 |
| R07 / P1 MCP 门禁 | `src/common/mcp_authorization.py:118`、`mcp_scoped.py:160` | 单元反例：任意 `never-verified-protocol` 只要不是字面量 unverified就获准解析，用默认Bearer。discovery连接也未注入认证，仅 tools/call有拦截器；原fixture允许匿名握手，未证明认证discovery | 明确已核验协议/provider登记白名单，未知全部拒绝；fixture生产隔离；认证 initialize/tools/list/call 与缓存/轮换/重试都需验证。不编造真实平台协议 |
| R08 / P1 管理可用性 | `admin/api/migrations/versions/0003_tenant_identity.py:400` | 纯声明迁移的独立库，admin套件94通过4失败；users.is_active UPDATE未授权，user_followed_regions UPDATE/DELETE未授权，账号停用/关注替换/带关注账号删除出现权限错误 | 用符合RLS/列权限的受限策略实现平台操作，不能用GRANT ALL或owner请求连接过测；清洁迁移后全套复验 |
| R09 / P2 轮换 | `src/common/identity/ticket.py:73`、后台签发路径 | 真实签发/验签单元反例：旧key签发kid=v1，配置从[old]→[new,old]后旧key被重编号v2，旧票据验证失败 | key ID稳定且显式，当前签发与上一个验证正确；跨服务、新旧部署交错及撤去旧key有测试 |
| R10 / P2 用户流程 | `frontend/components/chat/TenantBadge.tsx:35`、`chat-store.ts:196` | 源码：await一个返回void的stopStreaming，立即把isStreaming=false当作取消确认；实际 cancelRun仍在异步执行且可能失败 | 企业切换等待后端取消结果/可信终态；取消失败/超时不误称已确认，补未拿到run_id、失败与完成竞争测试 |
| R11 / P1 企业管理 | `admin/api/app/routers/departments.py:43`；`admin/web/.../enterprise/roles/page.tsx:33`；`routers/tenants.py:_apply_member_roles` | 独立PG/ASGI：企业停用后创建部门仍201；企业管理员roles200但权限树403，页面Promise.all导致角色流程失败；平台关联tenant_admin在双企业fixture抛IntegrityError，因为按code取到多企业同名角色 | 企业写入口统一有效企业/成员检查；提供有授权的本企业可授予权限目录；角色查找带scope/tenant并验证未知项。实际浏览器完成管理员关联及企业角色配置 |

R01、R02 是真实 HTTP 复现；R03 有活体 PG 查询及源码；R05 是新建空库实测；R06/R08/R11 是独立 PG 的实际应用代码；R04/R07/R09 为受控单元反例；R10 为确定的调用语义检查。不能把后三类说成已经在真实业务平台或全部六图长运行中验证。

R04补充（实际运行镜像只读核验）：`TenantRuntimeGuardMiddleware._config_of`读`request.runtime.config/get_config`，但实际镜像的`langgraph.runtime.Runtime`两者均不存在，提取结果为None；安装的agent factory把这一Runtime作为ModelRequest.runtime传入。DeepAgents的模型守卫需从真实框架支持的可信配置来源获取身份并增加合法调用测试，不能用虚构的SimpleNamespace(config=...)代替框架兼容验证。本轮核实对象形状与适配返回值，尚未重新执行三个DeepAgents图的完整合法模型调用，因此作者所称“LLM网关不可用”不能自动排除该本地适配缺陷。

## 独立复验结果与范围

| 检查 | 本轮结果 | 判断 |
|---|---|---|
| root 单元 | 237 passed / 26 skipped / 5 failed | 与作者报告一致；5项为checkpoint关闭1、knowledge_route缺接口4，与既有基线一致 |
| root 集成 | 7 passed / 7 skipped / 1 failed | 500k用例依赖缺失，与作者报告一致；本轮未独立重跑旧提交，不追加扣分 |
| admin API | **94 passed / 4 failed** | API/RLS分两个专用库，导入权限/区划后复跑；声明的98全绿未复现 |
| 聊天契约 | 8 passed | 这些mock覆盖普通代理行为，不覆盖R01/R02 |
| 两前端类型检查 | 通过 | 没有新跑生产构建；作者构建结果仍为作者证据 |
| OpenSpec strict / diff空白检查 | 通过 | 文档格式合格，不等于任务行为满足 |
| Premium UI静态审核 | 0 findings | 实际角色流程仍有403；静态审核不替代运行验收 |
| 额外负向探针 | R01/R02/R05/R06/R11失败复现 | 见下列脱敏附件 |

初次沙箱内MCP fixture端口/PG连接被拒后，已用授权环境复跑，未把这些环境错误计作模型缺陷。管理套件最初缺目录数据的失败也被剔除，最终94/4是补齐两库前置条件后的结果。

真实 HTTP 探针使用本地已有验收账号、新登录会话和新建空白合成线程；未读历史用户对话，合成线程已删除、新会话已登出。数据库反例使用 `air_agent_review01_api/rls/empty` 三个专用库，不重建业务卷。它们保留供修复复验，未改交付源码。

原生Auth配置、metadata修改保护正向复验有效。首期真实MCP材料仍不足，任务7.6保持未完成合理，本项不据此惩罚模型为虚报。修复后保留安全禁用，可先单独验收满足第二项依赖的身份/隔离基础；真实MCP上线仍需其独立材料与验收。

源码清单与指纹见 [输入与审查覆盖](evidence/flash-01/source-manifest.json)。OCR选取102个文件，其中1个是Codex准备的第二项schema配置，排除评分；其余101个按身份/运行/MCP/迁移/前端逐域审查。文件级审查不代表所有规格场景已实测；完整六图长运行、归档浏览器重对话、最终构建/恢复和负载验收留待阻塞修复后完成。

脱敏证据：[真实HTTP/PG](evidence/flash-01/live.json)、[独立PG管理/迁移](evidence/flash-01/admin-proofs.json)、[运行/MCP单元](evidence/flash-01/runtime.json)、[空库迁移](evidence/flash-01/empty.json)。复跑源码与环境说明见 [README](evidence/flash-01/README.md)。不保存 cookie、票据、真实密码或容器环境完整输出。

## 下一步

1. 将 [修复 /goal](../handoffs/tenant-identity-review-01-goal.md) 交给同一个匿名模型，保持第一项范围，先修复本表并补回归，不开始第二项。
2. 实施者纠正相关任务/报告，记录每项真实证据与未完成范围；首次4小时不修改，追加“修复用时”。
3. 修复提交回来后独立复验；身份/隔离基础通过才能交第二项。真实MCP材料缺失可继续安全禁用，不能宣称完整生产MCP授权通过。
4. 再积累其他匿名模型样本；比较首次/累计耗时和修复轮数，不能用不同change的一个总分给模型排普遍名次。
