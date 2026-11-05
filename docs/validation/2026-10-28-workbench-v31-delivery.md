# Easel 工作台 v31 公开交付归档（2026-10-28）

状态：**v31 冻结包、同机隔离启动、匿名订阅配置合同、实际 App 离线工具交付和包内图片 codec 已验收。** 本归档创建时，root 正对唯一真实 UI 轮次只读核对下载字节、钱包与新 execution；本 base 尚未收录独立 live-safe 回执，暂不宣称真实模型验收成功。后续 live 结论通过独立增量追加，保留此 base 的历史状态。

## 交付物与精确归档

- [macOS 15 Apple Silicon ZIP](../../outputs/_run/pi-portable-20261028-v31/Easel-Pi-macOS15-arm64.zip)：129,745,884 字节，SHA256 `f0e6e86581edb243496ce8e3ad8f3f40363e87e3e1a2c4a28fe2c6ace6c5f13c`。本次归档重新计算实际ZIP哈希通过。
- [实际 Easel.app](../../outputs/_run/pi-portable-20261028-v31/Easel/Easel.app) 和[包元数据](../../outputs/_run/pi-portable-20261028-v31/artifact.json)。最低 macOS 15.0；未签名、未公证；UI 是本机回环网页。
- [公开证据目录](../../outputs/_run/workbench-v31-delivery-20261028)与[完整白名单／复制清单](../../outputs/_run/workbench-v31-delivery-20261028/copy-manifest.json)：明确复制 71 个文件，共 480,987 字节；另生成README及live追加说明2个文件；含manifest共 74 个文件、518,694 字节。每项源文件复制前、副本、源文件复制后SHA256一致，实际目录文件集合与manifest闭合。
- copy-manifest SHA256：`920e5a92e4a05a2f125ecb540d328642b0d594561ea8f20531d6b3d1459958f7`。

不复制 App/ZIP 本体到证据目录，不复制原用户工作区、私有备份、before-private、new-owned-private、protected-backup、配置、认证日志、cookies、历史、隔离工作区或大体积before快照。activation目录只按4个精确文件路径读取，未扫描该目录。

## v31 修复、构建和包证据

[A1报告](../../outputs/_run/workbench-v31-delivery-20261028/onboarding/2026-10-28-subscription-onboarding-fix.md)、[源码补丁](../../outputs/_run/workbench-v31-delivery-20261028/onboarding/production-only.patch)及[独立limited PASS](../../outputs/_run/workbench-v31-delivery-20261028/reviews/subscription-onboarding-independent.md)记录：web/app.py三个return把本地configured与ready分开，App.tsx画像推荐额外要求gateway=true。执行gateway、route、model/lease、Send等门禁及UI文案未改。历史候选回执含42后端、95定向前端、541完整前端与tsc0，重叠数量不相加；本次归档未重跑测试。[guarded构建审阅](../../outputs/_run/workbench-v31-delivery-20261028/reviews/guarded-build-independent-review.md)为执行前静态审阅，后续实际包证据另列。候选报告内“运行中v30/未构建”保留其原阶段含义，不能据此否认随后v31构建，也不能据此声称当前live验收。

[基础包验收](../../outputs/_run/workbench-v31-delivery-20261028/validation/v31-validation-safe.json)逐一验证15,402资源文件、ZIP15,407完整容器文件及所有归档成员CRC；904源码只相对v30变化web/app.py与App.tsx，59构建输入完全一致。13前端文件、100源码receipt、3HTML引用与8JS资源引用匹配。11,932个Pi文件对v30完全一致；2,597个Python路径中仅6个安装器RECORD的未交付bin入口哈希不同。包内Python3.12.13、Node24.22.1、Pi1.4.0、Pillow12.3.0实际加载通过，26个模块均来自bundle。

