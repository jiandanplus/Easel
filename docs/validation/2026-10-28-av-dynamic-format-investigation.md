# AV 动态解码参数调查 / RED 回执

r1 阻断，r2 仅调查、未实现。原四源码/测试 SHA 全部与 r1 一致；原 tar、95 focused 与独立审查证据保留，不算新版本验收。

已用独立原样 H264/MP4 与 MP3 样本真实复现两次误接收，随后 pytest 断言得到 **2 failed in 0.29s**，均 DID NOT RAISE。原始样本 SHA、命令输出、行为 RED 和小合成实验已复制到 `outputs/_run/central-av-dynamic-format-investigation-20261028/`，未重新生成或覆盖 reviewer 样本。

根因：framehash 只观察转换后的输出，默认滤镜重新初始化及自动转换隐藏原生变化。单独 `-reinit_filter 0` 不足以拒绝视频几何变化。推荐新增一次 bounded native ffprobe frames audit，后续 framehash 禁用 reinit，并以视频帧数/audio nb_samples 总和作互证。rate 不能从缺失的 frame 字段假定；直接输入门已由短 rate 切换及补偿例独立证明，差额为23/80ms而命令直接非零，未调用100ms声明守卫。

完整方案与预算：`docs/superpowers/plans/2026-10-28-av-native-frame-audit-r2-plan.md`。单媒体2个full-decode child各CPU30（名义和60）；AV双流3个child各CPU30（名义和90），全部full-decode阶段共同60s绝对wall，原version/probe独立5/10s不变。新增一次全解码有成本，不宣称累计CPU30。未引入wait4、全局rusage或stderr/SEI协议。

当前仅本机小合成研究；本轮无SSH/PG/生产/模型/系统安装。Ubuntu6.1本轮复验尚未做；旧测试专用loader只证明算法兼容，cleanenv directELF及实际APIUID/systemd门槛仍未通过。本回合没有开启full，不把原95或1895通过沿用到未来源码。
