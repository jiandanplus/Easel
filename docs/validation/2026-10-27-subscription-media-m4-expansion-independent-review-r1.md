# M4 独立复核 review-r1

日期：2026-10-27。结论：**在原 11 文件桌面/Pi 源码审查范围内批准；M4-R1 已关闭，未留下已确认阻断项。** 本次只定点复查 rate 白名单修复，承接前一轮独立审查，不扩展为发行、中央联调或生产验收。

`easel/platform/media_contracts.py:118–121` 现在显式构造公开 rate，仅保留 `version` 和 `meters`，每个 meter 又仅投影 `unit`、`basis`、`numerator`、`denominator`。已有全部 meter 验证未减弱；未知 rate 字段被剔除，不压扁费率，也不依赖字段名黑名单。

独立复核保留原缺陷复现脚本，新建 `/private/tmp/test_easel_m4_independent_review_r1.py`，将原“能够读到私有字段”的复现断言改成安全断言：目录的 rate 精确等于原公开 rate；实际 `PiAdapter._run` 的 JSONL stdin 中没有合成 provider/owner/internal-note 值。再定点运行作者的目录和 Pi stdin 两条回归，确认双 meter 原目录完整保留。

执行命令：

```text
rtk proxy .venv/bin/python -m pytest -q /private/tmp/test_easel_m4_independent_review_r1.py tests/test_media_expansion.py::test_catalog_projects_rate_contract_without_nested_private_fields tests/test_pi_media_host.py::test_pi_stdin_never_contains_unpublished_rate_fields
```

结果：**4 passed / 0.16 s**，其中 2 条独立安全断言、2 条作者回归。本次未重复 259 项全套、实际 Pi SDK 或 PostgreSQL。测试仅合成账号、临时目录和 MockTransport；真实 PiAdapter 序列化到本地 Python 测试子进程，未连接任何实际模型端点。

11 个文件审前/审后 SHA 与 revision `review-r1` 冻结清单均一致。核心合同 SHA256：`050d9e3b327a37f3880d5744e3c6678834b09ca2d028dff2358aeeee7220b298`。新 patch `/private/tmp/easel-m4-expansion-freeze-20261027-review-r1.patch` SHA256：`d3e0c7bd5e8528ca33021fc85a1817329c024172dcf3b26189925bf2699caa0e`；仍为 untracked 完整快照，不是 v23 增量 diff。全文件 pre/post 和独立脚本 SHA 见同名 `.safe.json`。

旧独立审查报告和旧复现脚本保留，描述旧冻结候选，不适用于修复后的 review-r1 放行状态。

本次没有改动候选源码、services/platform、真实配置或生产；没有读真实凭据、真实 API/PG、SSH、build/package、部署或 commit。中央真实 ASGI/PG→新 host 联调仍由独立工作流证明，本报告未验证该结果。真实供应商可用性与产品上线仍不在批准范围。
