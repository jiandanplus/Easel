# 工作台中央体验额度与 Pi 状态 UI 审查

日期：2026-10-23。**规格符合性 PASS，代码质量 PASS；未发现本次增量的 P1／P2 问题。**

范围：`CentralAccountSettings.tsx` 体验元数据和非付款说明、`api.ts` 可选响应类型、对应前端回归，以及 `DashboardPage.tsx`／`App.tsx` 的 Pi 状态说明传递。未修改产品代码，未读取私有认证、配置或日志，未运行服务、网络或模型调用。

## 检查结果

- `CentralWallet` 的 experience_available／experience_issued 十进制字符串与 experience_expires_at ISO 字符串或 null，以及 membership 的 internal_experience／test／live funding_source，与当前后端返回契约一致。新增字段可选，兼容旧响应。
- 额度使用现有 BigInt 格式化保留微点精度；未读取、零值和无效发放数不会虚构体验额度。额度耗尽时仍显示真实累计发放，缺少到期值显示未知。
- 体验额度明确显示“内部体验额度 · 非付款”“运营者发放，用于真实模型体验；不代表购买订阅”。没有新增购买状态、认证动作或发放／财务写入路径。
- Dashboard 的 Pi 分支优先显示 runtimeDetail；缺失时才使用内置 Pi 提示。API、OpenClaw 与 Codex 原有分支保留，Pi 不再被提示安装外部 CLI。
- App 只向 Dashboard 传递已有 runtimeDetail，没有改变聊天身份、会话挂载或执行控制。

## 独立验证

实际执行 `node --test tests/frontend/centralAccount.test.mjs tests/frontend/piStatusCopy.test.mjs`：**21 passed，0 failed，0 skipped**，565.653375 ms。覆盖体验额度正常／无效／零值／耗尽、认证取消竞态、未知余额、中央状态，以及 Pi 状态文案。

实际执行 `web/frontend/node_modules/.bin/tsc -b web/frontend/tsconfig.json --pretty false`：**exit 0**。

此审查仅证明 UI 契约及显示行为，不包括内部赠送 grant 的权限、数据库迁移、新隔离环境部署、实际 OIDC 登录或真实调用费用验收，也不等于本次新 UI 的打包或浏览器验收。

## 后续单句文案复核

**PASS，无新增 P1／P2。** `CentralExecutionSettings.tsx:43` 将“本阶段支持本机 Codex 文本与工具调用。”改为“本阶段支持本机 Agent 的文本对话与工具调用（支持内置 Pi）。”其后的媒体能力、云执行待开放及测试点数说明保留。该句修正执行引擎范围，不改变模型目录、认证、费率、资金模式或可执行判断。

`centralExecution.test.mjs:32` 在原有模型／费率／能力说明测试中新增精确文案断言；作者报告 RED 7 passed／1 failed，GREEN 8 passed／0 failed。本次追加只读审查，未重复运行测试、构建或浏览器操作，未干预 17868 上的用户登录。v6 打包及其身份应单独记录。

审查 SHA256：CentralExecutionSettings.tsx 为 `ce56e19674953552f6d45de2ace67d5d63ba4825d28720fe21ecf905ee6d0fa7`，对应测试为 `d1f67a8c0626b50cdf88d7c4b36a5f8352f7051f48af4fc33a32c6991b89f5ee`。
