# 场景矩阵（PASS / FAIL / PARTIAL / NOT_RUN）

## 前端 UI（真实 ego-browser）

| 场景 | 状态 | 说明/证据 |
|---|---|---|
| 登录成功、企业选择 | PASS | `full-a-legal` 登录并选择“全面测试甲”；`screenshots/chat-home.png` |
| 错误口令/登录 503 重试 | NOT_RUN | 未注入错误；不能拿正常登录代替 |
| 退出登录 | PARTIAL | 入口可见；聊天端退出流程未点验 |
| 三图业务切换 | PASS | 基础问答/数据分析/溯源追踪三项均出现并可切换 |
| 基础问答发送/逐段正文 | PASS | 受控流首可见 6905.9ms、完成 7471.2ms；`chat-streaming.png` |
| 停止生成 | PASS | 慢速流停止后显示“未完成…不是完整终答”，停止按钮消失；`chat-stop-incomplete.png` |
| 点赞/点踩/改评价/意见 | PASS | 点赞→点踩→选择“内容不完整”→意见→保存，显示“已保存” |
| 复制 | PARTIAL | 点击显示“已复制”；剪贴板内容未读取验证 |
| 重新生成/重试 | NOT_RUN | 按钮存在，未点击 |
| Enter 发送 | PASS | 浏览器实测 Enter 发送 |
| Shift+Enter、空内容、超长错误、重复点击 | NOT_RUN | 未逐项实测 |
| 中文 IME / 真实剪贴板 | NOT_RUN | 系统输入法与物理剪贴板无法由自动化冒充 |
| 历史打开 | PASS | 已打开既有线程列表 |
| 历史搜索 | FAIL | 既有标题搜索返回“没有匹配的会话” |
| 历史删除 | PARTIAL | 删除按钮存在；未完成“删除→404”确认 |
| Markdown/表格/引用/工具 JSON 不串乱 | NOT_RUN | 受控固定文本不覆盖 |
| done→values→回读不重复 | PARTIAL | 受控单线程 UI 无重复；真实长线程未覆盖 |
| 390×844 窄屏 | PASS | `chat-narrow-390x844.png` |
| 键盘 Tab/Escape/焦点恢复 | NOT_RUN | 未系统实测 |
| 数据分析菜单驱动 | FAIL | UI 没有菜单选择器；只发通用消息 |
| 溯源逐节/画布/暂停恢复 | NOT_RUN | 示例只得到澄清；未生成报告 |
| 复制/点赞事件可信 attempt 关联 | PARTIAL | UI 显示已保存；未逐条查 attempt 关联 |

## 后端业务与安全

| 场景 | 状态 | 说明/证据 |
|---|---|---|
| 2 企业 × 2 用户正反例 | PARTIAL | IAM 已建 2 企业各 2 用户；tenant-B API 请求链未跑 |
| 未授权企业目录/直接运行拒绝 | PASS | `/api/capabilities` 403 `business_not_permitted`；run 403 `ledger_admission_failed` |
| 非 owner/non-BYPASSRLS 运行角色 | PASS | `air_agent_runtime`；IAM/业务启动与查询均经该角色 |
| 事务 GUC/FORCE RLS/复合关联 | PASS | tenant A 199 行；tenant B 0；A 从 B 视角 0；`evidence/rls-isolation.json` |
| 跨租户/跨用户正文 | PARTIAL | 跨用户 content 404；own/owner 200；tenant-B 未建 |
| 受理幂等、并发同提交键 | NOT_RUN | 工具/时间限制 |
| 撤权后下一条受保护步骤失败关闭 | NOT_RUN | 本轮未执行运行中撤权 |
| 删除墓碑/晚到写/重试前撤权 | NOT_RUN | 上轮有历史证据，本轮未重跑 |
| 观测实际尝试/失败/重试/usage | PASS | 373 模型、352 工具；6 model failed、247 tool failed；usage 367/373 |
| 运营四权限分离 | PARTIAL | API metrics 200/ops 403；UI metrics 页被挡；content/feedback 另测 |
| 反馈 CAS/幂等 | PASS | 同 operation_id replayed=true；旧 revision 409；处理中→已处理 |
| Change 6 统计一致性 | PARTIAL | 总览提问数/尝试数与 DB 可读；未做发送前后逐项对账 |
| Change 7 启用→作业→榜单 | PARTIAL | 5 来源作业 completed；2 cluster/5 intent；rename+merge 200 |
| Change 7 assign/split/delete/租约/取消 | NOT_RUN | 时间限制 |

## 性能

| 场景 | 状态 | 说明 |
|---|---|---|
| P/S 受控 40 题 | PASS | 40/40 stream_completed；92/92 台账关联；首正文 2.36/3.64s，完整 2.67/3.92s |
| E/S 真实模型 40 题 | FAIL（含基线候选） | 32/40 按时完成；6 aborted + 2 timeout；质量 28/40 |
| E/S Q4 历史 50 轮 | PASS | 50/50 stream_completed，单独计量 |
| P/R 并发 1/5/10 × 3 | NOT_RUN | E/S 20% 失败超过 10% 停止条件；预算超时；已补 `scripts/product_chat_perf/load.py`，但未执行 |
| E/R 真实并发 | NOT_RUN | 同上，且需额外费用/配额授权 |
| F 容量/开放到达/长稳 | NOT_RUN | 本轮不授权；只列计划 |
| 浏览器首屏/完成独立时钟 | PARTIAL | 受控模型有 6905.9/7471.2ms；真实模型未测 |
| 模型 TTFT/工具/Token/费用 | PARTIAL | 373/352/7,332,708 tokens；费用 null |
| 资源采集 | PASS | 5 容器 × 450 个 3s 样本；CPU/内存/网络/块 IO |
| 价格/费用 | NOT_RUN | 无价格表，未知不记 0 |
