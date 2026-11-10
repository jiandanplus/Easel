# 注册后中央回调 invalid_request 的定位与修复

## 本次根因

2026-10-23，Asia/Shanghai：最后一次本机登录事务约 18:11:53 创建、18:16:53 过期。Logto 的最新实际注册记录为 19:03:23，已经超过原五分钟登录窗口。中央 OIDC Cookie 同样只有 300 秒有效期；Cookie 缺失时，原回调在查询事务前的参数检查返回 `invalid_request`。

本次用户回调地址中已有 code 和 state，中央该事务仍为未消费状态。现场检查中央 handoff/session、内部赠送、执行及订单计数均为 0，没有授权码兑换、发点或模型扣费。仅收集事务过期时间、布尔标记和计数，没有读取密码或重新提交用户授权码。原始回调 URL、code、state、Cookie 均未写入报告。

现场安全摘要：[cause.json](assets/2026-10-23-login-expiry/cause.json)。注册已经完成，恢复方式是用新建账号重新开始一次正常登录，无需再次注册。

## 修复内容和范围

中央服务只对明确接受 `text/html` 的 OIDC `GET authorize/callback` 导航显示静态中文“登录未完成”页，说明回到 Easel 左下角重新登录，并关闭旧页面。API 的 JSON 错误结构、HTTP 状态、TTL、JWT、state、PKCE、Cookie 和事务消费校验不变。

页面不反射授权码、state、Cookie、错误详情或 URL，没有脚本、表单或自动跳转。响应同时设置 `no-store`、`no-referrer`、`nosniff` 和严格 CSP。`text/html;q=0`、通配符 Accept、其他路由和方法继续使用 JSON。

变更只涉及 `services/platform/src/platform_service/app.py` 及新测试 `services/platform/tests/test_oidc_browser_errors.py`。独立审查：[review](2026-10-23-login-expiry-review.md)。源码修改仍在 `codex/ui-redesign`，没有提交、推送或合并主分支。

## 验证

- TDD：13 项浏览器错误页场景先观察 RED，再通过。
- root 独立运行 OIDC／身份／浏览器错误页相关测试：78 passed，16.36 秒。
- root 独立完整中央服务回归：486 passed，0 failed，63.19 秒。
- 独立审查确认 handler 与 middleware 的两个异常出口均使用相同的安全响应逻辑。
- 服务器仅更新本次专属中央服务的 `app.py`，原文件先备份，核验 owned PID、父 wrapper 命令行、起始时间、监听 socket、源码摘要和私有配置摘要后受控重启。数据库及配置摘要保持。
- 部署后健康检查、无查询参数的合成 HTML callback、原 JSON callback 契约全部通过。该检查没有重放用户回调。
- 实际浏览器显示中文提示页，随后从当前工作台发起全新登录事务，给用户打开正常登录页。
- 17868 已运行已审查的 v6 包；原工作区、HOME、自定义供应商配置及中央会话文件摘要保持。[preview](assets/2026-10-23-login-expiry/preview.json)

服务器部署安全回执：[deployment.json](assets/2026-10-23-login-expiry/deployment.json)。随后用户因忘记密码要求另行注册；保留原账号和钱包，仅通过正常退出清理当前会话，再发起新注册。用户亲自设置密码并确认完成；新账号的正常 OIDC 来源已验证，10 点内部体验发放一次，真实文本、写入／读取工具和扣点通过。完整结果和边界见 [工作台订阅验证](2026-10-23-workbench-subscription.md)。个人密码始终由用户输入，没有读取或代设。
