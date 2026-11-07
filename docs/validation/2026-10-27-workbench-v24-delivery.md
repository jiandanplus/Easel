# v24 工作台交付：本机激活与原账号保留

**v24 已由 root 激活到本机 17868 工作台，当前账号仍保持登录，原共享钱包、历史和自定义配置保留。** 本轮交付是桌面前端更新及本机运行包更新；没有重新发起真实图片生成，也不表示剩余总目标已经全部完成。

本文作者只读取 root 已有的安全回执、测试日志、构建清单和截图，编写本报告及同名 safe JSON；没有重跑测试、SSH、读取真实用户原始 auth 或发起模型请求。root 实际操作、包作者记录及合成验证在下文分别注明。各输入的路径、字节数与 SHA-256 收录于 [安全汇总](2026-10-27-workbench-v24-delivery-safe.json)。

ZIP：[Easel-Pi-macOS15-arm64.zip](/Users/jiandan/Code/GitCode/Easel/outputs/_run/pi-portable-20261027-v24/Easel-Pi-macOS15-arm64.zip)。启动入口：[启动现有工作台.command](/Users/jiandan/Code/GitCode/Easel/outputs/_run/pi-portable-20261027-v24/启动现有工作台.command)。

| 已有包核验回执 | v24 |
| --- | --- |
| ZIP SHA-256 | `5789d1f441054f14479b25b5581bdaeb10d2fe5436f7d42b504386a42d53ee0f` |
| ZIP 字节数／条目 | 129,819,302／36,112，CRC 通过 |
| App 资源库存 | 15,399 文件已核验 |
| 公开源文件 | 901，较 v23 有 22 个组件路径差异 |
| 来源 manifest SHA-256 | `4b2ac6ce213a7e0223379a0e0d2a76dec97dd0ca9f0c44c31c459b26c581f978` |
| 适用平台与状态 | macOS 15+、Apple Silicon；未签名、未公证 |

这些是 root 既有 `package-safe.json`、`source-diff-safe.json` 与当前 `artifact.json` 的核验记录，本文没有重新解压或校验整包。22 个差异路径均位于 `web/frontend/src/components/`，涉及账号、设置、模型恢复、外观、首页及内容页面；完整准确列表保存在 safe JSON。新旧公开来源均为 901 文件，构建输入保持不变；中央 M2／M3 工作源码没有进入此桌面包。不能把公开组件差异数量当作整个 App 所有文件逐字相同的声明。

前端测试有两次分别记录的通过结果：作者执行 **449 项通过**，由 root 受信交接确认；root 独立执行也为 **449 passed、0 failed、0 skipped，3,715.64925 ms**，原日志为 `/private/tmp/easel-delivery-20261027-v24/frontend-tests-root.log`。这是同一测试集合的两次运行，不合并为 898 项独立测试；本文未再次运行。

root 的激活安全回执记录端口 17868、采集时 PID 3439、同一 HOME/workspace、当前 HTML 与新包匹配、model_calls=0。保全回执核对 **155 个受保护文件无变化**，没有新增私有 runtime 文件，原账号 scope、原钱包、历史和自定义配置均保留。PID 仅代表该次激活快照，不作为后续进程恒定不变的承诺。

旧 App／ZIP 保留。root 的 `legacy-forwarding-safe.json` 精确覆盖 **v19–v22 的 8 个启动／停止辅助入口**：仅把 v23 路径替换为 v24，原件另作 0600 私有备份，没有执行这些辅助入口；v23 入口在激活时已转向 v24。同日期 v18 目录不存在，本报告不声称已修改全部 v18 入口。

root 已在当前实际浏览器确认账号仍登录；本文作者也视觉读取了该次 [账号与共享钱包截图](/Users/jiandan/Code/GitCode/Easel/outputs/_run/pi-portable-20261027-v24/account-login-wallet.png)：

| 当前浏览器采集快照 | microcredit | 点 |
| --- | ---: | ---: |
| 可用 | 6,952,153 | 6.952153 |
| 预留 | 1,000,000 | 1.000000 |
| 累计已用 | 2,047,847 | 2.047847 |
| 累计发放 | 10,000,000 | 10.000000 |

截图同时显示待核算 1、执行中 0、待核算上限 3。余额守恒为 `6,952,153 + 1,000,000 + 2,047,847 = 10,000,000`。这是当前 v24 的新只读 UI 证据，没有新增模型 POST；它不是本文作者重新执行的服务器核账，也不能把旧待核算的一点解释为已解决。

root 随后在实际 v24 页面点击“刷新生图模型”，只发目录 GET，并在[正确生图模型视口截图](/Users/jiandan/Code/GitCode/Easel/outputs/_run/pi-portable-20261027-v24/image-model-catalog.png)确认 `gpt-image-2.5-flare`、`gpt-image-2.5-sunburst` 均显示“1 点/张 · 可用”；本文作者也视觉核对了该截图。该截图与 SHA 已纳入安全汇总，证明当前目录显示，不代表本轮重新生成、扣点或供应商交付验收。

同机新 HOME/workspace 验证的既有回执确认：使用包内 Pi、PATH 仅系统工具、默认订阅入口与统一登录地址可用，不需要旧凭据或供应商 Key；auth_posts=0、external_model_posts=0，测试自有实例已停止。该验证发生在同一台机器，不能代替干净设备或 Gatekeeper 验收。

包内验证另由 root 记录已成功执行：使用包内隔离 Python／Pillow 12.3.0 完整解码此前取得的两张 1024×1024 协议验收 PNG，并核对原 SHA，同时核对包内 Pi manifest；通过包内实际认证模块及合成 transport 验证正常轮换保存、未知刷新结果持久围栏、重启零重放。本文只读取对应验证脚本，并依 root 的成功执行记录陈述结果；没有单独结果 JSON，未冒充新的测试或真实认证请求。内置 Pi 的识别、启动及 manifest 验证也不等于 v24 新增一次真实模型／工具执行。

两款模型此前真实工作台交付、原图校验、下载和 1 点／张结算继续引用 [v23 真实生图交付报告](2026-10-27-newapi-image-workbench-delivery.md)。本次 v24 没有重做这两笔真实生成，没有改原图片证据 manifest，也没有结算、免单或释放旧 Flare 的 1 点预留。

尚未验收的事项明确保留：原生系统剪贴板复制后粘贴端到端、真实中文 IME 合成、VoiceOver、签名、公证、独立干净设备和 Gatekeeper。构建清单还明确 `python_archive_verified=false`，当前通过的是受控 Python runtime inventory，不能改称已核验原始上游 Python archive SHA。其余媒体能力、中央异常处置、恢复演练及正式支付等总目标，继续按各自交付记录验收，本报告不扩大完成范围。
