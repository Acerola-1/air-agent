# 匿名02独立验收证据

日期2026-10-13。首次输入`anonymous-input.json`，用户报告1小时52分。作者文件位于相邻`flash-02/`，本目录是协调者独立证据；不覆盖作者输出，不把协调者反例归为作者测试。

## 最终结果

管理141 passed；台账44 passed；根303 passed/46 skipped/5既有失败；集成20 passed/7 skipped/1既有500k失败；前端30契约；tsc通过；clean production build失败（TS2344，Route多余导出confirmRunWithMarker）。日志逐项保留，网络字体下载的中间失败不计模型扣分。

## 可复跑反例

| 输出 | 探针 | 实测边界 |
|---|---|---|
| live-admission.json | probe_admission.py | 同预留3个native run、同键2个BFF run；原键漂到regen |
| native-lifecycle.json | probe_native_lifecycle.py | metadata合法预留：平台success，台账admitting/无映射/无answer |
| pg-boundaries.json | probe_pg_boundaries.py | outbox故障后event=1/outbox=0；重试duplicate；删除清理无效却done |
| association.json | probe_association.py | A事件引用B尝试仍写入 |
| deletion-api.json | probe_deletion_api.py | resume新增request、他人成员可申请删除、删除中及过期原文仍可读 |
| receipt.json | probe_receipts.py | handler进2次但receipt1条；唯一receipt不代表处理一次 |
| rebuild.json | probe_rebuild.py | done事件在仅包含它的窗口请求v2重建，would_replay=0 |

所有数据库反例只在新建专用库执行。真实native/BFF反例仅创建合成线程，重复运行探针暂停在首节点前，最后按自己的ID删除204；metadata闭环探针用一次最小数字问答并清理204。不读历史用户消息，不重置业务卷。

## 环境复现

在repo根，已有受保护`.env`应提供本机专用TENANT_TEST_OWNER_URI/TENANT_TEST_RUNTIME_URI，前缀需指向测试库。执行`admin/api/.venv/bin/python docs/reviews/evidence/flash-02-independent/setup_isolated.py`创建三套随机新库并迁移到0007；临时非owner worker只授本次ledger库同等权限/RLS。连接参数只写mode600的`/tmp/air-agent-f02-env.json`，不在证据输出。根`.venv`没有SQLAlchemy，准备脚本须用admin/api解释器。

PG探针使用上述受保护临时文件，由根`.venv/bin/python`执行。deletion-api探针需admin/api解释器，并复用现有测试fixture/ContextClient；这也是实际FastAPI处理和PG/RLS路径，不是替身数据库。

真实native探针需要当前admin:8100、BFF:3000、native:2024、INTERNAL_API_TOKEN和REVIEW_FIXTURE_PASSWORD由环境提供。正确的同源归档签名应在当前服务/BFF配置一致；秘密不打印、不过库。探针只执行受授权测试流程；原生预留重放中可使用interrupt_before=*避免执行业务工具。

本轮三套新库及验收者准备失误遗留的三套新库、两临时worker角色最终仅按自己创建的确切清单清理，见cleanup.json；受保护临时连接文件清除。下一次需要重新建立环境，不使用已经删除的库。本轮API业务库保留，包括作者既有数据。

完整独立结果与修复范围见`../../flash-blind-02-add-agent-event-ledger.md`。所有product代码保持匿名输入；协调者仅新增报告/证据/提示词并重开相关OpenSpec任务。原生服务/MCP/模型、故障注入和跳过矩阵分开解释，不把静态校验或正向数量当作完整验收。
