# 文本请求的 New API 关联头补齐

本地最小补丁已验证，未部署。它修复后续文本请求缺少网关关联 ID 的可观测性问题，不修复或推断既有 HTTP 400 的业务根因，也不回填旧关联、重试原请求、释放预留或改变 `actual=null`。

## 改动

`execution_runtime.py` 保留原有选择顺序：先 `x-request-id`，再 `request-id`；两者都没有非空值时，追加 `x-oneapi-request-id`。选择后的值继续经过原来的 `correlation_id` 校验，正则、256 字符上限、HTTP 状态及关联写入时机均未改变。原优先头存在但格式无效时仍丢弃该值，不跳到其他头。

只保存经验证的 opaque ID，不保存响应 body／message、任意 header、供应商 Key 或请求正文。资金、幂等、未知提交、流式与恢复代码没有变化。生产源码的补丁只是一处取值表达式；Incho 的 16 文件候选逐 SHA 未变化。

[New API 官方 RequestId 中间件](https://github.com/quantumnous/new-api/blob/main/middleware/request-id.go) 的公开文档确认其响应头为 `X-Oneapi-Request-Id`。文档通过 Context7 `/quantumnous/new-api` 查询，未提交任何真实请求编号、用户内容或密钥；该公开代码不能替代当前实例的独立观察。

## 验证

首先通过合成 HTTP 400 和真实本机 PostgreSQL 复现：运行时已记录 `http_status=400`，但新头未被识别，`http_request_id=null`，1 项红测。应用最小补丁后，直接 Runtime→DB 验证通过。

最后运行全部 16 个 `test_execution*.py` 测试文件，**220 passed、0 skipped、0 failed，37.15 秒，退出码 0**，唯一工具会话 `91232`。206 个中央源码／SQL／测试输入前后逐 SHA 一致。供应商 HTTP 均为受控 fixture，PG 仅使用原专用本机库的新随机 schema，没有真实模型请求、SSH 或生产写入。

覆盖的新门槛包括：

- HTTP 400 的合法 New API opaque header 在报错之前持久化，HTTP 状态保持 400；原请求正文、响应错误、Key 和额外 header 不进入关联或尝试记录。
- 原两个头的优先级、空值语义和无效优先头处理保持；合法 256 字符值可保存，257 字符、空格、URL、Bearer 文本、逗号、等号及换行值均不保存。
- 未知失败继续 `actual=null`、保留正数预留，没有结果、终态或结算收据；同一操作再次访问和恢复只查已有状态，供应商 POST 总数仍为 1。
- HTTP 200 JSON 和 SSE 仍依据真实合成 usage 精确结算，重放同操作不再发 POST；原隔离、流式、中断、证据和核算测试继续通过。

红测和中间 18 项绿色收据均保留；最终 220 已包含新增测试，不将计数相加。这是文本执行测试范围，不代替完整中央新候选、真实供应商或生产验收。

## 固定证据

目录为 `/private/tmp/easel-text-newapi-correlation-20261028/`。

| 文件 | SHA-256 |
| --- | --- |
| `execution_runtime.py` | `60b803c0c1de11c69cf8f0a4235c58a3eb9b83477d2b82c38596b72cd83d17c7` |
| `test_execution_newapi_correlation.py` | `380c0edb63668df5a0d1ec2be5ed39a5a29706455892ea5b2b3690e934faf5a1` |
| `runtime-only.patch` | `3abf6fd4ec7d737d889eeb8cecf9dad11a654ee86272244a3e4564375c8c2814` |
| `candidate-manifest.json` | `9c272e326cc507c50e7a15d5303a03b9bf3410162333a3ecefcf9a2d0444a23c` |
| `final-execution.log` | `c3ddcdac49b61223ae27afe421477411440ec58c73d8c452ba08f4481cdb0eec` |
| `final-execution.junit.xml` | `4ddaf229a2b8e6ce281f4346f5249690bba85d47559ae26c4383b7f4f1792161` |
| `inputs-before.json`／`inputs-after.json` | `cf389054d6f0415c9d0f17bb609aad8b66ec4d0e92c2f7bf22548e3ffc45e6e8` |
| `execution-source-snapshot.tar.gz` | `101d7763a597f98daaafed74c7f074c91753d14bd36a6cb875ffdd49118d8b0b` |
