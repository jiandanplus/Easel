# API 图片目录写例外 helper：独立复核

限定范围内没有剩余已知阻塞。独立通读固定helper和新增测试，并实际运行 **45 passed、0 skipped，0.16秒，exit0**（新28＋原release/cutover17）。helper SHA `dee6c912a57df4a2659e8c2fcd205dd64e3754daefc4e429e94b2d1077c5ac6d`，测试 SHA `b2ad290e896e33a688f4a66b61f9d8e336f1c6b80a6fa8ffdab1697a49e1bdfc`，原ff9/32ba依赖保持冻结。本review没有server/SSH/provider操作。

固定变更仅在 API 的原 `90-m1-images.conf` 末尾追加 `ReadWritePaths=/var/lib/easel-central/media`。保留 ProtectSystem=strict，不变更console、env、media配置、f50候选或016迁移。plan输出仅固定路径、哈希、计数与动作合同，不输出业务Key、DSN、账号ID、数据库用户、原配置和进程argv。

执行必须先匹配脚本及root私有0600 plan原始字节SHA，再匹配全部输入文件、原API PID与release。0active并非锁；helper先保存原字节，停具名API，再确认inactive/MainPID0与无活动任务，比较非终态任务集合和credit财务完整摘要。只有窗口仍匹配才原子写dropin/reload/start API。若stop期间产生新任务、pending变化或资金变化，原文件未变且所有旧输入仍准确时才恢复旧API。不会为了准入删除或释放旧pending。

最终探针进入实际API MainPID mount namespace，降到服务UID/GID，以随机O_EXCL/NOFOLLOW0600文件执行write/fsync/unlink；同时验证媒体挂载可写、/etc与release挂载只读且不能写，并复核进程未在探针期间变化。仅宿主chown/chmod或目录存在不足以成功。更改文件后的异常保留备份和失败回执，不盲目回滚其他操作人的改动。

root在本review过程中另行报告已按批准维护窗口执行成功：API PID 246906→278494，媒体正探针成功且删除，/etc与release负探针通过；其余7配置hash及元数据、console原PID、financial/nonterminal摘要不变。**这些实际服务器结果来自root独立采集，本review只运行离线契约测试，不冒充自己的SSH验证。**

原Flare仍为pending、held=1,000,000微点、actual=NULL。目录写权限修复不会自动补录结果或核算未知费用。旧68/49df DR绑定旧90字节，不覆盖此次变更；有pending时不得删记录凑非空DR准入。本轮没有推进DR。

安全摘要 SHA256：`6ed3884b8904b198b895e0c8acc280f73c62fbf35f71753947e5a986be5c931a`。
