# 旧会话清理运行说明

代码：`scripts/purge_legacy_conversations.py`。默认 dry-run，`--apply` 才写入；按线程分批，失败可 `--resume`。本说明对应 change `retire-chat-modes-and-artifact-tools`。

## 范围

删除：

- LangGraph：`thread`、`run`、`checkpoints`、`checkpoint_blobs`、`checkpoint_writes`、`checkpoint_delete_queue`、`thread_ttl`、`cron`、`thread_ownership`；`store` 若非空则整体删除（跨线程记忆）。
- 台账正文：回答正文与摘要、原文快照、反馈意见、处理说明、处理动作快照、知识引用整行、工具归档、计算记录、问题意图/来源/缺口正文、问题簇文本。
- 浏览器 localStorage：`air-agent-chat-mode`、`air-agent-artifacts:*`（页面挂载时清理）。
- 遗留 SQLite：`db/checkpoints.db` 及其 `-wal`/`-shm`（需确认无进程占用）。

保留：

- 审计事实：`agent_requests`、`agent_submissions`、`agent_run_attempts`、`agent_answers`、`agent_events`、`agent_outbox`、能力选择与工具调用事实。
- `agent_thread_deletions` 墓碑（先导出，再保留表行）。
- `assistant`/`assistant_versions`、`audit_logs`、IAM/账号/成员/角色/权限表。
- 证据目录与历史报告文件。
- Langfuse 历史 trace（不在本脚本范围，也不导出）。

## 前置条件

1. 发布完成，且新代码已不再写入旧会话字段。
2. 停止写入端：`air-agent-langgraph-api`、admin API(8100)、chat BFF(3000)、admin web(3100) 及任何 outbox/insight worker。
3. 确认 `run.status` 无 `pending/queued/running`；有活动运行时先通过应用停止并等待终态。
4. 确认无应用数据库连接：脚本在 `--apply` 时默认拒绝其他连接。
5. 全库备份并做恢复演练（见下）。
6. 导出墓碑 CSV。
7. `lsof db/checkpoints.db db/checkpoints.db-wal db/checkpoints.db-shm` 无输出后，才可加 `--delete-sqlite`。

## 命令

设置 owner DSN（不要写入仓库或日志）：

```bash
export PURGE_DATABASE_URL='postgresql://air_agent:<password>@127.0.0.1:15432/air_agent_business'
```

备份、校验与恢复演练（在容器内执行 pg_dump / pg_restore，文件落在仓库外）：

```bash
mkdir -p ~/.local/share/air-agent-backups
out=~/.local/share/air-agent-backups/air_agent_business-$(date +%Y%m%d-%H%M%S).dump
container exec air-agent-postgres pg_dump -U air_agent -d air_agent_business -Fc > "$out"
shasum -a 256 "$out" | tee "$out.sha256"

drill=restore_drill_$(date +%H%M%S | tr 'A-Z' 'a-z')
container exec air-agent-postgres psql -U air_agent -d postgres -c "CREATE DATABASE $drill"
cat "$out" | container exec -i air-agent-postgres pg_restore -U air_agent -d "$drill" -Fc --no-owner --no-privileges
container exec air-agent-postgres psql -U air_agent -d "$drill" -c "SELECT count(*) FROM thread;"
container exec air-agent-postgres psql -U air_agent -d postgres -c "DROP DATABASE \"$drill\" WITH (FORCE)"
```

dry-run（只读；输出各表行数与批次数）：

```bash
.venv/bin/python scripts/purge_legacy_conversations.py \
  --dsn "$PURGE_DATABASE_URL" \
  --export-tombstones ~/.local/share/air-agent-backups/thread-deletions-$(date +%Y%m%d).csv
```

`--apply`：

```bash
.venv/bin/python scripts/purge_legacy_conversations.py \
  --dsn "$PURGE_DATABASE_URL" \
  --apply \
  --backup-file ~/.local/share/air-agent-backups/air_agent_business-<timestamp>.dump \
  --delete-sqlite
```

中断后继续：追加 `--resume --progress <同一进度文件>`。默认进度文件在 `~/.local/share/air-agent-backups/purge-progress-*.json`。

## 验收

- `thread/run/checkpoints/checkpoint_blobs/checkpoint_writes/thread_ownership` 为 0；`store` 为 0。
- `agent_answer_contents`、`agent_answer_content_metadata`、`knowledge_citations`、`artifact_computations`、`tool_artifact_storage`、问题意图/来源/内容表为 0。
- `agent_content_refs.content_snapshot` 为空且 `deleted_at` 非空；反馈意见/说明文本为空且 `deleted_at` 非空；处理动作快照为 NULL；问题簇文本清空。
- `agent_thread_deletions` 行数与导出 CSV 一致；`agent_requests/events/outbox/answers`、`assistant`、IAM/账号表数量不变。
- 重启全部服务，用 root 账号登录并成功发送一条新消息。
- 浏览器 localStorage 不再包含旧键；`air_agent_user_id` 保留。

## 失败与回滚

- 任一阶段失败：修复后 `--resume`；已完成批次和阶段按进度文件跳过。
- 已清理会话不可恢复。需要回滚时：用备份 `pg_restore` 到空库并切换 DSN，然后重放 `agent_thread_deletions` 中的墓碑（脚本不删墓碑表）。
- Langfuse 保留历史 trace，不作为“已清空”宣传。

## 已知限制

- `data-analysis` 前端入口已移除，但后端仍注册；持有 `agent:business:data-analysis:use` 的用户可通过 BFF/原生 API 指定 `assistant_id=data-analysis` 调用。
- `common/storage/artifact_db.py` 与 `common/knowledge/store.py` 仍保留 SQLite 回退路径；当前运行使用 Postgres，遗留 SQLite 文件删除后如触发回退会重新创建空库。
- `agent_question_cluster_vectors` 为聚类向量，不含可读原文，本次不删除；问题簇文本清空后其语义参考价值有限。
