# 备份目录损坏输入的受控拒绝修复

2026-11-03，`codex/ui-redesign`。本次推进剩余交付中的备份恢复校验，不改变前端、生产服务或历史验收结论。

## 问题与修复

实际本地反例证明：目录中的空行可使 `validate_profile_catalog` 抛出 `IndexError`；身份库目录在校验固定目录哈希前就索引列信息，`validate_source_facts` 也会抛出同类异常。空值和字典行另可导致 `TypeError`／`KeyError`。它们均应作为无效备份材料拒绝，不能绕出统一的 `B2Error` 处理。

修复只做两处调整：目录校验入口将明确的数据结构异常转换为无明文内容的 `B2Error`；身份库先验证固定目录，再提取表／列信息。原目录摘要算法、三个 `CATALOG_PINS`、迁移版本、身份库镜像、SQL 固定值与完整库存要求不变。不捕获 `BaseException`、取消或系统退出。

已从历史冻结公开 wheel（SHA `c23cb4cb555e24f96c509abec649fd19fa1e750c2e83b7861d05286eb3a1a6dd`）只提取这两个源码成员，与当前文件逐行比较；差异恰为上述异常处理和验证顺序。原始公开成员及 15／18 行 diff 保存在本次结果目录，不替换旧冻结包。这个对照证明修改范围，不能替代新版本真实捕获验收。

## 本轮实际验证

- RED：新增 20 个反例，修复前全部失败；覆盖空目录行、空值、字典行、截断类型／触发器记录，以及 public／Logto 身份库的三种截断列记录。
- GREEN：profiles＋source_facts 两文件 **61 passed，0.07 秒**。
- 相邻回归：profiles＋source_facts＋bundles **65 passed，0.10 秒**。
- 扩展认证归档自检：上述三组＋self_verify **80 passed，1.13 秒**。

扩展回归第一次因原平台 venv 缺少 `nacl` 在收集时失败；直接改用旧依赖 venv 又因缺少 `psycopg` 失败。最终仅在本次进程的 `PYTHONPATH` 加入已有隔离依赖目录，用原平台 Python 完成 80 项。两次环境失败不记为通过，没有安装或下载软件。

最终命令（项目根目录）：

```sh
rtk proxy env PYTHONPATH=services/platform/src:services/platform/scripts:/private/tmp/easel-b2-dependencies-20261030/venv/lib/python3.12/site-packages services/platform/.venv/bin/python -B -m pytest -q services/platform/tests/test_media_backup_b2_profiles.py services/platform/tests/test_media_backup_b2_source_facts.py services/platform/tests/test_media_backup_b2_bundles.py services/platform/tests/test_media_backup_b2_self_verify.py
```

## 范围与交付边界

以上为当前源码的纯本地校验与合成认证归档测试，不是新的真实 PostgreSQL 捕获、服务器来源事实、完整恢复或生产备份验收。self_verify 中原结构隔离夹具的事实校验替身不构成完整 source-facts 闭环。

原 F2 进程和 PG 组件的历史证据继续对应原冻结代码；本轮改动后，不将它们当作新版本完整通过的依据，也不改写其冻结材料。未运行 Docker、真实数据查询、供应商请求或资金操作。未打包、激活、签名、公证、提交、推送或合并。原 17868 的登录、历史和接口配置未改。

独立审查另见 [目录校验代码审查](2026-11-03-backup-catalog-review.md)。最终四文件 SHA 与范围记录保存在 `outputs/_run/backup-catalog-validation-20261103/RESULT.safe.json`。
