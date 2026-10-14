# 匿名02第二修复与协调者补修独立证据

模型输入在改产品前固定；原作者报告、首交与首修证据保持原样。

`service-boundary-before-final.json`是修复前的有效反例：三种服务宽读均包含合成私有文本，用户运行1/服务0，空列表释放裁决后同submission产生2个运行。`service-boundary-accepted.json`是补修后的实际结果：服务投影200无正文，原生宽读/state/history403，撤销后收敛映射，native重放已确认409、BFF200复用同ID、运行恒1。

`snapshot-before.txt`确认三个失败场景；`snapshot-accepted.txt`三项通过，最终管理套件160项含租户覆盖与来源边界。`browser-counts.json`与`browser-regenerate.jpg`由CUA真实操作新建合成会话，普通提交、重新生成与刷新均成功，请求1/尝试2/回答2/原文1。

`fresh-migrations.json`保留接管前0009环境，`fresh-migrations-final.json`记录最终新空库到0010。`restore-drill.json`用新随机库从零迁到0010，备份后删除、恢复旧备份、重施永久标记/权限并保留新写，演练库已自清理。

协调者自身的无效验证结果也保留：`service-boundary-before.json`未设置宿主runtime URI；两个final/retry JSON里BFF503来自探针循环覆盖提交键，使键变成7字的history，不能当产品缺陷。最终accepted脚本修正；前次重载与隔离测试并行造成连接中断，后续串行且清洁环境复跑；游标夹具用独立合成租户避免旧测试积压。

脚本只加载本地私有环境/fixture密码，不输出凭据。重跑先创建新独立数据库，将输出改到新目录，保留当前证据。根单元5项、集成1项旧失败保持原断言。模型修复用时2小时34分/累计6小时15分属于用户报告，不用测试秒数代替。

本轮测试库/角色的清理记录见cleanup.json；不重置共享业务卷、不删除历史真实对话。当前工作区未提交，v1实际冻结提交尚未填写。
