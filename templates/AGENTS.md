# Repository operating context

## Product and architecture

<!-- 用不超过 10 行说明产品、模块边界、主要依赖和事实来源。 -->

## Commands

- Build: `<command>`
- Unit tests: `<command>`
- Integration tests: `<command>`
- Lint/format: `<command>`
- Run locally: `<command>`

## Non-negotiable rules

- 所有变更从 `ai/changes/<change-id>/intent.md` 开始。
- 接受 `plan.md` 前不得修改代码。
- 声明完成前必须提交 `verification.md` 和原始工具证据。
- 不跳过、删除或弱化失败的测试；修复实现或记录经批准的风险接受。
- 不读取密钥文件，不直接推送受保护分支，不绕过 CI、Hook 或审批。

## Architecture and conventions

<!-- 只写 Agent 高频需要、且无法从代码直接推断的约定。 -->

## Protected areas

<!-- 列出生成代码、旧版冻结目录、迁移、生产配置等特殊边界。 -->

## Known agent mistakes

<!-- 同一种错误出现两次，就在此添加一条简洁、可执行的纠正规则。 -->
