# 订阅登录与首次配置引导修复：源码候选验收

日期：2026-10-28。已按审核通过的 A1 最小方案完成源码修改，交还 root 独立审读。当前正在运行的 v30 包未包含该候选；本轮未 freeze、build、打包、激活、commit、push 或 merge。

## 最终行为与改动边界

`web/app.py` 仅改三处 return：central/legacy `_subscription_readiness` 均保留客户端现成的 `configured`；订阅 bootstrap 的 `modelConfigured` 使用它。`routeOk`、`gatewayUp` 与 `/api/status.gateway` 仍由原来的 `ready` 决定，认证与模型目录计算不变。`web/frontend/src/App.tsx` 仅改画像推荐条件，额外要求同批 settled `data.gateway === true`。

已配置订阅但未登录、刷新状态待确认、目录暂错时，不再因为 readiness=false 被强制打开模型配置或画像推荐。缺少自定义 provider 的首次使用仍打开配置。保存的订阅默认模型可以为空；未新增 saved-model 条件。模型目录、lease、账号边界、Send 门禁、服务 schema、控制器、token/policy 和 UI 文案均未修改。

## 测试证据

先改真实测试、观察失败，再改四行产品代码：

- 后端 RED：真实 `_subscription_readiness` 与 TestClient bootstrap 8 failed / 2 passed / 17 deselected。失败是 configured 丢失及目录缺模型/暂错时错误返回 `modelConfigured=false`。
- 前端 RED：实际 App AST mount effect 8 failed / 10 passed。失败是 gateway=false 仍显示画像推荐；未复写实现。
- 后端最终 GREEN：`tests/test_pi_integration.py` + `tests/test_web_model_config_parity.py`，42 passed / 0 failed。
- 前端聚焦 GREEN：settingsNavigation、workbenchReadiness、composerModels、modelReadinessLifecycle、chatComposerFlow，95 passed / 0 failed。
- 一次完整前端：541 passed / 0 failed。
- `tsc -b web/frontend`：exit 0；指定文件 `git diff --check`：exit 0。

新增后端用例覆盖 central/legacy × configured false/true × auth false，以及认证后目录 ready/缺少保存模型/目录异常；断言 bootstrap setup 与 status readiness 分离。既有 Pi 自定义 provider missing/ready 与 OpenClaw env/route 用例保持通过。前端真实 effect 用例覆盖不可用状态、connected 且未看过/已看过/已有画像、bootstrap null/失败、两种响应完成次序，以及迟到旧 status+旧 bootstrap 不得重开 Settings 的序列保护。既有完整前端验证继续涵盖过期 lease、来源变化、模型确认与发送拦截。

运行命令：

```sh
rtk proxy .venv/bin/python -m pytest -q tests/test_pi_integration.py tests/test_web_model_config_parity.py
rtk node --test tests/frontend/settingsNavigation.test.mjs tests/frontend/workbenchReadiness.test.mjs tests/frontend/composerModels.test.mjs tests/frontend/modelReadinessLifecycle.test.mjs tests/frontend/chatComposerFlow.test.mjs
rtk node --test tests/frontend/*.test.mjs
rtk proxy web/frontend/node_modules/.bin/tsc -b web/frontend
```

最初使用系统 pytest/Python 发生导入路径及旧 Starlette TestClient 参数不匹配，随后使用仓库 `.venv`，未改依赖。首次既有 parity 测试继承了它原有的本机 gateway healthz 探测；发现后已把该既有 fixture 的 PROJECT_ROOT、OpenClaw path 固定到 tmp，并将 `check_gateway` stub 为 false，最终 42 tests 在此隔离 fixture 下重新通过。未发生中央服务、真实模型或付费请求，也未读取用户凭据内容；不把最初旧 fixture 的一轮称为严格零本机探测。

## 证据与交接

临时证据目录：`/private/tmp/easel-subscription-onboarding-fix-20261028/`，含 before/after 原文件快照、全量 before/after hashes、实际 RED/GREEN 日志、TypeScript 日志、`production-only.patch` 和 `task-only.patch`。这些 patch 以本轮编辑前用户 dirty 文件为基准，不混入既有 diff。只修改了两份产品源码和三份既有测试；未清理或重置用户原有 dirty 文件。

v30 编辑前 App SHA256 为 `475b2d4263d10cb278c99cfbb62d74abdf396dc4160a8dd0106add9d29191450`，web/app.py 为 `81f4b3fdc43295b3e4ee97cdb0e9dd149d3690ddc2c7e9b582fd0e5b46f7e1f2`。v30 source manifest 当前 SHA256 保持 `e66128ff51fd6146a78c16b5e19623833d2e1b263801848bd6edf14937895094`；没有为候选源码改写 authority。候选源码及测试闭合 hash 见相邻 safe JSON。

本轮证据支持源码逻辑与本地回归，未进行修复后的真实浏览器/App 验收。root 后续独立审读后决定新版本 freeze/build/activation，再验证同一 existing chat 重载不自动进入设置、avatar 登录路径、草稿保持及取得新有效 catalog/lease 前 Send 仍不可用。未触碰主计划文件。
