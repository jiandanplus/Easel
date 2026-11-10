# 图片变体私有插件

此插件填补 New API v1.0.0-rc.45 任务路由中的图片变体入口，使用原生 multipart variations 合同。它只转发到**已经实际提供该能力**的供应商；不能让仅支持 edits 的模型获得 variations，也没有把 edits 偷换成 variations。未接入真实供应商或执行收费请求。

宿主静态 `/v1/images/variations` 已占用且返回501，插件不能覆盖它。因此采用：

- POST `/easel/openai/v1/images/variations`
- GET `/easel/openai/v1/images/tasks/{task_id}`（读取宿主保存的成功结果，不再次请求上游）

客户端 `IMAGE_BASE_URL` 可使用 `https://网关域名/easel/openai/v1`，模型使用 `easel-image-variations`，由管理员渠道模型映射指定真实供应商模型。此配置只能用于该变体入口，普通生成/编辑继续选择已有核心或官方插件的渠道。本插件不设默认供应商地址。OpenAI官方已于2026-05-12退役DALL·E 2/3，官方variations文档也已标明接口停止服务；本插件仅适用于经核实仍提供该旧协议的其他兼容供应商。对于现代GPT Image，应明确选择images/edits与提示词实现新的变体工作流，不能把旧端点配置到OpenAI后声称已兼容。

输入限制：恰好一张PNG，文件元数据不超过4MiB，n为1到10，size为256/512/1024方图，response_format为url或b64_json。文件通过宿主fileRef传递，插件不读文件系统。客户端user字段不转发，用户身份以New API鉴权为准。未知字段、重复字段、mask和URL输入被拒绝。实际尺寸/PNG签名和目标模型限制仍由供应商验证。

成功要求HTTP 2xx且有可用的图片结果。预留按请求n估计，完成事实按实际返回图片数量，不把请求数量当作最终实耗。价格和最终扣费由宿主配置；没有配置渠道、价格或额度时不得称为可调用。上游提交后超限/断流/解析错误仍可能已消费，必须验证宿主不会自动重放收费POST，并由中央账本处理待核算。

当前 rc.45 提交JSON响应限制为1MiB，较大的b64_json可能超过宿主限制，优先选url。没有伪造图片artifact CDN下载：此插件返回供应商原生data数组，客户端按已有同步图片逻辑获取结果。API令牌只能发给网关，不应附带到图片CDN URL。

验证：

```bash
rtk proxy node --test integrations/newapi/plugins/easel-image-variations/1.0.0/tests/plugin.test.mjs
rtk proxy /private/tmp/new-api-rc45-cli plugin lint integrations/newapi/plugins/easel-image-variations/1.0.0/plugin.js
rtk proxy /private/tmp/new-api-rc45-cli plugin test integrations/newapi/plugins/easel-image-variations/1.0.0/plugin.js --fixture integrations/newapi/plugins/easel-image-variations/1.0.0/fixtures/golden.json
```

上述证明Node逻辑和真实rc.45/moejs hook兼容，不证明实际模型可用、用户隔离数据库验收或计费正确。

依据：[宿主路由和协议](https://github.com/QuantumNous/new-api/blob/6cf0d90b5ff27f901697dddd61e0c28aba8efefc/pkg/jsplugin/routing.go)、[原生路由占用检查](https://github.com/QuantumNous/new-api/blob/6cf0d90b5ff27f901697dddd61e0c28aba8efefc/router/plugin-router.go)、[variations协议](https://developers.openai.com/api/reference/resources/images/methods/create_variation)。

当前上游生命周期依据：[OpenAI退役说明](https://developers.openai.com/api/docs/deprecations)。官方Sora/Videos API亦列于2026-09-24退役；安装市场Sora插件不代表官方服务已恢复，其他兼容上游必须另外核实。
