# 轮次01独立验收证据

这些脚本由评审者编写，只用于复现首交问题；不是匿名模型的产品代码，也不是修复后完整验收套件。JSON保存原始脱敏观察，修复不得覆盖它们。所有密码/票据/凭据来自受保护环境或脚本内专用库的合成输入，不输出真实凭据。

## 已保存的观察

- `live.json`：实际8100管理API、3000聊天代理、2024原生API以及PG表策略；只访问本地验收账号和新建的合成线程，清理线程并登出新会话。`native_patch_keeps_trusted_owner=true`为有效正向结果。
- `admin-proofs.json`：真实独立PG + ASGI执行实际管理路由、迁移及票据签发/验证；含受控模拟历史thread表，不能称为真实平台metadata迁移证据。
- `runtime.json`：AsyncMock替换状态/绑定查询的单元反例，实际revalidate/resolve_binding代码执行；不是生产撤权或真实MCP验收。
- `empty.json`：全新独立空库的实际Alembic结果，只保存退出码与缺空格SQL匹配，不保存连接串。
- `source-manifest.json`：OCR文件清单、指纹与审查覆盖；1项Codex规划排除。原输入总览102，已审查101，排除1；不把源文件扫读比例当场景通过率。

## 环境和复跑

从仓库根执行，先在受保护环境准备真实密钥和本地连接；不把.env完整输出。`TENANT_TEST_OWNER_URI/TENANT_TEST_RUNTIME_URI`必须指向`air_agent_tenant_test`，诊断脚本从它们派生新的专用库名。PG运行角色须已按第一项安全引导存在。实际HTTP探针另外需要现有本地`acc_alice/acc_bob`验收账号，口令放`REVIEW_FIXTURE_PASSWORD`环境，不能用生产账号代替。

```sh
# 空库诊断：每次新建一个 air_agent_review01_empty_* 库；不删除已有库
.venv/bin/python docs/reviews/evidence/flash-01/empty_probe.py
# 原实现的“已有归档表”升级路径，创建独立API/RLS库并准备API权限/区划
admin/api/.venv/bin/python docs/reviews/evidence/flash-01/setup_probe.py
# RLS迁移测试库也需要权限定义这一前置目录
admin/api/.venv/bin/python docs/reviews/evidence/flash-01/seed_rls_probe.py
.venv/bin/python docs/reviews/evidence/flash-01/run_admin_suite.py
admin/api/.venv/bin/python docs/reviews/evidence/flash-01/admin_probe.py
.venv/bin/python docs/reviews/evidence/flash-01/runtime_probe.py
.venv/bin/python docs/reviews/evidence/flash-01/live_probe.py
```

`setup_probe.py`显式模拟原有归档表的旧库路径，是为了在已记录空库迁移失败后继续检查其他功能，不是修复0004，也不是证明空库通过。API库`air_agent_review01_api`、RLS库`air_agent_review01_rls`必须分开；RLS套件会清空它自己的企业fixture，不能指向业务库或API库。

脚本包含针对首交实现的诊断流程，修复后接口改变、冲突安全拒绝或重新迁移时可能提前结束；应将相同复现输入转为项目内正式回归，保留修复前失败证据和修复后期望。原live探针需要对它创建的空白合成线程暂移除ownership来模拟未知历史状态，然后恢复该合成对象归属并通过原生API删除；不得换成历史真实线程。

复验root套件要明确关闭外部Tracing（MCP fixture需要本机测试端口权限）：

```sh
LANGFUSE_ENABLED=false LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false .venv/bin/python -m pytest tests/unit_tests -q
LANGFUSE_ENABLED=false LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false .venv/bin/python -m pytest tests/integration_tests -q
npm --prefix frontend run test:contracts
(cd frontend && ./node_modules/.bin/tsc --noEmit)
(cd admin/web && ./node_modules/.bin/tsc --noEmit)
openspec validate add-tenant-identity-access --strict
```

独立结果为root237/26/5、集成7/7/1、admin94/4、前端契约8通过。沙箱网络错误、缺少前置目录的中间运行不计入模型评分；缺陷报告使用补齐前置条件后的结果。正式完整六图、浏览器/归档、构建、恢复及性能验收待阻塞修复后另记。
