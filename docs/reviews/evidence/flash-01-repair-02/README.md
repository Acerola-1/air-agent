# 第二次修复独立证据

`anonymous-input.json` 是接管前关键文件哈希；`*-anonymous.txt` 是匿名输入实测。`json-resume-before.txt`、`sdk-retry-before.txt`、`stop-race-before.txt` 保存先失败的反例。`*-codex.txt`、`migration-codex.txt` 和 `recovery.json` 是接管后验证，不计匿名模型分数。

最终：管理117；根单元279/26skip/5基线失败；集成20/7skip/1基线失败；前端19；两端类型及隔离生产构建通过。基线失败详情见独立报告。真实模型单次成功见`model-smoke.json`；浏览器确认弹窗截图`ui-switch-dialog.png`。真实企业MCP仍未验收。

## 复跑环境和边界

根项目、admin/api、两个前端各使用既有依赖环境。PG17由本机Apple Container运行。`fresh-migrations.json`列本轮新建的两套验收库及0001→0006迁移；OWNER/RUNTIME连接来自受保护环境，仅替换库名，不在文档记录口令。第一套供管理API，第二套供RLS/迁移测试。真实API仍连接原本业务库，`TENANT_E2E_OWNER_URI`必须指向该服务对应库，不可误用专用管理测试库；真实用例只操作自行生成的合成线程/归档。

- 单元：根`.venv/bin/python -m pytest tests/unit_tests -q`。MCP fixture需允许监听本机临时端口；5项既有失败不是通过。
- 管理：在`admin/api`，DATABASE_URI/ADMIN_RUNTIME_DATABASE_URI设第一套专用库，TENANT_TEST_OWNER_URI/TENANT_TEST_RUNTIME_URI设第二套，然后`.venv/bin/python -m pytest tests -q`。
- 集成：根`.venv/bin/python -m pytest tests/integration_tests -q`。上述专用PG变量、RUN_TENANT_LIVE_E2E=1、REVIEW_FIXTURE_PASSWORD、INTERNAL_API_TOKEN、TENANT_E2E_OWNER_URI由受保护环境提供；ARCHIVE_SCOPE_KEYS与实际服务一致。服务端缺少这些条件的skip不算通过。
- 前端：`frontend`执行`node --test tests/chat-contract.test.cjs`及两端各自`tsc --noEmit`；本轮生产构建在不含秘密的临时副本用webpack执行，日志保存，避免覆盖开发服务的.next。
- 恢复：`admin/api/.venv/bin/python admin/api/scripts/drill_recovery.py --output /tmp/air-agent-identity-recovery.json`（从根执行时），配置本机专用TENANT_TEST_OWNER_URI。仅脚本自己新建的随机库被清理，不触及原业务卷。
- OpenSpec：两个change均执行strict validate；相关Ruff与git diff --check通过。

`change2-source-baseline.json`以HEAD+393个非秘密代码/身份规格文件哈希冻结本地实施起点，尚未生成验收实现提交。实施前逐项核对sha256；删除/新增关键接口也应记录。`review-inventory.json`列本轮重点审阅/明确跳过范围。证据不是六图长运行和完整浏览器性能验收的替代品。
