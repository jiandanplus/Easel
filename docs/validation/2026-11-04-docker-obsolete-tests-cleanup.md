# 旧版 Docker 测试资源定向清理（2026-11-04）

按用户本次“删除没用的测试镜像和容器”要求完成。没有 force、system prune、重启 Docker 或操作远程服务器。

## 实际删除

- 15 个旧版 `easel-b2-t13:full020-*` 镜像，保留最新 `full020-a0cb31f4569645b7`。
- 引用上述旧版本的 17 个已停止测试容器，以及 4 个已成功结束的 AV／磁盘探测辅助容器，共 21 个。
- 旧测试版本及其失败／成功状态只保留历史验收记录和删除前的精确 ID、镜像、进程状态，不再宣称这些旧 Docker 容器或镜像仍保留。此次删除不把任何旧失败改写为成功，也不重启或重跑旧测试。

清单阶段捕获 48 个容器，本次之后 27 个；按 `image ls` 的唯一 ID 清单由 28 变为 13。删除前逐项复核 exited／PID0；删除镜像前确认没有剩余容器引用，使用 `image rm --no-prune`。

## 保留及复核

- 227 个数据卷全部保留，前后完整 ID 集合一致；没有使用 `rm -v` 或 volume prune。
- 其余容器完整 ID／镜像／进程状态与清单时一致，保留镜像 ID 集合与预期一致。
- 原 B3 源 PostgreSQL 50a0546b...仍以原 PID62835运行。
- 最新 B2 测试版本、当前及原失败 B3 worker、当前未完成 AV 环境、Logto／原预览、LobeHub 及其他产品资源保留。
- 未读取／导出容器环境变量、账号、供应商配置或数据库内容。删除前只保存筛选后的容器／镜像元数据。

## 空间观察

删除前 Docker 独占层表显示这些旧 B2 镜像每个约210 MB，合计约3.2 GB；不能把每个846–847 MB的标称镜像大小相加当作回收量，因为存在共享层。

清理后 `docker system df`：镜像总占用10.58 GB，容器68.63 MB，数据卷14.6 GB，构建缓存1.377 GB。后两项本轮未清理。宿主在删除命令刚结束时尚未立即返还稀疏磁盘空间，随后复测约12.03 GiB可用。其他进程和Docker Desktop回收时序均会影响宿主可用值，因此不把全部时间窗口变化归因为本次删除。

## 证据

`outputs/_run/docker-obsolete-tests-cleanup-20261104-once001/plan.safe.json`、`execution-intent.safe.json`、逐ID intent／terminal及`RESULT.safe.json`。结果为21容器／15镜像／0卷，其他保留资源前后核对通过。

## 第二轮补充清理

再次按用户要求，定向删除了20个已停止或从未启动的旧 Easel B2/B3/AV 测试容器，以及已被新版替代的 `easel-b3-worker:local-20261104-once001` 镜像。删除前保存安全元数据和逐 ID 执行记录，失败记录仍保留为失败，不把历史结果改写成通过。没有强制删除、重启或使用全局 prune。

本轮容器29→9；Docker统计的镜像占用10.66→10.08 GB，容器68.67→4.739 MB，约减少644 MB；这是Docker逻辑占用变化，不等同于宿主立刻回收的物理空间。全部227个数据卷保留，9个保留容器的完整ID、镜像及运行状态逐项一致。原合成源PG仍为原PID62835；正在运行的两个portfolio容器、原中央预览及LobeHub未改动。当前B2、B3和AV所需镜像保留。

证据：`outputs/_run/docker-obsolete-tests-cleanup-20261104-once002/plan.safe.json`、逐ID intent／terminal以及`RESULT.safe.json`。本轮实际删除20容器／1镜像／0卷。

## 2026-11-05 再次核对与清理

重新读取本机实际 Docker 清单后，删除了已被 independently reviewed once005 构建取代、且没有任何容器引用的旧 `easel-b3-worker:local-20261104-once002` 镜像（完整 ID `sha256:81e6305be6381a0d12c0d7a7bc38f2d0fb2b922af306691aafdcba36045b16b4`）。旧冷启动验收材料继续作为历史证据保存；不再保留旧镜像，也不将它的验收资格转移给新版。当前 once005 仍仅构建验收通过，冷启动验收尚待完成。

