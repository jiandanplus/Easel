# 中央 AV 官方 Linux 依赖独立准备（2026-10-28）

结论：官方包来源、签名、完整声明依赖闭包、私有 ELF 运行和四种纯合成全解码已验证；**尚不能作为生产 AVTools 使用**。有两个具体兼容性阻挡：冻结解码器的干净环境不能直接加载私有动态库；Ubuntu 6.1.1 的 progress 时间不等于本轮实际音频样本时长。未安装系统包、未改源码或容差、未修改服务沙箱。

## 范围与目录

- 目标仅 `easel-zhiyin` / `154.217.245.179`，SSH 全程 `StrictHostKeyChecking=yes`、`BatchMode=yes`。没有访问 115、生产配置/env、数据库、钱包、正式 release 或 systemd 单元。
- 全新目录：`/root/easel-central-av-preparation-20261028`，root 私有 0700。
- 宿主：Ubuntu noble / x86_64，kernel 6.8.0-48-generic，glibc 2.39。开始可用 42,380,566,528 bytes；结束 41,486,843,904 bytes。
- 233 个 deb，145,446,530 bytes，及解包 prefix 均保留服务器。本机只取回小回执；实际提取文件共 486,650,953 bytes，完整远端 prefix inventory 2306 项。
- 本地证据：`outputs/_run/central-av-preparation-20261028/`。没有重跑中央 full；此结果不改变旧 1794、1895/14 与补充 SDK 证据。

## 官方签名和固定版本

仅使用官方 HTTPS 的 noble / noble-updates / noble-security，main/universe/restricted/multiverse。`APT_CONFIG` 优先载入；`Dir::Etc`（包括 parts、sources、preferences）、State/status/lists、Cache/archives、Log 全部定向新目录，status 初始为空。无宿主 hooks；`Dir::Bin::dpkg=/bin/false`；只执行私有 `update`、`--simulate install`、`--download-only install`。Check-Date、Check-Valid-Until、TLS peer/host verification 保持开启，不允许 unauthenticated/insecure repositories。实际配置保存在 `apt-config.dump`。

使用既有 `/usr/share/keyrings/ubuntu-archive-keyring.gpg`，SHA256 `80a36b0a6de2f69f49d2df75ef473ccde121e9e190b9ea01d20a4f63778d5c31`。三份 InRelease 经 gpgv 验证，签名 fingerprint 为 `F6ECB3762474EDA9D21B7022871920D1991BC93C`。

| Suite | 签名元数据 Date | Valid-Until | InRelease SHA256 |
|---|---|---|---|
| noble-updates | Wed, 28 Oct 2026  3:25:37 UTC | 未提供 | `e99d7fbc7007bafb1d764d58a57afcc700cdfc4b1d0e8e6a720bc3c5dd333128` |
| noble | Thu, 25 Apr 2024 15:10:33 UTC | 未提供 | `cdb2f31d809f589719a53c6ad15f255b27569c4059542ada282aaa21b8e164b0` |
| noble-security | Tue, 27 Oct 2026 17:47:13 UTC | 未提供 | `03b4a340344bee27fe5de728cf9060b9a30a102a85ca30f25c209980c0d4f184` |

Ubuntu 在这三份元数据中没有提供 Valid-Until。本轮没有补造过期时间或关闭验证；这是下载时刻的签名快照，后续部署仍需重新核对官方索引和版本。noble 的较早日期是固定发行仓库日期，updates/security 则为本轮新鲜日期。

12 份 Packages 解压后 SHA/size 与相应 InRelease 的 SHA256 目录逐项一致。233 个 deb 的名称、版本、amd64/all 架构、size、SHA 与已验证 Packages 逐项一致，且和空 status 的 APT Inst 计划完全相等；没有使用宿主已安装库省略依赖。ffmpeg、ffprobe 来自同一 `ffmpeg 7:6.1.1-3ubuntu5 amd64`，相关 libavcodec60/libavdevice60/libavfilter9/libavformat60/libavutil58/libpostproc57/libswresample4/libswscale7 均同版本；私有 libc6 为 `2.39-0ubuntu8.9`。

