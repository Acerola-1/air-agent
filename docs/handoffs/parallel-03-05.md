# Change 3 / 5 并行实施交接

日期：2026-10-13。状态：本地源码与基础事件v1已独立验收冻结，两个实施包可在独立工作区启动；不是生产放行。源码冻结提交`9a1781e3f6d74ad99e5f325e14a69642f6474927`，机器冻结凭据`docs/contracts/agent-events-v1/freeze.json`。公共机器文件保留生成时的freeze_candidate标签，当前冻结状态以freeze.json的提交和哈希为准，不能仅看旧字符串。

## 实施与验收身份

运行03领取`add-chat-feedback-interactions`，运行04领取`complete-agent-observability`。03/04是匿名运行编号，不推断供应商，也不预设是否同一个模型。各自记录首次交付、每轮返工、环境等待、实际用时；Token等工具不给则写未采集。不同任务不直接按耗时或总分排名。匿名01/02的补修已经计入协调者，不能记作新模型成果。

## 工作区和环境

协调者从同一个“基础源码+两个规划包”提交创建两个managed worktree。用户在不同对话中打开对应工作区，不在主`air_agent`目录实施。工作区实际路径会写入主工作区的`docs/handoffs/parallel-launch.json`，该文件是启动记录，不修改机器契约；初次读取时也可从当前git worktree路径核实本项。

| 项目 | 运行03 / 反馈 | 运行04 / 观测 |
|---|---|---|
| 建议分支 | codex/change-03-feedback | codex/change-05-observability |
| 管理API | 8103 | 8105 |
| 聊天前端 | 3103 | 3105（仅验证运行，不改UI） |
| 原生API | 2027→容器8000 | 2029→容器8000 |
| 自建PG端口 | 15443 | 15445 |
| 自建Redis端口 | 16383 | 16385 |
| 应用/原生库前缀 | aa_feedback03_* | aa_observe04_* |
| 容器/卷/Redis命名空间 | aa-feedback03-* | aa-observe04-* |
| 安全证据目录 | docs/reviews/evidence/flash-03/ | docs/reviews/evidence/flash-04/ |
| 首个revision | 0011_feedback，down=0010 | 0012_observability，down=0010 |

仅端口是规划分配，环境尚未声称启动或通过。开始时检查占用，冲突选新的空闲端口并记录，不杀占用进程。可以通过共享PG实例上的**各自新建专用库**替代自建PG容器，但应用、native、运行角色和worker必须隔离；不得用主air_agent库测试/导入/删除。采用Apple Container，可复制编排到本项未入库的私有环境并指定独立项目名，禁止直接在根目录调用默认compose去重启主服务。不要复制真实.env到Git，配置仅在本工作区未跟踪文件；服务端密钥可以由有权限的本地环境安全读取/生成，但不能打印、上传或写证据。公网材料确实缺失只阻塞相关实测，其他任务继续。

浏览器Cookie不按端口隔离，两个localhost端口和主服务不能仅靠端口分开。真实浏览器验收必须使用各自独立profile/存储上下文及测试账号，会话与缓存不得覆盖主浏览器；API使用各自独立cookie jar。工具不支持隔离浏览器时保留该项，由协调者串行浏览器验收，先完成其他独立工作，不用mock冒充。配置ADMIN_API_URL、LANGGRAPH_API_URL、CORS及写入Origin白名单必须与本项端口一致，不为测试放开任意Origin，也不把误配403当产品正常验证。

## 文件所有者与明确授权的接线

本表取代旧ownership.md中的“冻结前一律不准公共接线”。两个分支都在独立checkout中编辑；集成者仍是公共接口的唯一最终所有者。本表授权必要的最小兼容接线，所有公共diff必须列入交付报告，不能扩展为重写基础体系。