仅执行一次精确 ID 的 `image rm --no-prune`，exit 0；未使用 force、全局 prune、数据卷清理或 Docker 重启。本轮没有发现可额外删除的已结束 Easel 临时测试容器：原构建辅助容器此前已删除，当前保留 9 个容器（3 个运行中），包含仍需使用的合成源 PostgreSQL、原中央预览及其他产品服务。所有保留容器的完整安全元数据、状态、其余镜像及全部 227 个数据卷集合均与删除前相同。

Docker 镜像逻辑占用 10.60 → 10.08 GB，减少约 520 MB；宿主可用空间复核为 24,055,115,776 字节（约 22.40 GiB）。不把宿主空间变化全部归因于这一项删除。加上前两轮定向清理，累计删除 41 个旧测试容器及 17 个旧测试镜像；本轮新增 0 容器／1 镜像／0 卷。无标签且归属其他产品或仍需验证的镜像未按“无标签”直接判为废弃测试镜像。

本轮证据：`outputs/_run/docker-obsolete-tests-cleanup-20261105-once003/` 的删除前安全清单、精确计划、删除 intent／terminal 和 `RESULT.safe.json`。

## 后续开发临时资源及时清理

R3 生命周期验证期间新增的失败冷启动容器 once001 和成功构建容器 once006，均已在归档、独立复审与新鲜身份／终态检查后普通删除；不使用 force 或 `-v`。旧 once005 worker 镜像 `2bfa53d9...` 在新版 once006 镜像实际构建验收通过、确认零容器引用后，以 `image rm --no-prune` 删除。失败记录继续保留为失败，不因清理或后续源码修复改写。

最新复核：保留9个容器（3个运行）、227个数据卷，新镜像4536d857...保留用于下一轮冷启动；镜像逻辑占用10.08GB，本机可用23,691,841,536字节（约22.06GiB）。源合成PG仍为原PID62835；容器身份／稳定运行状态／健康状态与清理前一致，健康检查日志自然更新。未删除其他镜像或数据卷，也未重启Docker或服务。

本次后续清理证据位于 `b3-worker-artifact-preparation-20261104/cold-worker-20261105-once001/ROOT-FAILED-ARCHIVE-AND-CLEANUP.safe.json` 和 `public-worker-once006/ROOT-BUILD-ACCEPTANCE-AND-CLEANUP.safe.json`、`ROOT-old-image-cleanup.RESULT.safe.json`。

## 冷启动验收后即时清理（cold002）

新 worker 的 cold002 实际冷启动已在原会话82825正常 exit0，并经独立保存证据复核通过。Root 新鲜检查其精确 CID、exited0/PID0、无执行中子进程及保留资源后，仅以普通 container rm 删除 a12181cd...，没有 force、-v 或 prune。清理后9个保留容器（3个运行中）的身份、稳定进程状态和健康状态一致，227个数据卷完整集合不变；原合成源PG仍为原PID62835、原启动时间、restart0。

镜像4536d857...继续用于后续真实隔离目标验证。Docker镜像逻辑占用10.08GB，容器4.739MB，卷14.6GB；宿主可用23,685,894,144字节（约22.06GiB），不把宿主变化全部归因于此次删除。未删除数据卷或其他产品资源。

证据：cold-worker-20261105-once002/ROOT-fresh-pre-rm.safe.json（591126）、普通删除7d7265、ROOT-COLD-COMPONENT-ACCEPTANCE-AND-CLEANUP.safe.json（c4137b）。首次只读清单405f69因缺失Health字段报错，未执行删除；修正检查模板后才执行上述普通删除。

## once010 失败验收资源清理

这轮ATTEST在请求发送前耗尽原60秒窗口，结果保留FAILED；77条执行日志、16条目标准备日志及RETAINED处置已独立复核（9a83a26f...）。Root确认原caller无活动命令后，原owner完成本次自有合成源资源关闭；两个新PG正常停止，parser已自然exit0，worker保留原exit1事实。四个精确容器全部确认exited/PID0/无exec后普通删除（40d311），未使用force或-v。