全新匿名工作区[实际回执](../../outputs/_run/workbench-v31-delivery-20261028/validation/fresh-install-safe.json)确认modelConfigured=true，envKeyOk、routeOk、gatewayUp、authVerified及status.gateway=false：配置存在不等于已认证可执行，未生成账号凭据或供应商Key。该实例认证停止、原句柄等待和端口关闭通过。

## 实际离线工具与图片 codec

[离线真实App回执](../../outputs/_run/workbench-v31-delivery-20261028/offline/offline-tool-safe.json)记录第二个新工作区实际App→PiSDK→write_file宿主桥→App回读及媒体下载。mock收到GET/models一次、POST/responses两次，第二次含同call_id的“文件已保存”host结果；使用真实catalog选择租约、session/turn、accepted receipt、SSE completed/clean_end终态。新文件、预期及下载168字节SHA256均为`f6d70fe1580047f80644550f8dfc2a0682bbd0a5e779c2124e70e8e1da10a834`。假Key/custom接口不需要Logto登录或全局Agent。App与mock均经owned停止/shutdown及wait0，端口全关闭。localhost-only sandbox预检允许回环监听、非回环TESTNET获EPERM；没有Node fetch hook，不宣称独立统计全部其他localhost尝试。离线mock不能证明真实模型推理或扣点。

[codec附加回执](../../outputs/_run/workbench-v31-delivery-20261028/validation/codec-safe.json)使用实际包内Python -I -B、系统PATH和deny network* sandbox，纯BytesIO产生并load完整解码16×16RGB JPEG与lossless WEBP。JPEG最大通道差2、平均约0.674；WEBP像素完全一致。基础验收已完整解码8张实际PNG。针对旧native graph的unused libjpeg RPATH关切完成定向补验；未启动App、读取真实图片或安装依赖。[独立delta](../../outputs/_run/workbench-v31-delivery-20261028/validation/v31-validation-codec-delta-safe.json)保留原总回执SHA，并解释一个继承guard字段名v30_v30只是标签，实际31,796路径及归档哈希明确对应v31/v30。

## 同一原工作区切换的安全快照

归档仅复制 [backup-safe](../../outputs/_run/workbench-v31-delivery-20261028/activation/backup-safe.json)、[activation-safe](../../outputs/_run/workbench-v31-delivery-20261028/activation/activation-safe.json)、[preservation-safe](../../outputs/_run/workbench-v31-delivery-20261028/activation/preservation-safe.json)、[login-identity-comparison-safe](../../outputs/_run/workbench-v31-delivery-20261028/activation/login-identity-comparison-safe.json) 四份安全JSON，不触及其背后私有数据。

备份回执记录1160条目/894普通文件，159受保护文件已核对；切换快照为同工作区、同origin、port17868、PID75194，HTML匹配v31，旧App/ZIP/启动器保留，未迁移profile或数据。安全回执记录当时已认证、账户比较一致、钱包已观察且切换保护核对一致；login比较只列出session_id字段变化而不输出真实身份值。这些是切换/登录比较时的历史快照，不证明随后真实模型的新execution或实际扣点，不能替代待追加live-safe。

## live-safe 追加与证据限制

按[追加约定](../../outputs/_run/workbench-v31-delivery-20261028/live/README.md)，后续仅接受root明确提供并去敏的精确文件，排他创建live/<唯一标签>/receipt.safe.json及delta-manifest.safe.json，记录源复制前/副本/源复制后SHA和base manifest SHA；不覆盖此文档、safe JSON、base manifest或任何原receipt。真实生成、文件交付、execution终态、钱包/账单分别按新证据声明。

本次只做本地归档，未启动App、浏览器、真实账号/钱包/HTTP/SSH或模型请求，未构建/测试/冻结，未改产品源码、App/ZIP、主delivery plan或已有receipt。尚无本base内的独立真实模型验收结论；不声明干净设备、Gatekeeper、签名/公证、原生输入法、VoiceOver或完整真实用户路径验收。
