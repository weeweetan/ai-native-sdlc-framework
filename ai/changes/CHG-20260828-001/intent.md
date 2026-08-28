---
change_id: CHG-20260828-001
status: accepted # draft | proposed | accepted | rejected | superseded
risk_level: R2 # R0 | R1 | R2 | R3
source: framework_dogfood # idea | ticket | incident | metric | security_scan
author: "user-request"
owner: "user-request"
created_at: 2026-08-28T07:48:10Z
related_records: ["README.md", "scripts/ain.py", "integrations/github/ain-gate.yml", "tests/test_ain.py"]
policy_versions: ["framework-governance-v2"]
---

# Intent：强化 AIN-Loop 的不可绕过门禁与可验证证据链

## 问题与证据

在一个真实原型中使用本框架时暴露出四类通用缺口：计划工件虽然要求先批准，但 CLI 和 GitHub Action 都无法阻止先修改源码；`approve --by` 接受任意字符串，并且批准不绑定被批准工件的内容；验证记录和 `commit_sha` 需要手工填写；当部分验证因环境依赖无法执行时，框架无法允许受限审查、同时禁止发布。

具体代码证据：`scripts/ain.py` 的 `cmd_approve` 只保存 stage/role/by/note，`infer_risk` 默认只扫描变更 Markdown，GitHub workflow 仅监听 `ai/changes/**` 和控制文件。现有 `tests/test_ain.py` 只有一条 happy path，未覆盖审批过期、源码绕过和证据篡改。

## 期望结果

交付一个兼容现有工件的 v2 控制层，使新初始化仓库具备：

- 审批记录含工件 SHA-256、策略 SHA-256 和 Git 提交上下文；工件被改动后对应批准自动失效。
- `guard` 能从 Git diff 判断产品源码是否关联 change-id，并要求该 change 的 plan 门禁已通过。
- `verify` 实际执行受控命令，保存原始日志、退出码、时长、提交 SHA 和哈希链；`pass` 的 verification 必须引用有效成功证据。
- `partial` verification 可进入受限 review，但必须显式 `release_blocked: true`；release 门禁必须拒绝它。
- GitHub Action 对所有 PR 运行，并从 PR 标题中的标准 change-id（例如 `[CHG-20260828-001]`）取得变更 ID；源码 PR 缺少 ID 时失败。

验收标准：新增自动化测试覆盖以上至少五个绕过/失效场景，既有测试仍通过；README 给出 Git/CI/验证使用方式。

## 受影响的用户与系统

- 用户/角色：使用 Codex、Claude Code 或其他 Agent 的研发团队；产品、技术、安全、验证和发布负责人。
- 服务、数据、接口、依赖：本地 Git 仓库、GitHub Pull Request/Actions、Python 3.9+ 标准库；不新增第三方 Python 依赖。

## 约束与不可做项

- 安全、隐私、法规、性能、成本或兼容性约束：不得把 `--by` 伪装成远端身份认证；本地 CLI 只能提供可校验的证据绑定，生产身份仍须依赖代码托管与受保护分支。现有 v1 工件不能因升级而无法读取。
- 明确不在本次范围内的内容：不接入真实 GitHub OAuth、CODEOWNERS API、远端不可变审计存储、云端数据库或生产发布权限；不自动修改用户业务代码。

## 风险初判

- 建议风险等级：R2；理由：修改研发门禁、批准语义和 CI 行为，会影响高风险变更的控制强度。
- 需要咨询的策略所有者：框架维护者、CI/安全策略所有者；本轮只在本地框架仓库实现和测试，不触及线上实例。

## 开放问题

- [ ] 后续是否需要 GitHub App/OIDC 对真实审批主体签名。
- [ ] 后续是否需要为 GitLab、Bitbucket 提供等价 CI 适配器。
- [ ] 特定语言、运行时或部署环境的完整策略包单独作为后续变更，避免把核心控制面扩大为平台工程。

## 接受记录

- 产品负责人：user-request
- 决定：接受，用户明确授权按实施者判断优化当前框架。
- 决定时间与链接：2026-08-28，本会话请求。
