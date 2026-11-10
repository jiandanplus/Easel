# 桌面登录加固与 Pi 媒体恢复：独立审查

结论：冻结候选范围内没有剩余已知阻塞。独立回归 **454 passed、4 skipped、0 failed，14.05秒，exit0**，另补一个真实 Pi 1.4 SDK 的媒体未知状态恢复场景。九份产品/测试输入在运行前、运行后和报告生成时哈希全等，没有把实现者的326或root的391结果与本轮相加。

本轮不连接服务器、不读取或修改真实用户会话、不调用真实模型、不修改运行中的v22或f50图片服务。当前真实生图故障的因果是另外已验证的 API mount namespace EROFS，本次登录加固不能冒充该故障根因或已部署修复。

## 冻结对象

| 文件 | SHA256 |
|---|---|
| easel/harnesses/pi.py | `414642b4ba6714ea1f7f7ae12d4944c8688cd3e3fbb5e8e9b9005157a74677f1` |
| easel/platform/auth_refresh.py | `57a5773a21cee59b2657aadd39b8350ed38e80eedd8b65f3ed896f65d5b6284f` |
| easel/platform/central.py | `9849d356ea3b5038cb2341bbb76848a33d209e2527646970ae1ad39e95fae6cf` |
| easel/platform/media.py | `e320cb7e21d2cda56cb64ac7609b865bc6dccf7251ee8b1a957b87970bb00f4c` |
| tests/test_central_auth_hardening.py | `541096c0657a9a3aa7e3eabe2d90b9d4dc5785d31e051a91d45705a47f0a5e28` |
| tests/test_central_media_auth_rotation.py | `f1bfdb9960860520631f4e52131384b47d72c717e3dafefdbbd93ec135e7d6b8` |
| tests/test_central_media_host.py | `0d8415066595ddd83fda19a0f2c0a7c3280f6802d2d98c980384545b666472c6` |
| tests/test_central_platform_web.py | `3ef7ab501b3516310caa247150ea4e0dca1ac1547fe5a552eec9705bc8f829b4` |
| tests/test_pi_media_host.py | `c6ab7f1dad492fd96c029e9e9791713342c2850762d9c2df4a75cf833be93959` |

候选产品补丁仍为 `7996ab9d27e2121e12e3fd05f098d9f494c48e40ec5056b076814157da8f1902`。真实 SDK fixture 使用 pi-agent-core/pi-ai 1.4.0，worker SHA `fa122e93d0d9cffae32aac5f95724189c039ab22a705f09d74c125d57515342f` 与当前源逐字一致。

## 复核要点

- auth_refresh.py 在任何 refresh POST 之前，以既有会话文件锁写入 sending 围栏；SessionStore 原子替换与文件fsync后再fsync父目录。围栏仅含原scope/origin/refresh的摘要和操作标识，不记录令牌。POST超时、截断、校验失败、账号变化或保存结果不确定时，原凭据与sending/uncertain围栏保留，下一客户端与重启后均拒绝自动提交旧refresh。
- 明确的 refresh endpoint 401（invalid_authentication/session_invalid/refresh_replayed）才清理该原会话；普通GET的access401本身不证明refresh失效。GET仅对明确access失败续期一次，再重试同一GET；已经刷新过的调用不会再刷第二次。订单或media job/task-token POST没有自动重发。
- MediaClient在无长时间凭据锁的GET之外，重新持锁核对完整scope、origin与使用的access token；迟到的旧401不能续期或覆盖另一账号。并发其他调用已经轮转时沿当前已保存凭据读取。后续snapshot/catalog/mint同步新access token，task-token401不清理中央产品auth。
- refresh、GET重试沿同一绝对deadline，不重新获得完整时间预算。登录状态存在围栏时报告未认证/待确认，不伪装可用。明确退出或相符原围栏下重新发起登录可清除本地unknown状态；begin拒绝用过期围栏删除一个不同的新登录。
- 诊断只含固定或校验后的code/status/path/scope_hash/time五字段，路径去query，不记录异常原文、body、Key、access/refresh值或其摘要。
- Pi修改仅让中央媒体工具把已redact的完整结果JSON放入model-visible content，details仍保留元数据；普通工具保持原text投影。原operation_key可被get_media_job使用，不引入第二个生成请求。Host仍执行归属检查、每轮生成上限和unknown只查GET；任务/供应商凭据没有进入可见JSON，MAX_LINE仍由原send合同限制。

## 独立实跑

运行私有runner `/private/tmp/easel-desktop-auth-independent-review-20261027/run_review.py`，核对全部输入后执行：全部 `tests/test_central*.py`；Pi adapter、media_host、tools、runtime、integration、central_admission六模块；独立三个围栏故障用例。启用既有已审Pi SDK payload与macOS sandbox，使用临时目录和合成凭据。

四个skip均为显式旧Codex验收：test_central_execution_native.py:92、test_central_execution_stream.py:357需要EASEL_TEST_CODEX_BINARY；test_central_execution_stream.py:362及test_central_native_protocol.py:14需要EASEL_TEST_CODEX_NODE。未跳过本轮新登录加固、实际loopback响应丢失、Pi SDK或macOS沙箱验证。

额外独立故障注入验证：

1. 围栏提交后发生SystemExit，重启客户端仍只看到原sending，不重放refresh；明确本地logout不发送旧refresh。
2. 围栏文件replace后的父目录fsync失败，POST数量为零；下一客户端仍闭锁。
3. 新auth已rename但父目录fsync失败，当前文件确为新凭据时重启使用新token；原refresh仍只提交一次。保守错误没有回滚到旧凭据。

另用真实 Pi SDK 执行 `/private/tmp/easel-desktop-auth-independent-review-20261027/sdk_media_recovery.mjs`：第一次create工具返回isError及content JSON；SDK下一次模型上下文实际读出operation_key并调用get_media_job；最后从查询结果读到pending_reconciliation和actual=NULL。create=1、query=1、三个合成模型事件流、零网络请求。这个额外scenario不是454的子测试，不把其次数再作为业务测试总数累加。

## 证据与限制

本机完整日志和输入回执在 `/private/tmp/easel-desktop-auth-independent-review-20261027/`。只报告源代码与合成/loopback/真实SDK边界结果，不代表v23已安装、激活或完成真实模型验收。未知refresh时的退出是明确的本地退出，未宣称服务端远程会话已撤销。原Flare pending/hold与后续服务器权限修复、可信结果补录及非空DR均是独立工作，本轮不推进DR或改旧账。

安全摘要SHA256：`e8dea780f2c42298d30ddebc45f24b7805e52f232e6cc643e7e25aae19e4a260`。
