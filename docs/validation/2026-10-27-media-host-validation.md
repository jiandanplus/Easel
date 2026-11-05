# M4 图片 host 候选验证

本记录仅覆盖桌面中央媒体客户端、Pi 工具和合成供应商下的真实中央 PostgreSQL 闭环。生产模型配置、真实供应商、真实用户钱包、v22 打包与浏览器结果由主任务另行验收；本记录不代替它们。

## 候选边界

实现文件为 `easel/platform/media.py`、`media_records.py`、`easel/harnesses/pi.py`、`runtime/pi/worker.mjs`。沿用中央 M1.1 的公开路由，不读取 New API／供应商凭据；产品 access 和媒体 task token 保留在 Python host。共享树中的 M2／M3 不进入本阶段工具和候选后端包。

仅中央订阅、工作区写入、已接受 session／turn 才公布媒体工具。Pi 文本模型保持原配置，目录只投影已开放的 `image.generate`；动态 schema 的模型枚举来自该目录，默认优先 Flare。四个工具为 `create_media`、`get_media_job`、`cancel_media_job` 和 `download_media_artifact`，工具参数不包含 URL、Key、钱包或 scope。

每轮唯一的私有操作记录在 mint 与 job POST 各自派发前持久化并 fsync；工具 ID 与请求摘要固定，原始提示词不落记录。mint 未知与生成未知分开保存。重复调用、重启或另一个工具 ID 均不得产生第二笔生成 POST；未知只查询原 invocation＋operation。重新登录的同一业务 owner 可读取旧作业，新生成必须匹配原接受的完整 session／authorization epoch。

网络使用无代理继承、无重定向的 HTTPX AsyncClient；在 host 线程中通过 `asyncio.timeout` 对响应头和停顿读取施加总时间界限，体积独立受限。恢复的 10 秒网络预算贯穿当前身份、原 operation 和 job 读取。媒体客户端复用原中央账号验证、同 Scope、私有存储和序列化规则，但刷新 HTTP 也改用绝对时间界限；旧 CentralClient 实现未修改。只有明确 401 身份失效才清理原凭据，迟到旧 token 的失败不能删除新登录。

图片必须属于原 job，元数据、字节数、SHA、MIME、格式和 **1024×1024** 尺寸核对后完整解码。完成态作业、下载元数据及最终保存分别检查这个已公布的尺寸；原 operation 摘要不含 artifacts，必须继续 GET 原 job 验证详情。目录通过 NOFOLLOW 的 fd 固定，最终采用不覆盖已有文件的原子 link。网络、图片解码与临时写入不占账号文件锁；只有最终发布 link 与 fsync 在短账号锁及 stop 锁内，防止退出或中断后的迟到图片写入。

## 已执行验证

- **248 项通过，0 跳过，9.60 秒**：Pi adapter、工具、runtime、integration、central admission、媒体 host、中央账号 client 及恢复回归。包含 macOS `sandbox-exec`，实际 v21 包内 Python，以及 Node **24.22.1**／Pi **1.4.0** SDK。所有模型事件均为本机合成数据。
- **实现者尺寸 PostgreSQL 三例通过，2.08 秒**：`services/platform/tests/test_media_desktop_host_roundtrip.py`，中央代码引用新尺寸冻结 staging `/private/tmp/easel-media-m1.1-size-staging-20261027/src`。实际中央 HTTP、identity product token、media task token、账本、typed New API 请求头证据、产物登记与 host 下载均为真实代码；仅供应商 HTTP 为 MockTransport。该运行之后仅增加本机已接受 Scope 完整性拒绝守卫，最终 freeze 的独立 PostgreSQL 复验仍待审查者执行，不能据此标为独立验收通过。
- 成功路径：无供应商对象 ID、无 usage，依据一张完整验证图片结算 **1,000,000 microcredits**；下载得到全解码 PNG，重放不重复生成、不重复扣点。
- 未知路径：供应商模拟超时，保持 **1,000,000 microcredits** 预留、actual 为 NULL；新 host 恢复仅 GET，原生成 POST、原 provider attempt 均各一笔。撤销原 session 后读取拒绝，原账本与记录不删除。
- 尺寸不符路径：实际 7×4 PNG 被新的中央 profile 拒绝，不登记可用 artifact，仍为 pending、actual NULL、保留原预留；重放只读原作业，不再请求供应商。早期两例 fixture 曾允许 7×4 成功，只证明当时的 HTTP／账本联通，是历史候选检查，**不作为当前尺寸一致性验收**。
- 安全负例覆盖：自定义模式／只读／无 turn 不访问中央媒体；同工具异请求、同 turn 新工具、并发 claim、损坏／符号链接记录；mint 超时；账号变化、logout 后迟到响应；下载 hash／内容错误、图片炸弹、路径越界、受保护目录、不覆盖原文件；慢流及停顿读取的绝对 deadline。
- 实际 macOS 沙箱验证：即使明确批准一次命令执行，旧生图脚本不继承 `NEW_API_KEY`，对本机模拟供应商的 curl 连接仍被 OS 拒绝，服务接受连接数为 0。订阅 prompt 在有／无媒体目录时均禁止旧脚本、自定义 Key 和本地绘图自动回退，自定义模式原配置与工具保持。

