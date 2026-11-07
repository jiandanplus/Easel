# FFmpeg 9.0.2 上游 RSA 归档验签完成：独立只读复核

**本次从 Debian 官方镜像取得的原归档字节，已经在服务器实际通过固定上游 RSA release key 验签。独立只读审查未发现这道来源认证门的阻断项；这不代表可生产安装。** 本次复核没有重跑 GPG、连接服务器或下载大归档。

固定候选仍是9.0.2。实际归档为12,040,788 bytes，SHA256 `8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e`；DSC声明 `Source: ffmpeg`、`Version: 7:9.0.2-4`。归档/asc与DSC的精确size和SHA一致。DSC自身签名未验，也未用它代替上游签名。[固定DSC](https://deb.debian.org/debian/pool/main/f/ffmpeg/ffmpeg_9.0.2-4.dsc)、[orig归档](https://deb.debian.org/debian/pool/main/f/ffmpeg/ffmpeg_9.0.2.orig.tar.xz)、[配套asc](https://deb.debian.org/debian/pool/main/f/ffmpeg/ffmpeg_9.0.2.orig.tar.xz.asc)。

服务器对核好 exact bytes/SHA 的归档实际执行gpgv，exit0，完整原始状态中只有一个VALIDSIG，签名及主指纹均精确为 `FCF986EA15E6E293A5644F10B4322F04D67658D8`，无expiry/revocation/bad-signature拒绝状态。公钥1709 bytes、SHA256 `397b3becedcd5a98769967ff1ff8501ddc89f8368b8f766e4701377d7dbaabe5`，匹配此前独立取得的[官方FFmpeg/web release key](https://raw.githubusercontent.com/FFmpeg/web/master/htdocs/ffmpeg-devel.asc)及固定指纹；该 exact 证书此前show-only记录无expiry。指定新私有homedir/keyring的信任来自完整key pin和字节绑定，没有导入真实用户keyring或设置ultimate ownertrust，不外推新在线全局撤销状态。

七份实际小回执与received-manifest及取回JSON的base64字节逐项一致。脚本先重核DSC/asc/key哈希，再核归档size/SHA并真正验签，结果不是仅依赖Debian元数据或badge。[原始GPG状态](../../outputs/_run/central-av-upstream-rsa-source-20261028/server-receipts/archive.verify.stdout)、[stderr](../../outputs/_run/central-av-upstream-rsa-source-20261028/server-receipts/archive.verify.stderr)、[实际归档回执](../../outputs/_run/central-av-upstream-rsa-source-20261028/server-receipts/archive.safe.json)、[独立完整复核](../../outputs/_run/central-av-upstream-rsa-source-20261028/review/review.md)及[逐文件清单](../../outputs/_run/central-av-upstream-rsa-source-20261028/copy-manifest.json)。

签名后只流式遍历tar，回执报告10,525 members、90,358,343 regular bytes，VERSION=9.0.2；VERSION至多65 bytes读入内存，没有文件系统提取、编译或安装。这不证明未来提取器安全。服务器原归档留在 `/root/easel-av-rsa-orig-source-20261028`，本机归档只含小公开证据和脚本。

`release_archive_byte_identity_cryptographically_verified=true`在本证据中严格表示“这份exact bytes通过固定上游release RSA签名”，**没有与不可达ffmpeg.org HTTP响应直接逐字节比较**，也没有证明Git树/归档等价。旧[ED tag数学验证与过期证书结论](2026-10-28-av-official-tag-signature.md)及[原官方源直连失败](2026-10-28-av-official-source-signature.md)原样保留；新RSA来源链不是延key、忽略expiry或修改旧失败。

普通noble6.1.1基础包的安全安装阻断仍保留。PQ、Git Merkle树、所有源码/依赖漏洞、LGPL/GPL构建配置、完整codec/profile、900s资源与真实运行/生产安装仍未验；9.0.2仅关闭本次来源认证门。原本机route阶段与服务器metadata阶段的pending/false保留为当时事实，不覆盖后续实际验签完成回执。主交付计划由root维护，本页未修改它。
