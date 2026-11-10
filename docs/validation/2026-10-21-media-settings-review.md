# 统一媒体网关设置独立审查

日期：2026-10-21。范围：2026-10-21 统一媒体设置方案 Task 1–3 的共享配置、设置 API、前端设置状态及 CLI 接入点。当前分支已有其他 UI/媒体修改，本审查仅评估新增统一设置带来的交互与执行语义。未读取或修改真实项目 `.env`，未连接供应商或部署服务器。

## 初次审查发现

### R1 — P2：旧手工 New API 配置被前端误判为受统一表单管理

- 位置：`media_gateway.py:139` 附近的 `gateway_settings`、`SettingsPanel.tsx:80` 和 `817–823`。
- 最小输入：`IMAGE_API_MODE=new-api`，同时有 `IMG_API_KEY`、`IMG_BASE_URL`、`IMG_MODEL`，没有 `NEW_API_IMAGE_MODEL`。
- 本地纯函数复现：`gateway_managed('image', env)` 为 `False`，但 `gateway_settings(env)` 返回 `modes.image='new-api'`、`configured.image=False`、空图片模型。
- 后端旧路由仍可工作，新前端只按 mode 判断，隐藏原供应商表并禁用旧保存/自测。用户必须迁移配置或改变实际协议模式才能重新编辑旧通道。
- 修复目标：读取状态要区分手工适配模式和共享表单管理；保留旧协议与独立编辑入口，不把旧适配配置显示为未配置的新统一通道。
- 同源工作流复现：`VOICE_API_MODE=new-api`、`VOICE_PROVIDER=openai-compatible`、`VOICE_MODEL=FunAudioLLM/CosyVoice2-0.5B`，无 `NEW_API_VOICE_*`，`tts._closed_voice_id(None)` 返回 `None`，`multivoice.cast_init(..., narrator_provider='openai-compatible')` 写出 `alloy`；仅将 mode 改为 custom，二者均返回原预期 `FunAudioLLM/CosyVoice2-0.5B:alex`。根因为这两个入口用 `gateway_enabled` 判断统一旁白而未使用 `gateway_managed`。主任务接手修复。

### R2 — P2：核心媒体路径错误输出仍可能回显网关密钥

- 位置：`ai_image.py:367–369` 的 `http_post`；图片 multipart/JSON 异常和 `voice_clone.py` 的核心 OpenAI/Gemini HTTP 错误路径存在同类行为。
- 本地定向复现：mock `_open` 抛出 HTTP 401，响应体为 `{"error":"invalid token unit-gateway-secret"}`。调用 `http_post` 后 stderr 包含完整 `unit-gateway-secret`。
- 新统一配置将网关业务密钥交给这些旧核心路径，但旧路径直接打印上游响应；私有插件的脱敏并未覆盖它们。
- 修复目标：受统一网关管理的核心路径在错误、无效 JSON 与 multipart 失败时屏蔽原始响应或脱敏实际业务密钥，并保持自定义路径兼容。

### R3 — P2：OpenClaw 子进程继承旧媒体环境时，新保存值无法覆盖

- 位置：`web/app.py:3667` 的 OpenClaw 启动环境，以及媒体 CLI `load_env_file`（例如 `ai_image.py:140–141`）。原生 harness 已在 `3275` 使用 `_fresh_media_gateway_env`，OpenClaw 路径未使用。
- 本地定向复现：进程环境设为旧网关地址、旧模型与旧密钥；临时 `.env` 写入对应新值。调用真实 `ai_image.load_env_file` 与 `resolve_media_environment` 后仍得到旧地址、旧模型、旧密钥。
- OpenClaw 的常驻 gateway 还可能保留启动时环境；仅刷新 Web 启动的客户端环境不能证明 daemon 执行环境已刷新。
- 修复目标：确保下次媒体 CLI 执行读取保存后的共享字段，同时保留明确的手工旧配置行为；验证时覆盖继承旧环境、新文件、清除密钥及跨能力隔离。

## 设计一致性备注

设计说明要求统一模式中的旧供应商配置仍可编辑保存；初版前端完全隐藏旧表，要求切回自定义并保存后才能编辑。该问题与 R1 的保留编辑入口一起处理，未额外计数。

## 复核状态

- R1：已验证关闭。设置响应新增 `managed`，前端明确显示“保留旧版 New API”，只在显式选择统一网关后提交 `adopt`；保存其他能力不迁移旧配置。供应商表在统一模式仍可编辑，读取与保存返回处均过滤合成的 `new-api` 行，避免旧保存入口误写统一设置。旁白两处分支改为 `gateway_managed`，保留旧 CosyVoice 默认值。
- R3：已验证关闭。四媒体 CLI 的文件加载器及注册表复用 `refresh_gateway_environment`，以保存的共享字段覆盖 daemon 继承旧值；删除的共享字段也会清除，供应商字段仍遵循原有环境优先级。`multivoice` 使用同一加载器，`tts`/`dubbing` 沿用已经接入此逻辑的加载器。这关闭了新进程执行时的旧环境复现；未对真实常驻 OpenClaw daemon 作端到端调用。
- R2：已验证关闭。`ai_image.py` 与 `voice_clone.py` 的受管理核心路径通过 `_remote_detail` 隐藏原始上游诊断；自定义路径保留原诊断。
- 独立复核命令：`python3 -m pytest -q tests/test_media_gateway_config.py -k 'http_errors_never_echo or custom_image_error or success_status_error or reject_redirect'`，结果 **8 passed, 40 deselected**。涵盖四条 HTTP 401 错误路径、HTTP 200 业务错误、自定义诊断兼容，以及图片/配音两条禁止跟随跳转路径。默认沙箱不允许绑定回环端口；相同命令在获准的沙箱外本地运行通过。
- R1/R3 独立复核：`python3 -m pytest -q tests/test_media_gateway_legacy_settings.py tests/test_media_gateway_workflow_config.py` **16 passed**；`node --test tests/frontend/mediaGateway.test.mjs` **10 passed**。另使用项目虚拟环境依赖与临时 pytest 入口运行 `tests/test_media_gateway_settings.py -k 'legacy or reentered or generic_env or blank_key'`，**5 passed, 16 deselected**。旁白回归也单独执行过，**1 passed, 5 deselected**（包含在后来的 16 项里，不另累加）。
- 测试环境说明：项目 `.venv` 没有 pytest，系统 Python 的旧 Starlette 不支持该测试使用的 `TestClient(client=...)`。API 复核改用 `.venv/bin/python /private/tmp/easel-settings-pytest.py`，仅将系统 pytest 包路径附加在末尾，项目运行依赖优先；未修改项目依赖。
- 当前结论：初审三项 P2 均已独立验证关闭。本次范围内未发现其余需阻止交付的 P1/P2；浏览器布局、真实供应商与部署验收由主任务分别记录，不由上述结果代替。
- 本报告的证据为本地纯函数和假 HTTP 错误复现，不代表真实供应商可用性或 OpenClaw 常驻服务实测。
