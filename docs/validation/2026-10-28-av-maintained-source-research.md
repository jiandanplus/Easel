# 受维护 FFmpeg 来源：只读研究与安装阻断

**普通noble `7:6.1.1-3ubuntu5` 的生产安装继续阻断；官方9.0.2只是后续隔离构建候选，不是已修复或可投产工具链。** 本页归档官方来源研究，没有下载源码归档、编译FFmpeg、安装、SSH、启用Pro、付费或修改AV源码／App。

[USN-8915-1](https://ubuntu.com/security/notices/USN-8915-1)于2026-10-12列出noble修复级别 `7:6.1.1-3ubuntu5+esm15`／ESM Apps。现有base的来源签名和Linux算法兼容性不能抵消该补丁差距；150包安装计划仍未执行。AV r2已授权的固定Linux14正例／3原负例／3短rate案例见[独立Linux报告](2026-10-28-av-native-r2-linux.md)，只证明测试loader下的算法行为；cleanenv直接执行仍exit127缺libavdevice，实际API UID／systemd及生产工具链未验。

官方[站点源码镜像](https://raw.githubusercontent.com/FFmpeg/web/master/src/download)列9.0.2、8.1.3和6.1.6等，提供签名来源说明。研究候选9.0.2的tag object为ce8f11b9fac4cd7ccfb41ca7c01bc17f5cda8cc3、commit为946fcce07b6dcd0331c8cc609192aeff5e1924f8；官方镜像metadata有签名状态，但**本次没有实际GPG验签，也没有生成Linux二进制**。

USN八项有正向源码证据：七项上游fix为9.0.2祖先；[CVE-2026-75147回移植](https://github.com/FFmpeg/FFmpeg/commit/f175bd50821f9adcded3acacc6b8e04037a92715)明确引用原983dae修复，tag限定提交史及源文件有对应guard。这不能扩大成全部当前CVE／依赖已清零；6.1.6原hash分叉同样不等于没有修复，仍需等价backport审计。

[完整研究及官方链接](../../outputs/_run/central-av-maintained-source-research-20261028/brief.md)列出：签名与精确source pin、当前security残余未核项、Pro需核真实授权及商业资格、无Pro官方源码＋公开补丁的私有prefix路线、构建依赖、LGPL/GPL/nonfree，以及完整验收矩阵。没有推荐未经审查第三方static包、换OS、给API UID Docker/socket权限或新增daemon。

后续必须保持固定绝对ELF／owned bytes、完整baseline decoder与profile，MP4／MP3／WAV／OGG／Opus／AAC、CFR／VFR／旋转／900s以及原native audit／framehash完整解码和资源限制。不得通过省外部库、裁剪codec、缩短时长、加宽容差或提高资源限额让候选通过。真实GPG／全部CVE与依赖、全profile parity、cleanenv ELF闭包、实际Linux API UID／systemd和fresh full仍未验；商业Pro资格也未读取或假定。

持久研究目录[copy-manifest](../../outputs/_run/central-av-maintained-source-research-20261028/copy-manifest.json)原样复制44个公开小证据加1份原始SHA receipt，共45文件、72,513bytes，另生成复制manifest。与本研究无关的F2测试／AV Linux测试不混入此研究计数。本次仅验证复制字节和哈希，未发起新的网络研究或运行生产代码；没有真实capture、凭据、源码归档或二进制进入该目录。
