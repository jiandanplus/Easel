# 第一批：统一媒体网关设置验收

日期：2026-10-21。分支：`codex/ui-redesign`。本批未提交、推送或合并；HEAD 与主分支引用保持开始时状态。

## 交付范围

- 设置 → 模型配置增加独立的统一媒体网关卡片，一次填写根地址、业务密钥，图片／视频／音乐／配音分别选择接口来源。
- 配置 API、技能就绪状态、媒体通道、四个媒体 CLI 和配音工作流使用共享解析合同。每个能力在自己的进程内派生供应商别名，不将音乐和语音的同名凭据全局混写。
- 新表单使用 `NEW_API_*` 字段，自定义供应商值继续保留。统一模式启用时保留表仍可编辑保存；原供应商自测暂停，避免将旧配置的测试结果当成统一网关结果。
- 旧手工 New API 配置显示“保留旧版 New API”。`managed` 状态和显式 `adopt` 区分旧适配与新表单接管；保存其他能力不会自动迁移旧能力。
- 空密钥保留、显式清除、换地址重新填 key、失败原子拒绝、GET 脱敏。统一核心图片／语音的远端错误不打印原始响应，不跟随重定向转发业务 key。
- 新媒体进程读取已保存的网关字段，覆盖常驻进程继承的旧网关值；清除的密钥不会复活。供应商自定义变量保留原有环境优先行为。

使用步骤见[统一设置说明](../platform/media-gateway-settings.md)，合同见[设计](../superpowers/specs/2026-10-21-media-gateway-settings-design.md)。

## 自动化验证

最终源码冻结后由主任务联合运行：

```sh
rtk proxy .venv/bin/python /private/tmp/easel-settings-pytest.py \
  -p pytest_asyncio.plugin -o asyncio_default_fixture_loop_scope=function \
  tests/test_media_gateway_config.py tests/test_media_gateway_settings.py \
  tests/test_media_gateway_legacy_settings.py tests/test_media_gateway_workflow_config.py \
  tests/test_media_gateway_settings_integration.py tests/test_newapi_image.py \
  tests/test_agnes_image.py tests/test_newapi_video.py tests/test_ai_video_env.py \
  tests/test_newapi_music.py tests/test_newapi_voice.py tests/test_gemini_tts.py \
  tests/test_models_fetch.py tests/test_web_security.py tests/test_web_anthropic_slot.py \
  tests/test_web_model_config_parity.py tests/test_web_openclaw_path.py \
  tests/test_runtime_env.py tests/test_subscription_execution.py -q
```

结果：**376 passed，5.01 秒**。私有临时 runner 只为复用当前环境已有 pytest：以项目 `.venv` 运行、禁用自动插件加载、补充本机已安装的 pytest 路径，没有安装或升级依赖。默认沙箱不允许绑定回环测试端口，以上命令使用获准的沙箱外权限执行本地 fixture 与正常 DNS 校验，没有供应商推理请求。

```sh
rtk proxy node --test tests/frontend/*.test.mjs
cd web/frontend
/Users/jiandan/.cargo/bin/rtk proxy npm run build
/Users/jiandan/.cargo/bin/rtk proxy npm run lint
```

结果：**144/144 前端测试通过**，TypeScript + Vite 构建成功，lint 无错误。保留既有提醒：JS chunk 大于 500 kB；`linkifyOutputs.ts:27` 的无用转义 warning。本批未为消除提醒改动无关模块。

### 保存到实际媒体子进程

`test_media_gateway_settings_integration.py` 使用临时工作区和本地假网关，先经真实 PUT 保存，再启动六个真实 CLI 子进程：文生图、图生图、图片变体、视频、音乐、配音。验证：

- 自定义代理 `/team` 前缀被保留，六类实际路径、模型和业务 Bearer key 与保存值一致。
- 视频完成无 URL 时只下载 content，没有重复提交；音乐产物下载不携带业务 Authorization。
- 假媒体字节被保存，业务 key 不出现在设置响应、stdout 或 stderr；原自定义字段没有被统一配置改写。

这是本地 HTTP 合同与进程配置集成，不是生成质量、音视频解码或供应商费用验收。

## 浏览器验收

使用本次专用临时工作区，服务监听 `127.0.0.1:7877`，启动参数含 `EASEL_PORT=7877`。所有业务 key 和模型均为假值；保存过程不访问所填网关。真实项目 `.env` 未参与写入。

已操作并读回：

1. 打开设置、选择四类统一模式，填写各模型、Kling profile、Suno 和 OpenAI 配音，保存成功；密码框清空。
2. 刷新并重新打开设置，模式、模型、profile、供应商和音色均保留；页面只提示已存密钥，不回显值。
3. 修改网关地址而不输入 key，页面阻止提交并明确提示；切回全部自定义后，原图片地址／模型／密钥仍存在；明确勾选清除密钥后显示未保存密钥。
4. 加载旧手工图片适配 fixture，页面显示“保留旧版 New API”，旧供应商表可编辑；只保存视频统一配置后，临时文件的 `IMAGE_API_MODE` 保留、`NEW_API_IMAGE_MODEL` 仍不存在，原图片字段完全保留。
5. 明确选择图片“统一网关”后才完成接管；统一状态下编辑保留的自定义图片模型并点击“保存配置”，新自定义模型保存成功，统一模式、统一模型和 key 保持独立。
6. A–E 五套主题逐一切换检查文字、边框和表单颜色。最终 A/C 截图见下方。窄屏卡片为单列，浏览器实测 CSS viewport 与文档宽度均为 400 px，所有可见表单字段位于卡片内；滚动后保存按钮和保留表可访问。浏览器临时 viewport override 已恢复。
7. 浏览器 console error 为空。

验收后已启动当前分支的实际项目服务 `http://127.0.0.1:7860/`，浏览器停留在设置页，只读确认新入口可见且仍使用原有自定义配置；未向实际项目表单提交配置。隔离测试标签和服务已关闭。

截图均为隔离假配置验收图：

- [主题 A 桌面](../../outputs/_run/media-gateway-settings-20261021/settings-a-desktop.png)
- [主题 C 桌面](../../outputs/_run/media-gateway-settings-20261021/settings-c-desktop.png)
- [窄屏上部](../../outputs/_run/media-gateway-settings-20261021/settings-a-narrow.png)
- [窄屏保存按钮与保留表](../../outputs/_run/media-gateway-settings-20261021/settings-a-narrow-bottom.png)

## 独立审查与保留核对

[独立审查报告](2026-10-21-media-settings-review.md) 的三项 P2 已全部修复并复核关闭：旧手工模式兼容及旁白默认值、远端错误内容脱敏、继承旧环境时的配置刷新。本批范围没有剩余 P1/P2。

开始前记录 75 个已有改动／未跟踪文件哈希。完成时核对：无缺失文件；已有内容的变化仅在本批授权的媒体脚本和 New API 汇总文档；原有主题 UI 改动保持原字节。真实项目 `.env` SHA-256、HEAD 和 main 引用均未改变。`git diff --check` 无错误。脱敏核对结果保存在 `outputs/_run/media-gateway-settings-20261021/preservation-check.json`。

## 验收边界

本批完成本地设置与执行配置链路，没有访问或部署生产 New API，没有配置真实渠道或花费模型额度。真实 OpenClaw daemon 的完整 Agent 任务未运行；已验证新 CLI 进程继承旧环境后能读取新保存字段。Fish 私有桥部署、供应商实测、订阅媒体和云端执行仍属于后续范围，不能以“配置完整”代替这些验收。
