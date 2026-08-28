# Repository operating context

## Product and architecture

<!-- 用不超过 10 行说明产品、模块边界、主要依赖和事实来源。 -->

## Commands

- Build: `<command>`
- Unit tests: `<command>`
- Integration tests: `<command>`
- Lint/format: `<command>`
- Run locally: `<command>`

## AIN-Loop commands

- 创建变更：`./.ain/ain new --title "..." --owner <负责人>`
- 计划完成且已提交后：`./.ain/ain approve <change-id> --stage plan --role engineer --by <身份>`
- 检查当前 Git diff 是否仍在已批准范围内：`./.ain/ain guard --change <change-id>`
- 运行真实验证并保存原始日志：`./.ain/ain verify <change-id> --kind unit --command '<command>'`
- 校验证据链：`./.ain/ain verify <change-id> --check`

## Non-negotiable rules

- 所有变更从 `ai/changes/<change-id>/intent.md` 开始。
- 接受 `plan.md` 前不得修改代码。
- `plan.md` 的 `planned_paths` 是代码修改的允许范围；任何计划外产品路径必须先更新计划并重新批准。
- 严格模式下，审批绑定工件哈希与策略配置哈希；工件或策略变化后必须重新审批。审批前工作树必须干净。
- 声明完成前必须提交 `verification.md` 和原始工具证据。
- `verification.result: partial` 可进入审查，但绝不能发布；只有 `pass` 且证据链有效时才可解除 `release_blocked`。
- 不跳过、删除或弱化失败的测试；修复实现或记录经批准的风险接受。
- 不读取密钥文件，不直接推送受保护分支，不绕过 CI、Hook 或审批。

## Architecture and conventions

<!-- 只写 Agent 高频需要、且无法从代码直接推断的约定。 -->

## Protected areas

<!-- 列出生成代码、旧版冻结目录、迁移、生产配置等特殊边界。 -->

## Known agent mistakes

<!-- 同一种错误出现两次，就在此添加一条简洁、可执行的纠正规则。 -->
