## Context

冻结源码9a1781e，业务head0010。六图有`with_langfuse_tracing`回调包装，不能据此证明实际落库和全部辅助调用被采集。运行结束时台账能写状态/answer/Trace；TTFT/usage/cost/span/tool_call预留类型仍禁用。显式业务图与DeepAgents图结构不同，辅助模型可能在独立调用路径，不应只看最终助手消息。已有授权逐外呼检查、SDK禁隐式重试、MCP身份隔离及图导入轻量约束必须保留。

## Goals / Non-Goals

目标：逐调用观测可信关联、精确去重、真实Langfuse映射与有界故障降级，为Change 6提供实际可查询数据。非目标：后台页面、用户反馈、能力选择、向量聚类、质量评分、告警和全面重做提示词管理。保留现有提示词适配并记录其实际版本，不把历史工作计成本模型交付。

## Decisions

### 1. 独立域和可信边界

新增`src/common/observability/`(context/collector/model_adapter/tool_adapter/skill_adapter/store/langfuse_adapter)及管理端`models_observability.py`、`schemas_observability.py`、`services/observability.py`、`routers/observability.py`、`services/model_pricing.py`。身份只取平台注入RuntimeIdentity；台账IDs按同租户request/attempt/submission校验，不采信客户端obs IDs覆盖身份或运行映射。私有运行配置/签名票据不可进入Trace。native rs-v1仍只读状态投影；观测查询使用用户身份的独立授权服务，不复用rs去宽读原文。

新revision=`0012_observability`，down_revision=`0010`；反馈revision另从0010分支。观测表按tenant强制RLS，parent/call/attempt/submission关联包含tenant；仅本域观测表和价格/计费修订表，本项不变更LangGraph内部表。附加revision用`0012o_*`，最终merge由协调者负责。

### 2. 观测对象和回调去重

区分logical_call_id、observation_id(一次实际尝试)、parent_observation_id、callback/native run、attempt/submission/Graph、role(main/normalizer/summarizer/subagent/other)。观测状态started/succeeded/failed/cancelled/timed_out/unknown，不等同台账运行状态。重试尝试有独立observation但同logical_call；结束更新使用版本/阶段幂等且不回退强终态。流式usage增量与累积需按提供商语义归一化，最终usage修订优先，冲突标记而不再次累计。

持久化安全计数/时间/非秘密模型名与诊断类别，不存自由kwargs和错误正文。观测事实与runtime.*事件/outbox同事务，使用运行角色与事务局部tenant上下文；公共store不增加owner旁路。事件投影消费者添加独立consumer_version并保留去重receipt，删除优先。

### 3. 预留字段与禁止键冲突的明确修复

冻结校验器按键名子串拒绝token，导致已预留`input_tokens`等启用后仍会被拒。本项明确授权在Python和TS校验器做**窄例外**：仅`runtime.model_usage`的来源runtime，在payload顶层、登记白名单中，允许非布尔、非负整数的input_tokens/output_tokens/total_tokens/cache_read_tokens/cache_write_tokens/reasoning_tokens；不对白名单外、嵌套键、字符串、credential/token/authorization键豁免。首Token类型使用`first_piece_kind`避免自由token键。保留全部基础非法样本，并新增numeric有效、string/negative/bool/嵌套secret/伪造browser来源的反例，两个语言及包内副本一致。不能删除整个token禁止模式。

runtime.*扩展登记写在本change的contract-extension.md；只有本项五类型启用，新Skill阶段通过runtime.span的span_kind/stage表达，不另造自由日志。Change 3的browser三类型仍禁用。共同冻结文件及生成副本由协调者最后合并，分支授权的变更逐项交接。

### 4. 模型、工具和Skill接入

优先可继承且不重复的LangChain回调；独立辅助模型显式传递可信观测上下文。先列出真实调用点，覆盖`models.py`、question_normalizer/conversation_summarizer/subagent_summarizer/text_reduction、业务图nodes及三个DeepAgents构造。模型usage来源优先provider实际返回；不存在则null，不把chat-store当前主模型usage当全运行。