- ffmpeg deb SHA256：`1a23baace5f2688a47e28119aedae6993cd638e6279a7227dfc52d9a337a1c17`。
- ffmpeg ELF SHA256：`ed16af623947494a72e284b6eb8ff225f2da22b38b5d5069c2fd4b4ba3384e41`。
- ffprobe ELF SHA256：`272f6ebc634a63d9c8b4ca68e964119d980f25154e5aa2c35e5487da48e9a58f`。
- 两 ELF 均 x86_64 ELF64，PT_INTERP 为 `/lib64/ld-linux-x86-64.so.2`，没有自身 RPATH/RUNPATH。私有 loader SHA256：`c20a2dc8917c755f02b94049356320fe1f62ac7d9f8994731f807d9df39302da`。

每 deb 的版本、架构、官方 pool 路径、Packages 来源、SHA、Depends/Pre-Depends/Provides 及 copyright SHA/位置/可解析 License 标签在 `packages.safe.json`。其中 56 项版权声明为 freeform，保留全文于远端 prefix，未把它们猜成单一 SPDX 许可。ffmpeg copyright 含 GPL/LGPL/BSD 等按文件声明，不能以一个简化标签代表全部依赖；未来分发仍需保留相应通知和适用来源材料。

## 安全解包与动态库归属

没有执行 maintainer scripts。提取前拒绝绝对文件路径、`..`、设备等特殊节点；使用 Python data filter，去除 setuid/setgid 与 group/world write 位。23 个绝对 symlink 不落地并逐项记录，避免后续写入逃出 prefix。相对 copyright 目录链接在 prefix 内解析并校验内容 SHA。

初次验证因 libgcc-s1 的 copyright 通过 `usr/share/doc/libgcc-s1 -> gcc-14-base` 提供而停止，发生在提取之前；这是校验脚本未覆盖文档目录链接，非签名/hash 失败。修正只涉及准备脚本，第二次完整校验通过，两个退出结果均保留。

显式 loader 的 `--list` 对 ffmpeg、ffprobe 各解析 214 个依赖，解析路径全部在 prefix。去重后的 216 个实际 ELF/库文件，逐项与对应官方 deb 内原始 payload 字节 SHA 一致；对应包名、版本、deb SHA 见 `elf-payload-ownership.safe.json`。未通过系统 `/etc/ld.so.cache` 补足闭包。

系统 `/var/lib/dpkg/status` 前后 SHA 同为 `b713f5033f356cbe7413280c90e8ac94b82abe3e5f8ae36723bedda0c7b4a991`；私有 status 仍为空。没有执行 apt install/upgrade、全局 apt update、ldconfig 或更新系统 PATH。

## 已执行的离线调用链

1. `/usr/bin/unshare --net` 创建独立 network namespace；记录并确认与宿主 netns 不同。
2. `/usr/bin/python3 -I` 只运行独立 staging 脚本。
3. 每个工具子进程设置 CPU 30s、NOFILE 32、FSIZE 1 MiB、AS 512 MiB、CORE 0、墙钟 30s；stdin 为 DEVNULL，stdout/stderr 有大小上限。
4. 调用私有 `prefix/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2 --inhibit-cache --library-path <固定私有目录>`，再传入精确官方 ffmpeg/ffprobe ELF 和参数。目录仅包括 x86_64-linux-gnu、blas、lapack、pulseaudio、usr/lib，精确 argv 在 `smoke.safe.json`。
5. 正常 smoke 环境为 PATH/LANG 加 OPENBLAS_NUM_THREADS=1、OMP_NUM_THREADS=1；追加 PCM 计数验证只保留 PATH/LANG。没有设置 LD_LIBRARY_PATH。先匹配两工具 `6.1.1-3ubuntu5`，再执行无外部素材的合成 WAV/MP3/OGG/MP4 全解码。参数保留 file/pipe protocol whitelist、固定 demuxer、MOV drefs 关闭、xerror/err_detect、单线程等约束，全部 exit 0 且 progress=end。

工具 smoke 不等于调用候选 `inspect_av`。这里只执行了已验证 ELF 的私有 loader 模式，没有 wrapper、修改 ELF、修改校验器或在真实 API sandbox 里执行。

