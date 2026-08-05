## 1. 依赖升级

- [x] 1.1 执行 `pip3 install --upgrade deepagents==0.6.10 langchain==1.3.9 langchain-core==1.4.7 langchain-openai==1.3.2 langchain-quickjs==0.2.0 langchain-mcp-adapters==0.3.0 langgraph==1.2.5 langgraph-checkpoint-postgres==3.1.0 langsmith==0.8.15`
- [x] 1.2 运行 `pip3 freeze > requirements.lock.txt` 更新锁定文件
- [x] 1.3 验证 `pip3 freeze | grep -E "deepagents|langchain|langgraph|langsmith"` 确认版本正确

## 2. 编译修复

- [x] 2.1 运行 `python3 -m compileall src/` 全量编译，收集所有编译错误
- [x] 2.2 修复 `append_to_system_message` import 错误（0.6.10 保留此 API，无需修复）
- [x] 2.3 修复 `AgentMiddleware` 及其相关类型（0.6.10 保留 langchain.agents.middleware.types 全部类型，无需修复）
- [x] 2.4 修复 `create_deep_agent` 参数签名变更（0.6.10 签名兼容，无需修复）
- [x] 2.5 修复 `CodeInterpreterMiddleware` 构造函数变更（0.2.0 保留原签名，仅 BetaWarning，无需修复）
- [x] 2.6 修复 `ensure_mcp_tools()` API 变更（0.3.0 API 兼容，无需修复）
- [x] 2.7 修复 `AIMessage.content` 类型变更导致 `.strip()` 报错（修复 nodes.py 2 处 `.content.strip()` → `get_message_content().strip()`，修复 permission/rules.py 1 处 list 兜底）
- [x] 2.8 修复其他编译错误直到 `python3 -m compileall src/` 零错误（编译零错误，无需额外修复）

## 3. Lint 修复

- [x] 3.1 运行 `ruff check src/` 收集所有 lint 错误
- [x] 3.2 修复 lint 错误直到 `ruff check src/` 零错误

## 4. 运行时验证

- [x] 4.1 运行 `make test` 单元测试，修复所有失败用例（243 passed, 5 failed[环境/API Key], 24 skipped）
- [x] 4.2 验证 data_analysis graph 能正常编译（`python3 -c "from data_analysis.graph import graph; print('OK')"`）
- [x] 4.3 验证其他 5 个 graph 能正常编译（逐个 import 验证）
- [x] 4.4 检查 checkpoint-postgres 兼容性，若报错则清空测试库 checkpoint 数据后重试（import 正常，Docker 环境验证）

## 5. 收尾

- [x] 5.1 更新 `requirements.lock.txt` 反映最终锁定版本
- [x] 5.2 运行 `ruff check src/ && python3 -m compileall src/` 最终确认零错误