删除后仍为9容器／3运行，原源PG保持PID62835／原启动时间／restart0，其他保留容器稳定状态及健康状态一致。全部229卷保留，其中2个为本轮新建的失败目标卷；原227卷未删除。当前worker镜像4536d857...未改变，宿主可用23,576,391,680字节（约21.96GiB）。已归档的失败材料继续保留，不作为成功结果。

专用监督器3791因已审hold循环捕获TERM而永久空等；Root重新核对精确PID／PPID／完整argv／nonce及无子进程、四个临时容器已删除后，仅对这个空闲测试监督器发送SIGKILL（dd28a4）。原Tool84895同一会话返回137（3abc94），记录为人工释放后的失败终态，不冒充自然exit0。不涉及Docker服务重启或其他进程。

证据：b3-execution-owner-qualification-20261105-once010/ROOT-FAILED-ARCHIVE-AND-RESOURCE-CLEANUP.safe.json、ROOT-IDLE-SUPERVISOR-RELEASE-INTENT.safe.json、ROOT-ORIGINAL-SESSION-TERMINAL.safe.json。

## 最新构建辅助容器清理（image007）

最新 image007 已完成独立构建证据复核。新鲜检查确认构建容器 e48d5157...为 exited0／PID0／无执行中命令后，普通删除该精确容器（c993d8）；后检 f45717 确认源 PG 仍为 PID62835，9 个保留容器的身份、稳定进程状态及健康状态一致，全部229个数据卷集合不变。没有 force、-v、prune、重启或远程操作。

本轮继续核对本机清单（82f548、214fd5）：保留9个容器、3个运行中；image007 与旧 image006 暂时同时保留，待新镜像独立冷启动验证通过后，再定向删除无引用的旧镜像。713MB 是各镜像标称大小，不能据此宣称可回收空间。当前构建通过仅证明 built-not-attested，不等同于完整恢复通过。

证据：`outputs/_run/b3-worker-artifact-preparation-20261104/public-worker-once007/ROOT-BUILD-ACCEPTANCE-AND-CLEANUP.safe.json`（SHA256 ed17e872bb4ce168b85e4a6d72241f7429a19b24cedc9e5373258ed3b86a839e）。

## image007 冷启动后清理

cold003 原会话51926正常exit0，完整保存证据独立复审C0/I0/M0（90be298b...）。新鲜删除前检查 b37131 通过后，普通删除本次精确临时容器9141d3db...（74d0bd）；后检2c4bc6确认其余9个容器稳定身份／状态／健康状态、原源PG PID62835和229卷完整集合不变。首次只读检查1c634f因六个旧容器缺少可选Health字段失败，诊断287a84后改用index安全读取，再通过检查才删除；失败脚本和记录保留，没有提前删除或重跑工作任务。

新版image007冷启动组件验收通过后，旧image006（完整ID4536d85782bd8386142beecf311eccd68faa55d92711daeccfcc6015c26a75d4）已无容器引用。新鲜检查902e0f通过后，用 `image rm --no-prune` 普通删除该精确镜像（4307ad）。后检f4edae确认镜像集合精确只减少此1项，其余镜像、9个容器、229个卷不变。无force、-v、全局prune、卷删除、重启或远程操作。

本次再删除2个临时容器（构建helper和cold003）及1个旧测试镜像。删除旧镜像前后Docker镜像逻辑占用10.60→10.08GB，约减少520MB；宿主可用23,326,052,352字节（约21.73GiB），不把宿主变化全部归于删除。当前worker镜像保留image007；旧image006不再宣称仍存在。所有旧失败材料继续保留为失败。

证据：`cold-worker-20261105-once003/ROOT-COLD-COMPONENT-ACCEPTANCE-AND-CLEANUP.safe.json`（71df53ba...）与 `ROOT-old-image006-cleanup.RESULT.safe.json`。

## 双目标验收完成后的临时容器清理（once011）

原任务85984正常exit0且独立实际复审通过后，新鲜停止检查88d7e1确认本轮专用资源无活动命令。仅正常停止两PG及parser（3548db／同会话58c17e exit0）；两worker已经exit0。新鲜删除前检查308b1b确认五容器全为exited／PID0／无exec，再普通删除这五个精确CID（1dae8b），没有force或-v。parser在显式stop后exit137，保留这一事实；不把其退出码改写为自然成功。

