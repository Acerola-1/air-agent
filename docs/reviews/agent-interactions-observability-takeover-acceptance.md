# Change 3 / 5 协调者接管与组合验收

日期：2026-10-14（Asia/Shanghai）。源码起点：`0f2cd027941cdb6fa773ef8aba41940e692b7e52`，主工作区 `dev`。**产品修复由协调者接管完成；2026-10-15用户接受本地验收并明确豁免Change3最后的剪贴板文本实机抽查，授权归档3/5、分批提交推送和清理测试容器。该抽查未执行，完整生产放行不在本次结论内。** 下文保留原验收边界，最新归档状态见末尾收尾记录。原作者两个工作区保持原样，34 / 42 项输入指纹均复核一致。

## 一、匿名交付归属

| 运行 | 同一第三匿名模型的任务 | 用户实测首交时间 | 独立首交评价 | 最终归属 |
|---|---|---:|---:|---|
| 03 | add-chat-feedback-interactions | 131分钟 | 58/100，未通过 | 模型未独立完成；协调者直接补齐 |
| 04 | complete-agent-observability | 83分钟 | 55/100，未通过 | 模型未独立完成；协调者直接补齐 |

模型首交累计214分钟（3小时34分），无后续模型修复轮。作者自估7.3h/4.5h不替代用户实测。后述修复与通过项**不算匿名模型成果，不上调其首交分数**。协调者完整人工/模型工作时长和Token未采集；工具等待、审批额度中断和本机服务恢复不计为匿名实现速度。不同change不直接按耗时排名，也不以本地采集开销替代应用整体性能。

原始独立报告：`flash-blind-03-add-chat-feedback-interactions.md`、`flash-blind-04-complete-agent-observability.md`。作者原交付报告与证据继续保留，不能把其历史“通过”表述当作本次验收结果。

## 二、接管修复内容

### 反馈、复制和浏览器计时

- 评价绑定已确认回答及不可变内容版本；修正意见双权限、CAS并发、完整操作意图摘要和旧操作原结果重放。增加按操作ID的事务锁，跨线程并发也只允许一次，目标删除优先于重放。
- 浏览器事件逐项保存点隔离、校验并事务写事实/事件/outbox；复制重投不报500，有效项不受非法项回滚影响。256KiB / 50条批次、100条队列、最多3次重试及60KiB卸载投递有界。
- 客户端按企业、用户、context_version、epoch隔离请求、缓存和队列；A→B→A旧结果失效。未确认回答不永久负缓存，消息映射限定线程，歧义不猜版本。点踩草稿不提前改变已保存评价，失败保留意见，409回读当前修订。
- 服务端默认关闭采集，显式启用时间与开关由policy端点下发；历史缺失不补零。复制次数、distinct回答和分母来自同一可复制回答集合。
- 使用同页面单调时钟、实际正文DOM可见/绘制和明确的final_output_done。进度、工具、推理和history回放不冒充首正文；中断/离开/完成分别标记。
- 真实浏览器发现的关键修复：平台run和台账attempt都是UUID，不能混作同一标识。新增本人提交映射读回，DOM完成早于确认时保留有界测量，等可信attempt后投递。真实读回有完成与未完成计时。
- 官方cancel(action=interrupt)返回`interrupted`，前端按“本次已停止执行”解除等待，**不把它写成业务cancelled**。停止意图按本人真实run/提交映射定位；伪造run不再污染映射，旧run迟到不改新attempt，线程与删除检查保留。
- 390×844实看发现旧固定侧栏挤掉回答区：改为复用Dialog的移动端会话入口；窄屏可见企业切换。实际验证列表打开/选会话/焦点恢复及反馈可见，桌面侧栏保留。

### 模型、Skill、MCP、费用和Trace

