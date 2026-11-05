# Incho 1.0.2 本地适配候选

`newapi_incho_v1` 已完成本地实现和定向验收，尚未生产部署或开放。当前分支仍为 `codex/ui-redesign`，没有 commit、push 或 merge。

## 当前行为

- 单次 native `POST /incho/submit/music` 使用明确的 `v3.5` 或 `v4.0`、`task_type=normal`、整数 n=1／2；默认 n=2。提示词与歌词分别限制 1000／3000 Unicode scalar。`v4.5` 不在该插件合同中，配置直接拒绝，不自动降级。
- submit 关联公开 task ID 后，只用原 ID 的 `GET /incho/fetch/{id}` 恢复；未知 POST 无 ID 时保留预留，不再次生成。原身份、绑定、幂等键、余额和私有供应商凭据边界延续中央合同。
- 终态必须与全部曲目状态一致；只有 `done` 且包含有效音频地址的曲目进入交付。失败／取消曲目上的 URL 不当成成品，重复 ID、超过冻结 n、未知状态及缺音频结果都不结算。
- 音频使用精确 origin／路径白名单、无授权头转发、无重定向；完整解码后保存至 owned storage。一次完成两首时按两个实际音频计量，部分成功只计有效成品，合并精确有理费率后一次取整。
- 新增 020 只扩展异步队列的 adapter check；001–019 与 M3 冻结包逐字节一致。不新增模型定义、费率、会员、点数或启用状态。缺 020 时启动失败关闭，不自行执行 DDL。
- 桌面 `music.generate` 参数白名单仅增加 `n`／`lyric` 两个已发布 schema 字段，现有私有费率字段投影未变。该增量不在已发布 v26 中，由后续桌面包独立交付。

## 真实本机证据

最终相关定向套件为 **346 passed、0 skipped、0 failed，78.46 秒**，唯一工具会话 `66760` 退出码 0。它覆盖新协议／真实 PostgreSQL／原异步与声音功能／存储／实际 Pi SDK／桌面 host；不代替完整中央套件。

其中的供应商 HTTP 均为受控 fixture，音频是实际 ffmpeg 生成的 MP3 并全解码，账户与点数均在本机随机测试 schema。没有生产或真实供应商 POST。包括双曲目一次收据、部分成功一次取整、第二首下载丢失后进程恢复且不重发生成、未知提交保持实际费用未知、缺下载前提关闭、跨用户 404，以及实际 ASGI→M4 host 的正常下载、停止／换号后不发布文件。

Pi SDK 对 n=0／3、未知键及过长歌词会拒绝，但实际 SDK 会将 `n=true` 工具请求传给 host。这里没有将 SDK 视为权限边界：另外六种非法值通过实际 Python host／ASGI 路径验证，均在 mint 前拒绝，供应商 POST 为 0。原 SDK 的 12 个门槛在本轮使用 v26 的确切 Pi／Node payload 实跑，未再跳过。原 rate 私有字段投影回归也通过。

最终 429 文件 superset 前后比对中，仅并行恢复工作的 `services/platform/scripts/verify_mixed_media_restore_drill.py` 变化。它不属于这份 Incho 候选或所选测试的执行路径，因此不能声称 429 文件全相同；单独冻结的原 14 个 Incho 候选文件逐 SHA 均未变化。该差异已保留在 `final-result.json`。

之后只将验收 runner 与对应测试的显式迁移列表追加 020；严格列表相等、零供应商、无付款和真实身份鉴权断言全部保留。原 prepare-only 节点首先在 runner 的旧迁移常量处红测，再运行整个 runner 测试文件，结果为 **16 passed、0 skipped、0 failed，2.65 秒**。这是一份单独收据，不与 346 相加。最终候选因这两项常量更新共 16 文件。

## 候选与收据

候选目录为 `/private/tmp/easel-incho-adapter-20261028/`。

| 文件 | SHA-256 |
| --- | --- |
| `candidate-r1-manifest.json` | `a5411543bc5eac3dd42a60ff4c0e9872c222fde132fbd559952d8e7383f3680d` |
| `candidate-r1-source.tar.gz` | `e4240159beb3726d742dccc7ef919c82c052efd640acf671cd72189023e1ee32` |
| `candidate-r1.patch` | `da18b7ea574762f557a326936b17750961c0dac2ee586a7b5092b5a8a87fb8e0` |
| `final-focused.log` | `9b906f8b79eee2709540d6491098802fd204c186900ee447bf2153ac170169ad` |
| `final-focused.junit.xml` | `8fbb754883419cd9ae24e7adb37c3ecd2a93868a377381219b7993b58a6c870f` |
| `prepare-runner-green.log` | `1be2bb0a34acdd27fbb18ccf18623ad6a2d7aa69f35312d0163180ef35371fef` |
| `prepare-runner-green.junit.xml` | `e146a3151c654cdf75d4102809803f6ce63b8abd687c73f0c430feaea676c909` |

最初缺适配器、020 启动门槛、桌面 schema 缺字段及严格类型的红测均保留。早期误将已派发取消等同免扣的测试失败、跨根收集错误、未配置 owned SDK 的 12 项 skip，以及 SDK 布尔类型边界的旧失败也原样保留，未改成成功。最终以这两份明确收据为准。

## 生产开放仍缺的事实

[只读路由证据](2026-10-28-newapi-incho-routing-readonly.safe.json) SHA-256 为 `b4efddd0bb4944fa68d0619c1cab998ba08518b96c46b5d949b39f2cdcacebb6`。channel 7 当前只有 `v3.5`／`v4.5`，没有 `incho_music` ability，三者没有覆盖映射；native decode 固定的路由模型为 `incho_music`，所以当前 native 路由未配置。公开固定插件为 1.0.2，仅支持 `v3.5`／`v4.0`，现有 `v4.5` 不能视为已接通。

运营接入须明确新增 `incho_music` 路由与业务令牌范围、验证所有候选渠道不会覆盖显式 body.model，再验证真实任务、可下载音频和费率。还需经过 020／运行权限部署及真实解码工具预检。当前没有修改旧目录名、渠道、映射、费率或用户权益，也没有开启音乐。真实供应商及生产验收、完整中央新候选回归和独立复审仍需后续完成。