首次只读后检f8a370断言失败，未保存比较操作数，原因未确定；后续只读诊断bdd7b7没有ID／状态差异，全部五CID已不存在，卷集合不变。最终新鲜只读52302f再次确认9容器／3运行、其他9容器稳定身份及健康状态与原记录一致、原源PG PID62835及image007不变、231卷完整集合保留。没有重复停止或删除。新增的两卷为本次合成目标卷；原229卷未删除。

本段新增清理5容器／0镜像／0卷；连同最新构建helper、cold003及旧image006，本轮开发期间已及时清理7个临时容器和1个旧测试镜像。宿主最新可用23,222,763,520字节（约21.63GiB），不将全部变化归因于清理。正式服务、其他产品、用户历史与接口配置不在本轮操作范围。

证据：`outputs/_run/b3-execution-owner-qualification-20261105-once011/ROOT-ACTUAL-ACCEPTANCE-AND-CLEANUP.v2.safe.json`，以及fresh stop/preRM、只读诊断和最终post快照。


## 当前测试基础镜像再清理（once004）

2026-11-05 再次按用户要求核对本机 desktop-linux（本机 Unix socket）。当前已无额外已结束临时测试容器；9 个保留容器包含仍在使用的恢复验证源 PG、原中央预览及其他产品资源。

已定向删除无容器引用的两张公开测试基础镜像：旧 AMD64 Python `623674a14ef93d052bb9b0de4dbf5f28eb578a35263838941cc8aa43213c1487`，以及已构建进当前 worker 的 Python Trixie 基础镜像 `adbc7c33e0abc183557d1d14ce5eb5d261aaadff5451c81a8db636b3ebefcdf6`。使用一次普通 `image rm --no-prune`（725f02，exit0），不使用 force；当前完整 worker `1ed361fd...` 和 AV ARM64 基础镜像保留。未来重新构建需要重新取得已删的独立基础镜像，不把历史镜像可用性记录当作当前可用。

新鲜后检55517f通过：镜像ID集合精确仅少这两项；9个容器的ID、镜像、运行/PID/启动/重启/exec状态一致，仍3个运行；231卷完整集合不变。Docker镜像逻辑占用10.08→10.01GB，约减少70MB（共享层使标称185+202MB不能直接相加）。宿主可用23,240,269,824字节，约21.64GiB；该数含其他进程与Docker回收时序影响，不全归因于此次删除。

构建缓存描述显示主要为另一个作品集项目，本轮保留138项；未执行任何 prune、删除卷、停止/重启服务或访问远程。首次只读buildx模板因无InUse字段失败，修正显示字段后取得描述，未因此执行清理。

证据：`outputs/_run/docker-obsolete-tests-cleanup-20261105-once004/before-and-plan.safe.json` 与 `RESULT.safe.json`。本轮新增删除0容器／2镜像／0卷。


## 旧 B2 验收镜像退役（2026-11-05）

再次按用户要求核对后，普通删除无任何容器引用的旧 `easel-b2-t13:full020-a0cb31f4569645b7`，完整 ID `sha256:bb956b07042c3704a730d9f9cd799bad5e014475d4b3be2d934044add5b94f52`。当前 B3 使用独立 worker，旧 B2 历史报告和数据卷保留；此后不能声称该旧镜像仍可直接运行，未来重测需重新构建或取得精确材料。

本轮新增删除1镜像／0容器／0卷。Docker镜像逻辑占用10.01→9.802GB，减少约210MB；9个容器（3个运行）身份、镜像、状态、PID、启动时间、重启计数及规范化挂载信息不变，231个数据卷完整名称集合不变。当前仍需使用的B3 worker、AV ARM64 Python、合成源PG、旧中央预览与其他产品保留。没有停止服务、force、全局prune、卷删除或远程操作。

最初后检因Docker返回的lobe-searxng挂载数组顺序变化而失败；只读诊断证明是相同挂载的顺序差异，按Destination/Source规范化后全部安全元数据相同。原失败保留，没有重复删除。首次证据目录重名的脚本在任何Docker操作前退出。

证据：`outputs/_run/docker-obsolete-tests-cleanup-20261105-b5085e7fdba0/PLAN.safe.json`、`RESULT-RECOVERED.safe.json`、`RESULT.safe.json`。宿主当时可用18,634,403,840B，不能把宿主空间变化全部归因于本次删除。
