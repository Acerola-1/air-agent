# 匿名05独立审计证据

2026-10-15。source input、额外忽略日志及用户实测131分钟见anonymous-input.json；稳定R01–R10见findings.json，结论和评分46/100见../../flash-blind-05-add-skill-tool-selection.md。

原作者.log确实存在但被Git忽略，已逐份核对并保留原始author-*.txt；本轮.log也有字节相同.txt。映射见raw-log-preservation.json，不能据rg --files未列出就说原始日志缺失。

## 复现顺序

使用当前仓库src/admin依赖均满足的Python环境。本次组合环境位于/tmp/aa-takeover35-py313；这是本机路径，不保证其他机器存在。原aa-sel05-pg/redis与专用库保留，原始模型配置未改；不要对共享库运行探针。probe-live.py只新建合成审计账号/企业/请求并调用本地ASGI和native hook，留样本复验；它不等于官方镜像HTTP运行24格。

1. node probe-client.cjs：编译真实TS store/BFF路由，受控认证context，生成/tmp/aa05-default-payload.json。
2. Python probe-modules.py：真实文件系统与SDK/工厂函数；目录发现使用受控返回来隔离分页逻辑，明确不是生产MCP证明。
3. Python probe-live.py：真实专用PG+ASGI登录/受理、runtime非owner启动裁决/native hook，验证前端422、摘要409、伪造目录200与恢复错误。不读取真实用户数据，不调用实际模型。
4. probe-react.cjs：输入/tmp/aa05-skill-item.json由common.capabilities.list_graph_skills('basic-qa')首项dataclasses.asdict生成；使用真实React与真实TSX、受控store，证明目录DTO渲染错误，不冒充浏览器实机。
5. run-tests.py接受“输出日志名 测试参数”；AA05_TEST_ROOT可切只读导出的8620cf7基线。baseline-comparison.json逐node记录共同/新增失败。当前原root运行额外artifact失败在原HEAD同环境也复现，不计模型回归。

真实浏览器使用本轮自建回环8104/3104服务、合成账号，已完成登录和目录403验证，截图/AX保留；后端native URL刻意指向未运行端口以防真实外呼，线程列表不可用不计产品回归，没有把这套环境冒充正常聊天/六图实测。

两个空库迁移的原始日志包含新版本失败、原HEAD成功及旧head→新head成功对照；审计临时服务/空库已清，原作者容器未清。专用库审计前dump仅/tmp，不进Git。秘密扫描与交付指纹最终复核另记review-closeout.json。
