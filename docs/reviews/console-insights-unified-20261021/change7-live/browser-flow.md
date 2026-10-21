# 管理页面真实浏览器验收

时间：2026-10-21。使用 ZCode 内置浏览器（IAB）+ Playwright 页面 API，不是接口调用冒充。

## 环境

- 管理页：`admin/web` dev server，`http://localhost:3100`（127.0.0.1 下 Next dev 会拦截 `_next/hmr`，改用 localhost）。
- 隔离管理 API：`127.0.0.1:25446`；隔离 IAM：`127.0.0.1:25445`。
- 为管理端 SSO 在**隔离 IAM** 新建合成客户端 `air-agent-admin`，回调 `http://localhost:3100/api/auth/iam/callback`；未改正式 IAM。

## 已实测

| 步骤 | 结果 | 证据 |
|---|---|---|
| IAM 登录 + 管理端 SSO + 选择企业 | 成功进入 Campaign A | 页面快照 |
| 打开 `/operations/insights` | 榜单渲染：合格请求 272、已处理 263、未归类 132、覆盖率 96.7%、归属修订 271；首行是改名后的「隔离验收·空气质量查询」 | `browser/insights-leaderboard.png` |
| 打开簇详情 | 22 原子意图、19 去重请求、来源表、约束、纠错表单均渲染 | `browser/insights-cluster-detail.png` |
| 改名 | 输入新名→保存→页面标题更新；刷新后仍为「隔离验收·空气质量查询(浏览器改名)」 | 快照 + 详情截图修订 3 |
| 窄屏 390×844 | `scrollWidth=375 < innerWidth=390`，无横向溢出；移动导航可展开 | `browser/insights-narrow-390.png` |
| 键盘 | Tab 聚焦 + Enter 激活「需求洞察」导航，URL 变为 `/operations/insights`，焦点停在链接；「刷新」也用 Enter 触发，但“截止”取最新来源时间，无新来源时不变，未据此判定刷新失败 | 快照 |
| 撤权后刷新 | 重定向到 `/login`，管理会话失效 | 快照 |
| 恢复授权后重新登录 | 洞察页恢复，改名仍在，榜单可读 | 快照 |
| 删除→刷新 | 在聊天页侧栏删除内容补齐线程；BFF 最终 DELETE 204，侧栏 2→1；回到洞察页刷新，合格/已处理请求 2→1，被删请求进入 `deleted` 缺口 | `delete-refresh-browser.json`、BFF 日志 |
| IME 输入 | compositionstart/input 后输入「洛阳市」、发送按钮启用；组合期间 Enter 不发送；compositionend 后 Enter 发送并得到回答 | `ime-browser.json`、`browser/ime-composing.png`、`browser/ime-sent.png` |
| 迟到响应 A→B→A | 受控首包 3 秒；发送后停止并切到 B，B 无迟到问题/回答；切回 A 见迟到问题但无迟到回答 | `late-response-browser.json`、`browser/late-response-thread-b.png`、`browser/late-response-thread-a.png` |

## 工具/环境限制（如实记录）

- 该 Next dev 页面上的 Playwright `click` 在 IAB 后端持续超时（元素可见、启用、命中点正确），即使坐标点击也没有触发 React 处理。企业选择和改名保存改用**页面内触发真实 React onClick 处理器**完成，导航/键盘使用真实 Enter；这不等于物理指针点击通过。
- 洞察页没有“删除来源”控件；本轮在**聊天页侧栏**用真实浏览器删除线程，再回洞察页刷新验证。首次 DELETE 因隔离管理 API 的 `CSRF_ALLOWED_ORIGINS` 未含聊天 BFF 端口返回 503；补隔离配置后最终 204。该配置是环境允许源，不是放宽业务校验。
- 迟到响应/IME 已完成。聊天页在运行中按设计锁定线程切换，因此迟到响应按真实 UI 行为“先停止生成、再切换线程”验证；IME 使用原生 value setter + composition/input/KeyboardEvent 驱动受控 textarea（IAB 没有系统 IME 注入通道），不是接口调用。
- 聊天输入/发送控件本身可交互：`fill` 后发送按钮启用，原生 `.click()` 提交成功；此前的“不可交互”是测试定位问题（发送按钮是仅图标按钮，`textContent` 为空，且 Playwright click 在 IAB + Next dev 下 actionability 超时），不是产品缺陷。
- 窄屏下移动导航为覆盖式，内容需滚动；未发现横向溢出。
