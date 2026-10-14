# 匿名 01 首轮修复独立证据

这是 Codex 独立复验资料，产品实现方不得覆盖原观察。原首交证据仍在相邻 `../flash-01/`。本目录新建，不改变原始结果。完整结论见 `../../flash-blind-01-repair-01-review.md`。

## 观察来源

- `live-claim.json`：当前真实 admin API / 聊天 BFF / 原生 LangGraph / PG。Bob 新建空白合成线程；移除该测试对象归属模拟未知历史；Alice 经 BFF strict 创建同 ID，200 且读到合成私有标记。恢复 Bob 归属、原生删除 204、登出新会话。**不是历史用户对话。**
- `admin-proofs.json`：专用 PG + 实际管理 ASGI 路由及实际运行身份库访问。停用企业部门403但角色201；角色收权后成员revision不变、管理403而旧运行复查仍放行。
- `runtime-proofs.json`：实际 revalidate、BusinessGraphNodes、invoke_with_data_deadline、摘要函数，替身身份状态/模型，不发送真实 LLM。模拟首次模型失败后修订撤销，第二次仍外呼；缺快照仍放行；无身份摘要外呼。不得称真实六图长运行通过。
- `migration-live.json`：当前实际原生 API，单个新建空白合成线程，mt-v1 PATCH 在 migrated 归属提交前403、提交后200；恢复归属并删除204。
- `migration-proofs.json`：实际 MigrationRunner + 专用 PG，API替身使用独立 runtime连接复现真实auth可见性；首次pending=1，复跑already_applied，回调只1次。非真实完整历史迁移。
- `stop-proofs.json`：加载实际 Zustand store，复用项目契约测试加载方式，服务替身模拟409/未知/未找到，三者均confirmed=true；不是浏览器或真实取消性能证据。
- `fresh-migrations.json`：两套全新专用空库，按声明0001→0005升级，已有安全运行角色为明确前置。
- `admin-tests.txt` / `root-unit.txt` / `root-integration.txt`：独立套件输出。集成显式开启真实HTTP与PG专项。已知基线失败保留。
- `admin-build.txt` / `chat-build.txt` / `ruff.txt`：独立生产构建与定向静态检查；聊天因沙箱字体DNS失败后在同一构建副本放开公开字体网络重跑成功。
- `changed-inputs.json`：相对首交代码指纹的37个变化文件；`source-manifest.json`：本轮输入/检查范围。检查范围不等于场景通过率。

## 安全边界和复跑

从仓库根运行；已有受保护 `.env` 保存连接/内部密钥。`TENANT_TEST_OWNER_URI` / `TENANT_TEST_RUNTIME_URI` 必须为本机 `air_agent_tenant_test`（仅用来派生新的独立库名）。运行角色按项目部署前置引导为非owner/非BYPASSRLS。不要输出 `.env`、完整容器JSON、票据、cookie或真实密码。

HTTP探针额外需要本地测试账号 `acc_alice/acc_bob` 的口令，通过 `REVIEW_FIXTURE_PASSWORD` 受保护环境提供，不用生产账号；其所有新建资源都清理。专用API与破坏性RLS套件必须分库；禁止指向业务 `air_agent` 跑schema截断测试。运行脚本前检查用途及当前接口；这些是评审诊断输入，不取代项目回归。修复后反例的“观察结果”应改变，不能修改接受标准。

脚本默认把复跑输出写 `/tmp/air-agent-review02-replay`，可用 `REVIEW_REPAIR_OUTPUT_DIR` 指定新的独立目录；不得指定当前证据目录或首交目录。默认复用当前专用库元数据；先跑新建双库脚本则后续探针使用新派生库。

```sh
.venv/bin/python docs/reviews/evidence/flash-01-repair-01/run_fresh_admin.py
admin/api/.venv/bin/python docs/reviews/evidence/flash-01-repair-01/admin_probe.py
.venv/bin/python docs/reviews/evidence/flash-01-repair-01/runtime_probe.py
admin/api/.venv/bin/python docs/reviews/evidence/flash-01-repair-01/migration_probe.py
.venv/bin/python docs/reviews/evidence/flash-01-repair-01/live_claim_probe.py
.venv/bin/python docs/reviews/evidence/flash-01-repair-01/migration_live_probe.py
node docs/reviews/evidence/flash-01-repair-01/stop_probe.cjs
.venv/bin/python docs/reviews/evidence/flash-01-repair-01/run_integration.py
```

本轮评审脚本修正过接口引用与类型、构建副本路径；这些评审者中间错误不计为实现方缺陷。日志敏感值检查未发现真实配置值。无新产品代码、Git提交或OpenSpec归档。完整恢复、六图/浏览器矩阵与性能尚未实测。
