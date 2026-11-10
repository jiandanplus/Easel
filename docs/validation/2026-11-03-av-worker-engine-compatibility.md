# 原 worker 的 Engine 回显兼容验证

2026-11-03，`codex/ui-redesign`。仅修改原 worker 配置校验、执行器的 profile 源码哈希，以及两份对应测试。其他 14 个 AV 源码／测试文件保持不变。历史容器、失败记录、旧 stage 和既有冻结清单没有升级或执行。

## 修正内容

1. 镜像 descriptor 只接受固定的三字段形式，或相同内容加上精确的 `platform={architecture:arm64,os:linux,variant:v8}`。其他字段、错误值、缺失字段及类型混淆仍拒绝。完整 descriptor 保留在哈希中；两种形式之间的变化仍属于配置变化。
2. 请求继续固定 `OomKillDisable=False`。回显必须存在这个字段，且严格为 `False` 或 `None`。策略哈希只在副本上将这个字段的 `null` 归一为 `false`；新增 `configuration_raw_sha256` 保留归一化前的完整观察配置。输入不被修改。
3. 执行器生产代码只更新 `PROFILE_SHA`，继续认证实际 profile 文件。配置比较、最终完整事实比较、原有期限、资源、停止声明和来源标记均保留。

最终 profile SHA：`e04523a67281dd24fe66e4e2d168505fe5e5b314c4f3438dba69588e25cde3c9`。最终 executor SHA：`a9084f86e6b15d276722c0c3ebf9a607f908976fbe86616b9ccb10123bb3d2db`。

## 关联验证

覆盖命令同时运行 profile、executor 和 capacity probe 三份测试：

```text
rtk proxy services/platform/.venv/bin/python -I -B -m pytest --noconftest services/platform/tests/test_av_pack_profile.py services/platform/tests/test_av_pack_executor.py services/platform/tests/test_av_capacity_probe.py -q
```

作者在原源码／原哈希上运行新增测试，得到 **5 failed、261 passed**，有效 descriptor 和 false／null 路径产生预期的准入失败；不是旧源码哈希不匹配导致的假失败。修正后作者运行得到 **267 passed in 27.45s**。

Root 独立新鲜运行同一覆盖命令，得到 **267 passed in 29.71s，exit 0**。四个 live 文件、四个冻结副本、14 个受保护文件、冻结包 SHA 清单及完整四文件 diff 均已核对。执行器的生产 diff 被额外断言为仅替换 profile 哈希字面量。

新回归经过实际 validator 和 controller，使用合成 API 保持精确 descriptor 不变，在 CREATED 回显 False、START 后回显 None，验证一次正常的 injected 回执、原始哈希变化、策略哈希相同、正常回收及无重试。另一路在保存产物后改变原始表示，证明原有最终完整事实比较仍会拒绝并记录 abort。原 `guardian_main` 的合成循环也覆盖 running／exited None 与 created False 的比较。测试具有资源调用探针及测试后计数检查；没有运行真实 Docker、线程、进程、FFI 或供应商请求。

独立源码审查完成：**规格符合性 PASS、代码质量 PASS，无需修改的具体阻断项**。审阅者独立核对完整四文件变更、live／冻结副本、14 个受保护文件及作者完整 SHA 清单，读取作者和 Root 的测试输出，没有自行执行测试或原生操作。报告 SHA：`f34bc32e41145a1b921d1ca59bece2a075be3ab17efd46e2475239fcc5c72750`。

## 仍未完成的运行门

本次证明源码／合成协议兼容。它没有关闭 [精确日志容量验证](2026-11-03-av-capacity.md) 的未确定失败，也没有执行原 worker／guardian 原生控制、产物保留、真实源码 pack 解码、完整工具链、生产备份恢复或发行验证。旧 stage 绑定旧源码，不能将新源码通过自动视为旧 stage 可执行。

原工作台、账号、历史、自定义／订阅接口和钱包未在本批修改；没有前端解释文字、新打包、签名、公证、commit、push 或合并。

[实现报告](../../outputs/_run/av-worker-compat-20261103/report.md) · [完整变更](../../outputs/_run/av-worker-compat-20261103/full4.diff) · [Root 新鲜结果](../../outputs/_run/av-worker-compat-20261103/ROOT-FRESH-COVERING3.safe.json) · [原始测试输出](../../outputs/_run/av-worker-compat-20261103/ROOT-FRESH-COVERING3.raw.txt) · [独立审查](../../outputs/_run/av-worker-compat-20261103/INDEPENDENT-REVIEW.safe.md)。
