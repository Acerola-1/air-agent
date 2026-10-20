# 快速流程

1. 解析并冻结请求；缺字段/同名站先补问。
2. 并行运行 station-data 与 grid-data。
3. 运行 meteo-analyst；必要时 evidence-reviewer 找反证。
4. 由执行层生成 claim allowlist 与 ValidatedReport；非法计划回退确定性模板。
5. 只发布已验证报告；画布只接受已验证报告引用。
