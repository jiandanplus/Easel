# AV 完整解码计量与安装 dry-run 方案

> **当前状态（2026-10-28）：** 原设计 `3748a8aa…` 已获 root 批准并完成 r1 四文件源码实现；作者 95 项 focused 及独立常规回归为历史证据。随后独立审查实际复现动态 H264 尺寸和 MP3 声道变化被自动转换隐藏的两个 P1，r1 因此阻断，不能投产或复用原绿作为当前交付验收。r2 已经 root 批准并完成四文件 TDD 与本机 137 项 focused，正在等待独立复审、固定 Linux 6.1 重放及 fresh full，不能投产。原设计快照、r1 源码 tar/SHA/日志均保留。无部署、系统安装、commit 或模型请求授权。

**Goal:** 从完整解码的 PCM 样本和视频帧时间区间精确得到既有 `duration_ms` / `audio_duration_ms`，修复 progress endpoint 短计量，同时保留 900000 ms 合同及所有既有安全/账务边界。

**Architecture:** 保留固定 ffmpeg/ffprobe、owned 原始字节、现有探测/容器声明守卫。把 `_decode` 的 null/progress 测量改为固定 PCM/rawvideo 的 framehash 协议，由常量内存的私有流式解析器汇总；不保存帧列表、不存新的供应商事实。库加载与生产服务可见性是独立门槛，仍未通过。

**Tech Stack:** Python asyncio / Fraction；官方 Ubuntu FFmpeg 6.1.1；严格 framehash v2 / SHA256 文本协议；现有真实 AV / 隔离 PG 测试。

## Global Constraints

- 公共 metadata、计费单位与 basis、001–020 SQL、价格、provider usage / typed facts / scope / 钱包锁序不变。
- 原 bytes 保持原样；PCM 只为进程内部全解码验证，不新增 PCM 上传或产物格式、不替换 owned 文件。
- 不放宽 100 ms 声明一致性守卫；不把 probe.duration、平均帧率或 progress endpoint 当实际时长。
- 原 900000 ms 上限、max_bytes/max_pixels/max_channels/max_sample_rate 保留，不用约千帧的小子集替代。
- CPU 30s、NOFILE 32、FSIZE 1048576、AS 536870912（Linux）、解码 wall 60s、版本 wall 5s、探测 wall 10s 保留。stderr 仍丢弃/固定错误净化；不回传 FFmpeg 原文。
- 仍使用 PATH/LANG 的 clean execve 环境、固定绝对路径与精确版本验证、protocol file/pipe、format mov/mp3/wav/ogg、MOV drefs/absolute path 关闭。
- 无新 POST、unknown 无法证明完整成功时保留原 hold；不重算已结算账单，不改写 immutable artifact/terminal evidence。

---

## 1. 研究事实与推荐结论

