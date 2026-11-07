# AV r2：独立审查与固定 Ubuntu 6.1 隔离重放

**本机独立复审及固定Linux工具下的算法兼容性通过；仍不是生产放行。** 原两个P1小样本均已按原始字节拒绝，所有稳定格式正例保留。没有安装系统软件、修改生产服务、连接PG、读取账号／供应商配置或调用模型。

## 具体授权与唯一运行

首次SCP被自动审批拒绝，命令没有执行。随后用户明确授权13个文件、56,314字节的冻结源码、测试脚本／清单和8个纯合成小媒体文件，发送到154.217.245.179的root0700目录并完成无网络隔离测试。Root核验本地清单后，按这份新增授权提交同范围传输，服务器再逐项核验同SHA；授权前没有重复执行或更换目的地绕过审批。

- payload清单SHA：`1d521fefce0c609cb8d30a6bf20604c27c2d36bc408b92e99c04a0a52521d71e`。
- 上传64810／远端核验35485均terminal exit0；stage为 `/root/easel-central-av-preparation-20261028/metering-tdd-candidate-r2`。
- 唯一Linux运行48647，terminal exit0；取回小回执90245，terminal exit0。没有因观察延迟重开测试。
- 执行以unshare关闭网络、清环境、CORE0，并复用原固定官方216个ELF／libs及隔离venv。显式TEST_ONLY loader桥只用于算法验证，不是生产wrapper、LD_LIBRARY_PATH或服务配置。

## 实际结果

| 范围 | 结果 |
|---|---|
| 原稳定媒体 | 14正例通过：WAV／MP3／Opus、CFR／VFR／B帧／单帧／分数帧率、stereo1001、AAC、音视频双流、静态旋转、真实900秒音频／视频 |
| 原越界与上限 | 3负例按预期拒绝：原动态32→64 MP4、原mono→stereo→mono MP3、fractional最大值先于floor |
| 短采样率输入门 | 3例直接解码器均exit234，原生audit已完成后在固定reinit输入门失败；相对原生时长差分别23／23／80ms，不依赖100ms声明容差 |
| 完整阶段预算 | 每个正例的一次native audit与1或2次framehash都收到同一个绝对deadline，双流没有悄悄重置预算 |
| 来源与原字节 | 两源码、216个ELF及14个旧合成媒体在执行后再次逐SHA一致 |

实际工具均为 `6.1.1-3ubuntu5`。Linux结果SHA `db044bdafe95751bfd74d2ca063c8234cabab8e36983035b5815cb22a0df02a1`，1880字节双流native compact输出也已单独核验SHA。大900秒素材留在服务器复用，没有下载本机。

本机独立复审另为137定向／0跳过、原11未改协议与资源检查、新22边界分别通过，不能与作者137或上述Linux案例相加为一次全量。r2四文件仍匹配 [作者冻结清单](../../outputs/_run/central-av-native-audit-r2-20261028/source-manifest.json)；F2等并行工作没有混入这道AV证据。

## 生产门槛

同一ffmpeg／ffprobe在cleanenv直接执行仍均exit127，缺libavdevice.so.60；实际API UID／systemd沙箱未验，fresh完整中央测试未运行。测试专用loader成功不能替代这些门槛。

此外，Ubuntu于2026-10-12公布的 [USN-8915-1](https://ubuntu.com/security/notices/USN-8915-1)列出noble的修复版本为 `7:6.1.1-3ubuntu5+esm15`，通过ESM Apps提供。当前测试的普通仓库base版本不等于该修复级别；来源签名和兼容性通过不能证明安全补丁已齐。本轮不宣称各CVE在现有白名单／资源限制下的具体可达性，也不据此擅自启用Pro或升级宿主系统；base150包安装方案继续停在未执行状态，受维护来源需另核。

同名safe.json与 `outputs/_run/central-av-native-r2-linux-20261028/`保存授权载荷、独立报告、Linux结果和协议小回执。旧r1失败、旧95绿、旧源码／stage及最初上传拒绝记录均保留。中央生产仍为原M1／016，原unknown资金记录未改。
