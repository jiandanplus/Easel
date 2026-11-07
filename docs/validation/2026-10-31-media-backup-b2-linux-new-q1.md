# B2 新 Q1 Linux 实际验证增量

2026-10-31，分支 `codex/ui-redesign`。本报告是中央服务两个候选包的本机合成隔离验证，不是生产上线、桌面新包或完整 B2 验收。

当前修复后的 SQL SHA 为 `6ce3762c4f63b81a659738faf7189ce86323cbb7961dd8abfb27708074638bba`。current020 wheel SHA 为 `c23cb4cb555e24f96c509abec649fd19fa1e750c2e83b7861d05286eb3a1a6dd`，frozen016 为 `2cbd3002bb028b9fddf5f8d9bc154ad451b696887bbfcd4a620abc369c64e861`。旧版本和失败现场均保留。

## 已取得的实际结果

| 范围 | 实际结果 | 证据边界 |
|---|---|---|
| 新 Q1 macOS 完整链路 | current6、frozen4，共10 PASS | 实际安装前缀；源与原分发文件未改；正常图片检查子进程各新增11份已核验 pyc |
| 新 Q1 Linux 两个 profile 的离线安装与 whole | 两组首次 PASS | 新 PG、实际 root/app/PG 分权、完整捕获、COMMITTED、OPEN 和 fresh B1 私有文件恢复；未做 B3 数据库恢复 |
| full020 原21个 selector | 全部取得原 guardian 接受的 exit0/noOOM 结果 | R6原19项，R7-r2 regression，R8 storage-invariants；不同 harness 的最小增量明确记录 |
| frozen016 storage-invariants、regression | 两项首次 PASS，27.659s／54.368s | R8、原1800s期限；复用原冻结产品，不包含017–020 |
| 32次任务／第33次拒绝 | 两个 profile 均实际核验 | 32个原任务、许可、完成审计，64条 journal、128条 entry；第33次409拒绝且业务摘要不变 |
| GC unlink 后回执丢失 | 两个 profile 均实际核验 | close前原许可已unknown、local invalid/notclosed/physical0；原5个unknown身份不变；20金融表和2序列 canonical bytes 不变 |

full020 最后6项为 inode-replacement、controller-death、controller-death-capturing、keeper-loss、phase120、resource-crypto。原150s owner和120s phase期限均未延长。resource-crypto实际完成超过32MiB的合成归档与 fresh B1 文件恢复，以及39个认证、损坏、EOF、IO、fsync、旧格式及资源组件断言；没有调用真实模型或扣真实钱包。

## 最小 harness 修订与保留的失败

R6到R7仅更改GC与quota的4个Python文件，补真实close前unknown观测、只读全行／序列财务核对，以及32/33额度断言。R7-r2仅补回原准备清单要求的 `public-tree-drift.safe.json`，原准备失败发生在创建Docker资源之前。

R7 storage首次在pre-app财务快照因PostgreSQL序列不是composite type失败。R8仅把该一条 `to_jsonb(sequence_row)` 查询改成显式三个scalar组成的JSON对象；原READ ONLY策略、全行比较、序列值及其余17个payload Python不变。原失败容器、日志、nonce、期限和输出没有覆盖。R8的后续成功单独记账。

Root从终止容器仅复制固定、限长的纯合成结果，独立重算两profile的财务摘要、许可身份和额度计数。frozen storage原结果12966B，SHA `86dd9df71a3ac0798258d4641c1c55b42921d6f5a0f4b8ae34fb71969255d753`；frozen regression原结果52921B，SHA `efe0622f64473d9957d930f7c7bf14eef4f23281539e273801da5ceed5bfe582`。原始结果仅位于各自私有测试目录；本报告不含凭据或真实业务数据。

限定独审已通过，未发现新的P1/P2：独立重核415个来源／安全回执pin、5套wheel到已安装镜像的来源链、25个固定容器和5个镜像的新鲜只读GET，以及四份合成quota/storage结果的结构。Quota原结果没有保留完整business表行，精确bucket约束仍依靠冻结且实际退出0的owner SELECT断言和ID／hash链；没有声称再次查询数据库。原始42个checklist的历史标志不改，当前21项运行结论单独映射。公开审查材料已归档到 `outputs/_run/b2-development-20261030/new-q1-linux-runtime-map-independent-20261031/`，原11件258057B及root归档清单均保持；原manifest SHA `dae5f4e176540ac254e2e211ae85e9b475d08bfd8dbf3e2607cb4ebb8762e9f8`。

## Logto145 当前状态

首个portable exporter因把monotonic秒值传给要求纳秒绝对期限的产品helper，在BEGIN前被拒绝。一次独立只读probe确认精确调用栈；原失败没有创建Exec或dump。

r2只取一次 `monotonic_ns()` 生成90秒窗口，三个SQL wrapper共用同一纳秒期限；host/remote从该期限派生，不延窗。真实已安装helper的旧秒值RED与新4项定向检查、原17项契约检查均完成。

Root审查r2后，以新nonce进行一次明确的有界尝试。该次已通过源连接和快照事实阶段，但dump阶段返回 `remote_child_failed`；remote终止已确认，保留0B dump、intent和脱敏failure，未自动重试。该次没有产生可接受的可移植资源。失败输出为私有目录 `easel-b2-logto145-portable-resource-67f6453a156e466c88c4266cdb1224b3`。

