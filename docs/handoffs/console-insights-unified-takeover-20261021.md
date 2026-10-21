# Change 6/7 联合验收接手说明

时间：2026-10-21。分支 `dev`，开始时 HEAD `336ad9b`。本轮没有 commit、push、合并、归档，也没有改 IAM 仓库，没有连接正式端口 `15432` / `19132`。

给下一位验收的人。不要把下面任何一项写成已经全部通过。OpenSpec `openspec/changes/validate-console-insights-live-workflows/tasks.md` 的勾选仍然全部未勾，这是故意的：跨层任务没做完就不能勾。

## 现在停在哪里

统一测试工具、隔离环境、固定 40 题 P/S 冒烟、Change 6 计数核对都有实测。Change 7 归类没有跑起来，因为 IAM 目录没有 `agent:insights:manage`。浏览器验收没有做。真实案例、外部模型、性能回归、容量、后台规模和组合负载都没有启动，也不要自行启动。

最后一次代码改动还没重新实测：受控模型从用户文本里提取城市名的正则已经写进 `scripts/unified_campaign/controlled.py`，对应单测断言也写了，但 pytest 和隔离溯源都没有在这版上再跑。不要把它当成已验证。

## 先读这些

| 用途 | 路径 |
|---|---|
| 原始目标 | `docs/handoffs/console-insights-performance-unified-goal.md` |
| 当日进度 | `docs/reviews/console-insights-unified-20261021/status.md` |
| 冒烟原文 | `docs/reviews/basic-qa-performance/20261021-ps-smoke/` |
| 冒烟当时的 6/7 核对 | `docs/reviews/console-insights-unified-20261021/ps-smoke-reconcile.md` |
| 后续探测后的 Change 6 | `docs/reviews/console-insights-unified-20261021/post-probe-change6.json` |
| 工具说明 | `docs/testing/unified-campaign-runner.md` |
| IAM 目录提议 | `docs/reviews/console-insights-unified-20261021/proposed-iam-v28-agent-insights-manage.sql` |

口令、DSN、cookie 只在 gitignore 的 `.local/campaign-20261021/`。不要把这些内容抄进报告或提交。

## 隔离环境

这些是本轮自建资源。接手时先看进程和容器是否还在，不要假设一直健康。正式栈不要动。

| 组件 | 地址或名称 |
|---|---|
| IAM Postgres | `aa-campaign-20261021-iam-pg`，`127.0.0.1:25441/campaign_iam`，Flyway V1–V27 |
| IAM Redis | `aa-campaign-20261021-iam-redis`，`127.0.0.1:25442` |
| IAM HTTP | `127.0.0.1:25445` |
| 业务 Postgres | `aa-campaign-20261021-biz-pg`，`127.0.0.1:25443/campaign_business`，Alembic `0029`，分离标记 `remote/ready` |
| 恢复副本 | 同实例上的 `campaign_restore`，不要把它当成正在服务的库 |
| 图 Redis | `aa-campaign-20261021-graph-redis`，`127.0.0.1:25444` |
| LangGraph | `aa-campaign-20261021-langgraph`，`127.0.0.1:25447`，镜像 `air-agent/langgraph-api:tracing-merge-20261020` |
| 管理 API | `127.0.0.1:25446` |
| BFF | `127.0.0.1:25448` |
| 受控模型 | `127.0.0.1:25449` |
| 宿主机桥 | `192.168.64.1:25450`，配置 `.local/campaign-20261021/bridge.json` |

容器重建后 IP 会变。LangGraph 环境文件和桥的来源地址都要用新 IP，不能把 `192.168.64.x/24` 整段写进 DSN。图容器访问宿主要用桥，不要用 `127.0.0.1`。官方镜像需要本机已有的 LangSmith 键，哑键会 403 后退出。不要把键写进文档。

启动脚本在 `.local/campaign-20261021/`：`start_admin.py`、`start_langgraph.py`、`start_bff.py`、`start_controlled.py`。受控模型脚本要带 `PYTHONPATH` 指向仓库根，否则找不到 `scripts.unified_campaign`。

