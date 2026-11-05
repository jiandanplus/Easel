# B3 worker 入口源码与公共运行库准备（2026-11-04）

当前分支 codex/ui-redesign。本轮完成 worker ATTEST 入口代码、相关模拟测试和离线公共运行库准备；没有构建或启动实际 worker，没有执行 pg_restore 或恢复业务数据库。完整 B3 与最终桌面交付仍未完成。没有提交、推送或合并主分支。

## Worker 源码

新增 execution_worker.py：固定 Linux 入口、单帧 stdin/EOF、原始本地时限、受信构建清单和实际文件／模块／原生映射来源校验，以及自身 UID/caps/NNP/netns/lo/cgroup 限制检查。它只输出 v2 绑定的自身观察；宿主仍须通过原始 live handles 独立证明 worker/image/CID 与目标双方 namespace。

首轮独立审查发现错误输出直接阻塞写 FD2 可越过时限。已通过 RED/GREEN 修复为非阻塞、最多一次的固定诊断；设置失败或 EAGAIN 不重试、不等待、不续期。Root 独立复跑24项模拟测试通过，0.082s；修正的规格／质量复审批准。源码 b6a5c1dcaf34d63c76bf0b7a0d39ec72d97f6e4911438be067952a7072c6818a，测试20c47c35d4662b9b08634d7834163525814776cbb5aa7cf212a40800fe28a945。证据 outputs/_run/b3-execution-worker-20261104/fix。

## 公共输入

固定官方 Python3.12.13slimtrixie ARM64 manifest adbc7c33e0abc183557d1d14ce5eb5d261aaadff5451c81a8db636b3ebefcdf6，单次拉取与本地内容核对完成，无隐式 VOLUME。当前 Docker containerd 的 imageId 是 platform manifest，不能误当成 OCI config digest；初次 Root 假设错误和只读修正记录保留。选择与原生 wheel 平台兼容性参考 [官方镜像元数据](https://hub.docker.com/v2/repositories/library/python/tags/3.12.13-slim-trixie) 和 [Debian Trixie libc6](https://packages.debian.org/trixie/libc6)，实际安装／链接仍须后续验证。

从当前 pyproject/uv.lock 精确解析27个运行依赖，排除6个测试专用依赖。21个通用 wheel 复用原已校验缓存，6个同版本ARM64 wheel来自官方PyPI，逐一核对SHA、长度、版本和依赖元数据。共22912142字节；未安装依赖。wheelmanifest3042a68ce9a0df8976b80de5202dd40a95113e3ec202efcdece71a3a5dcd4179，requirements562a0840e7e28ba6c192ba6a06ebbcb4652540c33a93f45e3abb4608ac3ef2fb。

现有官方PG ARM64镜像按固定摘要导出为公共镜像归档，不是运行容器／数据卷导出。164720640字节，c8f46ee3f95d822edee6a1056ac3484e410464e3dc0d260f9f3f466afd6827d3，14个gzip层。原PG工具265904字节，7831a506282d19fa0a89e020c8a522aa493692e6738fe022e114809b3c0abce2，与目标工具pin一致。

## 完整原生库准备与真实验证

新公开文件材料化helper先核原始FD完整SHA，再核OCI/config/blob/gzip/diffID与层顺序；仅输出固定PG工具及完整multiarch库树，按虚拟路径解析和重定位别名。最终输出核验位于所有layout／fsync之后，核对每个真实目录的精确子项，以及保留的原marker/layoutFD、命名绑定和实际字节。独立审查的两项最终核验缺口已修复，Root34项合成测试通过0.139s，规格／质量复审批准。

首次真实host运行在mkdir失败并保留。独立诊断证明Linux的Perl Sys/sys两个目录被当前Mac文件系统映射为同一inode；未改名、丢弃文件或复用失败目标。随后把同一已审helper及相同公共归档放入新隔离Linux准备容器，1CPU/1GiB含swap/PID128、networknone、capdropALL/NNP、无端口／宿主绑定／数据卷；UID0和可写根仅用于公共安装准备。原工具会话28896退出0，容器实际exit0/PID0/无OOM／exec／重启。

Root在host上用标准tarfile直接重建原14层的全部1760条布局行，不调用候选helper派生预期；逐项比较导出TAR的所有1426个普通文件字节／模式、86个别名及目录。原文件共292521998字节，1768个导出成员含8个scaffold目录。Sys/sys均保留，未把Linux命名空间解压到Mac。

公共运行库TAR293918720字节，SHA256 eb49d7d7427c7544ddb332ee022c678046b84e501fc2d3d52bb11f261b37432a；layoutSHA11de769b44090eb367bf2b42f6260393ec799b549e057077d7f9aaa6aa77e32f。公共实际证据独立审查批准，实际执行与字节观察明确归于Root；审查者只核安全元数据及其内部一致性。

审查归档后，按既有无用测试资源清理授权仅删除该已退出公共准备容器9d9e3d...；docker rm退出0，独立typedinspect确认原CID不存在。未force/prune，镜像、数据卷、导出包、原始归档和host失败现场保留。原专用源PG保持原PID62835运行。终态raw比较初次误拒已知OomKillDisable创建false／终态null，精确只读诊断后记录该差异，未重跑；缺失检查初次字符串前缀假设也保留，实际daemon明确返回原CID不存在。

## 待完成

仍需构建当前平台wheel与不可变worker镜像、独立安装来源／原生链接验证、宿主执行所有权和闭合graph接入、真实双端namespace证明。之后执行已批准bootstrap交接、角色／凭据、全量SQL与媒体恢复、新控制CAS、实际只读应用、完整矩阵及制品回归。原生库准备不是worker运行、模型／扣点、SQL恢复、生产部署或桌面发行通过。

后续镜像构建须从原始公共输入保留原生库相关的版权／许可材料与来源记录。最终库放在私有PG前缀，避免覆盖Python底座的库；固定原始loader/tool/dependency链仍要实际验证。运行目录需明确0755/root0:0以供UID10001访问，构建manifest和原始wheel必须留存且只读。完整TAR保持单文件处理并校验SHA，不能再次解压到当前大小写不敏感的host目录。

证据总目录：outputs/_run/b3-worker-artifact-preparation-20261104；PUBLIC-ACTUAL-REVIEW.safe.md、ROOT-independent-runtime-verification.safe.json、linux-public-terminal.safe.json、linux-public-removed.safe.json。当前UI未新增说明文字，登录／历史／自定义接口没有被本轮操作。