官方文档说明 framehash 每行是 `stream_index,dts,pts,duration,size,hash`，默认转为原始音频/视频；显式指定 PCM/rawvideo 可以将 output packet 与完整解码对应。`fps_mode=passthrough` 保留帧 timestamp；`enc_time_base=demux` 使用 demuxer timebase。Context7 本问题恰好 library→docs→docs 三命令，依据 [FFmpeg 官方文档](https://ffmpeg.org/ffmpeg-all.html#framehash) 与 [advanced options](https://ffmpeg.org/ffmpeg-all.html#Advanced-options)。

Ubuntu 官方 `6.1.1-3ubuntu5` 在原私有 prefix、无网络 namespace、严格 RLIMIT 下的实际实验：

| 样本 | framehash / 样本结果 | 结论 |
|---|---|---|
| 500 ms WAV / MP3 | 8000 samples @ 16000 Hz | 500 ms；MP3 首 47、尾 465 samples 已反映 skip/padding |
| 500 ms Opus/OGG | 24000 samples @ 48000 Hz | 500 ms；首 648、尾 312 samples，未 pad |
| stereo 1001 samples @ 44100 | size 4004 bytes、duration 1001、tb 1/44100 | 1001/44100 s，最终一次 floor 得 22 ms |
| CFR 5 frames @ 10 fps | PTS 0..4096、每帧 duration 1024、tb 1/10240 | 500 ms，包含末帧 |
| VFR PTS 0/0.1/0.4/0.7 | duration 0.1/0.3/0.3/0.1 | 800 ms；默认 encoder TB 却量化成 900 ms，故不能省略 demux TB |
| B-frame / 单帧 MP4 | rawvideo 输出按 presentation 排列，完整正 duration | 分别 500 / 100 ms |
| 30000/1001 fps，3帧 | tb 1/30000，3×1001 ticks | 100.1 ms，最终 floor 为 100 ms |
| AAC/M4A 声明500ms | 实际24576 samples @48k | 完整解码512ms |
| AV 视频500ms/声明音频300ms | 视频5120 ticks @10240；音频15360 samples @48k | 视频500ms、音频320ms，各自计量 |

Root 已确认原 M3 spec 的 basis 为“完整解码音频毫秒”：AAC 的 512/320 ms 必须保留，不拿 header/edit duration 裁回 500/300。MP3/Opus 对 decoder 已应用的 skip 不再手工扣减。声明值只作原独立一致性守卫。

研究专用 parser 原型对 7 份真实输出按 1/7/4096 bytes 分块共 21 次通过，19 个坏协议反例拒绝；900 秒/27000 帧的合成 parser trace 为 2,434,985 bytes，900秒/43200000样本 trace 为 4,197,318 bytes，均用常量 carry 汇总成功。它们证明协议解析/范围，不冒称真实900秒媒体性能已验。

## 2. 精确工具命令合同

继续先进行现有 ffprobe metadata 探测。video 增加读取并严格验证 `stream.time_base`；不是读取展示 duration 来计量。命令参数由固定模板生成，不接受输入里的 argv。

所有 decode 共用前缀：

```text
<fixed-ffmpeg> -nostdin -hide_banner -v error -xerror
-err_detect explode -max_error_rate 0 -threads 1
-filter_threads 1 -filter_complex_threads 1
-protocol_whitelist file,pipe -format_whitelist mov,mp3,wav,ogg -f <fixed-demuxer>
[仅mov: -enable_drefs 0 -use_absolute_path 0]
-i <private-owned-copy> -map 0:<a-or-v>:0 -sn -dn -threads 1
```

音频固定尾部（不设置 ar/ac，不重采样、不重分包、不 pad）：

```text
-c:a pcm_s16le -f framehash -hash sha256 pipe:1
```

视频固定尾部：

```text
-c:v rawvideo -fps_mode passthrough -enc_time_base demux
-f framehash -hash sha256 pipe:1
```

不需要 pcm_rechunk；现有实测已精确获得首尾部分包。filter/output codec 单线程明确设置；研究混合素材曾因未限定输出编码器线程触发现有 AS 限制，两次失败保留，补单线程后成功，没有提高 AS 或 CPU。

## 3. 关闭式 framehash 解析 schema

固定一条输出 stream，index 必须为 0。整个输出严格 ASCII、无 NUL；header 完成之后不再允许 header/comment。合法键不得重复；unknown 键、重复/缺失键、第二条 stream、非零 exit、空流、未结束半行全部失败。

公共 header 必须且仅出现一次：

```text
#format: frame checksums
#version: 2
#hash: SHA256
#software: Lavf<1..3digits>.<1..3digits>.<1..3digits>
#tb 0: <positive-int32>/<positive-int32>
#media_type 0: audio|video
#codec_id 0: pcm_s16le|rawvideo
```

音频额外字段集合：`#sample_rate 0: <validated_rate>`、`#channel_layout_name 0: <bounded ASCII layout name>`。timebase 必须精确等于 `Fraction(1, rate)`。layout 名称不用于推断收费或声道数；声道数来自已验证 probe，并与每包 PCM size/duration 交叉验证。

视频额外字段集合：`#dimensions 0: <validated-width>x<validated-height>`、`#sar 0: <nonnegative-int32>/<positive-int32>`。timebase 必须精确等于已验证 probe.time_base；SAR 仅语法验证，不改像素上限或显示比例。随后必须有固定列声明 `#stream#, dts, pts, duration, size, hash`（允许工具固定空格）。

每行只有六列：

```text
stream=0, dts, pts, duration, size, [0-9a-f]{64}
```

- 数值是规范十进制整数，拒绝 exponent/NaN/float/空值/布尔字面量；单值和 `pts+duration` 均在 signed int64 范围。
- `dts == pts >= 0`；rawvideo/PCM output 已为 presentation 序；源 B-frame 的 reorder 由真实 decoder 处理。
- `duration > 0`、`size > 0`。首个 PTS 可非零，后续必须 `pts == previous_pts + previous_duration`；不靠平均帧率填 gap、不合并 overlap、不删除重复帧、不修负时间。
- 音频：`size % (2*channels) == 0`；`samples_i=size//(2*channels)`；精确要求 `duration_i*tb == Fraction(samples_i, sample_rate)`。输出总 samples 与总 ticks/rate 再核对。
- 视频：每个成功解码 rawvideo packet 对应一帧；保留正 duration，最后帧 duration 也必需。sum(duration)*tb 必须等于 `(last_pts+last_duration-first_pts)*tb`。不使用 `(frame_count-1)/fps`；不假设 CFR。
- 单包输出大小须正且在 signed int64；实际像素/声道/采样率上限、地址空间与 CPU 上限继续由现有检查与真实 decoder 强制。实现阶段需特别覆盖中途格式/尺寸/采样率变化，无法证明完整一致时失败，不为其新增“默认修复”路径。

## 4. 精确计量与常量内存预算

设 `D` 为调用配置的最大毫秒数（至多900000）、`tb=p/q` 为约分后的正 Fraction、`R` 为已验证采样率。

```python
max_ticks = (D * q) // (1000 * p)
max_samples = (D * R) // 1000       # audio only
max_records = max_samples if audio else max_ticks
max_trace_bytes = 4096 + 256 * max_records
```

推导依据：音频每个输出包至少一完整 sample；视频每个 packet duration 至少一 timebase tick，并且区间连续、总 ticks 受 D 限制。**不从可伪造的平均帧率推出新 fps 上限**。没有默认1000帧或128KiB整段上限；较细 timebase 的理论记录预算可能很大，但墙钟/CPU/AS/FD、每条严格语法和累计总时长仍独立强制，预算不是“无限输出”授权。

- 每次异步读不超过4096 bytes；unfinished carry 永不超过原131072 bytes硬界。
- 合法 header 单行≤1024 bytes、全部 header≤4096；合法数据行≤256 bytes（固定六列、最多19位整数、64位hash及固定空格）。超出即失败，不等待无界换行。
- 从首块开始记录总 bytes；header完成后计算上述 total/count预算，每条/每块检查。增量状态仅保存固定 header字段、first/next PTS、ticks/samples/count/bytes、可选滚动摘要。**不保存所有帧或其hash列表**。
- 类型/数值先验证后 Fraction，限制 p/q int32，防超大整数计算；累计 duration、size、count 等做溢出边界检查。
- 内部 duration：audio=`Fraction(total_samples,R)`；video=`Fraction(total_ticks)*tb`。先用完整 Fraction 核对 `duration*1000<=D`，最后只 floor 一次为现有整数毫秒；结果<1ms仍拒绝，不能零价成功。
- 原 probe/MP4 track timing/edit 独立守卫保留，100ms不扩大。既有 public properties 的字段名和含义保持；不加入新的供应商事实、单位或价格。

## 5. 私有执行 helper 与异常安全

新增私有 streaming executor 复用现有 `_execute` 的启动器、RLIMIT、cleanenv 与 kill/wait 规则，供 `_decode` 使用；版本/ffprobe的小响应继续原有有界整体读取。

解码 wall 60s 覆盖创建后的全部 read/parse/wait；任何 parser error、输出超界、超时、取消或非零exit，均 kill 子进程并 await wait，关闭管道，再只返回既有 `invalid_media_artifact`。版本/工具路径问题仍对应既有 unavailable。stderr保持DEVNULL，不把源文本、路径或原文传入用户错误。`finish()` 只能在 stdout EOF、exit0、头部完整、至少一帧/样本包、无carry残留且所有约束通过后产出摘要；不把一次合法前缀当完整文件。

不得在这里加入私有 loader、wrapper、LD_LIBRARY_PATH 或全局 ldconfig。研究前缀调用仅证明算法可在官方6.1运行；直接 AVTools cleanenv 仍失败。生产安装/实际API UID/systemd namespace需另门槛。

## 6. 确切改动范围与待批准 TDD

拟修改/新增仅四个代码/测试文件：

1. 新增 `services/platform/src/platform_service/media/decoded_timeline.py`：纯 parser/汇总与内部结果类型；输入分块 bytes+已验证 stream contract，输出精确 Fraction/样本或帧总量/整数ms；不依赖DB/HTTP。
2. 修改 `services/platform/src/platform_service/media/av_artifacts.py`：私有 stream executor、probe video time_base 验证、固定 framehash 命令及 `_decode` 汇总；现有声明/原始字节/异常/限制守卫保留。
3. 新增 `services/platform/tests/test_media_decoded_timeline.py`：关闭式协议、分块、预算、退出和取消边界。
4. 修改 `services/platform/tests/test_media_av_artifacts.py`：真实AV精确样本/帧时长及现有截断/track timing回归；夹具明确输出codec与filter单线程。

现有 rates.py、usage_facts.py、audio_execution.py、async/voice runner、钱包服务和001–020只作回归输入，不改合同。`verified_input_v1/verified_delivery_v1` 继续从已验证 properties 取量；`provider_usage_v1` 只按真实供应商证据，不使用本地样本量替代。旧immutable artifact与已结算账单不重写；如旧unknown已存在不可变交付证据，按原恢复规则处理，不能重POST或静默重价。

- [x] 首先将本次真实6.1 WAV 500ms→384ms旧行为、VFR默认TB800→900ms及AAC样本512ms建立repo红测；红证据保留。
- [x] 纯解析器红→绿：见下表全部正负矩阵，长trace生成器不创建全列表，验证峰值carry/常量状态。
- [x] 真实 AV 红→绿：真实库加载环境未准备前只用显式命名的测试专用loader桥证明算法，不声称生产路径；运行本地直接固定工具版本另列。
- [ ] focused 通过后冻结源码/测试/工具版本，交独立review；root安排一次完整中央suite，不复用旧1895/14或1794证据为新hash。

### 正负测试矩阵

| 类别 | 必须覆盖及预期 |
|---|---|
| 音频正例 | WAV mono/stereo，8k/16k/44.1k/48k与配置上限；非整ms1001sample；MP3首尾skip；Opus preskip+尾partial；AAC/M4A decoded512ms/320ms；每次size、ticks、sample count一致 |
| 视频正例 | CFR、30000/1001分数帧率、VFR四不同区间、B帧重排、单帧正duration、视频500ms+音频320ms分别返回 |
| 长范围 | parser900秒音频与900秒视频，文本远大于128KiB；真实低分辨率900秒CFR及VFR不靠新增字节阈值截断；若CPU/wall真实超限则记录资源失败，不能称范围已验 |
| 分块 | 1/7/4096bytes、每个分隔符/数字/hash跨chunk；终态需EOF+exit0；peakcarry有界 |
| 协议负例 | unknown/duplicate/missing header、第二stream、wrong codec/hash/tb/rate/dimensions、负/零duration、NaN/exponent/int64overflow、PCM size不整除/与duration不符、空帧流、尾残行、非法ASCII/超长行 |
| 时间负例 | PTS重复/倒退/gap/overlap、DTS≠PTS、末帧无duration、精确Fraction超D但floor未超D、累计溢出、零ms输出 |
| 媒体负例 | 原截断/corrupt、MP4 dref、非法brand、64位timing/missingtail/未知edit、超bytes/pixels/channels/rate/duration，中途format变化，拒绝且无artifact/settlement |
| 资源负例 | 慢流超过wall、CPU/AS/FD限制、巨大无换行输出、超total/count、子进程exit1但已有合法前缀、取消后无残留子进程、stderr原文不泄漏 |
| 集成 | `test_media_audio_runtime.py`、`test_media_voice_runtime.py`、`test_media_async_runtime.py`、`test_media_m3_desktop_roundtrip.py`：raw SHA相同→正确duration→一次artifact/计量/settlement；重复恢复不重算/重扣，unknown留hold，provider facts不变 |

研究原型位于 `outputs/_run/central-av-metering-research-20261028/stream_prototype.py`，它不是可直接投产实现；其 assert 需在真实实现中变为严格固定错误分支，并补上表的完整TDD。

## 7. 系统安装 dry-run 的独立只读方案（尚未执行）

目标不是立刻安装，而是证明“仅新增必要 ffmpeg/运行库、零升级、零删除”是否可解。**现在尚无 installed-status solver 结果，不能声称可行；`--no-upgrade` 不作为 transitive 零升级保证。**

root批准后步骤：

1. 新建另一 root0700 私有目录，读取并复制 `/var/lib/dpkg/status` 到该目录，记录前后SHA；只解析包名/arch/version/Status/Depends/Pre-Depends/Provides。保留 held/half-configured 状态，若有半配置立即停。不要操作宿主dpkg selection、lock、数据库或APT state。
2. 复用本次隔离 APT 配置模式；Dir::Etc/State/Cache/Log全部新路径，所有host hooks禁用，dpkg=/bin/false。**仅 status 指向上述副本**，source为同样官方signed-by HTTPS；索引可复制已验快照，若要刷新仅更新新目录并重新记录签名链。
3. 固定 `ffmpeg=7:6.1.1-3ubuntu5`，先做未加版本约束的只读solver并留原方案：

```text
APT_CONFIG=<new-private>/apt.conf apt-get --simulate --no-install-recommends --no-remove install ffmpeg=7:6.1.1-3ubuntu5
```

`--simulate` 不下载/解包/运行脚本，禁用 dpkg 是第二道护栏。输出只写私有 log。

4. 解析全部 `Inst/Remv/Conf`，结合完整installed-version map：任何 Remv、已有包Inst（包括upgrade/downgrade/reinstall）、held改变、宿主状态SHA改变均标为失败；只有原未安装的新包可列候选。逐个新包检查必须属于ffmpeg Depends/Pre-Depends（含明确选定alternative/Provides），不得把Recommend或任意服务塞进闭包。
5. 若第一方案有已有包变化，保留失败；可在**第二个新的私有solver配置**用显式preferences禁止所有已安装包的其他版本，再simulate一次，判定新增-only是否有解。不能通过放开依赖版本、删既有包、改系统hold或宣称no-upgrade即可满足。
6. 零upgrade/removal只是必要条件。安装会运行新包maintainer scripts、triggers和安装工具hooks，`--simulate`不能证明零service restart。必须额外审查新包control脚本/triggers及将来实际安装命令的APT hooks策略，明确 invoke-rc.d/systemctl/deb-systemd-invoke/needrestart 等；任何无法界定的restart副作用继续阻断。此阶段不执行脚本、不抑制真实服务、不写系统policy或升级系统库。
7. 输出精确新增包版本/SHA/总空间、已安装版本受影响清单、solver失败原因和restart未知项交root。只有root另批安装方案与真实API/systemd验收后，才能进入实际安装；本设计不授权该步。

## 8. 证据和交接门槛

研究输出在 `outputs/_run/central-av-metering-research-20261028/`；服务器原签名/包/prefix证据仍在 `/root/easel-central-av-preparation-20261028`。新增合成素材/输出位于 metering-research 及 metering-research-extra-single-codec，两个失败生成目录保留。大包/合成媒体不下载本机。

本方案只请求root审查流式解析/计量设计和未来installed-status dry-run；没有改生产源码、SQL、价表、服务、真实钱包或pending。cleanenv直接ELF、实际APIUID/systemd门槛仍failed/未验，不因研究算法成功改变状态。

## 9. 实施进度（2026-10-28，r1 freeze）

四文件实施完成。仓库首轮58红、真实6.1旧源码500→384ms/800→750ms红已保留。额外发现并红绿修复输出管道积压导致kill后wait卡住、spawn交付handle期间取消遗留子进程；旋转正例先误拒红，再固定输入-noautorotate绿。所有改动仍在批准四文件内。

本机最终 focused session15089 exit0：95passed/0failed/0error/0skip，7.57s，包含真实900秒音频/视频。真实Ubuntu6.1私有loader算法session77201 exit0：14正例/3负例，含rotation、CFR/VFR/B-frame/单帧/分数fps/AAC/Opus/900秒真实音视频；两个新模块SHA与本机候选完全一致。direct cleanenv仍exit127，不是生产AVTools已通过。

源码manifest、四文件快照、红绿日志与JUnit：outputs/_run/central-av-decoded-timeline-20261028/。001–020 SQL逐SHA匹配旧431冻结源；9项明确记录的直接输入前后一致，这不是完整中央依赖冻结或full证据。未跑PG、APTdryrun或完整中央suite。完整验收说明见 docs/validation/2026-10-28-av-decoded-timeline-r1.md；后续独立review和fresh full仍待root。