MCP在已有每次授权及重试边界周围加采集，不能重构credential/cache隔离。tool_wrappers和mcp_client双层明确一个物理attempt所有者，其他层仅parent span，不重复计调用或费用。Skill router/discovery/menu mapping分别记录matched/selected/loaded/executed证据，未执行标明not_executed。失败分类通过既有规范业务返回解析，不凭HTTP200判断。

六图导出仍满足当前官方镜像的CompiledStateGraph/配置要求，不能用with_listeners把导出变成不受支持对象。真实镜像验证GraphLoad及合法预留运行，覆盖直接native和BFF入口、stream/wait、恢复。图导入零外部I/O。

### 5. Langfuse与隐私

继续已锁SDK版本，不顺带升级依赖。运行期主动验证真实写入/flush/支持API回读，metadata保存可信tenant/run/attempt/project_scope及非秘密release/environment，辅助trace单独映射。默认屏蔽input/output、工具结果和reasoning；不能仅在新数据库脱敏却让旧CallbackHandler仍自动上报正文。共享项目只允许可信代理按本地归属映射查询，浏览器无密钥/共享控制台直链。专属且有实际权限的项目才能提供经过授权的控制台入口。

进程内待投递队列默认最多1000条、每批最多100、等待flush最多2秒、重试最多3次且指数退避；可配下调，记录丢弃/积压。不能创建无界后台任务。业务答案失败开放，原有准入与外呼授权失败关闭。关闭采集不暂停业务、不假报同步；外部删除追踪pending/succeeded/failed并本地立即禁读。

### 6. 价格与查询

价格记录不可变version、provider、actual_model、currency、unit、effective_from/to和分项费率；禁止重叠，用Decimal，计费结果关联observation和price_version，显式重算产生审计修订。价格操作需新增`agent:pricing:manage`，平台/企业权限范围明确，不默认给所有成员。普通query复用agent:operations:read/metrics:read，无原文。

建议接口：`GET /agent/observability/runs/{attempt_id}`、`GET /agent/observability/observations`(limit<=200，cursor绑定tenant/filter)、`GET /agent/observability/metrics`、`GET /agent/observability/traces/{trace_id}`；价格可用受控管理API或CLI，选择后冻结OpenAPI/命令契约。总usage/cost以observation事实去重，不按Trace或多个Skill重复累加。价格未知和usage未知分列；不合并不同币种；未归因桶保证守恒。

### 7. 并行接线责任

本项独占runtime模型、Skill/MCP、六图采集接线；Change 3独占聊天UI/store。共享main.py只追加本域router，config/权限仅追加本域配置和权限，ledger summary不要重写，新增本域metrics接口先独立交付。公共validator只做上文窄例外，既有运行准入/终态/store/reconcile/身份实现不可修改；确需变更需写具体接口需求，由协调者解决。详情见handoff和parallel-03-05.md。

## Risks / Trade-offs

- 供应商usage格式和回调重复 → 固定真实样本、来源优先规则、逻辑/物理ID与确定性反例。
- 主/辅助上下文丢失 → 逐真实调用路径核对并统计unattributed，不猜租户或最新run。
- 默认SDK上传正文 → 真实Langfuse内容核验和敏感marker测试，旧callback也必须受隐私配置约束。
- 观测存储与业务共用PG → 队列、批次、超时有界，实测至少30样本的本地P95增量，超过50ms不自称性能通过。
- 真实企业MCP缺材料 → 受控MCP证明采集代码，生产门禁单列；Langfuse不可达则保留真实落库任务未勾选。

## Migration Plan

独立测试库从零和0010已有数据升级到0012_observability；验证强制RLS与同租户父子关联、幂等、删除和历史兼容。启用前价格缺省为空、费用unknown，不生成假价格；新的采集开关默认关闭。迁移/权限/服务就绪后打开观测，先确认真实usage与Trace，再扩到所有图。回滚关开关和回退兼容应用，保留已采集事实和删除记录；不要DROP观测事实或降级旧revision。
