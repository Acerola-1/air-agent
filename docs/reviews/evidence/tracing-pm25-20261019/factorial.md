# 析因 fixture 结果（A0/A1/B0/B1）

设计与 `evaluation.md` 一致：O=固定/DeepAgents，R=LLM reviewer 开关；确定性门禁四组全开；专员、内核、渲染器相同。

- A0：固定流程，仅确定性门禁。
- A1：固定流程 + LLM reviewer + 确定性门禁。
- B0：DeepAgents lead，三专员，无 reviewer。
- B1：DeepAgents lead + 四专员（默认业务候选）。

`factorial-fixture.json` 记录 3 次配对运行 × 4 组 = 12 行。所有行的 `report_digest` 相同：`83c187aa6b33d6f0131a103acc7f7265bc24e0da38476d09c4d411e72069612e`，说明相同 fixture/请求/策略下确定性内核、allowlist、渲染器一致。B0/B1 lead 调用数由 scripted model 记录；A0 无模型调用，A1 仅 reviewer 调用。Token/真实时延未采集；不作统计显著性结论。

本次是 G0 fixture，不是真实模型调查能力评测；B1 相对 A1/B0 的成本与调查收益仍需 G1 真实模型数据。
