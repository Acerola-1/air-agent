## Purpose

为多种企业资源建立统一的目录、平台开通和成员授权边界，使资源回收能够即时停止使用且保留企业配置，并让首个知识库接入遵循可推广的资源模型而非专用知识库布尔开关。

## ADDED Requirements

### Requirement: 资源目录与企业开通
IAM SHALL 维护具有稳定code、中文名称、排序和状态的资源目录，并以tenant与resource_code的唯一关系维护active或revoked的entitlement。仅获平台开通权限的主体可修改entitlement；不存在、停用或未开通资源必须默认拒绝。

#### Scenario: 给企业开通资源
- **WHEN** 平台给企业A开通knowledge:lib:air资源
- **THEN** 仅A可对该资源配置可授权限；B保持未开通，目录名称或排序变化不改变稳定权限码

#### Scenario: 企业自行伪造开通
- **WHEN** 企业管理员请求创建entitlement或向未开通资源授予权限
- **THEN** 拒绝，不因其拥有企业角色管理权而取得平台开通权

### Requirement: 可授目录与有效权限双重约束
资源权限 SHALL 显式关联资源code；企业可授集合必须同时满足权限启用、grantable_to_tenants、资源开通及操作者可授范围。使用时有效权限 MUST 再与当前资源状态及entitlement求交集，不能仅检查历史角色授权。企业管理员模板和平台角色不得隐式绕过资源开通。

#### Scenario: 可授标记但无开通
- **WHEN** knowledge:lib:air:read标记grantable_to_tenants=true而A没有有效air entitlement
- **THEN** A可授目录中不提供该码，构造授权请求被拒绝，introspection不返回该有效权限

#### Scenario: 回收后旧角色仍有授权行
- **WHEN** 平台回收A的air entitlement但原角色权限行仍存在
- **THEN** 下一次核验与知识库air访问拒绝，角色配置保留并可显示因资源未开通而失效

### Requirement: 开通变更实时生效且可追溯
资源状态、entitlement及有效授权变更 SHALL 在权威事务内推进相关企业授权修订并写入审计，后续核验立即反映结果。回收不得删除企业或成员的既有配置；重新开通时须明确展示保留授权恢复的影响并记录审计。

#### Scenario: 进行中查询前回收资源
- **WHEN** 资源回收事务已提交而旧会话、执行上下文或页面仍持有先前权限
- **THEN** 下一受保护步骤拒绝，失效通知延迟不影响撤销生效

#### Scenario: 重新开通保留授权
- **WHEN** 平台审阅影响后重新开通先前回收资源
- **THEN** 保留授权重新进入有效集合并推进修订，用户可再次读取授权库，历史业务数据不被重新建档

### Requirement: 知识库授权映射与技术存储分离
首个资源 SHALL 使用knowledge:lib:<library_code>，读权限为knowledge:lib:<library_code>:read。IAM只管理资源标识与授权，知识库负责将library_code映射到实际workspace/PG/向量/图存储及就绪状态。相同资源code不得授权任意客户端提交的库位置或文件路径。

#### Scenario: 所有已授权库选择
- **WHEN** 用户请求all
- **THEN** 知识库仅展开当前有效读权限与本地已就绪目录的交集；空集合拒绝，显式包含无权库时整体拒绝

#### Scenario: 目录存在但存储未就绪
- **WHEN** IAM已授予water读取而知识库本地water仍未就绪
- **THEN** 返回明确不可用或不可访问状态，不查询其他workspace代替，不暴露内部数据库地址