## 两个生产使用阻挡及未验证边界

**1. 私有库加载和服务可达性。** 冻结源 tar `54ac2460d2a912eeaaa47f0348ba67fd192965202b22db96f7080b0ec5f9731d` 中 `media/av_artifacts.py` SHA `11254764d0b956b7074687ec16fd001e6a62db275ca6e470a9899971adfa002a` 的 `_execute` 以 execve 清空环境，只保留 PATH/LANG，且不支持配置 loader 参数。AVTools 检查绝对路径、文件权限/owner 和精确版本，本身没有 ELF magic 检查；这不是授权使用 wrapper 的理由。本轮按相同 clean env 直接执行两 ELF 均 exit 127，明确缺 `libavdevice.so.60`。因此给 AVTools 填入该 prefix 路径也不会工作；root0700 staging 也有意不向正常 API UID 开放。实际 API UID 权限、systemd mount/network namespace、scratch 可写性及依赖可见性均未验证。

**2. 6.1.1 的 progress endpoint 与冻结计量语义不兼容。** 在同一官方二进制、无网络、资源受限环境中，三个音频另行完整解码成 16 kHz mono s16le，均为 16,000 bytes，即 8,000 个样本、500 ms：

| 自制文件 | 实际 PCM 样本时长 | null decode progress out_time_us | 16 kHz mono 样本数 |
|---|---|---|---|
| tone.wav | 500 ms | 384000 µs | 8000 |
| tone.mp3 | 500 ms | 470938 µs | 8000 |
| tone.ogg | 500 ms | 493500 µs | 8000 |

另一个 32×32、10 fps 的视频输出 5 帧，声明 500 ms，progress 为 400 ms。冻结代码直接使用 `out_time_us // 1000` 作 duration，再和声明值作 100 ms 差值检查。由已记录数据和冻结逻辑可推导：本轮 WAV 的差值 116 ms、MP3 声明 576 ms 与取整 progress 470 ms 相差 106 ms，会拒绝；OGG 或该视频即使落入容差也不能把 endpoint 冒充准确样本时长。这里没有伪称实际 API validator 已执行，这是一项可由上述固定素材复现的 codec/version 合同门槛。没有放宽阈值、伪造 usage 或修改计量。

后续应先独立审查这项兼容性并补真实样本/帧计量验证，再另行批准精确 runtime 库可见性方案。选用系统官方包安装或只读服务根目录映射都涉及新授权与独立验收；本报告没有执行任何一种，也不建议绕过 clean env、扩 ReadWritePaths 或加入未经审查 wrapper。任意硬件、插件、未测格式的可选 dlopen 路径不在本次成功声明内。

## 回执与保留边界

- 远端 `receipts/final.safe.json` SHA256：`6124b9e59703f0e7cf629ae04e7d8c04b83206feb8b0a28d0c61c2f45f4401a1`，对应签名/闭包/payload 审计；追加时长兼容性证据独立保留，不覆盖这一收据。
- `smoke.safe.json` SHA256：`27867215ea863f7eed410beca74b655964407426abbbf8f72e0927a0acc030d0`。
- `packages.safe.json` SHA256：`8f81dbd15ac71437578d038b169bf1c4f0359bd63412dceba4a028963395c06d`。
- 本报告同名 `.safe.json` 列出所有小材料 SHA，包括新增 `duration-compatibility.safe.json`、脚本、冻结源合同及工具终态。

回退边界是继续不引用该 staging；没有生产安装需要回滚。本轮不清理远端包、prefix、旧证据或旧对象。后续部署、服务重启、DR、模型请求和任何钱包操作均未执行。

资料依据：[APT 配置读取源码](https://github.com/debian/apt/blob/main/apt-pkg/init.cc)、154 本机 APT 2.7.14 apt.conf(5)、本轮已签名的 [noble](https://archive.ubuntu.com/ubuntu/dists/noble/InRelease)、[noble-updates](https://archive.ubuntu.com/ubuntu/dists/noble-updates/InRelease)、[noble-security](https://security.ubuntu.com/ubuntu/dists/noble-security/InRelease)。外部索引 URL 会变化，以保存的 SHA 快照为本轮证据。
