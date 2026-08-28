---
change_id: CHG-20260828-001
status: accepted # proposed | accepted | rejected | superseded
input_intent: intent.md
owner: "user-request"
technical_owner: "Codex-framework-architect"
policy_versions: ["framework-governance-v2"]
risk_level: R2
---

# Specification：强化 AIN-Loop 的不可绕过门禁与可验证证据链

## 要解决的问题与成功标准

AIN-Loop v2 必须把关键阶段从“有一份 Markdown”提升为“内容、批准、代码变更和命令证据可交叉校验”。成功标准：

- 对 intent/spec/plan 的批准必须持有被批准工件和当前策略配置的 SHA-256；任一文件变更后，阶段立即显示为待重新批准。
- 新的 `guard` 命令在检测到非文档/非工件 Git 变更时，要求明确 change-id、已通过的 plan，以及变更路径在已批准计划的 `planned_paths` 范围内。
- 新的 `verify` 命令以 `shlex` 拆分命令、实际运行命令、保存 stdout/stderr 原始日志，并写入包含日志 SHA-256、提交 SHA、退出码、时长的 JSONL manifest。
- `verification.md` 标为 `pass` 时，必须有成功、完整且指向同一 subject commit 的证据；标为 `partial` 时可进入 review，但必须显式阻断 release。
- 所有新增能力仅用 Python 标准库，保留 v1 配置/旧批准记录的读取兼容性；缺少 v2 控制字段时给出明确降级信息，而不是静默声称已强制。

## 用户与行为需求

| 场景 | 触发 | 系统行为 | 验收标准 |
| --- | --- | --- | --- |
| 批准计划 | 负责人执行 `approve` | 记录 actor、角色、工件哈希、策略哈希、Git HEAD 和时间 | 修改 plan 后 `gate --through plan` 必须失败并提示批准过期 |
| 源码 PR 门禁 | CI 或本地执行 `guard` | 读取 Git diff，识别产品变更，检查关联 change 的已批准 plan 和范围 | 无 change-id、plan 未通过、越出 `planned_paths` 的源码变更均非零退出 |
| 运行验证 | 工程师执行 `verify` | 在指定工作目录实际运行 argv，保存不可覆盖的带时间戳日志和证据 manifest | 成功/失败退出码与日志、SHA、subject commit 一致且可被校验 |
| 部分验证 | 环境/外部依赖阻断部分测试 | `partial` 允许产出审查结论，但要求 release_blocked | `gate --through review` 可进行；`gate --through release` 必须拒绝 |
| GitHub PR | PR 修改任意路径 | workflow 总会触发；从标题提取标准 change-id 并调用 guard | 纯源码 PR 没有 `[CHG-日期-序号]` 时失败，不再被 paths 过滤跳过 |

## 设计与技术边界

- 主要流程、接口/事件、数据和依赖：`scripts/ain.py` 新增 Git 辅助函数、哈希/批准有效性判断、`guard` 和 `verify` 子命令；`config/framework.json` 定义 v2 governance；`templates/plan.md` 添加 `planned_paths`；`templates/verification.md` 添加 subject commit、release 阻断与证据索引；GitHub workflow 通过 PR 标题和 base/head SHA 调用 guard。
- 批准绑定：intent/spec/plan 默认绑定工件 SHA 和策略 SHA；verification/review 还绑定工件声明的 `commit_sha`。不实现真实身份提供商；可选 `role_bindings` 仅校验本地声明的 actor 值。
- Git 范围：guard 支持 `--base/--head` 用于 CI，以及工作树默认范围用于本地。无法获得 Git diff 时，以明确错误失败，不把未知状态视为通过。
- 失败与降级行为：v1 工件没有哈希的旧批准会显示为 legacy，并在启用 `require_bound_approvals` 时被视为待重新批准；`partial` 不会变成 release 许可；验证命令失败仍保存日志和 manifest，但返回非零。
- 非功能需求：不执行 shell 字符串，使用 `shlex.split`；日志文件名安全、大小上限 5 MiB；任何审计/证据记录均包含 SHA-256；无网络、无第三方依赖、默认命令超时 15 分钟。

## 策略与风险

| 策略/标准 | 适用方式 | 冲突或例外 | 所有者决议 |
| --- | --- | --- | --- |
| 审批不等于身份认证 | 记录可校验 bind，不声称本地 `--by` 是身份认证 | 生产身份由 GitHub/OIDC/分支保护补足 | user-request 接受 |
| 先计划后实现 | guard 对产品源码强制已批准 plan 和路径范围 | 文档与 change 工件不属于产品源码 | Codex-framework-architect 提议 |
| 证据先于结论 | pass verification 依赖真实 verify manifest | 无法执行的验证只能 partial，并阻断 release | user-request 接受 |
| 最小权限 | verify 只执行调用者明确给出的 argv，不接收 shell 管道 | 长时间命令可通过显式 timeout 调整 | Codex-framework-architect 提议 |

## 验收与发布策略

- 必需测试：CLI 单元测试覆盖批准失效、role binding、guard 缺 change/未计划路径、真实证据记录/篡改、partial release 阻断、GitHub workflow 路径覆盖；现有 happy path 保持通过。
- 发布条件：在临时 Git 仓库运行完整 Python unittest；验证 `ain init --with-github` 安装后可执行；框架 README 示例同步更新。回滚方式是恢复单一 Git 提交；不会迁移用户业务数据。
- 风险级别与所需批准人：R2；产品负责人和技术负责人接受规格，工程师接受计划；正式发布前仍需要框架维护者审阅。

## 未决事项

- [ ] 是否把 role binding 对接 GitHub App/OIDC，留给后续集成变更。
- [ ] 是否为已安装的 v1 运行时提供无覆盖升级命令，留给后续迁移变更。
- [ ] 具体语言/平台 Profile 不在本次范围内。

## 接受记录

- 产品负责人：user-request，批准通用控制面范围。
- 技术负责人（R2/R3 必填）：Codex-framework-architect，仅作为本地实现的技术设计记录，不替代生产责任人。
- 策略所有者（如适用）：待框架维护者在发布审查阶段复核。
