# 交给实施者的任务说明

## 1. 唯一计划与已确认选择

Change：`add-shared-knowledge-retrieval-tool`；计划主目录：`/Users/acerola/Dev/Python/air_agent/openspec/changes/add-shared-knowledge-retrieval-tool/`。

先读[proposal](proposal.md)、[design](design.md)、[四份规格](specs/shared-knowledge-retrieval/spec.md)、[三仓契约](contracts.md)、[任务清单](tasks.md)和[固定验收](acceptance.md)。所有任务尚未实施，strict只验计划结构。

用户已确认六项选择，不必重新询问：

1. 默认检索当前用户、当前企业有权访问的全部活动资料，资料选择只缩小范围。
2. 具体法规/条款/标准限值必须实际检索并附来源；一般概念、纯计算按需。
3. 溯源报告增加独立知识依据，不把标准原文当本次污染证据。
4. 三个主Agent所有轮次默认schema都有工具，纯问候不主动调用；内部专员保留职责限制。
5. 用户可以点击原文，每次读取重新校验身份、资料权限和来源。
6. 实施范围包括Agent、IAM和RAG必要配套接口，不改检索算法、不重建数据。

技术选择已冻结：共享本地HTTP工具`knowledge_search`；默认PG；不新建MCP服务；单operation短期用户委托；保留RAG record；不借共享service_account；正式运行切换单独授权。

## 2. 三仓职责与接线次序

| 实施包 | 独占内容 | 必须交接 |
| --- | --- | --- |
| IAM | 新签发scope、用户委托签发/内省、opaque摘要存储/TTL、旧鉴权回归 | DTO/OpenAPI、knowledge专用一致权限投影、错误/摘要向量、正反例、私有client配置说明 |
| RAG | 委托受限消费、稳定源owner、纯query/原文、批量validate、deadline/取消/文本分页 | 真实响应、空命中reason、状态/owner/快照/取消、原文与PDF测试，不改变算法 |
| Agent公共层 | HTTP适配器、默认工具、receipt/引用、断言支持、观测及受限来源API | 三图可复用的稳定工厂、tool DTO、超时/大小、来源与原文保护 |
| 三图/前端 | QA与分析默认工具接线、tracing独立知识区、引用显示/原文入口 | 12格真实执行、菜单不绕过、子任务边界、旧digest和真实浏览器 |
| 集成/验收 | 依赖/迁移/公共契约串行集成、跨仓/负向/性能验收 | A01—A25、K01—K23、分层报告、回滚与资源回收 |

次序：契约/隔离夹具及官方平台读/流拦截可行性 → IAM → RAG → Agent公共层 → 三图与引用UI → 三仓组合/独立验收。可按已冻结契约并行开发独占部分，但不得并发改共享文件或用mock联调冒充真实身份接入。

三仓各用自己的隔离分支/worktree；命名默认`codex/`。本change只保存在Agent仓库，不假定其他worktree自动有未提交计划；派发前由协调者把当前完整计划纳入共同起点或显式复制这组非秘密文件。不要只checkout设计中的旧HEAD就认为新计划已存在。

跨仓必要配套范围由用户确认，但本次会话只授权写计划。后续实施者收到实施任务后依各仓AGENTS和OpenSpec流程执行；不因Agent本地apply输出的单仓allowedEditRoots而忽略外仓指南，也不以本文件自动获得正式环境部署或凭据变更权限。若工具要求按目标仓选择作用域，必须显式选择对应仓，而不是绕过限制。

共享接缝：Agent的runtime_tools、graph/nodes/prompt_builder、common包装器/输出、native auth/custom HTTP、lifecycle、引用迁移、前端Markdown/ArchiveReader/BFF；tracing的schemas/claims/gate/publishing/report_store必须与溯源负责人串行协调；Change6的正文/删除和迁移不被覆盖；根锁/官方镜像/langgraph.json只作必要显式接线，不顺手升级。

## 3. 可直接复制的实施任务

