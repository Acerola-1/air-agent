# SSO 会话过期恢复

日期：2026-10-23。Agent 基线 `dev / 0b3c4bf13b26ec26b8aaa93fb06c7bb5ffb8059d`，IAM 基线 `dev / f5abb800169e325e66f5cbfee3b4758442cb29ba`。本次修改仅涉及两仓的登录恢复；保留此前溯源及其他未提交改动。未提交、推送、合并、迁移数据库或重启正式服务。

## 问题与行为

用户停留一夜后再次点击统一身份登录，旧中央会话已经失效。IAM `BrowserSessionInterceptor` 原来直接抛出 401；顶层浏览器导航停留在 JSON 错误页。Agent 两端的 SSO 回调失败也原来直接显示 JSON。

现在：

1. 中央仅对两个 SSO 入口的顶层 HTML GET 导航处理未认证恢复。缺失、过期、撤销、匿名句柄及权威核验 401 都进入此判断。普通 JSON API、跨域 fetch、iframe 继续拒绝；403 与 503 不转成过期恢复。
2. 返回地址继续通过同一个客户端启用状态、精确 callback、回环限制、参数长度/state 字符及 Origin 校验。校验成功才清除失效中央 Cookie，并以 303 返回注册回调，附 `error=login_required&state=...`。不签发 ticket、不写 state/ticket、不建立新身份。
3. Agent Python 回调仍须通过原发起浏览器的 HttpOnly state cookie 验证；错误或缺失 state 不兑换、不建立会话。正常未登录恢复清除该一次性 cookie，返回本端登录表单。
4. 聊天和管理 BFF 将回调失败、超时和服务不可用转成干净的同源登录地址，不回显上游错误正文。回调响应禁止缓存，成功仍透传应用会话 cookie；拒绝外部 Location。保留浏览器实际 Host，避免 Next 内部将 `127.0.0.1` 归一化成 `localhost` 后破坏 host-only Cookie。
5. 表单分别显示固定中文提示：登录已过期、统一登录未完成、身份服务暂时不可用。过期/失败提示聚焦账号框，密码保持掩码，Tab 可进入密码框。提示读取后清理 reason，ticket/state 不留在最终地址；不自动再次发起 SSO。

账号密码仍交由现有统一身份服务验证，未恢复本地账号、延长会话期限或放宽权限。公开接口的既有 API 401、权限 403、依赖 503 语义保留；仅新增已注册回调上的未登录 HTML 导航恢复语义。

## 验证

- IAM：JDK 21 下定向 Maven 测试 11 项通过：新的真实 MVC/拦截器/注册校验/授权编排 7 项，已有知识身份委托 4 项。会话权威、Mapper、事务和限流为受控 mock；没有连接正式 PG/Redis。包括缺失/过期/撤销会话恢复、错误 callback/客户端/Origin、Bearer 拒绝、API/fetch/iframe 拒绝、403/503，以及有效会话真实授权编排签发 ticket 的正向控制。见 `iam-mvc-tests-final.json`、`iam-run-final.txt`。
- Python：新增 6 项（双端正确 state、错 state、缺 cookie、ticket/error 冲突及正常兑换/建会话正向控制）；连同现有远程身份回归共 46 项通过。见 `python-regression.txt`。
- 前端：最终契约检查 88 项通过；其中 SSO 双端回调 16 项，覆盖失败重定向、cookie、禁止缓存、外部地址拒绝和 Host 保留。见 `frontend-contracts-03.txt`、`sso-bff-final.txt`。
- 聊天与管理前端均在自有、无私有环境文件的源码副本完成 Next.js 16.3.0 webpack 构建及 TypeScript 检查；最后两次构建见 `frontend-build-03.txt`、`admin-build-03.txt`。保留既有 middleware 弃用、Edge Runtime 等构建提示，不宣称零警告。
- Python Ruff 检查/格式检查、两仓 diff 空白检查通过；UI 静态审计无发现，见 `ruff-*`、`ui-static-audit.json`。

## 实际页面

使用独立回环端口 13126/18126，真实 Next 页面/BFF及实际 Python `iam_remote` 回调路由。中央响应、票据兑换与后端写请求 Origin 依赖为明确受控夹具；真实 IAM MVC 防护另由上述 Java 测试验证。本页检查不等同于正式 IAM 数据库/Redis 的跨进程整链验收。

- 点击统一身份登录，经过真实跳转回到账号密码表单；显示“登录已过期，请使用账号和密码重新登录。”，账号自动获得焦点，Tab 进入密码框。最终地址 `http://127.0.0.1:13126/login`，不含 ticket/state/reason。过期夹具票据兑换次数为 0。
- 错误 state 同样回到“统一登录未完成”的可恢复表单；后端兑换仍为 0。
- 兑换服务返回 503 时显示“身份服务暂时不可用，请稍后重试”，不显示为密码错误，也不自动循环 SSO。
- 390×844 窄屏下表单、提示和按钮完整，页面宽度与 scrollWidth 均为 390；密码为 `type=password`。

证据：`browser-expired-final.json`、`browser-wrong-state.json`、`browser-unavailable.json`、`browser-narrow-expired.json`、相应 source-count 和截图。没有输入真实账号或密码；没有实测正式会话跨日到期，也没有对正式 IAM/PG/Redis 做撤权或故障注入。

## 测试过程及资源

- 第一轮 Maven 被沙箱阻止写依赖锁；使用正常缓存权限及 JDK 21 后可运行。最初 Java 测试夹具有重复参数/重复请求头及只接受 HTML 导致错误信封无法协商的问题，按真实浏览器请求形状修复后通过；原失败日志保留。
- 页面夹具最初的函数局部 Request 类型与延迟注解不能被 FastAPI 解析，返回 422；修复测试夹具后重跑。该问题没有进入产品代码。
- 实际页面发现内部 Host 归一化会把 `127.0.0.1` 变成 `localhost`，已修复并增加双端回归；早期 `browser-expired.json` 和后续 `browser-expired-final.json` 分开保留。
- Ego 浏览器创建空间的客户端一直未返回，未创建空间；终止了本任务的挂起客户端，保留用户的浏览器本体/窗口，改用应用内浏览器完成实际页面验证。原生窗口读取也出现长时间等待，不将等待时间记为产品性能。
- 自有 Python/Next 进程、临时源码副本及验收标签已回收。没有创建容器、正式账号或数据库资源；没有操作正式端口/库。资源清单与清理记录见 `probe-resources.json`、`probe-cleanup.json`。
- `browser_probe.py` 为复跑夹具；需要新的证据目录或另存运行，不覆盖旧文件。服务日志是合成登录流，交付前清除了随机 state/ticket 参数值；不包含真实用户凭据。

修改已在工作区完成并通过上述验证。正式 IAM/API 尚未重启，当前正式进程仍可能运行旧逻辑。正式重启、真实账号整链验证与 Git 保存分别是后续状态，不把本次受控检查写成已经上线。
