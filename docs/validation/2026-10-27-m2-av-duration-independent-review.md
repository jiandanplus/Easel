# M2 音轨缺尾修复：独立限定审查

日期：2026-10-27；独立 reviewer：`delivery_media_review`。本次只复核 `media/av_artifacts.py` 和对应 AV 测试，不修改实现，不扩展到尚未完成的 M2 adapter/worker/恢复，也不影响当前图片发布。

**最终结论：新冻结输入内没有剩余已知阻塞。独立 AV 集合 27 passed、0 skipped，2.07 秒；原有用例没有排除。** 27 已包含原 26 和新增 1 个反例，不与实现者的联合 135 项重复相加。

## 冻结输入

- `services/platform/src/platform_service/media/av_artifacts.py`：SHA256 `11254764d0b956b7074687ec16fd001e6a62db275ca6e470a9899971adfa002a`。
- `services/platform/tests/test_media_av_artifacts.py`：SHA256 `74fa9498368fe9c6e6a01277bce8e9dd669659a228b69aba3ea139902a04ea0d`。
- 两文件执行前、执行后及写报告前逐项 hash 相同；实际本机 FFmpeg 为 8.0.1。

最初交审的 efa0…/f584…快照原 26 项也曾通过，但独立边界发现了额外问题；该旧结果不用于冒称新问题已修复。

## 已复核的原始声明与解码

解析器从 owned 原始 bytes 独立读取 movie timescale、每个 track handler、32/64 位 mdhd 和单段 elst。以精确 Fraction 计算原始时长；有编辑时分别保留原剩余媒体时长与 edit segment 时长，再对照该轨完整解码结果，不能使用视频长度遮盖音轨缺尾，也不能仅相信会被 STTS/编辑处理缩短的 ffprobe duration。

AAC priming 的非负 media_time 从原 mdhd 时长扣除；原正常短音轨/长视频和 64 位头正例仍通过。只延长 mdhd、只延长 elst、同时延长三种缺尾反例全部拒绝。沿用既有 100ms 误差，未扩大。

未知 header version、零 timescale、unknown-duration sentinel、多段/空编辑、负 media_time、超出原 duration 或非 1.0 速率闭锁。范围是保守的有限 timeline：即使是单段正常速率，也要求原剩余媒体和 edit 长度各自与 decode 在 100ms 内一致；不能据此称任意合法裁剪或复杂 MP4 时间线都支持。

## 独立发现并修复：存在音轨却返回 0ms

独立用实际 FFmpeg 创建 500ms MPEG4 视频，加 48kHz、只有一个 sample 的 PCM 音轨，movie_timescale=48000。旧代码在 `require_audio=True` 时仍接受并返回：

```json
{"duration_ms":500,"audio_duration_ms":0,"channels":1,"sample_rate":48000}
```

原 `_decode` 仅要求正微秒，21µs 向下取整为 0ms；末尾只核验主 duration_ms，没有覆盖独立 audio_duration_ms。实现者观察该反例红测后，将每个独立解码流的 `out_time_us` 下界改为 1000，再向下取整，未改其他时差或编辑策略。

新冻结集合包含这一真实单 sample 反例。独立再次按原物理输入生成并调用 `inspect_av(require_audio=True)`，现在拒绝；不再出现“存在音轨、却有 0ms 验证交付”的状态。

## 解析与外部工具边界

- 原 dref/QuickTime external reference 检查保留；FFmpeg/ffprobe 只接收私有目录内 owned bytes 的副本，固定 demuxer、format/protocol 白名单、dref/absolute-path 关闭，无任意用户路径/URL。
- 不存在/错误版本工具与拒绝输入的测试仍执行；外部引用和不支持的 timing 在工具打开路径前拒绝。
- box 计数 8192、递归深度 12、输入/输出/像素/声道/采样率/时长限额保留，子进程无凭据环境，CPU/内存/文件描述符及 wall timeout 保留。
- 独立额外生成 8193 个 free box 和 14 层 moov 的输入；parser 直接拒绝，使用不存在的工具路径调用也得到输入无效，未进入外部工具。

## 独立执行与证据范围

```text
27 passed in 2.07s
0 skipped
exit_code=0
```

额外物理单 sample 复测和两个 parser 资源边界是命名验证场景，不与上面的 pytest 或实现者联合数字累加。完整本机日志：`/private/tmp/easel-m2-av-independent-review-20261027/av-final27.log`；机器回执为本目录同名 safe JSON。

`f50e52511e941d3bea8e724eddbd68330e20eaa6339c5e2437b3880ca9705abf` 图片 wheel 的 SHA 未变，归档内不存在 av_artifacts.py；本次只涉及开发树 M2，不将其合入该已冻结图片候选。没有 SSH、PG、服务器操作、供应商调用或生产写入，也没有修改 v22/桌面发布源码。这不表示 M2 全能力、异步 worker、恢复和真实供应商验收完成。
