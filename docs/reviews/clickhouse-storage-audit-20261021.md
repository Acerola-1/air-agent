# Langfuse ClickHouse磁盘只读核查

2026-10-21。ClickHouse及正式栈当时处于停止状态。通过自有一次性容器只读挂载两个卷检查，不启动ClickHouse，不修改/删除业务观测、系统日志或卷。

| 范围 | 结果 |
|---|---|
| data卷宿主机实际分配 | 12.55 GiB |
| logs卷宿主机实际分配 | 12.94 GiB |
| data卷内现存文件 | 约3.3 GiB |
| logs卷内现存文件 | 约1.3 GiB |
| 两卷声明容量 | 各512 GiB；是稀疏磁盘上限，不是当前占用 |

最大数据目录为ClickHouse系统表：trace_log约2318 MiB、text_log约637 MiB、part_log约176 MiB、metric_log约148 MiB、asynchronous_metric_log约76 MiB。对照之下Langfuse default.events_full约4.2 MiB、events_core约1.4 MiB，其余业务表更小。

普通日志当前文件：server.log约476 MB、server.err.log约427 MB，另有多份压缩轮转日志。未读取日志正文，不能据此判断具体错误原因。

结论：磁盘大头是系统日志及磁盘镜像已经分配而卷内当前未使用的块，不是几十GB真实Agent调用。宿主机已分配与卷内在用的差额约21 GiB，符合删除/轮转后未回收稀疏镜像空间的现象；具体回收需验证Apple Container对discard/trim的支持，不能直接裁剪或删除volume.img。

建议下一步单独调整系统日志级别、轮转及日志表保留期；确认恢复方式后再做磁盘空闲块回收。此次仅诊断，不做TRUNCATE、DROP、prune或卷级写操作。辅助检查容器已自动删除，正式历史数据保留。