- 标准LLMResult/ChatGeneration.message提取usage，校验来源冲突、别名冲突、非负整数及缓存/推理子集，流末/结束回调覆盖去重，不重复相加；非Chat LLM标准回调也验证。
- started与结束事实更新同一观测；逐尝试、逻辑调用、submission、平台run和父观测分开。缺结束证据保留started/unknown及null用量，不伪造失败、取消或免费。
- 主模型、同步normalizer、text_reduction、cleanup显式接线；DeepAgents默认子Agent不继承自定义中间件，因此显式接入守卫和观测。SDK内置摘要通过同名中间件覆盖保留原阈值/归档策略，只补观测；进程内调用声明保持摘要重试属于同一逻辑调用，避免依赖可变metadata。
- Skill候选/选择/真实加载/执行阶段分别记录，版本为实际材料摘要。空规则加载失败，不进入成功执行；观测不重复读取已加载规则，观测文件读失败不改业务结果。受控候选与模型的真实工具执行器/文件/PG测试不冒充Embedding效果评测。
- MCP使用服务+工具+attempt/submission逻辑标识；SDK默认吞掉isError的问题已修复，同时保留旧公共文本返回约定。实际网络尝试、授权前拒绝=0网络、重试、恢复/降级分开，不把同名跨服务混计。
- 模型/工具/费用事实、事件、outbox同事务；补齐父子/提交复合约束、FORCE RLS和最小角色。collector最多1000/100/2秒/3次重试，有积压/丢弃/关闭统计；内部健康端点受internal token保护。
- Decimal精确分项、版本化价格及半开生效区间；修订不覆盖旧价格。未知价格/usage不伪零，真实零可计费。重算遍历全部500条批次且幂等；汇总在数据库单快照按最新修订计算，按币种/用户/Graph聚合且分组截断可见。共享/未归因桶守恒，外部工具费用明确未采集。
- Langfuse应用metadata白名单、正文/工具/票据/推理脱敏；原生OTel跨度也经过出口过滤。SDK生成的resource/scope属性另列为基础设施元数据，不宣称全量metadata仅有应用白名单字段。Trace代理先验证本地归属，只返回本地允许的逐模型跨度与安全计数，不向浏览器发共享项目凭据或控制台旁路。
- 冷进程6图导入已用网络尝试即失败的护栏验证0次外呼（企业身份强制、Langfuse开启）。导入不做Embedding预热或MCP目录发现，LiteLLM使用随包表；首次语义匹配可能承担索引冷构建，未计入本地采集开销。MCP认证目录与选择运行时的接线属于下一项4，不能把导入期匿名发现当授权。
- 24条真实矩阵额外核对终态后发现DeepAgents三图的下划线名称导致收尾误判图不匹配，已统一为公开Graph ID并重跑受影响12条。最终矩阵24/24台账`succeeded`，保留修复前未知状态证据。
- 删除立即本地禁读、拒绝晚到/重放；local与external cleanup独立投递。远端异步删除未可见时进入outbox退避重试，读回为空才确认。真实备份恢复后先重施最新可信删除清单与数据库权限，再开放运行角色。

## 三、最终证据与验证边界

证据根目录：`docs/reviews/evidence/takeover-03-05/`。下表不混加不同套件次数。