> 依据`/Users/acerola/Dev/Python/air_agent/openspec/changes/add-shared-knowledge-retrieval-tool/`实施三图共享知识库工具。先读proposal/design/specs/contracts/tasks/acceptance及各目标仓AGENTS，按OpenSpec apply流程推进；本轮实施范围包含Agent、`/Users/acerola/Dev/Java/air_agent_iam`和`/Users/acerola/Dev/Python/RAG/env-rag-final`的必要配套。保护所有已有未提交工作，采用隔离工作区与自有空测试资源，不修改正式15432/19132或共享RAG资料、凭据、索引。
>
> 实现默认knowledge_search真实可用，不只添加schema；三图包括QA纯问候（可见但不主动调用）、分析无菜单（不扩大数据分析权限）和tracing lead，内部专员保留原限制。MCP关闭不关闭本地知识工具。检索始终以原用户和当前企业执行，先实现受限IAM短委托及RAG消费，不能用共享机器账号代替。
>
> 具体法条/限值在公开前必须真实检索且原文支持；真实来源ID不能掩盖错误数值/单位/版本/范围。所有未判定正文/画布在服务端先缓冲，原生stream/state/replay不能绕过。引用可点击原文并逐次核权；派生摘要即使删掉引用ID仍保留服务端依赖，超过50项须分批全量校验。tracing知识只进独立受控知识区，旧报告digest兼容，不进入污染claim/evidence。
>
> 将K01—K23和A01—A25落实为可复跑驱动与证据；受控模型、真实IAM/RAG、真实配置模型、浏览器和性能分别报告。每完成任务即运行该域验证，不能最后才补所有测试；真实条件缺失标BLOCKED，不把mock或HTTP200写成完整通过。用新目录保留失败、修复、基线和源码来源，不删题/放宽断言；交付可审查diff、使用说明、迁移/配置/回滚与独立验收材料。没有当前明确授权不commit/push/合并/归档或正式部署。

## 4. 实施前已知事实，避免重复走错

- 当前Agent登录token不能直接按knowledge audience内省；运行verification_ref也不能跨client/audience使用。所需新协议已在contracts固定，不用SSO模拟OBO、不放宽现有runtime票。
- RAG已有纯检索`/api/query`和受保护source/document接口，没有统一页码、现成OBO或MCP服务。record严格绑定源session，浏览器另登知识库不等于能读Agent创建的record。
- 当前query/source/record操作需使用各自新短委托；operation不加入稳定auth_binding。knowledge权限投影只保留有效knowledge:lib:*:read，四修订保留，避免各operation投影不同导致旧record打不开。
- 当前PG有效空候选也可能返回missing；必须新增可信empty_discovery reason，不能把任意空evidence或未知格式转成no_match。
- 平台原生`on_thread_read`目前只有owner校验。必须先用当前官方镜像证明state/history/各流和只读校准的拦截覆盖；有缺口先封闭绕过路径，再发布知识工具。
- 现有basic QA固定题库7个概念意图反例属于已记录基线，本次不通过换题掩盖。当前用户选择的是默认工具，因此即使意图误分也不能丢知识schema；额外延迟和误路由照实记录。
- standalone题库离线检查、strict通过、工具schema存在和现有测试绿，都不能证明实际检索或引用闭环完成。

## 5. 交付应包含

- 三仓各自diff、当前源码/锁/迁移head、实际服务来源和必要配置形状；所有secret仅在受保护本地配置中。
- 可执行固定问题/模型夹具/跨仓驱动、调用计数、三图12格、来源打开/撤权/摘要回放/原生绕过等硬门禁证据。
- 新版本验收报告、逐项未完成/阻塞、真实性能与外部等待/用量缺失、旧失败集合与本轮补修记录。
- 正式运行切换前的最小部署/客户端scope/迁移及回滚清单，沿用正式环境唯一入口；回收自有资源，不清理共享库、卷、镜像或知识数据。
