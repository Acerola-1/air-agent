# 迁移前图与持久化基线（进行中）

- 输入：`.local/graph-module-ownership/20261029-apply/preimplementation/` 的源码与 admin app；快照缺少管理 API 的 Alembic 配置和迁移目录，已将本次迁移开始前现存的配置、迁移和锁文件另行冻结在 `fixture-repo`，SHA-256 见 `migration-fingerprints.txt`。迁移文件未由本任务改写。
- 图拓扑：`scripts/graph_module_ownership/topology_baseline.py` 在迁移前源码进程调用两个公开 `graph` 工厂，产出 `topology-baseline.json`。两个默认 checkpointer 都是 `None`；阻断并计数 socket connect 后导入成功，计数 0。该计数仅说明被观察的 socket 路径，不等同所有可能外呼的完整审计。
- 环境：本任务命名容器 `graph-ownership-20261029-apply-{pg,redis,api}`；回环端口 27631–27636；新空 PG 和 Redis；配置中的镜像 `air-agent/langgraph-api:tracing-merge-20261020`；合成 IAM、模型及计数 MCP；源数据和证据均为合成，不连接正式业务库或正式 IAM。实验过程使用当时实测网络网关 `192.168.65.1`，复跑需重新检查网关与空闲端口。
- 第 01、02 次为空库迁移准备失败：快照缺失迁移配置；保留各轮私有目录与结果。第 03 次 BQA 实际 SDK 查询、重启后追问、暂停后重启恢复通过；合计模型请求 10、MCP 执行 3，v2 能力跨重启保留。原始证据 `native-baseline-03/result.json`。模型是确定性受控替身，并非真实模型质量或生产业务验收。
- 第 04 次 BQA 同路径成功，数据分析显式空工具选择在管理 API 受理时 409：夹具给管理 API 配置空 MCP，而 Agent 配置合成 MCP，目录摘要不一致；不能据此断言产品缺陷。完整日志在 `native-baseline-04-console.log`。此轮所有自有容器均已删除。
- 样本转储和合成夹具密钥只存在被忽略的 `.local` 目录。私有 dump 不代表已完成可恢复性验证；真实模型、摘要成功水位、两图升级与回退，以及跨版本执行恢复均须分别核验。
- 第 05、06 次两图自动选择基线通过。数据分析固定 `CityHourBroadcast` 选中 `broadcast-hour`，请求模型 2 次、合成 MCP 执行 0 次；两图合计模型 12 次、合成 MCP 执行 3 次。第 06 次另保留 BQA 停在 `call_model` 的独立旧线程。终答文本、完整事件流及真实业务系统尚未作为此报告的通过项。
- 第 03 次回读验证以 `native-baseline-06` dump 在另一空库、另一进程启动旧代码：BQA 读到 17 条消息、4 条工具结果和版本 2 能力；数据分析读到 3 条消息及 `broadcast-hour`；另一个线程读到 `next=[call_model]` 并经真实 SDK resume，模型 4 次、MCP 1 次。结果 `old-restore-03/result.json`；三个本任务容器已清除。第 02 次回读在端口残留 TIME_WAIT 时被探针误判占用，未创建资源，已修正预检并保留记录。
- 当前缺口：两图成功摘要水位样本、数据分析暂停与恢复、相同输入事件对比、新旧版本互读和真实配置模型均未完成；本报告不将 1.2、1.3 或 7.x 宣告完成。
- 第 07 次尝试在受控夹具将模型窗口缩至 16k 并降低摘要触发比例，首轮未激活预期 Skill/MCP，因正控断言失败；记录为受控配置下失败，不推断为迁移引入或正式配置问题。未取得水位样本，资源回收结果见该轮 `result.json`。

## 2026-10-30 对照补充

`native-baseline-data-11` 为完整旧样本：BQA v2能力与摘要水位、数据分析菜单／合成页面数据／合法预报工具结果／成功水位及两图 call_model 暂停；旧实现在新进程 `old-data-read-15` 回读通过。当前源码的 `current-native-21` 跑同一冻结载荷，两轮均为模型39次、工具8次；同拓扑 JSON 完全相同、九 Skill材料摘要无漂移。没有用这一成功的新会话对照冒充扩展旧样本的完整执行恢复。此前失败试验和局限保留，最终状态以 implementation.md 和 tasks.md 为准。

扩展恢复对照：`upgrade-data-22` 与 `old-data-resume-23` 在相同旧样本均为模型2／新预报工具0，schema相同，未满足夹具金标；不是只在新实现失败。`rollback-read-24` 则以旧代码实际SDK读取当前 `current-native-21` 新写的两图摘要／工具结果／暂停状态成功；不把回读成功等同所有恢复执行通过。
