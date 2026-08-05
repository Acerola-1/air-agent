## 1. 修改 Graph Backend 配置

- [x] 1.1 修改 `src/basic_qa/graph.py`：将 `FilesystemBackend(root_dir=str(Path(__file__).parent), virtual_mode=True)` 替换为 `CompositeBackend(default=StateBackend(), routes={"/skills/": FilesystemBackend(root_dir=str(Path(__file__).parent), virtual_mode=True)})`，添加 `CompositeBackend` 和 `StateBackend` 的 import
- [x] 1.2 修改 `src/intelligent_analysis/graph.py`：同 1.1 的替换模式
- [x] 1.3 修改 `src/data_analysis/graph.py`：同 1.1 的替换模式
- [x] 1.4 修改 `src/deep_research/graph.py`：同 1.1 的替换模式
- [x] 1.5 修改 `src/intelligent_report/graph.py`：同 1.1 的替换模式
- [x] 1.6 修改 `src/intelligent_tracing/graph.py`：同 1.1 的替换模式

## 2. 验证 Skill 文件加载

- [x] 2.1 编译所有 6 个 graph.py，确认无语法和 import 错误
- [x] 2.2 验证 basic-qa graph 的 SkillsMiddleware 能通过 CompositeBackend 正确加载 Skill 文件列表
- [x] 2.3 验证 data-analysis graph 的 Skill 加载路径兼容性（使用 create_data_analysis_find_skill_tool）

## 3. 清理 Agent 生成文件

- [x] 3.1 删除 `src/basic_qa/` 目录下 agent 生成的 .py 文件（排除 graph.py、__init__.py、skill_discovery.py 等源码文件）
- [x] 3.2 删除 `src/basic_qa/` 目录下其他 agent 生成的非源码文件（.sh、.txt、.md、tmp/ 目录等）

## 4. 代码质量检查

- [x] 4.1 对修改的 6 个 graph.py 运行 `ruff check`
- [x] 4.2 对修改的 6 个 graph.py 运行 `python3 -m compileall` 确认编译通过
