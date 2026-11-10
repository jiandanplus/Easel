# M4 桌面媒体能力扩展

日期：2026-10-27；范围仅桌面 host、Pi 工具、对应受控测试。以媒体总设计、M4 host 约束、M3 音频声音设计为准；不修改中央服务、真实配置、发行包或生产。

## 已核实缺口

- `media.py` 目录、mint、作业、下载目前仅接受 `image.generate`、单张 1024 图；`media_records.py` 每轮一个记录并固定图片能力。
- PiAdapter 只把首项 image meter 向上取整当成整张价格；worker schema 固定图片参数。
- 没有实际工作区素材冻结和 owned upload；没有视频/音频/转写 JSON 的 metadata/header/hash/size 校验；没有 public owned voice 查询。
- 现有 refresh fence、同 owner 读旧任务、完整 scope 新操作、原子 NOFOLLOW 输出和未知只查原 operation 可复用，必须保留回归。

## 实施次序与验证

1. TDD 增加桌面独立公开合同：能力、参数和资源 schema、全部有理数 meter/basis、大小限制；目录为中央真实启用和 host 支持的交集。保留 M1 单张/1024 约束。未实现能力不提前公开。
2. TDD 扩展记录 capability/上传阶段；稳定工具身份、原键恢复、崩溃/未知不再次 mint/upload/generate；M1 旧记录保持可读。
3. TDD 扩展 host：实际文件 O_NOFOLLOW 打开后冻结 bytes/hash/格式，拒绝隐藏环境/账号/runtime/outside/symlink 与变更，owned upload 返回严格匹配后才可提交。中央返回上传未知立即停止。
4. TDD 通用 owned artifact：原 job 与 metadata 一致、限定 MIME/properties/有效期、内容 header/length/hash 与格式匹配；最多 64 MiB，原字节另存 outputs，不转码、不覆盖。stop/账号改变禁止迟到结果或链接发布。
5. TDD 公共 ready voice 查询及 enroll → synthesize：只接受中央 UUID 与公开标签/ready，不暴露 provider voice ID。公共合同由 M3 对齐。
6. PiAdapter 传完整安全目录；worker 逐 model/capability 构造严格 schema，费率展示完整 meter 分项且说明合计取整，不能声称部分费率是总价。实际包内 Pi SDK 运行受控往返，不安装新运行环境。
7. 更新源码 runtime manifest（不 build/package），跑 M1/auth/host/Pi/custom 回归，冻结本次文件 SHA 和测试结果后移交审查。

## 防重复决策

根任务已确认：image.generate/edit/variation 共享每轮一次 image bucket，旧 image turn 文件完全兼容；video.generate、music.generate、audio.speech、audio.transcribe、voice.enroll、voice.synthesize 各自每轮最多一次。每个 bucket 固定 owner/session/turn，换 tool ID/参数/模型不能重派；同 tool ID 异内容拒绝。声音登记 ready 后可在同轮正常发起一次合成，各有独立预算与记录。未知只 GET 原 operation，不可把另一个能力作为失败回退。保持通用 create_media 并对扩展能力要求明确 capability，避免增加重复工具面。

## 证据边界

所有测试只使用合成身份和 HTTP fixtures；新能力无真实供应商验收，生产保持 closed。取消只反映中央实际执行/财务状态，不承诺退款。没有默认渠道/费率/可用 voice ID。保留所有自定义旧配置与订阅路径，不读真实 auth，不 SSH，不打包/激活。

## 2026-10-27 本机交付状态
+
+已实现上述桌面协议/记录/素材/产物/voice/Pi schema 步骤。通用 `create_media` 固定工具名；非 image.generate 调用必须显式 capability。旧 image 文件名与已有记录格式保持，缺少 capability 的遗留记录仅以内存 image.generate 投影读取、不改写。
+
+取消补强已获根任务确认：POST 前验证当前 RunRequest 完整 accepted scope。旧 RunRequest 重新登录后可同 owner 查询/下载，不能继续取消；新的 accepted RunRequest 可依据当前授权取消同 owner 原 job。取消状态仍由中央返回，不宣称退款。
+
+最终本机验证见 `docs/validation/2026-10-27-subscription-media-m4-expansion.md` 和同名 safe JSON。新 M3 中央实际 PostgreSQL/ASGI→桌面联调、独立审查与真实供应商验收仍分别记录，不从本机 MockTransport 或 SDK fixture 推导成功。未构建、打包、激活或部署。
+MD