# DeepSeek 正式环境评测启动准备（2026-10-25）

用户明确要求下一轮只测 `cn:deepseek-v4.1-flash`，使用现有正式环境与 root，启动 Langfuse 和相关配件，不另建测试栈、不删除数据。本会话仅完成启动准备及交接，没有发送真实模型评测请求。

统一入口 status：IAM API/管理页、Agent 业务 API/聊天/管理页、知识网关、原生 LangGraph 均 HTTP 200，受限桥 19110 可达。保留的 17 个应用/存储容器运行中；构建器不是评测依赖，未为本轮启动。

Langfuse 六组件运行，健康端点及使用当前项目凭据读取 v4 observations 均 HTTP 200。该结果只证明健康与项目连接，不能证明本轮真实模型调用已采集，执行 Agent 必须用第一条真实产品请求完成闭环核验。

三个知识工作区的旧容器位于 lightrag-net，而保留配置指向当前 default 网关地址，启动后连接超时。先在受控目录备份原始配置，再停止各应用并保存完整容器文件系统为本机镜像，保留文件系统、环境、CPU/内存、回环端口和启动命令，仅刷新到与现有配置匹配的 default 网络。启动既有知识网络转发配件后，air/water/coastal 的 9622/9623/9624 健康端点均 HTTP 200。没有重新入库、迁移或重建数据服务；PG、Qdrant、Neo4j、文档及原应用文件系统快照保留。原网络本身保留。

配置备份位于 `.local/i4-live/backups/knowledge-network-20261025-104127/` 和 `knowledge-network-rest-20261025-104326/`；本机保留镜像名以 `localhost/lightrag-*-preserved` 开头，不上传注册表。该私有证据不提交。

交接时 `.env` 和 `.env.langgraph` 主模型仍为 hy3，尚未切换或发起 DeepSeek 请求。`.local/i4-live/credentials.md` 实际账号为 platform_boot，正常登录 HTTP 200，不是 root。历史 root 验证脚本引用的 `.local/i4-live/root-password` 当前不存在，已经向用户索取当前 root 受保护凭据来源；没有猜密、重置或更换账号。下一轮先落实 root 正常登录与模型切换，不能据此声称 root 已复验或 DeepSeek 已开跑。

执行提示词见 [DeepSeek 正式环境交接](../../handoffs/deepseek-formal-root-langfuse-performance-goal.md)。默认范围为单并发固定题库和最高并发 10 的有界探索，不是完整 F 最大稳定容量验收；数据、会话、台账、Langfuse 观测及失败样本全部保留。

本轮提交前复核：Ruff/格式、diff 检查、性能 OpenSpec strict 及两份示例的离线 validate 通过；沿用正文撤回后已完成的 77 项性能工具校准结果，没有为文档重复执行模型或容量负载。
