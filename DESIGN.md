---
version: alpha
name: Air Agent
description: 面向空气质量查询与分析的中文工作界面，以回答为中心、过程卡片可回看。
colors:
  primary: "oklch(0.21 0.006 285.885)"
  background: "oklch(1 0 0)"
  foreground: "oklch(0.141 0.005 285.823)"
  muted: "oklch(0.967 0.001 286.375)"
  muted-foreground: "oklch(0.552 0.016 285.938)"
  destructive: "oklch(0.577 0.245 27.325)"
typography:
  sans:
    fontFamily: "Montserrat, system-ui, sans-serif"
  mono:
    fontFamily: "ui-monospace, monospace"
rounded:
  DEFAULT: "0.625rem"
spacing:
  conversation-max: "48rem"
  conversation-gap: "1.5rem"
components:
  button:
    backgroundColor: "{colors.primary}"
  tooltip:
    backgroundColor: "{colors.background}"
    textColor: "{colors.foreground}"
  tool-steps:
    backgroundColor: "{colors.muted}"
    textColor: "{colors.foreground}"
  processing-notice:
    textColor: "{colors.muted-foreground}"
  processing-notice-error:
    textColor: "{colors.destructive}"
---

# Air Agent Design System

## Overview

中文空气质量查询工作台；当前用户决策与 OpenSpec 变更是业务来源。界面延续已登录聊天页的灰白底、细边框和绿色助手标识。回答正文是主阅读区；运行状态采用小型状态条，资料与诊断在工具卡片内主动打开。保留现有身份，不增加装饰性图表或重做导航。

运行时 tokens 由 `frontend/app/globals.css` 的 CSS 变量及 Tailwind 映射负责；本文记录其语义，不能用文档中的另一套常量覆盖它们。light/dark 主题沿用同一语义层级；本次不改变色值。面向当前中文业务与本地测试使用，不推定额外市场或监管政策。

## Colors

正文使用 foreground，辅助信息使用 muted-foreground，归档回读采用普通边框。绿色表示处理正常；红色表示失败且配可读文字，不能仅靠颜色区分状态。容量接近后端输入预算时使用现有 amber 语义。所有新区域复用语义 tokens。

## Typography

继承 `frontend/app/layout.tsx` 的 Montserrat 与系统中文回退，标题与正文保持现有字号层级。数值使用 tabular-nums，诊断与原文使用等宽字体，长串允许换行，局部阅读区有自己的滚动条。

## Layout

聊天阅读区保持 max-w-3xl；工作台壳与会话侧栏沿用现有布局。工具详情在当前轮卡片内打开，归档每页最多 8000 字符，避免整份资料冲击页面。状态条跟随当前会话，内部摘要不作为聊天气泡。用户上翻时停止自动跟随，回到底部恢复。

## Elevation & Depth

正文保持安静的平面层级，工具卡使用现有 muted 背景和边框。悬浮说明复用 Tooltip，不建立第二套弹层。错误区域固定在对话流中且保留恢复按钮。

## Shapes

延续圆角卡片与小型胶囊容量按钮；半径来自运行时 --radius，不在新组件定义一套形状。

## Components

按钮、说明和字体分别由 shared Button、Tooltip、全局样式拥有。ProcessingNotice 为历史整理／资料处理共用状态组件；ArchiveReader 为归档资料分页读取组件；CapabilitySelector 为技能与 MCP 工具选择配置组件。详情切换必须使用原生按钮，显示焦点并可用键盘操作。状态使用 polite live region，失败提供重试入口；动画尊重 reduced-motion。

## Do's and Don'ts

显示已确认终答、可读失败说明及后端容量；原文和诊断由用户主动展开。禁止把工具 JSON、内部历史摘要或模型调用草稿填入临时正文。不能从“存在引用字符串”推定资料已保存，也不能从前端累计历史长度推定模型当前上下文用量。能力选择在运行中锁定，不得在请求中途动态变更。
