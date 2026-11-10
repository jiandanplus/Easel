# M0 / M1 媒体独立财务与安全审查

日期：2026-10-27。分支：`codex/ui-redesign`。审查者与实现者分开；本次未编辑实现文件、发布生产媒体模型、访问真实供应商或运行生产数据库写入。

## 结论与证据范围

本次冻结快照在**默认关闭的 M0 合同和 M1 同步图片代码交付**范围内，没有剩余已知阻塞问题。三个实质审查问题已经修复并独立复验。此结论不等于媒体已经适合在生产启用，也不覆盖 M2–M5、真实供应商、桌面媒体工具或商业费率。

独立运行全部七个媒体测试模块：**94 passed in 10.30s**。其中包含实际本机隔离 PostgreSQL、随机测试 schema、真实图片像素解码、HTTP 协议 fixture、权限角色和共享账本测试；不把 94 项都称作 PostgreSQL 集成，也不把 fixture 成功称作真实供应商成功。

```text
EASEL_CENTRAL_TEST_DSN_FILE=<本机既有隔离库的私有配置文件>
services/platform/.venv/bin/python -m pytest -q \
  services/platform/tests/test_media_contracts.py \
  services/platform/tests/test_media_credits.py \
  services/platform/tests/test_media_auth.py \
  services/platform/tests/test_media_artifacts.py \
  services/platform/tests/test_media_config.py \
  services/platform/tests/test_media_images_runtime.py \
  services/platform/tests/test_media_images_integration.py
```

## 已发现并关闭的问题

1. **P1：测试点数可能访问真实供应商。** 原 `media_config.origin` 允许 `mode=test` 使用任意 HTTPS 地址；独立构造公共 HTTPS 配置证实被接受，未发出网络请求。修复后测试模式仅允许字面 loopback HTTP，供应商与下载源使用同一规则。独立重新构造公共 HTTPS、loopback HTTPS 和公共下载源，均被拒绝；现有文本模式资金边界保持一致。
2. **P1：计量来源可能混用。** 原 M1 将交付图片数放入 `image`，只要响应存在任意 token usage hash，就允许 `image/provider_usage_v1`。M1 现在只允许四个明确组合：`image/verified_delivery_v1`、`unicode_scalar/verified_input_v1`、`input_token/provider_usage_v1`、`output_token/provider_usage_v1`。目录、mint、新准入、派发及 readiness 均按该能力过滤；原操作只读重放不受后来目录过滤影响。
3. **P2：时长转换并非精确且溢出未统一拒绝。** 原 Decimal 默认精度令 `1.0000000000000000000000000000000001` 的 ceil 结果为 1000，而正确值是 1001；`0.9999999999999999999999999999999999` 的 floor 结果为 1000，而正确值是 999；`1e999999` 抛出未捕获的 `decimal.Overflow`。修复改为按有限 Decimal tuple 精确缩放，先限制数量级并统一捕获 `DecimalException`。独立复核上述输入以及 `1e-999999` 的 ceil=1、floor=0，全部符合合同，不构造巨大整数或幂。

## 核对的不变量

- 准入和结算使用原 `CreditLedger`、同一 billing account 锁、lot、hold 和 journal；不建立第二钱包。跨产品及独立连接池竞争同一执行并发和待核算限制。
- 同 invocation / 原键先查不可变请求；同内容回读，异内容冲突。精确旧任务回读不依赖原素材是否过期或当前磁盘是否低水位。
- 供应商 attempt 和绑定在付费 POST 之前提交。HTTP、响应关联和可信终态分别持久化，正常结算在可信终态提交后执行。超预算、超时、无可信终态和未知请求保持原预留；财务恢复只使用原证据，不再生成。
- 已提交任务在 UI 请求断开后继续；停止和进程关闭不会把已派发请求假定为免费取消。未派发的已知任务才可释放。
- 素材与产物受完整 owner / tenant / product / wallet Scope 保护；当前会话撤销、跨产品、跨用户、外来 artifact / voice ID 均不能转成下载授权。公开投影不含供应商 Key、voice 私有 ID 或存储键。
- 上游端点和允许下载 origin 由私有配置固定；不接收用户下载 URL，不跟随重定向，CDN GET 不带供应商 Authorization，HTTP 客户端不继承环境代理。
- 图片不以扩展名或 MIME 宣称有效。隔离子进程实际 verify 和完整解码，限制字节、像素、帧数、CPU 和墙钟时间；Linux 另有限制地址空间。子进程环境不继承供应商凭据。
- 存储使用私有目录、0600 文件、独占临时文件和 fsync 后原子发布；读取校验 UID、模式、真实大小及 SHA256。输入在活跃任务期间被数据库 pin 住，素材清理更新与准入的 `FOR SHARE` 锁相容。
- 未配置媒体时目录为空、派发关闭；旧 variations 必须显式 legacy 配置，不能自动从新接口回退旧接口。

## 生产启用前仍需完成

- 物理 TTL / 孤儿 blob 清理和每 owner 的全局资产配额。本快照仅有 invocation 内限制及磁盘低水位，不能宣称已完成安全回收。实现者将另开 TDD 修订，需对新快照复审。
- 具体供应商、版本、能力、真实费率以及逐能力真实生成、下载与扣点验收。当前 New API 的文本模型不构成媒体渠道。
- 媒体备份和恢复、容量、长任务压力与重启恢复证据；M1 的财务恢复不等于 M2 异步媒体 worker。
- 桌面 / Pi 媒体 host 工具、M2 视频和音乐、M3 音频和声音、M4 / M5 完整交付仍未在本审查中完成。

## 审查快照 SHA256

| 文件（相对中央服务） | SHA256 |
| --- | --- |
| `src/platform_service/media/contracts.py` | `d39d8a1e8e88418093390229a901868d10d3c4bb6842b7d095ad5570b2c583cb` |
| `src/platform_service/media/rates.py` | `711371e2810257876add81556bc14443513003cc5fd8173db65d14502baa9948` |
| `src/platform_service/media/core.py` | `18da752c202c87ebe7a41f52028d07a7f52a77b974a62611c36e8415631c1a14` |
| `src/platform_service/migrations/014_media_execution.sql` | `7072464cd2d9735a22a7d74e455bdd6347b8b7eaa536d8a8c7f3a8e6736887ad` |
| `src/platform_service/media_config.py` | `07f97d2ae5edb1863ca85f48a1d33b9cec97e8e42f9821f24ee29fbcfdf1ba29` |
| `src/platform_service/media_runtime.py` | `5e2e21dcdea318c19bb03d0f8b7657f79a5550331efef27a53168a990238a8e8` |
| `src/platform_service/media_web.py` | `13b334549e9a30ff5ddc661034ee8818215f8065a0c5cd7c8f8e091c59cb2fec` |
| `src/platform_service/media/artifacts.py` | `ebbf097a4f9ca7abc53c8ef449440dc2f5f02695ec76a32feb9be3457d4c9979` |
| `src/platform_service/media/adapters/openai_images.py` | `14483067aa6166677421d96d4dfa9a1954b4b994a2bdf9fc1b6173ca7ea1150d` |
| `src/platform_service/app.py` | `0460deedf6252af5304bc724b29b3c4579b5e9b101971534096e51031b441028` |
