# FFmpeg 官方源码签名：实际获取尝试，尚未验签

2026-10-28，root 在只读来源研究后尝试取得官方 FFmpeg 9.0.2 归档、公钥和签名。**获取未完成，GPG／gpgv 没有运行，来源签名验收仍未通过。** 没有解包、编译、安装、模型调用或生产服务操作。

官方[下载页的仓库镜像](https://raw.githubusercontent.com/FFmpeg/web/master/src/download)明确给出 9.0.2 归档、对应 `.asc` 和 RSA 公钥完整指纹 `FCF986EA15E6E293A5644F10B4322F04D67658D8`，并另列 git tag 与可选 PQ 验证说明。页面元数据与归档的真实签名校验是不同证据；本轮没有用网页、GitHub 签名徽章或归档文件名代替验签。

| 实际位置与方式 | 结果 |
|---|---|
| Mac：保持 TLS 校验、禁用隐式 proxy 的有界 Python 获取 | 四项均为 URLError，未取得可验证归档；没有据此判断官方站点全局不可用 |
| Mac：系统 curl 对照官方站点仓库镜像 | exit28，SSL connection timeout，HTTP000；未关闭证书校验 |
| 154：严格主机校验 SSH，新 root0700 私有目录；系统 curl `-q`、干净环境、HTTPS、超时／字节上限 | 仓库镜像页面 HTTP200，23,806bytes，SHA `9db8d7bf7707f693232a9db4083fe2a4bcd2ea37c11269bcf19e612ffb7d1400`；ffmpeg.org 的归档、公钥及签名三项均 exit28，获取不完整 |

服务器目录为 `/root/easel-av-source-verification-20261028`，只保存公开来源材料／下载回执；不读取业务配置、账号、模型密钥或真实数据。服务端代码在任何一项获取失败时不会进入公钥解析或 gpgv 分支。没有换用第三方预编译产物、关闭 TLS、回退旧版本或重复安装。取回的只是下载回执，其原始 SHA 为 `4ffb65ecee53aedf7252fa4ec25515307b1f73c079cb97cccfb43cfc64f65926`。

两份脚本、Mac 获取回执与服务器小回执已保存在 [证据目录](/Users/jiandan/Code/GitCode/Easel/outputs/_run/central-av-source-signature-attempt-20261028)。脚本的 GPG 分支只是尚未执行的候选流程；不将 AST／代码审阅解释为真实签名、tag 与归档等价、Linux ELF、完整 profile、依赖安全或生产运行验收。普通 Ubuntu 6.1.1 基础包的安装阻断继续保留，详见[受维护来源研究](2026-10-28-av-maintained-source-research.md)。