| 检查 | 结果/边界 | 证据 |
|---|---|---|
| 管理端完整回归 | 183 passed / 3 skipped；两套新空库从零迁移 | admin-final.txt、final-fresh-migrations.txt |
| 根完整单元 | 362 passed / 75 skipped / 5既有失败；保留原断言 | root-unit-final.txt |
| 根默认集成 | 7 passed / 31 skipped / 1既有失败；live场景另有独立证据 | integration-final.txt |
| 前端契约 | 41 passed；包含scope时序、队列、迟到attempt、interrupted停止 | frontend-contracts-final.txt |
| 类型/干净生产构建 | 最终独立构建目录通过；未替换字体或依赖 | frontend-build-final.txt |
| 静态检查 | 改动Python Ruff/format、diff检查；前端premium静态审计 | static-final.json、premium-audit-final.json、import-network-guard.json |
| 原反馈反例 | CAS 200/409；异载荷409；原结果快照；复制重投/outbox；删除与分母 | backend-probes.json、integrity-probe.txt、comment-permission-probe.txt |
| 真实浏览器 | 两版评价独立、点踩意见、撤销、键盘、刷新、A/B/A、窄屏、用户停止；3次复制/2个回答，DOM完成及未完成计时真实落库 | browser-feedback.json、browser-stop-final.json；可见画面在本聊天工具记录 |
| 业务日 | 上海00:00与UTC前日16:00等价、半开上界、窗口外改当前评价 | business-day.json |
| 六图矩阵 | 24条native/BFF×流式/非流式，全部实际主模型成功usage；24条台账成功 | runtime-matrix.json、runtime-matrix-provenance.json、final-environment.json |
| 真正中断恢复 | 1个attempt，2个submission/平台run；旧观测缺结束，恢复观测成功，归属分别保留 | native-bff-resume.json |
| 子Agent/摘要 | 受控模型+真实SDK/身份/PG：2主+1子+1摘要，4逻辑调用、60Token，子父关联；4个真实Langfuse跨度回读 | deepagents-langfuse-readback.json |
| Skill阶段 | 受控router/模型，真实文件、工具执行器、身份与PG；有效规则4阶段，空规则无成功执行 | skill-stages-True.json、skill-stages-False.json |
| 受控MCP | 真实协议子进程+PG绑定，两企业两服务同名工具；2次技术失败后成功，取消，撤销后网络增量0；9条均写入 | controlled-mcp.json、controlled-mcp-tests.txt |
| 费用批次 | 501条扫描，500条新修订，重放0新增；USD0.0350000000与CNY0.0000600000分开，用户/Graph守恒 | cost-501.json |
| Langfuse隐私与代理 | 最新24条映射24条真实可见，非空input/output字段0；本人代理confirmed、B和伪造404 | langfuse-scoped-readback.json、trace-proxy.json |
| 外部删除 | 本用例新建Trace真实读回，异步传播3次重试后清理，远端最终0行 | langfuse-cleanup.json |
| 恢复与受限worker | 真实pg_dump/pg_restore无ACL、重施权限/删除、旧意见不可读、晚到拒绝；worker非super/nonBYPASS、无payload列读取权 | restore-deletion.json、worker-fresh.txt |
| 本地采集开销 | 35样本×2轮；开时每组80入队/80写入、无丢弃/失败；P95增量当前为1.442ms，阈值50ms | overhead-benchmark.json |

性能只指受控模型的本地采集路径，排除真实LLM网关/Langfuse网络等待，尚未作生产并发负载或长时稳定性验收。根5项失败为checkpointer关闭与4项已废弃knowledge_route缓存接口；默认集成1项为500k旧monkeypatch路径。未以删除/放宽断言取得通过。可选live测试默认跳过，表中真实MCP/恢复/外部删除均单独显式执行。

## 四、调用点覆盖表

| 图/调用点 | 采集路径 | 角色/父子 | 当前证据 |
|---|---|---|---|
| basic-qa / intelligent-analysis | business_graph.nodes.call_model | main；显式逻辑调用/最多3尝试 | 真实4入口组合/每图 |
| data-analysis | nodes.call_model / finalize_output | main / cleanup；菜单Skill单独 | 真实4入口组合；辅助usage由受控回归 |
| 三张DeepAgents图 | ObservabilityModelMiddleware | main，实际SDK调用 | 真实每图4入口组合 |
| 默认general-purpose | observed_subagents | subagent；关联发出task的主观测 | 真实SDK受控模型+真实远端跨度 |
| SDK内置历史摘要 | observed_summarization | summarizer；原触发/重试规则不变 | 真实SDK受控模型+真实远端跨度 |
| parse_region_tool | common.tools / invocation_config | normalizer，sync/async外呼身份守卫 | 标准回调与授权回归；不声称全辅助生产模型矩阵 |
| 工具大结果压缩 | common.text_reduction | summarizer | 受控回归与调用点检查 |
| Skill语义/菜单/文件加载 | skill_discovery、skill_content、data_analysis、observability.middleware | matched/selected/loaded/executed，实际材料摘要 | 真实工具/文件/PG阶段fixture，Embedding质量未验 |
| MCP/本地工具 | tool_wrappers、mcp_scoped、tool_adapter | service/tool逻辑调用与物理尝试；授权拒绝不外呼 | 真实受控MCP/既有19项授权回归 |

## 五、迁移、开关、运行与恢复

