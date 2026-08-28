---
change_id: CHG-20260828-001
status: accepted # draft | accepted | superseded
input_spec: spec.md
engineer_owner: "Codex"
accepted_by: "user-request"
risk_level: R2
planned_paths: "scripts/ain.py,config/framework.json,templates/plan.md,templates/verification.md,templates/AGENTS.md,integrations/github/ain-gate.yml,tests/test_ain.py,README.md,AGENTS.md,REVIEW.md,.ain/**,ai/changes/CHG-20260828-001/**"
---

# Implementation Plan：强化 AIN-Loop 的不可绕过门禁与可验证证据链

## 变更范围

| 文件/组件 | 修改内容 | 原因 | 风险 |
| --- | --- | --- | --- |
| `scripts/ain.py` | Git/哈希辅助函数、绑定审批、guard、verify、partial/release 交叉门禁 | 把关键控制从声明升级为可执行检查 | 兼容旧工件、错误阻断正常流程 |
| `config/framework.json` | v2 governance、计划路径和验证证据策略 | 让控制可配置而不是硬编码 | 配置语义升级 |
| `templates/plan.md`、`templates/verification.md` | `planned_paths`、`release_blocked`、subject commit、证据索引 | 让新工件可被 CLI 验证 | 用户需要补充新字段 |
| `integrations/github/ain-gate.yml` | 移除窄路径触发，解析 PR 标题 change-id，调用 guard | 防止源码 PR 绕过控制 | 旧 PR 标题不合规会失败 |
| `tests/test_ain.py` | 临时 Git 仓库、审批失效、guard、verify、partial 的回归测试 | 防止本次发现的问题回归 | 测试依赖本机 Git |
| `README.md`、`templates/AGENTS.md` | 更新真实使用、身份边界和 CI 接入说明 | 不让文档夸大本地身份能力 | 文档与命令不一致 |
| `.ain/`、`ai/changes/...` | 将框架自举到新版 runtime 并保留本次决策证据 | dogfood 新门禁 | 生成副本与分发源不同步 |

## 实施顺序

1. 增加安全的 Git、SHA-256、文件模式和配置兼容辅助函数；扩展 stage evaluation，使批准绑定工件/策略内容，验证 stage 支持 pass/partial 的不同条件。
2. 增加 `guard`：从 Git base/head 或工作树读取变更，跳过纯工件/文档，要求 change-id、已批准 plan 和 `planned_paths` 覆盖。
3. 增加 `verify`：以非 shell argv 执行命令，限制输出大小和超时，持久化日志与 hash-linked JSONL evidence；pass verification 校验同一 subject commit 的成功证据。
4. 更新默认 config、模板、GitHub workflow 和操作文档；明确本地 actor 只是 attestation，不能替代远端身份认证。
5. 扩展 unittest 覆盖普通路径与攻击/失效路径；初始化临时 Git 仓库模拟真实 PR diff。
6. 以本框架重新安装 `.ain` runtime，运行全量测试、`doctor`、dogfood gate，并将命令输出写入 verification evidence。

## 备选方案与未采用原因

- 方案：仅在 README 中建议使用受保护分支；未采用原因：建议无法阻止本地或 CI 中的源码绕过。
- 方案：接入 GitHub OAuth/API 直接认证审批人；未采用原因：会破坏厂商无关和零依赖目标，应由外部身份系统集成完成。
- 方案：允许 `verify --command` 使用 shell；未采用原因：管道、重定向和插值会扩大本地执行风险，首版只接受可解析 argv。
- 方案：把 partial 视为失败且永远不允许审查；未采用原因：会让已验证部分失去审查价值，采用“可审查、不可发布”的显式状态。

## 风险、依赖与回退

- 最可能破坏的相邻流程：已安装 v1 业务仓库的审批与 gate；因此新字段采用可选解析，v2 config 才启用严格绑定，并在错误信息中说明迁移路径。
- 外部依赖、数据迁移、权限与兼容性影响：新增能力依赖 Git 可执行文件；没有 Git 时仅 `validate/status` 可继续，`guard/verify` 明确拒绝。无数据迁移、无网络或凭据读取。
- 回退方式、验证方式、触发条件：单个 Git 提交回退即可；若 guard 对现有工作流造成误阻断，可暂时关闭 config 的 strict governance 开关，同时保留日志和待修复用例。

## 验证计划

| 层级 | 命令/方法 | 证明什么 | 通过条件 |
| --- | --- | --- | --- |
| 单元 | `python3 -m unittest -v tests/test_ain.py` | CLI 在临时 Git 仓库中的功能、拒绝和兼容行为 | 全部通过 |
| 集成 | `./bin/ain --target <temp> init --with-github` 后运行 guard/verify/gate | 分发源安装出的 runtime 可用，workflow 具备通用路径覆盖 | 关键命令 exit 0；绕过场景非零 |
| 端到端 | 框架仓库自身的 `CHG-20260828-001` | intent→spec→plan→实现→evidence 的 dogfood 闭环 | plan gate、验证日志和 review 前检查一致 |
| 安全/性能 | 检查无 `shell=True`、命令受 `shlex.split`、日志 ≤5 MiB、无第三方 imports | 不扩大命令注入或依赖面 | 静态检查无违规；大输出被截断并注明 |

## 计划接受记录

- 工程师：Codex。
- 技术负责人（R2/R3 必填）：Codex-framework-architect，仅接受本地框架实现计划；正式分发仍需框架维护者审阅。
- 接受时间：2026-08-28，用户授权按实施者判断完成通用优化。

> 实现与本计划不一致时，先更新本文件并在同一 PR 中解释原因。