真 SDK fixture 先使用未修改的 v21 worker 观察新增媒体场景失败，再替换为当前源码副本通过。临时测试目录 `/private/tmp/easel-m4-sdk-20261027` 的 worker 与仓库源码 SHA256 同为 `fa122e93d0d9cffae32aac5f95724189c039ab22a705f09d74c125d57515342f`；Node 与 node_modules 仅引用 v21 原 payload，没有修改原软件包。新 runtime manifest 与离线 Pillow 依赖由主任务重新打包验证。

## 复验入口

```sh
rtk proxy env \
  EASEL_PI_TEST_PAYLOAD=/private/tmp/easel-m4-sdk-20261027 \
  EASEL_PI_TEST_RESOURCES=/Users/jiandan/Code/GitCode/Easel/outputs/_run/pi-portable-20261027-v21/Easel/Easel.app/Contents/Resources \
  .venv/bin/python -m pytest \
  tests/test_pi_adapter.py tests/test_pi_media_host.py tests/test_pi_tools.py \
  tests/test_pi_runtime.py tests/test_pi_integration.py tests/test_pi_central_admission.py \
  tests/test_central_media_host.py tests/test_central_platform_client.py \
  tests/test_central_execution_recovery.py -q
```

PG 验证使用 `services/platform/.venv/bin/python`，PYTHONPATH 优先 `/private/tmp/easel-media-m1.1-size-staging-20261027/src` 和仓库根目录，`EASEL_CENTRAL_TEST_DSN_FILE` 指向既有本机隔离测试库的私有文件。不在文档记录或复制 DSN；测试要求 literal loopback、`easel_s1_test` 数据库前缀，每次建立并回收独立随机 schema。该测试文件不进入后端 wheel。

没有执行生产发布、真实模型调用、付费操作、点数发放、commit、push 或合并。

## Root追加：历史分页恢复修复（最终候选）

独立审查复现：旧 owned() 借用最多50条的显示列表，导致第51次以后旧已付费 job 不能查询、取消或下载；旧快照冻结1138…已被此增量替代。Root 为51条旧记录、满容量后保留原键、扫描不完整及host在mint前拒绝新增先观察失败，再补完整有界ownership扫描及直接turn定位。

新claim在私有文件锁内先读原turn；总记录达到1000或目录扫描超界时，不创建新生成，不发送mint。全部历史继续保留；不自动删除/归档。owned()不使用显示分页，扫描不能完成时明确报不可查询；exact turn直接按固定hash读取。UI显示页仍最多50条；容量与扫描错误映射固定短中文，不泄露异常原文或误导用户查不存在的新任务。

独立复核已实际创建999条记录并并发两个fresh claim，只1个落盘达到1000，另1个容量拒绝；原claim及owned、exactturn仍可读取，crossowner拒绝。本机直接SDK/Pi/账号回归最终结果与最终freeze后独立PG见后续报告；先前248项结果仅属于P2前快照。Root当前216项定向跑过，但冻结前补充了错误标签常量，最终以新freeze后的相同回归为准。
