# 匿名02首修独立证据

此目录由协调者独立创建，保留作者 `flash-02-repair-01/`及首交证据。输入指纹固定于评审记录修改前。3套隔离PG迁到0008，角色/URI仅存本地mode600配置；原测试fixture密码仅从私有文件读取，不在此目录。

- `live-start.json`：真实BFF首笔409/重试202，合法native200/实际换图200，换图暂停首节点；清理合成线程204。
- `resume-deletion.json`：管理API已受理恢复保留request/attempt但start-claim业务mismatch；待删除正文404，显式模拟清理完成后明细/新写200。未声称实际人工等待恢复已跑通或清理后原文泄漏。
- `native-lifecycle.json`：真实主模型最小答案，平台success/台账succeeded/答案1，平台run ID未保存；清理204。
- `restore-drill.json`：新随机专用库，备份后删除→恢复旧备份→重施删除和权限，数据库演练通过；不是运行API永久删除边界通过。
- `unit/admin/ledger/contract/integration/build.txt`：独立原始测试输出；失败保持基线单列，测试秒数不代表应用性能。
- `review-inventory.json`：OCR候选清单，逐文件评审/跳过原因，不声称全部混合工作树审查。
- `findings.json`：结构化源码定位。
- `cleanup.json`：仅本轮独立创建数据库/角色清理，保留业务卷和作者测试环境。

可复跑：从仓库根使用管理端venv执行 `setup_isolated.py`，它创建随机专用库并把秘密配置写到 `/tmp/air-agent-ivrp01-env.json`；然后用根venv执行 `run_isolated_tests.py ledger` 或 `integration`，管理端venv执行 `run_isolated_tests.py admin`。恢复探针 `probe_restore.py` 内的DB名是本轮随机值，**再次运行前必须重新随机命名**，不要DROP已有同名库。命令需本地容器/PG权限，probe原生部分需管理/聊天/原生服务和已授权专用fixture登录。fixture私有文件不可用时改用本地环境变量，不重置真实账号来绕过。

各probe会产生新的合成记录和JSON；再次复跑请将输出改到新的独立目录，保留本次证据。异步清理只对本轮合成线程；关闭fixture登录，不清理其他数据。独立配置已在本轮结束清理，迁移日志不含URI。
