# 一键启动独立验收（2026-10-29）

结论：**正常重复启动通过，整体暂不验收通过**。本轮只评审与验证，未修改启动器、Compose 或业务代码。

## 范围与基线

- 分支 `dev`，HEAD `7d79526bb4cdd4c5d04911f62943fd11a50c9153`；评审对象包含工作区尚未提交的启动优化，不能仅用 HEAD 标识受测代码。
- 范围：`run-local.sh`、`deployment/run_separated_local.py`、`docker-compose.yaml`、相关启动文档与启动器单测。其余已有工作区改动保留。
- 按 IAM 仓库 `docs/CURRENT-RUNTIME.md` 与 `runtime-manifest.json` 核对正式环境；未创建测试容器、测试库，未迁移、重建或清空正式数据库。
- 原始执行记录与代码哈希：`.local/oneclick-startup-review-20261029-160552/`（Git 忽略，本机证据）。

## 实测结果

- 正式环境连续两次执行默认 `./run-local.sh`：退出码均为 0，分别耗时 **1.10 秒、1.16 秒**。
- 前后均为 18 个正式保留容器；没有新增、缺失、重启或重建容器（比对名称、状态、启动时间、配置网络）。
- 最终 `./run-local.sh status` 返回 0；聊天、IAM、管理 API、知识网关、LangGraph 和 Langfuse 的对应 HTTP 检查通过。
- 在正式 LangGraph 容器内，使用既有项目凭据只读访问 Langfuse `/api/public/projects`，HTTP 200。没有生成合成业务 Trace。
- `tests/unit_tests/test_separated_local_launcher.py`：20 项通过；受影响 Python 文件 Ruff 检查、格式检查、Shell 语法与相关 diff 空白检查通过。
- 没有停止正式数据库进行整栈冷启动；机器/容器系统关闭后恢复、真实网段切换及 Langfuse 网络迁移未作现场故障演练。本次成功仅证明当前配置的重复启动，不能替代这些验收。

## 必须修复的问题

### 1. P1：自动替换网关会修改无关内网服务地址

位置：`deployment/run_separated_local.py:895`，调用 `:300` 的 `_render_gateway_value`。

当前遍历全部 dotenv 配置；只要一个 HTTP(S) URL 的主机为非公网 IPv4 且末段是 `.1`，就替换为本机容器网关。完整 `_native_env_bytes` 合成输入已复现：`INTERNAL_MCP_URL=http://10.20.30.1:8000/mcp` 被改为 `http://192.168.65.1:8000/mcp`。这会使合法内网连接失效；末段 `.1` 不足以判断该地址属于 Apple container 宿主桥。

建议：按已知宿主桥配置项明确列出替换范围，验证这些配置与目标路径；保留其他 MCP、检索及业务服务地址。补充“其他内网地址以 .1 结尾仍不得修改”的回归。

### 2. P1：Langfuse 的重复启动分支可能虚报成功

位置：`deployment/run_separated_local.py:1717`。

当六个容器状态都是 `running` 且网络正确时，函数立即返回，没有检查 HTTP。通过受控替身复现：把健康检查设置为失败，执行完整 `_up` 调用路径并用成功的宿主启动替身隔离后续步骤，返回仍为 0，健康检查调用次数为 0。这是异常路径验证，不是向正式环境注入故障；当前真实 Langfuse 是健康的。

类似遗漏位于 `:1585`：既有依赖容器只要是 `running` 就被移出等待范围；只检查本次启动的容器端口。受控复现中，运行中的 PostgreSQL 即使端口不可用，也没有触发端口探测。部分依赖故障会由后续预检间接发现，但不能依赖该行为覆盖整套组件。

建议：所有分支汇合到相同的必要健康检查；容器无需重复启动，但必须核对服务就绪。Langfuse 故障时启动命令应等待恢复或明确非零退出，不能只有单独的 `status` 能发现。

### 3. P2：失败命令的输出过滤不能保证隐藏凭据

位置：`deployment/run_separated_local.py:165`。

`_command_tail` 按行过滤 password/secret/token/key= 等单词，保留下来的文本会进入公开错误。合成 stderr `connection failed: postgresql://fixture:fixture-credential-123@127.0.0.1/db` 中的凭据仍被原样保留。没有使用或输出真实凭据，不能据此声称当前已经发生真实泄露。

建议：终端只输出受控错误类别与退出码；如需展示命令尾部，应使用覆盖 URL 用户信息、认证头等形式的现有脱敏机制，并用不含上述关键词的合成凭据验证。

## 其余待补齐事项

- `.env.langfuse.example` 仍使用 `langfuse-postgres` 等裸服务名；新设计及本机私有配置采用固定网关。自定义 Apple container 网络的服务名解析不能假定可用，需统一模板与文档并验证从模板配置的路径。本机已配置为 `10.99.0.1`，所以本轮正常运行没有证明模板可用。
- README 的“完整栈”示例仍先列 `start`，新文档则要求 `up`；`start` 仍可能启动 LangGraph 容器，不能描述为完全“不动容器”。旧文档中“输出丢弃”“不自动启动依赖”和固定旧桥地址的说明，也应与当前入口及私有输出文件行为一致。
- 增加覆盖 `up` 的场景测试：停止组件恢复、运行但不健康、依赖缺失、网段变化、迁移失败及重复执行；现有 20 项单测没有覆盖完整启动编排。

这些问题修复并完成相应验证后，再给出“一键启动整体通过”的结论。当前正式服务保持正常运行。
