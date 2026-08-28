根据已接受的 `plan.md` 实现变更 {{CHANGE_ID}}，并持续运行仓库提供的构建、测试、静态检查及验收反馈回路。使用 `.ain/templates/verification.md` 完善验证工件。

完成后创建 `ai/changes/{{CHANGE_ID}}/verification.md`，填写真实 commit SHA、运行命令、结果和日志链接。不得伪造输出、跳过失败检查、删除测试或弱化断言；若无法通过，保持 `result: fail` 并报告阻塞。风险等级为 {{RISK_LEVEL}}。
