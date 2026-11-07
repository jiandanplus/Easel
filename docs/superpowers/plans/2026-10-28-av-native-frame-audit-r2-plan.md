# AV r2 原生解码帧审核修订方案（待批准实现）

状态：2026-10-28，root 已批准设计 4f8fbd33…，四文件 TDD 实施及本机 focused 已完成（137 passed / 13.45s）；当前等待独立复审、Linux 6.1 实测及 root fresh full。原获批设计完整副本保存在本轮 outputs 目录。r1 被动态 H264 尺寸及 MP3 声道变化两个 P1 阻断，原 95 项绿不是当前交付通过。原设计 3748a8aa、r1 tar、失败及成功日志原样保留。本轮不含 SSH、PG、系统安装、生产、模型请求或账务动作。

## 1. 原因与已实证边界

framehash 审核的是输出 PCM/rawvideo。FFmpeg 默认在输入帧参数变化时重新初始化滤镜，并可能将后续帧转换为最初输出几何/声道；严格校验输出 header 不能证明原生输入始终符合上限。官方 reinit_filter 文档列出视频尺寸/pix_fmt，以及音频 sample_fmt/sample_rate/channels/channel_layout 作为变化触发项。依据：https://ffmpeg.org/ffmpeg-all.html （本问题 Context7 library→docs→docs 共三次，已用尽）。

原样独立样本本机复现：

- H264/MP4，2492 bytes，SHA 4de11598450cbdf62f4652760622f1a75869e406fc75fb34fc32298a58d20a4b。原生前 3 帧 32×32、后 3 帧 64×64；r1 max_pixels=1024 却成功返回 32×32 / 600ms。
- MP3，1296 bytes，SHA abb130f8e9ab2b674e59a44b5a0a2b6810be96bc6a415476dfdd2baf4687fc98。原生 mono→stereo→mono；r1 max_channels=1 却成功返回 mono / 324ms。
- 两个原样 pytest 行为断言均 RED：2 failed / 0.29s，失败均为 DID NOT RAISE；没有改生产代码制造失败。
- 单独添加 -reinit_filter 0：音频在真实声道变化处 exit 234；视频仍 exit 0，只是 rawvideo packet size 从 1536 变成 6144。因此不能只加一个开关当成完整修复。
- 新生成极短 rate 切换 16→48→16、48→16→48 kHz，默认转换相对各原生分段样本时长只差 23ms；16→48→8→48→16 kHz 差 80ms。这三例固定解码命令加 -reinit_filter 0 都直接 exit 234，完全未调用声明时长检查。证明这一输入变化门不依赖 100ms 差异拒绝；不能据此把原 reviewer 已拒绝的 rate 例称为 r1 绕过。

以上是本机固定真实工具结果；本轮尚未在 Ubuntu 6.1 私有 prefix 重放。旧 loader 兼容证据、AVTools cleanenv/direct ELF 失败和 API UID/systemd 未验边界不变。

## 2. 最小实现选择

保留原 ffprobe metadata 守卫和完整 framehash 计量。新增一次原生 ffprobe -show_frames 全解码审核，**一次遍历所有已确认的 1–2 个媒体流**，逐帧在自动转换之前确认参数；然后既有 video / audio framehash 分别完整解码。后两者增加固定输入参数 -reinit_filter 0；视频仍 -noautorotate，保留 demux TB、passthrough、原始编码几何。没有 select/drop、ar/ac、pad 或补采样。

不使用 showinfo/ashowinfo stderr 协议：本机 showinfo 会输出 SEI 内容。原 stderr 丢弃及固定错误净化保持。原生 audit 的 stdout 只允许固定 show_entries 的结构化数值/枚举字段；不包含用户标签、SEI、提示词或业务标识。

原生命令固定前缀沿用 probe：

```text
<verified-ffprobe> -v error -threads 1
-protocol_whitelist file,pipe -format_whitelist mov,mp3,wav,ogg
-f <fixed-demuxer> [mov: -enable_drefs 0 -use_absolute_path 0]
-show_frames
-show_entries frame=media_type,stream_index,width,height,pix_fmt,sample_fmt,channels,channel_layout,nb_samples:frame_side_data=
-of compact=p=1:nk=0 <private-owned-input>
```

不用 -select_streams，只允许 metadata 已确认的原始 stream_index 集合；index 不重新编号。所有帧必须属于该集合，且 media_type 与索引绑定一致，每条预期流必须至少一帧。