企业 `campaign-a`。操作者 `campaign-operator`，角色 `campaign_operator`。BFF cookie 名是 `air_agent_iam_handle`。当前会话文件是 `.local/campaign-20261021/bff-session.json` 和 `bff-cookie`。会话会因授权代次变化变成 401，需要重新登录再切企业。管理 API 的写请求不要带 `Origin: http://127.0.0.1:25448`，否则 CSRF 403；不带 Origin 的本机调用可以过。

## 已有结果

### 工具和冒烟

`python -m scripts.unified_campaign` 会拒绝正式端口和正式库名，默认只有 P/S。固定题库 `bqa-reference-202605-v1` 共 40 题。代表场景在 `docs/testing/data/public-graph-representative-cases.v1.json`：`REP-DATA-001` 是 `data-analysis` / `CityHourBroadcast`，`REP-TRACE-001` 是 `intelligent-tracing`。

冒烟 run_id `20261021-121015-5a659c`。40 条计量都是 `stream_completed` 且有台账关联。另有 50 条历史轮和 2 条代表场景。`REP-DATA-001` 回答非空。`REP-TRACE-001` 原记录是空正文、`unknown`；受控模型补上 `assistant` 角色后的另一次重跑非空。原冒烟行没有改写。Token 未采集。这不是性能回归或容量通过。P95 样本远小于 200，只是探索值。

单测：`tests/unit_tests/test_unified_campaign.py`。冻结前委派那次改动之后的全文件 pytest 没有再跑完。桥的单测此前 9 项通过。

### Change 6

冒烟当时 `GET /agent/metrics/summary` 提问数 94，等于 92 条冒烟加之前 2 条连通探测。历史导入数是 0。

后来又有探测请求。排空 10 条 outbox 之后提问数是 105，与近 7 日非历史受理请求一致，排空没有把请求变没。冒烟的 40 / 50 / 2 都还在库里。这 50 条是测试清单里的历史轮次，不是 `identity_revision.historical`。5 条已落墓碑的非历史请求仍算在 105 里：提问统计不因墓碑减少，正文读取会因墓碑 404。

### Change 7

`POST /agent/insights/jobs` 用合法作业体仍是 403 `permission_denied`。总览归类数保持 0。没有启动归类 worker，也没有用库所有者插入作业。

IAM 仓库最高迁移是 V27。提议的下一份是 V28，只插入权限码 `agent:insights:manage`，id `01930000-0000-4000-8000-000000000515`，不 GRANT 给角色或 root。SQL 在上面的提议文件里。没有应用到 IAM 仓库，也没有应用到隔离 IAM 库。

### A 阶段里已经测过的边界

证据都在 `docs/reviews/console-insights-unified-20261021/`。

- 删除：墓碑写入后归档清理曾 503 并声称还没删除。现已允许删除路径读取墓碑；归属行没了但墓碑还在时，清理按完成返回。重复登记不追加删除事件。平台线程已经不在时重复删除返回 204。之后读会话和归档是 404，再运行是 403。
- 晚到写回：模型已收到 1 次调用后删除，之后模型调用为 0，该请求没有回答正文。对照请求在删除前有正文，删除后运营正文接口 404。
- 台账 worker：必须同时有 `LEDGER_WORKER_DATABASE_URI` 和 `RUNTIME_DATABASE_URI`。缺运行角色时任务失败后退避，没有死信。补上之后 524 条全部完成。调度角色密码与隔离库运行角色密码相同，这是本轮建库时的选择。
- 台账租约：未过期时另一个 worker 被拒绝且回执为 0；过期后接任者完成 1 条，原持有者确认被拒绝，原因 `lease_generation_moved`。这不是 Change 7 的归类租约。
- 运行中撤权：去掉 `agent:business:basic-qa:use` 后，已发出的那次调用不再增加，回答正文没有写入，但台账仍有 1 条回答行。新会话创建 401。权限后来加回，恢复后的新请求 200、1 次模型调用、1 条正文。
- 同一提交键并发两次：一次 200 流，一次 202。模型 1 次，请求、已确认提交、平台运行各 1 条。
- 备份恢复只在 `campaign_restore`。去掉授权后运行角色读原文被拒绝。重施 432 条授权后 114 条策略、139 条授权。副本保留 1 条已删除原文和 1 条未完成墓碑。洞察侧没有可处理作业。RLS 本身不隐藏 `deleted_at`，隐藏在读取查询。

