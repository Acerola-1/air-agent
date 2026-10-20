## MODIFIED Requirements

### Requirement: 知识检索快速路由
系统 SHALL 通过受保护部署配置确定 Knowledge API 检索路线，默认使用PG纯检索，不为区分政策与一般知识额外调用路由LLM或恢复已删除的本地检索模块。路由元数据可按版本复用，但资料读取和缓存回放必须重新确认当前身份、资料范围、来源与删除状态，不能缓存肯定授权。

#### Scenario: query 命中政策指标
- **WHEN** 知识检索query包含政策、标准、法规或文档元数据指标
- **THEN** 系统使用配置的纯检索路线和已获准资料范围
- **AND** 不调用额外路由LLM，具体条款答案仍要求真实检索引用

#### Scenario: query 命中通用知识指标
- **WHEN** 知识检索query包含技术概念、污染物解释或治理知识指标
- **THEN** 显式检索调用使用同一受管知识服务，不恢复旧Milvus/Ollama工具
- **AND** 一般概念可按需调用，但默认工具schema保持可见

#### Scenario: query 路由命中缓存
- **WHEN** 同一normalized query再次使用相同版本的路线元数据
- **THEN** 系统可复用不含凭据的路由元数据，但必须重新复查当前身份、知识权限和来源状态
- **AND** 不得借旧路由或结果缓存放行已撤权、跨企业、过期或删除资料

#### Scenario: query 路由模糊
- **WHEN** query不足以确定目标文档或所选资料范围
- **THEN** 系统在授权范围内按固定路线检索或请求澄清，不使用LLM临时选择任意地址、凭据、workspace或扩大资料范围
- **AND** ambiguous、partial和missing保持明确状态，不作为完整法规依据发布