ffprobe 的逐帧结果不提供可靠 sample_rate 字段，不能伪造这一事实。rate 变化拒绝由固定 framehash 命令的 -reinit_filter 0 输入门承担；第一帧输入形成滤镜参数，后续 sample_fmt/rate/channels/layout 变化直接出错。原生 nb_samples 总和与 PCM 的真实样本数还要完全相等，但**总和相等本身不是逐帧 rate 不变的证明**。本机短 rate 及来回补偿样本已有 direct-command 证据；固定 Ubuntu 6.1 同命令实测是 r2 acceptance 的必需项。

## 3. 关闭式 streaming schema 与互证

使用同一 4096-byte read chunk 和常量 carry；原 framehash schema 不放宽。新增 NativeFrameAudit 只保存最多两条流的标量汇总/首帧参数，不存帧列表。

- 严格 ASCII，无 NUL；逐行必须以 frame| 开始。key=value，不允许重复 key、未知 key、未知记录、跨行值、反斜线转义、缺少必需字段。有限的行尾空分隔符仅对应 frame_side_data= 抑制后的占位，不将任意空字段解释成新数据；固定工具正例必须确定其实际形态后锁定语法。
- 每行最多 1024 bytes，carry 仍最多 131072 bytes；每次 feed 至多 4096 bytes。此为新 native 行协议上限，不改变原 framehash 行 256/header 1024 限额。原生总输出由合法最大帧/样本数推导，不设 128KiB 整段上限。
- 公共必需字段：media_type 为 audio/video，stream_index 为 0..int32 上限的规范十进制，必须匹配 probe 已验证映射。
- video 必需且仅允许 width,height,pix_fmt，加公共字段；尺寸正整数逐帧与 probe 编码尺寸严格相同，逐帧仍核 max_pixels。pix_fmt 为有限长度安全 token，首帧冻结，后续严格相同。即使尺寸面积相同或不超 max_pixels，变化也拒绝；不能仅比 raw packet bytes。
- audio 必需且仅允许 sample_fmt,channels,nb_samples，加公共字段；channel_layout 可缺省（合法 unknown layout 不能仅因缺省缩出原范围），但其存在性与值从首帧冻结。sample_fmt 安全 token，channels 每帧等于 probe 且符合 max_channels，nb_samples 为正 int64。sample_fmt/channel/layout 任意中途变化拒绝。
- 所有整数规范十进制，禁止负数、指数、前导加号、溢出；数字和字符串长度先界定再解析。layout 只接受工具定义的有限长度 ASCII 字符集，未知/转义形态关闭，不能把任意字符串当可执行参数。
- video 累计帧数上限沿用 max_duration_ms 与已验证 demux TB 推导的最大正 ticks；audio 累计样本上限 floor(max_duration_ms × probe_rate / 1000)，每帧至少一个样本，所以帧数不得超过同上限。双流总行数为两者上限之和，总字节上限 1024 × 总行数。任何真实资源超限关闭，但合法 900 秒不会因固定总文本大小被截断。
- audit EOF 必须无半行且各流非空，child exit 0。取得 video 帧数、audio 原生 nb_samples 总和后，与后续 framehash 的视频记录数、size/(2×channels) 样本总和逐项完全相等；不需要帧列表。保留 framehash 自己的 PTS 连续、duration、TB、size、Fraction 最大值检查、最终一次 floor。
- audit 不承担时长计费，计费仍使用完整 framehash 时间/PCM 样本；公共 metadata、100ms 独立声明守卫、原 bytes/sha、价格 basis、SQL001–020 一字不改。

## 4. 明确资源成本与生命周期

Root 已明确原 CPU 30s 是**每个 child**，不是请求累计 30s；不新增 wait4/rusage 协议，不读取全局 RUSAGE_CHILDREN。

| 阶段 | 子进程数 | 每 child CPU | wall |
|---|---:|---:|---|
| 两个工具 version | 2 | 原 30s | 各原 5s |
| 原 metadata probe | 1 | 原 30s | 原 10s |
| 纯音频/无音轨视频：native audit + 对应 framehash | 2 | 各 30s，硬限额名义和 60 CPU-seconds | 共用一个 60s 绝对 deadline |
| 含音轨视频：一次双流 native audit + video framehash + audio framehash | 3 | 各 30s，硬限额名义和 90 CPU-seconds | 三者共用一个 60s 绝对 deadline |

