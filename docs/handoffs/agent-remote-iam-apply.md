# I2匿名实施提示词（标准OpenSpec apply）

请在 /Users/acerola/Dev/Python/air_agent 实施OpenSpec change switch-agent-to-remote-iam。你是本轮匿名实施者；不写模型/供应商名称，不读取历史模型身份映射或评分表。没有匿名编号就使用“本轮I2实施”。本次授权实现本仓库、测试和自有隔离环境联调，交付待独立验收的diff；不要commit、push、merge或archive。

使用客户端的标准OpenSpec实施工作流（通常为/opsx:apply switch-agent-to-remote-iam），不使用goal模式。先读取openspec status和openspec instructions apply --change switch-agent-to-remote-iam --json，按其中contextFiles与本包范围实施；这些CLI只提供工作流上下文，不会代你修改产品。任务进度只写tasks.md，不另外创建goal或checkpoint文件。

## 先读这些文件，不要先吞整个仓库
1. AGENTS.md及本change的proposal/design/tasks/acceptance/spec。baseline.json先看HEAD/已有改动摘要，文件hash在G0核对；contract.md先读共同段和I2段，I3专属段仅作边界参考。不必一次载入全部历史附件。
2. 只在进入对应阶段时读取实现入口及其调用者/测试；起始入口见design和baseline。涉及Next时读frontend/AGENTS.md和当前安装版本Next文档。
3. 旧extract-shared-iam-service只作历史背景；其中“Java直接接同一身份表”、旧Change4暂缓等描述不覆盖本包。

## 本轮交付定义
完成显式remote部署适配：真实IAM身份→受保护业务→签票和执行引用→实际Graph与外呼撤销；再接两前端SSO并扩展六图/业务回归。local保留原语义。只有隔离测试副本可写，不切现有用户或共享服务。正式存量导入、中央业务权限发布和生产切换另列，不冒充已完成。

## 执行方法
按G0→G1→G2→G3→G4自主推进。每阶段先做设计表中的最小正例和相邻负例，修通再扩面；不在最初同时重写客户端、全部页面、迁移和六图。通过一个任务必须同时有实现和对应验证，不能仅凭作者解释勾选。正常工程决定自行完成，不逐任务向我确认。

关键设计已经冻结：
- Python旧身份表与Java并非同构，不套Flyway、不换DSN猜接管；业务引用投影只在自有库、UUID一致且不授权。
- 严格区分IAM客户端、业务客户端、SSO客户端、主体token和运行票据。业务权限逐码清单须保留原边界，不用平台管理员兜底。
- rt-v1没有verification_ref；使用contract.md确定的服务端票据摘要绑定方案，支持BFF/native不同进程。不能把ticket当subject_token或发明session_id核验端点。
- 每次实际同步/异步发送和retry都重新复查；保留业务GUC/RLS、线程/MCP、mt/rs窄能力及已完成的反馈/台账/选择/观测。
- SSO当前实际为303+ticket/state，state必填，按contract探针确认；不能照抄历史OpenAPI中缺state或code的描述。

如支持子智能体，可按客户端/纯契约测试、业务适配、前端接线分工；迁移、共享DTO/绑定存储由主实施者统一拥有，不能多人同时改同一文件。不存在子智能体工具就顺序做，不编造委派结果。主实施者负责真实整合与回归，不能只拼子任务报告。

## 验证与交付
按acceptance.md的A01–A14提供正负实证，完整回归对照启动时具体失败nodeid。真实PG/Redis与IAM、受控外部HTTP、实际SDK、实配模型及真实浏览器分开；不换配置模型、不用导入成功或HTTP200冒充完成。外部能力确实缺失时，继续独立工作、保留相关checkbox并一次写明缺少什么，不伪造通过。

进度与续接位置只写本change/tasks.md末尾的短“实施备注”，checkbox仍须实现和验证同时完成；证据按唯一run追加到忽略目录。保留源码指纹、命令/退出码、时间、成功/拒绝网络计数和局限；不回显口令、cookie、原始票据、内部凭据或敏感内容。模型实施/验证/外部等待分别实测，Token不能取得写未采集；不要从Git时间估算。

最终写docs/reviews/agent-remote-iam-acceptance.md：改动与理由、A矩阵、基线/新增失败、迁移/配置影响、实际地址与本地凭据文件位置、资源所有权、尚未验证及正式切换前置。首次交付注明“待独立验收”，保留diff，不自封最终通过。