随后一次单独批准的只读诊断实际通过：GNU timeout9.7、直接路径和wrapper的pg_dump／psql均为16.15，两个psql都成功导入同一只读快照，79表／8枚举和读取权限布尔均符合预期。没有在诊断中执行导出。核对[官方pg_dump文档](https://www.postgresql.org/docs/16/app-pgdump.html)与[实际16.15自定义格式实现](https://raw.githubusercontent.com/postgres/postgres/REL_16_15/src/bin/pg_dump/pg_backup_custom.c)后，确认 `--file=-` 会作为非空文件名处理；该格式输出到stdout应省略文件选项。历史stderr未保存，不能还原当时的精确错误文本。

r3仅移除该命令参数，另补有限的收集进度、私有ExecID和真实退出码记录。原五项984KiB资源上限不变，新增4KiB ExecID单列，总上限988KiB；同一90s原窗、单dump、完整只读源后置检查不变。新增8项检查先RED后GREEN，原17项及4项时钟／脱敏检查保持原测试字节并全部通过。

Root亲读补丁与清单后，以新nonce进行的**r3唯一一次实际导出已通过，exit0、0.979s**。五个私有0600文件合计427990B：custom dump401428B、压缩facts24121B（解压469825B）、manifest1301B、intent980B、ExecID160B。Root独立核对PGDMP头、全部payload长度／SHA、无failure、源前后不变，以及79表、8枚举、4个owned角色、5条membership。manifest SHA为 `18af2f959c7551715e8f0cf6b128769a667eb8da3a53e996f1c0260081c65731`。资源仅包含合成夹具材料，保留在私有目录 `easel-b2-logto145-portable-resource-c6beb734e96c4f31b7de2a319d0b15e0`，不复制数据内容或凭据进仓库。**Linux fixture恢复、LOGTO145 whole和B3仍未完成。**

## 仍开放的验收

Linux async/audio/voice真实claim、recovery、physical和finally链、Logto145 Linux变体、完整T14独审及完整B2仍开放。21个selector的绿色结果不能替代这些family覆盖。六个不依赖codec的测试变体已经完成新Linux镜像实际离线安装；前三个文件写入变体在各原1800s期限内exit0/noOOM，safe终态均为OPEN、unknown1、physical0。第四个slow-get实际exit1，原200ms／1s条件未放宽；只读标准traceback定位到原测试227行的 requests／cancelled 联合断言，尚未导出locals，不能判定哪个子条件失败。第五第六未启动，首次失败后没有重试、清理或将失败改为通过。原22函数／37节点、剩余20函数／33节点和六种AV格式要求保持。

**专用卷统计与f50局部预算已实测并限定独审通过。** 唯一helper `a97d8711…` 的create/start各一次，原20s guardian在0.709967s接受exit0/noOOM；实际capget为effective/permitted/bounding=2、inheritable=0、NNP1，全部25卷只读，原source仍exited。计数器只用目录FD、scandir/lstat，未读取卷内普通文件内容或输出内部名称。25卷共923个可见节点，regular logical为706103852B，按每卷inode去重的all-node `st_blocks×512` 合计708739072B；这些数不是全Docker占用、可释放空间或独占物理空间。

f50本轮仅选其storage/regression两卷：logical292648B，all-node allocated409600B。四个精确容器的最新只读size与同一f50镜像history给出如下预算。旧的镜像总Size差值60784700B和旧总额209608764B没有用于最终接受。

| 指定组件 | 字节 |
|---|---:|
| f50 builder SizeRw | 148783104 |
| 两个f50 runtime SizeRw | 57344 |
| 新f50镜像history首项未压缩Size | 148750336 |
| f50两个卷all-node allocated | 409600 |
| 统计helper SizeRw，保守加入 | 110592 |
| 合计／指定限额 | 298110976／536870912 |

以上仅核对指定writable／image／可见inode元数据口径，预存共享base和daemon／全局文件系统元数据不在该局部预算内，也不是hard quota或完整B2资源验收。新执行目录all-node allocated为253952B，低于262144B。三次后处理失败均保留：首个tail1读取未保存原stdout，JSON解析失败的确定原因无法恢复；后续同一已退出helper仅做一次有界完整日志GET，取得17685B raw，两帧16384／1285B拼接后的17669B与统计JSON逐字相同，没有重跑helper。错误的builder/image一致性断言和预设大小断言也未覆盖成成功。Root另外获取精确size后重新计算，预算回执SHA为 `881e4af1c113761c6066640b44699c861e1b7922e1e3e127de5e243835ea633d`。

限定独审材料位于私有目录 `easel-metadata-actual-independent-20261031-_5iu_s0g`，manifest SHA为 `8a4a646abea6c92e9c359d21078cec74dc486cfca1f731d09148771e68afa2a3`；独审未再次访问Docker或卷内容。此结论没有关闭完整T13／T14／B2。

生产B3同窗捕获／定时器、真实旧密文密钥、数据库／角色／ACL恢复、AV第四载荷、永久工作区迁移、签名／公证和净机原生验收、第二个真实产品仍保留在总执行账。正式支付按用户决定延期。本轮没有修改前端文案、原17868工作台、用户历史或接口配置，没有提交、推送、合并或远程维护。

相关完整证据与历史失败见 [运行集成报告](2026-10-31-media-backup-b2-runtime-integration.md)。
