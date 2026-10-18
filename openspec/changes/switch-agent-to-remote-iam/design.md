# I2设计与实施边界

## Context
源码起点319dab499585e640d0d216699cd767a3a1c567e1；工作区已有docs/reviews/anonymous-model-scorecard.md未提交，必须保留且不阅读其模型映射。当前身份入站位于admin/api/app/services/identity.py，运行时状态在src/common/identity/db.py直连PG；同文件还包含线程归属和MCP绑定。frontend/lib/admin-api.ts目前把身份与业务请求共用ADMIN_API_URL。RuntimeIdentity已有四修订，但没有IAM执行引用。实际入口六图见langgraph.json，不能用其他拼写替换。

## Goals / Non-Goals
本轮目标是可部署的remote适配代码、明确local回归、两个前端登录接线、全部受影响边界和自有环境实证。模型不需要在第一次读取时吞完整历史仓库；按下面阶段只读取当前闭环的文件及调用者。
不迁真实用户、不接管共享身份表、不改IAM/RAG源码、不新增身份CRUD、不改变Skill/台账/价格语义、不物理拆库。现场生产发布和真实企业外部MCP授权不是合成夹具可以代替的内容，已有门禁保留。

## Decisions
### 部署选择与客户端
使用启动时明确的AGENT_IDENTITY_BACKEND=local|remote，默认local保留当前部署；非法值报配置错误。remote必须具备IAM URL、精确issuer/audience、内部client、SSO client及会话存储配置；缺失即拒绝启动或503，不能尝试local。
IAM请求客户端和业务admin客户端分开，显式路由清单，不对ADMIN_API_URL做全局替换。Python同步/异步共用一套严格DTO/错误语义，不在async请求里调用阻塞网络。remote权限完全来自IAM，不沿用本地角色join或平台标记放行。

### 票据与长运行
严格执行contract.md的票据摘要→执行引用绑定方案。专用存储由Agent拥有、按服务端边界登记，BFF与native不同进程可见；绑定必须与已验签票据的全部身份/四修订一致。入口TTL与运行中引用生命周期分开；取消/超时清理及退出回收有界。
引用登记与native复查必须使用同一个已登记Agent internal client_id；不同SSO client、I3 client不能复查该引用。不要为了拆分进程临时增加尚不存在的IAM跨client委托接口。
原始subject_token留在受控服务端应用会话，不放入LangGraph state/config/checkpoint/Trace。可信运行上下文仅保存经内部认证复核的引用与非秘密ID/修订；不能从metadata/input接收替代上下文。同步、异步主调用和每个实际retry发送都重新复核。
保留mt-v1与rs-v1的原有窄用途、独立密钥与投影约束，remote用户凭据不回退这些主体，不把校准写成已撤销用户的新执行。

### 数据和授权目录
本轮隔离夹具先由IAM创建用户/企业/成员，再以相同UUID装载业务FK所需的最小投影。投影只保证引用/展示与业务锁，不授权、不存有效人类会话、角色或密码认证材料；旧不可空列如必须占位须使用无法登录的技术值并证明remote旧登录已关闭。
现有业务表内部FK、业务GUC/RLS、偏好上限锁、线程归属与删除墓碑继续存在。不能整文件删除identity/db.py，也不能广泛drop FK、CASCADE、关闭RLS来接新ID。
从现有seed与require_permission调用导出业务权限逐码清单，受控测试迁移注册到自己的IAM副本；保持原租户/平台可授边界，原文读取不因运营权限而开放。历史UUID、未知归属、同名多ID、业务权限注册缺口只dry-run报告；不能在正式源库自动合并。该发布与真实历史导入属于正式切换前置，不写成已经完成。

### 浏览器与旧API兼容
BFF保留现有/auth与/api/auth消费外壳，逐字段翻译IAM响应，不直接让旧前端解析新字段。remote旧Python身份写入口关闭或受控代理；明确区分关注城市等业务端点，不能整体废弃/auth路由文件。
两个前端均采用不同名称的HttpOnly应用会话handle，服务器保存IAM派生token；SSO遵循contract.md的实际303/ticket/state要求。callback只绑定精确注册值，错误state/重放/Origin/CSRF拒绝，登出后中央会话关联复查不能继续。不要重做页面设计；保持当前交互，A→B→A/退出换账号的旧结果清空。
IAM管理入口使用受控链接/跳转，Agent现有运营、MCP绑定和偏好页面保留。

## 分阶段推进（不逐项等待用户确认）
| 阶段 | 只先打通的闭环 | 进入下一阶段条件 |
|---|---|---|
| G0 | 基线、独立资源、真实HTTP契约探针、差异/权限/投影清单 | 契约与调用者明确；阻塞项精确定位，不虚构上游能力 |
| G1 | 真实IAM→客户端→业务身份→一条本人受保护读；错误身份/断网负例 | 正例可用，拒绝路径后端操作0，local同题对照不回归 |
| G2 | 登录→签票/登记→一张实际Graph执行→撤销→下一外呼0 | 真正跨进程桥接成立，>60秒合法运行可继续；再扩展六图与辅助边界 |
| G3 | 两前端会话/SSO与企业切换→业务页面读写 | 浏览器真实正例和409/CSRF/迟到响应，不复制身份CRUD |
| G4 | 全相关回归、六图矩阵、隔离迁移/故障/性能采样、交接 | acceptance.md逐项有实证或明确阻塞，未验证不勾选；提交待独立验收 |

标准OpenSpec apply按tasks.md实施；进度与续接只在tasks.md末尾维护短“实施备注”（已完成/下一步/变更文件/验证/资源），原始证据按run追加，不另建goal、checkpoint或大量过程计划。上下文不足时从tasks与备注接续，不重写已冻结契约。

## Risks / Trade-offs
一次remote核验引入可用性依赖；保留500ms总deadline，不以正向TTL缓存换性能。票据绑定存储不可用时拒绝新受理，不绕过native；同UUID投影不是历史迁移完成。SSO快照存在已知差异，按contract-probe显式适配，不静默改共享服务。新增网络guard须防自身HTTP递归触发运行守卫。

## Migration Plan
只在自有隔离副本演练local旧版本→remote配置，验证本人/跨租户、业务新写保留、退出/撤销不复活。回退应用版本仍指向同一IAM权威或停流量前向修复，不以换回旧身份库开放作为回退成功。真实维护窗口不在本轮授权内。

## Open Questions
需要现场探针确认注册客户端、SSO回调、权限发布与历史同UUID映射。它们影响正式启用，不能由未知环境值猜补；完成其他独立工作后在报告一次列明，不据此放宽授权。
