# Service Contracts Phase One Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development for implementation and independent review. Steps use checkbox syntax for tracking. User has already authorized execution on the current branch; do not create another branch or merge.

**Goal:** 建立独立的来源／位置／引擎契约并验证真实引擎，为内置运行时和订阅网关提供可测试接入边界。

**Architecture:** 在独立 Python 模块定义不可变选择与执行上下文，通过现有配置存储和任务入口接入。先保持 custom/local 的真实行为，明确拒绝未接通的订阅与云端执行。引擎验证在独立工作区进行，不影响当前应用会话。

**Tech Stack:** 现有 Python 标准库、FastAPI/Pydantic、pytest、TypeScript；不增加运行依赖。

## Global Constraints

- 模型服务来源为 custom／subscription；执行位置为 local／cloud；执行引擎保留现有适配层。
- 所有运行中的任务和现有原生会话保持创建时归属，不在切换设置时迁移。
- 自定义本机使用无需平台账号；平台模型和云端执行要求平台鉴权。
- 所有套餐能力以实际后端、权益与兼容性验证为准，不做假余额和假开通。
- 不在客户端内置上游平台总凭证，不静默切换付费来源。
- 本阶段不修改现有日夜模式文件，不合并主分支，不自动部署云端服务。

## Task 1: 不可变契约、旧配置与 Web 接入

**Files:**

- Create: `easel/service_contracts.py`（名称可随职责合理细化）。
- Modify: `easel/harnesses/store.py`, `web/app.py`, `web/frontend/src/lib/api.ts`。
- Create: `tests/test_service_contracts.py`。
- Extend when needed: `tests/test_agent_harnesses.py`, `tests/test_direct_api.py`。
- Final review follow-up: `easel/cli.py`, `easel/commands/skill.py`, `easel/commands/ping.py`, `easel/harnesses/__init__.py`, `tests/test_service_cli_contracts.py`, `tests/test_sync_task_outcomes.py`。

**Interfaces:** 来源字段为 `service_source`，位置字段为 `execution_target`。服务模块提供严格校验、当前可用性检查和不可变执行上下文；存储层向旧客户端保持兼容，并向新客户端返回新字段和禁用原因。

- [x] 写契约与无写盘迁移测试：没有新字段的旧文件读出 custom/local，文件字节不变；四种合法枚举可建模，非法持久值明确拒绝。
- [x] 运行新测试确认因缺少契约功能失败，再实现最小服务模块和存储集成。
- [x] 写设置接口测试：缺字段保持当前值，未接通组合返回 409，响应含可用性原因，失败不改 config 或 .env。
- [x] 接入设置接口并更新前端 API 类型。保留运行中任务保护与环境锁，不增加新运行依赖。
- [x] 写任务与历史测试：来源／位置冲突在执行前被拒绝；旧绑定解释为 custom/local；更新保留原生 ID 和其他已有元数据。
- [x] 在任务提交时建立不可变上下文，在三个通道保持同样的来源／位置边界；任务记录包含不含凭证的路由信息。
- [x] 验证受控等待期间默认配置变化不会改变已提交任务的路由与选定模型。
- [x] 运行定向回归，提交前检查原有 UI 文件没有混入。

测试设计示例（实际测试使用实现定义的公开类型）：

```python
def test_legacy_settings_are_read_without_rewrite(tmp_path):
    config_path = tmp_path / "_harness" / "config.json"
    config_path.parent.mkdir()
    config_path.write_text('{"runtime":"codex","models":{"codex":"m"}}')
    before = config_path.read_bytes()
    value = store.settings(tmp_path, {})
    assert value["service_source"] == "custom"
    assert value["execution_target"] == "local"
    assert value["models"]["codex"] == "m"
    assert config_path.read_bytes() == before
```

测试使用项目虚拟环境依赖；当前虚拟环境未安装 pytest，验证时仅在 sys.path 末尾追加 `/opt/anaconda3/lib/python3.12/site-packages` 来加载已有测试工具，不改运行依赖。完整可复现命令见交付验证报告。

## Task 2: 候选引擎的独立真实验证

**Files:** 新增 `docs/validation/2026-10-15-engine-compatibility.md` 和可复现的非计费探针 `scripts/validation/verify_codex_gateway.py`；真实模型操作脚本和脱敏原始输出保存在忽略的 `outputs/_run/phase1-engine-validation/`。

**Interfaces:** 复用现有 `RunRequest`、`CodexAdapter.run`、RPC 流式事件与 prompt 范围中断；不修改全局引擎设置或用户凭证。

- [x] 检测候选 CLI 的版本、实际启动资源和协议，不安装未授权的其他引擎。
- [ ] 真实上游模型写入／读取标记文件验收：原生登录遇到 HTTP 401；用户已明确选择稍后补齐。已通过真实引擎＋模拟模型网关的工具与流式验证，两者分开记录。
- [x] 对自定义网关认证、协议与工具循环使用受控模拟服务验证；记录它与真实模型证据的区别。
- [x] 运行已有权限、恢复、错误脱敏与取消测试；如发现协议缺口，先加入复现测试，再修复。
- [x] 记录默认候选、固定版本、打包所需资源与真实验证未覆盖的边界；不把已安装状态视为认证通过。

## Task 3: 审查、回归与分支交付

**Files:** 上述实现、测试、阶段设计和验证报告；更新总体路线图的实际交付记录。

- [x] 独立审查规格覆盖、历史兼容、副作用顺序、并发入口与凭证暴露。
- [x] 修复审查发现并重跑覆盖测试，保持审查证据。
- [x] 运行完整 Python 测试与前端测试；运行前端构建，记录既有告警与新失败的区别。
- [x] 核对阶段验收项，分别报告代码验证、模拟协议与真实模型结果。
- [x] 仅提交第一阶段文件到 `codex/ui-redesign`，保留其他未提交修改，不合并主分支。

## Progress

- Task 1: completed。契约、迁移与全部 Web／CLI 入口实现，审查发现的会话删除、同步归属与结果状态问题已修复。
- Task 2: 模拟网关／真实引擎协议验证 completed；真实上游模型验收按用户明确要求 deferred（HTTP 401），不再重试。
- Task 3: completed。a0c8157 最终整体审查通过；完整 Python 1314 passed／6 skipped，前端 116 passed、构建成功。分支与原有 UI 保留检查通过。

完整证据、复现命令和测试隔离事件见 [交付与验证记录](../../validation/2026-10-15-phase-one-delivery.md)。内置候选仍为暂定，不把延期验收当作通过。
