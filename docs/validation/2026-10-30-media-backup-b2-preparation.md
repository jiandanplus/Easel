# B2 接入设计、静态调查与旧格式容量对照

**本轮完成 B2 设计准备和独立文字审阅，尚无 B2 实现或生产维护。** B1 原源码包仍为 `6b40acaf3aac814bb4b4115666f2ca8ce4bcd2976d09effdbbc07ae2db33494b`，229 项测试记录未改。分支保持 `codex/ui-redesign`，没有前端修改、提交、推送或合并。

## 新确认的代码事实

[完整窗口调查](../../outputs/_run/media-backup-b2-preparation-20261030/evidence/audits/window.md)固定 25 项当前源码／文档及行号。后台 run_once 不按模型 enabled 停止；stage 与物理写分离，prepare／claim 不持现有 storage lock；GC 在事务外 unlink 后才 finish_gc 核 token／持锁。原 window flock 只串行化维护 transition，不能代表捕获全窗。finish_gc 留有历史 token／lease，通用活跃GC不能按全表token非空判断。八个竞态例是静态调度论证，不是服务器实测。

[旧加密接口调查](../../outputs/_run/media-backup-b2-preparation-20261030/evidence/audits/legacy-crypto.md)绑定公开 helper `7dd81fbc…` 与旧备份源码 `d330bccf…`。v1 固定32MiB、整体bytes、预先知道完整plaintext SHA；其inner archive不是B1格式。B1 sink与decoder接点明确：write返回明文count；restore末尾read(1)不能早于final-auth返回EOF，ciphertext fileno也不能冒充明文FD。

这些事实改变下一步实现选择：新producer／控制器必须独立版本；旧加密接口不能直接接B1的大归档流，模型disabled或单独advisory lock不能被包装成完整屏障。没有改变F1/F2/旧DR的冻结合同。

## 实际本地容量对照

[观察回执](../../outputs/_run/media-backup-b2-preparation-20261030/evidence/observations/legacy-cap.safe.json)来自已存在公开helper，纯合成32／33MiB字节与合法协议metadata。key loader被刻意替换为只计数并抛出受控错误，PEM仅为语法占位，未解析真实或合成RSA key，未执行加解密：

| 明文规模 | 现有seal结果 | 到达替换key loader次数 |
| --- | --- | ---: |
| 32MiB控制 | 在替换loader处拒绝 | 1 |
| 33MiB | 在loader之前拒绝 | 0 |

两次最终均拒绝；控制只证明能越过前面的size/metadata门，不是32MiB真实加密成功。源SHA前后相同。新B2代码、内存性能、密码实现或生产恢复没有由此通过。

## 设计 R1 → R3

[当前完整设计](../superpowers/specs/2026-10-30-media-backup-b2-design.md)为24,487字节、SHA `f9614a317705fd25663bd942e847759910a8d1c4fcf9c9a70e9107b8d8603cff`。路线A采用持久状态／opaque permit、完整mutation lifetime及单存储主机物理锁；新generic producer、独立维护schema、配置／source-facts无环保存、显式v2认证流、durable重读与commit，以及独立加密context／恢复anchor共同组成B2。本地阶段不部署控制schema、锁文件或服务。

两位审阅者的R1提出共5项P2，均在R2回应：原始source-facts实际归档及无环hash；sequence/roles/config非MVCC规则；v2精确字节规范；新作业目录／backup-root／祖先fsync；交付后独立expected plan的持久可信来源。R2窗口复审另发现`1–32MiB`下界歧义；R3仅改成`1 byte ≤ plaintext_length ≤ 32MiB`且不padding。R1/R2设计、全部原报告均原字节保存。

R2密码文字复审未见新P1/P2，窗口原问题闭合；R3精确差分复核只关闭最后一处尺寸文字。它们是设计层面的有限PASS，不是运行、安全认证、实际Linux／PG、完整B2或用户批准。当前设计确认已向用户发出，写入本记录时仍待答复。

## 官方接口与候选依赖

按Context7先resolve PyNaCl为`/pyca/pynacl`，再查询secretstream接口，两次exit0；补读[libsodium官方说明](https://doc.libsodium.org/secret-key_cryptography/secretstream)及[PyNaCl官方绑定源](https://github.com/pyca/pynacl/blob/main/src/nacl/bindings/crypto_secretstream.py)。源码main说明不是发布wheel证明。Linux flock说明仅支持本机primitive语义，不替代应用许可／数据库／多节点证明。

PyPI网页首次获取失败，随后固定[1.6.2官方JSON](https://pypi.org/pypi/PyNaCl/1.6.2/json)GET成功，候选Linux/macOS文件size/SHA与依赖markers已保留。项目venv当前cryptography=50.0.2、PyNaCl未安装；cffi=2.1.1是平台lock静态值。未下载或安装wheel，未验证其实际native库版本、来源链或目标运行。不能把候选metadata称为依赖交付完成。

## 保全与交付边界

[本轮保全](../../outputs/_run/media-backup-b2-preparation-20261030/preservation.safe.json)复核B1和旧冻结源21项、原B1 ZIP均匹配；另[桌面来源核对](../../outputs/_run/media-backup-b2-preparation-20261030/desktop-source-preservation.safe.json)复核v33的904源码／60构建输入、四个指定中央工具，差异均空。没有访问工作台账号／workspace、真实配置、key、归档、媒体或钱包，没有模型／支付、生产SSH或AV运行。官方文档／元数据的公开GET与服务器操作分开记载。

本轮证据归档只包含公开参考源码、静态报告、设计历史、合成probe及脱敏metadata；目录／清单见[证据README](../../outputs/_run/media-backup-b2-preparation-20261030/evidence/README.md)，实际闭集／hash复核另记[安全回执](../../outputs/_run/media-backup-b2-preparation-20261030/preparation.safe.json)。不含合成大字节本体、真实备份或密钥。

固定18份副本共203,393字节，加README／manifest为20文件、234,586字节；清单SHA `686af466a1906e1eb007e74f5dbcf18639a6579f134cd00514bf0aca44b4d5d1`。副本前后source stat／path身份／SHA及copy SHA相同，目录0700／文件0600。root独立核对闭集、全部副本与当前18个固定源；原设计修订和原P2报告没有重写成通过。

完整目标仍未完成：B2实现／隔离验证、B3真实fresh restore／timer、F2 Linux／PG／WPD、旧mixed失败、AV真实隔离／完整源码／编译／供应商、永久工作区批准及迁移、macOS签名／净机等继续保留；支付按用户要求关闭、第二真实产品尚未提供。设计就绪不更改任何门的完成状态。
