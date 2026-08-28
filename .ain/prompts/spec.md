读取 `ai/changes/{{CHANGE_ID}}/intent.md`、`.ain/templates/spec.md`、`AGENTS.md` 和 `ai/policies/`，基于已接受的意图完善 `ai/changes/{{CHANGE_ID}}/spec.md`。

规格必须包含可测试的成功标准、用户行为、技术边界、失败/降级行为、策略冲突、可观测性、发布与回滚要求。风险不得低于 {{RISK_LEVEL}}。显式标记无法确定的事项，不要修改产品代码，不自行审批。完成后运行 `./.ain/ain validate {{CHANGE_ID}}`。
