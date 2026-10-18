## ADDED Requirements

### Requirement: 显式身份后端与唯一远程权威
Agent SHALL 在启动时选择local或remote；remote SHALL 只以共享IAM裁决人类身份与有效权限，不在失败时查询本地身份表或解析旧会话兜底。

#### Scenario: 远程失联
- **WHEN** 已成功认证的remote部署随后无法核验IAM
- **THEN** 受保护请求返回503且下一外部调用数为0
- **AND** 不恢复local账号、匿名或旧HMAC人类身份

#### Scenario: 保留本地部署
- **WHEN** 明确选择local
- **THEN** 既有本地身份和业务断言继续通过，且不发IAM请求

### Requirement: 严格身份与业务路由分离
Agent MUST 验证IAM协议、主体、受众、上下文及修订，并使身份API与原业务API具有明确独立的路由目标。

#### Scenario: 合法身份但业务请求
- **WHEN** 用户查看关注城市、MCP绑定或运营台账
- **THEN** 请求仍由Agent业务服务处理，以IAM已核验身份设置本地事务作用域

#### Scenario: 错误协议返回200
- **WHEN** 上游返回缺字段、错主体或bool修订的HTTP200
- **THEN** 消费方拒绝并不执行受保护业务

### Requirement: 可验证的票据与执行引用桥接
Agent SHALL 以自身服务端登记的短票据摘要绑定真实IAM执行引用；native Auth SHALL 比较身份和四修订并在线复查，引用本身 MUST NOT 发起运行或换票。

#### Scenario: 双进程合法启动
- **WHEN** BFF完成真实签票与引用登记，native进程收到对应有效票据
- **THEN** native能通过共有绑定与IAM复核取得一致可信上下文，不读取身份PG

#### Scenario: 伪造或错配绑定
- **WHEN** 请求修改票据ID、修订、引用或缺少服务端绑定
- **THEN** 拒绝且不创建实际运行

### Requirement: 每个实际发送边界可撤销
Agent SHALL 对同步、异步、辅助模型、摘要、子任务、MCP及每次重试实时复核原授权；原始token MUST NOT 进入checkpoint或Trace。

#### Scenario: 入站票据到期
- **WHEN** 已受理运行超过入口60秒而原会话与修订仍有效
- **THEN** 经真实引用复查后可以继续，不能只因入口票据到期中止

#### Scenario: 中间撤销
- **WHEN** 上一合法调用后会话或相关权限被撤销
- **THEN** 下一次实际发送前拒绝，网络增量为0，后续重试不绕过

### Requirement: 业务数据引用与窄服务能力保留
Agent MUST 保持业务归属、GUC/RLS、内部FK、删除墓碑及mt/rs的窄能力；隔离业务身份投影 SHALL NOT 成为授权权威。

#### Scenario: 业务关系可用
- **WHEN** 自有夹具使用与IAM一致的UUID完成业务读取和新写
- **THEN** 外键及当前企业作用域保持，偏好锁与原业务规则继续生效

#### Scenario: 历史身份不匹配
- **WHEN** dry-run发现同名不同UUID或未知历史归属
- **THEN** 列出差异并阻止正式切换，不自动合并或放开RLS

### Requirement: 安全浏览器会话与阶段证据
Agent SHALL 为两前端提供受控服务端会话/SSO兼容与真实切企业保护，按阶段交付可复跑实证，不以测试数量替代闭环。

#### Scenario: 旧页面回调
- **WHEN** A切B再切A或退出换账号后旧请求返回
- **THEN** 旧结果不能更新新上下文，旧写409，敏感应用会话不进入JS存储

#### Scenario: 完成范围报告
- **WHEN** 实施者交付本轮结果
- **THEN** 报告明确真实IAM、受控HTTP、实际SDK、实配模型与浏览器范围，并保留正式切换未完成声明
