# Unified Media Gateway Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Implement and independently review each owned unit, then perform browser acceptance.

**Goal:** 用户能在设置页统一配置媒体网关，并按能力切回保留的自定义接口。

**Architecture:** 共享纯环境解析模块提供统一合同；后端保存与状态、CLI执行、前端编辑分别复用该合同。持久配置保留自定义值，运行时按能力派生实际参数。

**Tech Stack:** 现有 Python/FastAPI、React/TypeScript、Node tests、pytest。

## Global Constraints

- 当前分支 `codex/ui-redesign`，不合并主分支；不混入或回退已有UI及媒体适配修改。
- 不修改真实项目 `.env`；浏览器保存测试使用临时工作区及假业务密钥。
- 原有自定义凭据不被统一模式覆盖；不向客户端回传真实密钥，不在localStorage保存密钥。
- 模式或参数错误在提交前失败，不回退重发收费请求；不调用供应商进行付费验证。
- 新增功能限第一批设置与统一配置；订阅媒体、云端执行、供应商高级功能保持后续范围。

### Task 1: Shared resolver and scripts

**Files:** 新增 `skills/shared/scripts/media_gateway.py`、`tests/test_media_gateway_config.py`；修改model_registry及四个媒体脚本。

**Produces:** 设计文档中全部共享函数及环境字段，供后端复用。

- [x] 为自定义不变、四类路由、根前缀、provider冲突、跨能力隔离和key规则写失败测试并记录预期失败。
- [x] 实现共享合同；在每个CLI加载.env之后解析本能力，保持旧手工New API配置可用。
- [x] 用现有139项媒体测试及新增测试验证实际行为。

### Task 2: Settings API and readiness

**Files:** 修改 `web/app.py`，新增 `tests/test_media_gateway_settings.py`。

**Consumes:** Task 1的共享函数与schema。

- [x] 写GET脱敏、PUT往返、原子拒绝、保留自定义、清key/换地址和配置状态测试，确认失败。
- [x] 增加GET/PUT接口，将字段注册到env白名单；通用env写接口同样验证。
- [x] 调整技能状态、媒体行和任务新启动环境，验证新保存值实际传入CLI且不混写供应商凭据。

### Task 3: Settings UI

**Files:** 新增 `MediaGatewaySettings.tsx`及scoped CSS、前端配置模型测试；修改`SettingsPanel.tsx`和`lib/api.ts`。

**Consumes:** 固定GET/PUT嵌套payload合同。

- [x] 写模式切换、keep-key、模型与provider选项、错误/保存反馈测试并确认失败。
- [x] 实现统一卡片、按能力选择和编辑；自定义模式保留旧表，网关模式避免误用旧保存按钮。
- [x] 前端测试、build、lint；检查五主题及移动布局样式使用既有tokens。

### Task 4: Integration acceptance

- [x] 独立审查共享resolver、API、UI跨层语义，修复实证问题。
- [x] 在临时工作区启动本地实例，浏览器保存假网关配置并刷新回读，切换四能力、恢复自定义、验证密钥不回显；检查桌面与窄屏。
- [x] 记录测试、浏览器截图和运行限制；核对真实.env、既有UI文件和main引用未被误改。

## Execution result

已完成并独立复核。最终联合后端 376/376、前端 144/144；构建和 lint 无错误。浏览器验收及未改真实 .env / main 的哈希核对见 [验收报告](../../validation/2026-10-21-media-gateway-settings.md)。旧手工模式保留通过额外 managed/adopt 合同显式处理；常驻进程继承环境通过共享字段重读修复。无真实模型或生产服务器验收声明。
