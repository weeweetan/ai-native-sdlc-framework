---
change_id: CHG-YYYY-NNN
commit_sha: "待填写"
agent_identity: "待填写"
policy_versions: []
completed_at: YYYY-MM-DDTHH:MM:SSZ
result: pending # pending | pass | fail | partial
evidence_manifest: "" # result=pass 时必须为 evidence/evidence.jsonl
ready_for_review: false
release_blocked: true
---

# Verification Evidence：<变更名称>

## 计划符合性

- 与 `plan.md` 的偏差：无 / 说明
- 偏差已更新到计划并被接受：是 / 否

## 实际执行证据

> 使用 `./.ain/ain verify <change-id> --kind <类型> --command '<命令>'` 执行检查；命令、退出码和原始日志会写入 `evidence/evidence.jsonl`。不要手填“通过”。

| 检查 | 命令或方法 | 结果 | 日志/报告链接 |
| --- | --- | --- | --- |
| 构建 |  |  |  |
| 单元测试 |  |  |  |
| 集成/E2E |  |  |  |
| 静态检查/依赖扫描 |  |  |  |
| 验收场景 |  |  |  |

## 独立验证（建议 R1 及以上）

- 验证者（独立 Agent 或人员）：
- 检查的相邻流程：
- 发现：

## 未通过项与风险接受

- 未通过项：
- 临时豁免、到期日和批准人：

## 完成声明

- `result: pass`：必须有与 `commit_sha` 匹配的成功命令证据，且 `release_blocked: false`。
- `result: partial`：允许进入审查，但必须保持 `release_blocked: true`，直到缺失验证补齐。
- 只有所有必要检查已执行，或未完成项和风险接受已明确记录时，才可将 frontmatter 中的 `ready_for_review` 改为 `true`。
