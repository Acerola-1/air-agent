# air_agent 后台管理前端

Next.js 16 + React 19 + Tailwind v4 + shadcn/ui，与主前端（`frontend/`）共用同一套设计令牌
和组件写法。

## 运行

需要先启动 admin API（见 `../api/README.md`）：

```bash
npm install
npm run dev        # http://localhost:3100
npm run lint       # tsc --noEmit
```

默认管理员由后端种子脚本创建（`root` / `root`，本地开发用；对外部署前务必改掉）。

## 与后端的连接

浏览器只与 `:3100` 通信，`next.config.ts` 把 `/api/*` 反代到 admin API（默认
`http://127.0.0.1:8100`，可用环境变量 `ADMIN_API_URL` 覆盖）。

走同源代理而不是浏览器直连后端的原因：会话是 **httpOnly cookie**，同源下浏览器会自动
携带，既不用处理 CORS credentials，也不用把 token 暴露给跨站请求。

## 结构

```
app/
  layout.tsx                根布局 + SessionProvider
  login/page.tsx            登录页
  (dashboard)/
    layout.tsx              受保护外壳（未登录跳登录页）
    page.tsx                概览
    system/users            用户管理（列表/搜索/增改删/角色/关注城市）
    system/roles            角色管理 + 权限树配置
    system/permissions      权限树（只读）
    system/audit            审计日志
    business/regions        行政区划浏览（省/市/区县下钻）
    business/followed       用户关注城市
    profile                 我的资料
lib/
  api.ts        API 客户端（统一错误处理，把 FastAPI 的 detail 归一成提示语）
  session.tsx   会话上下文：当前用户、可见菜单、权限判定
  types.ts      与后端 schemas.py 对应的类型
components/
  app-sidebar.tsx            侧边栏（按 /auth/menus 渲染）
  region-picker.tsx          关注城市选择器（级联 + 搜索，最多 5 个）
  permission-tree-picker.tsx 权限树勾选
  user-dialogs.tsx           用户相关弹窗
  ui/                        shadcn 基础组件
```

## 几个约定

**路由路径由后端权限表决定。** 侧边栏从 `GET /auth/menus` 读取，而菜单的 `path` 来自
数据库里的权限记录（`/system/users`、`/business/regions` 等）。改菜单路径要同时改种子
脚本和这里的目录结构。

**前端 gate 只是 UX。** `useSession().can("user:create")` 用来隐藏按钮，但真正的授权在
API 侧；手输地址或直接调接口一样会被 403 拒掉。

**中文不进 cookie。** 会话 token 里只有 `user_id` + `account`（ASCII）——JS 侧 `btoa`
遇到非 Latin1 字符会抛异常。中文（显示名、城市名）都在数据库里按 id 查。

**弹窗不使用进出场动画。** Radix 靠 CSS 动画结束事件决定何时卸载节点；`tw-animate-css`
的 `exit` 关键帧在这套组合下会让节点停留在 `data-state="closed"` 且 `opacity: 1`，
表现为"弹窗关不掉"。后台工具不需要这段动画，因此直接不启用。