不能从单线程配置推断 CPU 总和等于 wall。此次增加一次全解码成本；不是资源成本无变化。deadline 在进入 native audit 前由 loop.time() 固定，此后不重置，所有 spawn/read/wait/finish 都只使用剩余预算；某阶段用尽后不能再启动后续 child。Linux AS 512MiB、NOFILE32、FSIZE1MiB、clean PATH/LANG、私有文件权限和固定 executable/version 均不变。

复用现有 _spawn_limited / _reap，所有解析异常/非零 exit/timeout/cancellation 都走现有 kill、drain、wait，spawn 迟到取消也收回真实 child。原 stdout drain 不存数据，stderr 仍 DEVNULL。不增加并行解码；native audit EOF+exit0 后才启动下一阶段。异常一律现有 invalid_media_artifact；取消继续传播，不能把取消/部分有效前缀当成功。

## 5. TDD / 独立复核矩阵

待 root 批准后，只修改原四文件：av_artifacts.py、decoded_timeline.py、test_media_av_artifacts.py、test_media_decoded_timeline.py。旧原样独立样本和 RED 继续保存；仓库回归采用固定小合成生成过程并断言生成出的 native 参数确有变化。

1. 真实动态 H264 32→64、同面积不同几何、pixel-format 中途改变（需生成并核确存在）、MP3 mono→stereo→mono；拒绝不是 duration 差异导致。
2. direct-command 短 rate 23ms / 80ms 差额和多段补偿；sample format/layout 改变正向生成核验，无法生成的维度明确标 parser/protocol 覆盖，不能伪称真实 binary case。
3. 跨流索引错配/未知流/重复 key/缺字段/unknown/尾部半行/空结果/非零退出；native 参数首帧/后续不一致；PCM 样本总和及视频帧数不等拒绝。
4. 三种 chunk 分块、900 秒长 trace >128KiB、tracemalloc 常量内存、实质 row/sample budget 超量；不重复无意义全 suite。
5. audit 慢流用尽共同 deadline 后不启动 framehash；第二/第三阶段只得到剩余 wall；取消每个阶段及 spawn 迟到均检查无残留 PID；binary 异常/非零/超量净化。
6. 真实稳定 CFR/VFR/B-frame/单帧/分数最后帧、静态 rotation/display-matrix、MP3 skip/padding、AAC 完整 512ms/320ms、Opus、stereo1001、900秒音频和视频都保留正例。原生审核不 autorotate，framehash 显式 -noautorotate，公共编码尺寸仍旧。
7. 本机 focused + 新冻结后交独立 review；Ubuntu 6.1 私有 loader 重放需 root 后续单独授权，仍只能算算法兼容，不能算 cleanenv/真实 API UID/systemd 通过。根随后安排 fresh 完整中央 suite；旧 95 / 1895 均不可沿用到新 SHA。

## 6. 实施前仍需审核的具体项

- 本方案已明确一次双流 audit 的 3-child / 90 CPU-seconds 名义总限额及共同 60s wall，等待 root 批准。
- compact frame_side_data= 的空分隔符、unknown channel layout 的精确版本输出需在实现测试中固定；不为让测试通过忽略未知完整字段。
- 音频第一帧实际 rate 与 probe/header 的一致性由实际固定命令输出和既有 framehash header 复验，后续 rate 变化依赖禁用 reinit 的直接失败门；若固定 6.1 反例揭示仍有缺口，停止并回报，不能用样本总和或时长容差掩盖。

证据：outputs/_run/central-av-dynamic-format-investigation-20261028/；安全清单 docs/validation/2026-10-28-av-dynamic-format-investigation.safe.json。原四源码 hash 全部仍匹配 r1。

## 实施回执

唯一最终 focused session 79282 exit 0，137 passed / 0 failed / 0 skipped / 13.45s。原两个 reviewer bytes 回归另有3项小检查全部绿（含AV双流三个children同deadline）。source-manifest 及四源码 tar 位于 outputs/_run/central-av-native-audit-r2-20261028/。只修改原四文件；旧 replay fixture 在原 owned test 内补 native 帧与 index。未进行PG/SSH/生产或full；本机8.0.1不替代固定Ubuntu6.1。
