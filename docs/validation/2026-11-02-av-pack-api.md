# AV 主机固定连接与单次请求：Task2B2 验证记录

2026-11-02，当前分支 `codex/ui-redesign`。新增 `pack_api.py`，提供固定 Unix socket、固定镜像/API 路由及有限状态客户端；实际调用方只能选择规定的操作枚举。

创建、启动、stdout 和 artifact 请求均先消耗尝试次数，失败后禁止重试。创建返回的完整容器 ID 单独保存，不能被调用方修改响应字典后替换。日志读取要求同一容器已正常退出、无 OOM／重启；artifact 还要求 stdout 已由可信控制器完成语义验证。每次 I/O 和清理使用同一个原始截止时间，不重新计时。

本次独立复审发现并关闭一项 P2：socket 清理锁的获取中断会跳过剩余 timer 清理；锁退出中断可能丢失已转移的局部 socket 句柄。修复后，局部句柄通过 finally 保证尝试关闭，调用方仍继续有界 join／is_alive；失败保持固定错误和禁止重试状态。两个假锁用例在修改前得到2 failed／138 passed，分别暴露遗漏；修改后全部通过。原始代码、138/633结果、失败日志和审查记录保留。

Root fresh 运行：

```sh
rtk proxy services/platform/.venv/bin/python -I -B -m pytest --noconftest services/platform/tests/test_av_pack_api.py -q
```

结果 **140 passed in0.80s，exit0**。使用实际已审查 HTTP 接收器连接假 socket／timer／clock；没有真实 socket、线程、定时器或 Docker 任务。原633联合测试是修复前的历史结果，本次没有将其改写为新结果。Root核对全部10个受保护文件不变，并验证两个新文件及完整当前diff逐字节匹配。

当前源码 SHA256：`2d81101eed1a46ef17d91f616edd6309c50e31401b40278f11b0a309afb85765`。测试 SHA256：`d25fbca8eedb83c9de4e2f1ad184eba6981c2cc0daa62e6904e3244cda980cf7`。

独立复审 `outputs/_run/av-pack-api-20261102/TASK2B2-REVIEW-r2.safe.md`，规格 PASS、质量 PASS，原P2关闭。报告 SHA256：`b5b00785723df7aec7c1562191c46c0885c7ff17fa2f1b6c2eca318065b6fa73`。Root已完整读取首版源码／测试／报告、完整修订diff及复审报告。

下一项 Task2B3 仍须完成精确 recipe／容器配置、私有代码pin加载、stage／handle持久化、guardian、同boot时钟、完整控制器和最终落盘检查。真实资源限制、超时竞态、近16MiB留存、固定真实pack和源链尚待独立运行验收。所有原AV19、完整FFmpeg工具链、B2/B3/F2/恢复／发行门继续保留。本项没有前端、原17868工作台、账号／钱包、模型调用、远程或Git变更。
