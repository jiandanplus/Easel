# 官方n9.0.2标签数学验签完成，证书信任门未通过

**实际GPG数学验签通过，但本次从publisher API取得的证书包已过期，发布信任门仍未通过。** 正向exit0／VALIDSIG不能盖过同一完整日志中的EXPKEYSIG／KEYEXPIRED；不推断官方现用证书全局过期，也不称标签伪造。

本次从官方FFmpeg/web完整tree确认RSA公开key在repo内，实际GPG主指纹匹配FCF986EA15E6E293A5644F10B4322F04D67658D8且自签名检查通过。git-tag-key.asc不在该tree；随后按固定官方release commit关联的michaelni第一方公开gpg_keys API取候选key，完整fingerprint精确匹配预先从官方web固定的DD1EC9E8DE085C629B3E1846B18E8928B3948D64后才导入专用keyring。没有keyserver／任意账号穷举，不把API徽章当密码学结果。

已重构payload＋signature的Git annotated tag object为ce8f11b9fac4cd7ccfb41ca7c01bc17f5cda8cc3，object指向固定commit946fcce07b6dcd0331c8cc609192aeff5e1924f8。GnuPG2.5.18实际verify exit0，VALIDSIG为该完整key；但此次证书包expiry1722982067（2024-08-06），并在2026-09-18签名验证中报告EXPKEYSIG。数学正确与有效证书状态／发布策略是两道门。没有设置ultimate ownertrust、刷新／延key或faked-system-time。

三个负控分别为改payload→exit1 BADSIG、改签名数学数据→exit1 BADSIG、RSA-only新keyring→exit2 NO_PUBKEY；该阶段随即停止。之后仅执行下文另行授权的一次本机primary正文转存show-only补充，补充结束后立即停止；没有新增网络请求。所有stdout／stderr完整保留。

[完整brief](../../outputs/_run/central-av-official-tag-signature-20261028/brief.md)、[原始positive status](../../outputs/_run/central-av-official-tag-signature-20261028/tag-verify.stdout)、[stderr](../../outputs/_run/central-av-official-tag-signature-20261028/tag-verify.stderr)、[负控](../../outputs/_run/central-av-official-tag-signature-20261028/negative-controls-safe.json)及[copy-manifest](../../outputs/_run/central-av-official-tag-signature-20261028/copy-manifest.json)均为白名单小证据，不包含临时keybox／trustdb／真实用户keyring。所有key操作使用新0700 homedir、no-options／no-autostart／no-auto-key-retrieve、clean PATH／LANG且不覆盖HOME；新homedir无GPG sockets，无本流程持久daemon。

仍未验证源码归档RSA／PQ签名、原始commit对象与完整Merkle树、源码全取、归档等价、CVE／完整profile、构建或生产可用。签名git源码路线只完成上述固定tag的数学和对象绑定；必须先从允许主权来源关闭证书包状态，再审完整源码链，不能把API任意payload当source。普通noble安装仍阻断，9.0.2仍只是候选。没有SSH、clone、源码归档、编译或安装；[旧官方源获取失败](2026-10-28-av-official-source-signature.md)及[既有来源研究](2026-10-28-av-maintained-source-research.md)不覆盖，本页没有修改总账。


## Primary网页正文转存补充：同样未过信任门

Root后来一次web工具取得primary地址正文并转存，1232bytes、SHA `cbb45d9f59b2b3a595cd4107958f8e6499765bb0185d6ae90942e3f0d46f024f`。这不是curl原始wire bytes。另建独立homedir做show-only，exit2并有缺armor分隔空行的invalid header警告；stdout解析完整DD1EC…指纹、pub:e、expiry1775086047（2026-04-01T23:27:27Z，北京时间04-02）。仅证明本次转存证书包的过期观察，不是成功primary验签，不推断官方当前key全局状态。

按停止条件没有规范化armor、刷新／延key、改时间或继续primary自签名／tag验证。primaryCertificatePolicyAccepted=false；publisher原数学验签、过期状态及三负控不变，整体releaseTrustPolicyAccepted=false。[原始转存](../../outputs/_run/central-av-official-tag-signature-20261028/primary-web-tool-tag-key.asc)、[show-only stderr](../../outputs/_run/central-av-official-tag-signature-20261028/primary-key-fingerprint.stderr)、[stdout](../../outputs/_run/central-av-official-tag-signature-20261028/primary-key-fingerprint.stdout)和[失败safe](../../outputs/_run/central-av-official-tag-signature-20261028/primary-supplement-safe.json)原样保留；publisher-only brief／safe另存v1。没有后续network／SSH／构建／安装，仍无归档、完整tree、源码全取或生产放行结论。