- 两条作者分支0011_feedback / 0012_observability均保留，经0013_merge汇合，再顺序0011f_integrity →0012o_integrity →0012o_pricing → **0012o_delivery**。从0010升级与空库迁移在隔离实例内验证。生产不直接回退DDL：用备份恢复并重施权限与最新删除清单。
- root/admin的`.env.example`只给非秘密配置形状：`AGENT_INTERACTIONS_ENABLED=false`、`AGENT_INTERACTIONS_ENABLED_AT=`、`AGENT_OBSERVABILITY_ENABLED=false`。迁移后再显式启用，时间必须含时区；空值保持未采集，不能据部署猜历史启用时间。
- 队列配置：MAX_QUEUE=1000、MAX_BATCH=100、FLUSH_WAIT_SECONDS=2、MAX_RETRIES=3（均带AGENT_OBSERVABILITY_前缀）；OTel MAX_QUEUE_SIZE=1000 / MAX_EXPORT_BATCH_SIZE=100。0队列为关闭，不是无界。
- Langfuse在runtime、admin代理、外部清理worker使用同一已核验项目与`LANGFUSE_PROJECT_SCOPE`；密钥仅服务端配置。企业普通用户不能读取内部全局collector统计，健康端点使用既有internal token。
- 管理权限：本人反馈/本人观测；运营明细agent:operations:read，意见还需agent:content:read；聚合agent:metrics:read；价格agent:pricing:manage独立授予与审计。
- outbox启动包含`observability_projection`、`deletion_cleanup`、`external_trace_cleanup`。worker用专门受限角色；完成local cleanup不代表外部完成，外部失败/死信需告警与重放。当前真实外部删除证明一次投递闭环，晚到远端导出与灾备后仍需按清单重施并复查，不承诺第三方存储永久无副本。
- `scripts/restore_ledger_permissions.py`重施RLS/GRANT；`scripts/reapply_thread_deletions.py`支持导出/预览/应用可信最新清单。先关闭恢复库读入口，恢复备份、重施权限和删除，再开放服务；manifest从可信独立来源取得，不能信任旧备份自带的删除历史。
- 真实验收服务用独立8107/3107/2031、独立业务库/原生库与专有Redis；额外空库验证在无宿主端口、无持久卷的临时PG内。没有对已有业务集群新增持久LOGIN账号，也未改主.env/compose/配置模型/依赖锁。临时凭据仅在私有/tmp目录，不写入证据。
- 可复验管理端需同时加载root+admin锁定依赖与显式测试库/运行角色、feature flags；默认admin独立依赖可跳过root SDK专项。真实服务专项需显式设置相应AIR_AGENT_*测试开关。不得把默认skip计入真实通过。

## 六、保留的门禁与下一步

1. **系统剪贴板文本一致性**：按钮等待writeText成功、失败不记成功、真实事件数和提示已验；IAB的剪贴板读回为空、外部Chrome不可用，不能证实物理剪贴板文本。Change3任务4.1继续未勾选；这不阻塞Change4的授权目录/选择协议开发，但阻塞把本项标成“所有实机条件已通过”或自动归档。
2. **真实企业MCP协议/企业授权材料**尚未提供，继续禁用；受控fixture成功只证明本地协议、授权和计量实现。后续模型不得为演示开启未验证协议或替换为全局账号。
3. **生产上线/长时负载**：当前是本地组合验收，没有部署生产或全并发SLO承诺。辅助各提供商的流块差异仅按已测路径/明确fixture覆盖，未知usage保持缺失。观测故障不放宽业务准入。
4. **下一项改为IAM抽取可行性与HTTP契约评审**，见 `openspec/changes/extract-shared-iam-service/`。按用户最新指示暂停Change4目标提示词；其目录目前只有规划脚手架，不代表可实施。3/5的业务事实、指标和隔离成果保留，IAM只替换身份提供方。任何后续工作区必须包含本次未提交源码，不能只从HEAD启动。

逐规格场景证据索引见本目录的`takeover-03-05-spec-evidence.md`。以上门禁不以OpenSpec strict或作者勾选替代。


## 2026-10-15 用户接受与归档收尾

用户明确指示归档3/5、视为3完成且不再做最后实机项。Change3关闭32/32（31完成+1用户豁免），Change5关闭39/39；剪贴板产品要求仍保留，豁免的是本轮实机验收，不能补造已通过证据。生产企业MCP、生产负载等原门禁不因此放宽。

主规格将同步本次7项能力，归档路径为`openspec/changes/archive/2026-10-15-add-chat-feedback-interactions`及`2026-10-15-complete-agent-observability`。用户已授权分批commit/push，实际提交/远端结果与容器清理清单在`docs/reviews/enterprise-closeout-2026-10-15.md`记录。旧作者输入指纹与匿名58/55分维持不变。
