# Pi 媒体工具根对象 schema 修复

新 worker 已完成独立本地验证，尚须随 v28 重新 prepare、打包和真实模型验收。源码仅修改 `runtime/pi/worker.mjs` 的一处 schema 选项，没有重试原真实回合、释放预留、改中央资金或升级 Node／Pi 依赖。

## 根因与最小修复

锁定的 Pi 1.4.0 使用 TypeBox 1.3.27。媒体工具通过 `Type.Union(variants)` 生成时，实际 Pi→OpenAI SDK 序列化的 `create_media.parameters` 根仅含 `anyOf`，没有 `type`；每个分支本身都是 `type=object`。这与服务器只导出类别的根对象缺失错误一致。

修复为 `Type.Union(variants,{type:'object'})`，不 flatten、不删除 anyOf、不切模型，不改变每个分支的常量、参数、required、additionalProperties、素材限制或 host 校验。所有原分支均要求对象，所以根新增对象条件不会允许任何原先被拒绝的实例。

按项目要求，先通过 Context7 解析 TypeBox，再读取官方联合 schema 文档（共两次外沙箱命令）。公开文档说明 Union 产生 anyOf；最终依据为实际锁定 1.3.27 的 `build/type/types/union.mjs`／`.d.mts` 和 SDK 最终序列化：`Union(anyOf,options)` 将 anyOf 与 TSchemaOptions 交给 Memory.Create，根 type 选项保留。没有根据旧 0.x 记忆推断行为，也没有升级依赖。

## 实际 SDK 红绿

新 fixture 使用实际 Pi Agent 和实际 OpenAI Responses SDK，通过显式 Mock fetch 捕获最终 JSON 请求，再交给合成严格网关；不是从源码 grep 或只查看工具上下文。其他 global fetch 被显式禁止并计数，全部运行值为 0。

两模型红测捕获根只有 anyOf 的真实序列化结果，模拟网关返回一次 HTTP 400，host 工具请求为 0，没有网络调用或 host 业务文件写入。最小修复之后，同一路径返回 200 并完成。

另将冻结的旧 worker 与新 worker 分别通过同一 SDK 序列化，逐项比较 0、1、2、10 个模型：

- 0 模型不声明 create_media，原／新均为模拟 200。
- 1／2／10 模型的旧序列化均为模拟 400，新序列化为 200。
- 删除新 create_media 根的 `type` 后，所有最终序列化工具 parameters 与旧结果完全相等；全部 anyOf 分支、枚举、required、limits、additionalProperties 及已有 TypeBox optional 注解都保留。
- 旧失败场景的 host 请求全部为 0；每份比较的非 Mock fetch 计数均为 0。

## 最终闭包回归

唯一会话 `49995` 退出码 0：**168 passed、0 skipped、0 failed，11.18 秒**。148 个源码／测试输入前后逐 SHA 相同；旧 v26 基础 Pi payload 的 11,932 文件前后相同。新 worker 在独立测试 payload 中覆盖，未改旧包或旧 manifest。

本轮包括新增实际序列化门槛 34 项、原媒体 SDK 12 项、Incho SDK 7 项、通用 SDK 6 项，以及 Pi adapter、host 和费率私有字段投影回归。新门槛包含 0／1／2／10 模型、关闭工具、图片生成／编辑／变体、视频、音乐、语音和声音注册／合成；10 种合法组合各产生一次合成 host 请求，9 种跨模型／能力／参数组合由 SDK 拒绝，实际 Python host 也在任何 POST 前独立拒绝它们。

已有 SDK 对某些布尔类型的转换仍不能作为付费权限边界；Incho 原 SDK 回归及 Python host 的独立严格校验没有被移除或放宽。新测试的 Mock host 回调与真实 Python host 拒绝测试分别计证据，不宣称该 Mock 回调就是真实中央请求。

本轮无 PG 或生产操作，没有真实供应商 POST。旧中央 M3／Incho／关联头 full 及 SDK 补充收据不包含这个新 worker，不能替代本轮或后续包内验证。模拟网关只执行已确认的根对象门槛；真实供应商验收须在新包上线后使用新回合独立进行，旧未知回合不重放。

## 固定候选

候选目录：`/private/tmp/easel-pi-root-schema-20261028/`。

| 文件 | SHA-256 |
| --- | --- |
| `runtime/pi/worker.mjs` | `7fc068daae8e9cc9dc201252dbb412de9adb935569e0cb20e641ad6e2b193f6d` |
| `tests/pi_media_root_schema_fixture.mjs` | `9ac7b04ce1fedd01ab84328e175ce1efee334d06d6a151865e006fa90564730c` |
| `tests/test_pi_media_root_schema.py` | `a1c06f50190bbf88c908fbe3e79cc4c1ecee38e8100e67f0cf0a2fe6e16b6108` |
| `candidate-manifest.json` | `d97febe9d90da9bae44f596a5896f033de0580dfe3f9d305f4c93949fdd90081` |
| `candidate-source.tar.gz` | `1c44d29796c140608cf455ce3de2d4a84ac8ed6d81957a460a321c1207dd7860` |
| `worker-only.patch` | `923deca353f2488aae0037413abde872411233cd924b08cdce14a395e28712e3` |
| `closure-r1.log` | `fbd13263436c1e540d11927a28cad5be5dfdda934ae92875aad7c3754fe9aab4` |
| `closure-r1.junit.xml` | `bbb7646eae1d8b6e6ef5b75fdd219103b439a1dd8e181ecb2454b4ecb2fbe580` |
| `closure-r1-before.json`／`closure-r1-after.json` | `9f81ef3a493a71ebe757929156fa2f4084a71833c03b3c8dbd1ccf0dee3bde62` |
| `serialization-equivalence-r1/equivalence-safe.json` | `30b9cf8ab5b09c11519b6d979f334bc6dcb02047e97192db0ffe4f249da09ce7` |

原红测、对旧 optional 注解预期不足的 3 项测试失败、缺测试环境依赖的收集失败，以及较早 34／168 项收据均保留。最终以 session49995 的闭包收据为准，计数不相加。

公开文档：[TypeBox Union](https://github.com/sinclairzx81/typebox/blob/main/design/website/docs/type/union.md)。实际 SDK 和 TypeBox 版本身份另见候选目录中的 `sdk-candidate-identity.json`。