撤权加回时，管理 API 在平台上下文下 INSERT 企业角色的资源权限会碰到 RLS，返回 403「无权执行该操作」。当时用隔离 IAM 的库所有者把 `agent:business:basic-qa:use` 插回 `campaign_operator`，触发器推进了授权代次。角色权限数回到 7。不要对正式 IAM 做这件事。

## 工作区里还没提交的产品改动

接手人自己看 diff，不要顺手还原别人的未提交文件。和本轮相关的主要是：

- `src/common/artifact_http.py`：只有归档删除可 `allow_deleted`；墓碑在而归属行没有时，删除仍可清理。
- `admin/api/app/services/ledger.py`：已有墓碑的删除登记直接返回，不再追加删除事件。
- `frontend/app/api/[..._path]/route.ts`：墓碑和归档清理已经成功、平台线程返回 404 时，重复删除返回 204。
- `admin/api/app/routers/feedback.py`：确认结果带 `request_id`。
- `deployment/local_host_bridge.py`：可选回环上游；缺省仍是原来的 `19100` 和 `7863`，并拒绝 `15432` 与 `19132`。
- `scripts/unified_campaign/`：发送、采集、受控依赖、守卫。
- 受控模型：普通请求返回固定非答案；提供 `freeze_tracing_request` 时先冻结，只有工具结果含 `frozen` 才发出四个 `task`。没有 gold，也没有结论。

## 溯源专员：不要误读

有两次隔离溯源，都不是专员执行通过。

1. 受控模型直接发了四个 `task`。站点专员抛出「缺少已冻结请求或可信作用域，拒绝取数」，另外三个是 `CancelledError`。平台运行是 error。线程 `bbec6398-868b-4e88-b1b7-185cf1b1a2ad` 的 `next` 仍可能显示四个 tools。台账尝试当时看到的是 `running`。这不能当成还在跑的成功任务。
2. 改成先冻结之后，新请求 `stream_completed`、正文非空、关联成功，模型 2 次，MCP 0 次。工具只有 `freeze_tracing_request`，结果是 `invalid`，消息是「缺少行政区名称，无法读取授权站点目录」。没有专员子执行。原因是当时没从模型收到的消息里抽出「洛阳市」。正则修补还在工作区，没有再跑。

就算城市名抽对了，隔离图的 MCP 连接仍是空的。冻结会去读站点目录，目录走 `ipp` 上的 `mcp_station_base_air_station_list`。没有受控 MCP 和 `approved_query_tools`，专员取数不会成功。不要用所有者身份伪造冻结请求来假装专员跑完。

## 不要替用户决定的事

- 不要把 V28 写进 IAM 仓库或隔离 IAM，除非用户明确授权改那个仓库。
- 不要插入洞察作业来绕过 403。
- 不要启动 R/F、后台规模、组合负载、未批准的真实案例或外部模型。
- 不要覆盖 `docs/reviews/basic-qa-performance/20261021-ps-smoke/`。新证据用新目录。
- 不要清理正式容器，也不要全局 prune。本轮容器名都带 `aa-campaign-20261021`。结束时只回收这一批，并先确认没有人还在用。
- 不要勾 OpenSpec 任务，除非该项的全部场景都有这次实测。冒烟不是性能通过，归类 403 不是归类通过。

## 建议的接手顺序

1. 看本文件和 `status.md`，再看 git diff，分清本轮改动和别人的未提交文件。
2. 确认隔离进程还在。不在就只用 `.local/campaign-20261021/` 的脚本拉起，并核对容器 IP。
3. 若继续溯源：先补上并实测城市名提取，再决定是否接一条只指向受控 MCP 的 `ipp` 连接。专员计数以子执行的 role 和 status 为准，不以线程状态里出现四个名字为准。
4. Change 7 归类、撤权后的归类租约、榜单到删除的浏览器链，都等 `agent:insights:manage` 进目录并且有浏览器条件后再做。
5. 用户明确选择范围、时间和费用之后，才做 B 或 C。