| 区域 | 03允许 | 04允许 | 最终集成责任 |
|---|---|---|---|
| 新域模块 | admin反馈models/schemas/service/router；frontend反馈client/组件 | src/common/observability；admin观测/价格models/schemas/service/router | 分支独立审查及组合回归 |
| 聊天共享文件 | ChatPanel.tsx、chat-store.ts、langgraph-api.ts、必要新增BFF route：仅反馈/映射/复制/计时/停止阶段 | 只读；通过现有运行路径验证，UI不改 | 03独占；不得重做消息体系 |
| runtime共享文件 | 只读 | models/langfuse_tracing、辅助模型、Skill/MCP及六图构造的采集hook；不改业务算法和授权 | 04独占；导出类型/每次外呼授权回归 |
| admin main/config/deps/权限 | 仅追加反馈router/开关；复用现有身份/权限 | 仅追加观测router/开关/agent:pricing:manage | 按新增模块合并；禁止修改身份解析/RLS上下文 |
| ledger管理router/service | 仅browser事件分派/必要适配，逻辑放feedback服务；保留内部native生命周期 | 只读；新观测service写本域事实，不能重写ledger受理 | 03独占适配；任何基础语义变更先交需求 |
| event-registry及生成副本 | browser.feedback/copy/timing三类型的字段/开启；其余保留 | runtime五类型字段/开启；其余保留 | 合并登记后生成所有副本，不拷贝整文件覆盖另一项 |
| 公共validator | 不改安全规则，只扩本域枚举/校验所需窄接线并交接 | 确认runtime numeric usage的窄例外，src/admin两Python副本和TS一致 | 基础非法样本全部保留，browser不能报usage |
| 公共OpenAPI/样本/测试 | 新增反馈端点及browser样本；原19路径不删不改行为 | 新增观测端点及runtime样本；原19路径不删不改行为 | 显式兼容扩展后重新生成候选件，冻结提交中的原件可对照 |
| 迁移 | 0011_feedback；后续0011f_* | 0012_observability；后续0012o_* | 集成者新增merge revision解决双head；旧0001–0010不可改写 |
| 根依赖/compose/运行脚本 | 原则不改；本项私有环境配置 | 原则不改；需要新增依赖先列理由，复用锁定SDK | 集成者处理必需变化，禁止顺手升级框架 |
| identity、event_ledger准入/终态/store/reconcile/service_read | 不改 | 不改（只有contract.py数字计数窄例外明确授权） | 基础保护负责人仍为协调者 |

注意`admin/api/app/services/event_contract.py`也是Python校验副本，不能只修src和TS。既有“至少8种预留”断言在合法启用后需要转成精确启用/预留集合和保留非法来源测试；不得删除整个测试。OpenAPI新增路径会触发冻结检测，这是必须显式更新候选件并交接的兼容扩展，不得直接跳过检测。旧响应、路径及基础非法样本不得为了绿灯放宽。

## 收件、合并与后续

每个实现者交付分支diff、任务状态、spec→证据矩阵、公共文件清单和未完成项，状态待独立验收。实现者可以在自己的分支完整运行真实服务和浏览器/PG测试，不必等另一分支；不自行commit/push/merge/archive，不修改主工作区，也不替另一项补代码。

协调者先分别验收，再合并迁移、公共登记、生成副本、管理入口与配置，验证同一请求→两版回答→反馈/复制→主辅助usage→Skill/MCP→Trace/成本→授权汇总。两个分支通过不替代组合通过。生产MCP及完整长运行矩阵门禁保留；组合通过后开始Change 6后台页面。

## 当前已知基线

管理160通过；台账84通过；前端契约31通过；根314通过/75跳过/5既有失败(checkpoint关闭1、knowledge路由4)；完整集成31通过/7跳过/1既有500k monkeypatch失败；干净前端构建通过。原始证据保留主工作区，本分支可读验收摘要`docs/reviews/agent-event-ledger-takeover-acceptance.md`。不得要求不相关旧失败消失才开始本域，也不得以它们掩盖新回归。安全敏感反例、真实PG/运行授权及新用户流程仍必须通过。
