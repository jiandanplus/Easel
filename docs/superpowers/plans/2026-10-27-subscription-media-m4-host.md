# M4：桌面和 Pi 媒体 host 实施约束

本文件细化已经授权的媒体总设计第 8 节，后续在 M2／M3 数据面合同确定后实施。当前 v21 不含本阶段工具，不据此宣称订阅媒体已能在桌面对话中使用。

## 身份和调用边界

新增 `easel/platform/media.py` 和 `media_records.py`。中央客户端通过现有受保护账号存储读取当前产品会话；URL 沿用已确认中央地址，不接受模型或浏览器传入供应商地址、钱包、租户或 Key。`RunRequest` 的已接受中央 Scope 是新媒体操作的来源，不能从工具参数重建身份。

供应商 Key 始终留在中央；产品 access／refresh 和媒体 task token 不进入 Pi worker、工具 JSON、shell 环境、网页返回、运行日志或持久 operation 记录。host 只将短期 task token 留在内存，中央会话保存规则不改变。HTTPX 不读代理环境、不跟重定向、响应大小和超时有界。

读取持久账号时使用原文件锁，不能跨整个长媒体请求持锁阻止用户退出。取出凭据后复验当前身份；外部提交、上传、查询、下载继续由中央复验授权。已接受任务即使发起会话撤销，也保留原 job／attempt 和当地原键记录，不能以客户端退出为据清理金融未知或重新生成。

恢复所属账号按现有 `operation_owner` 思路绑定中央 origin、user／tenant／product／billing，不把可续期 token 值当身份；重新登录同一个账号可查询其原作业。新任务仍绑定当次完整 Scope／session／authorization epoch，异账号的迟到响应不得更新当前对话、草稿或产物。

## 本机持久记录和唯一生成

每次媒体 host 工具调用先生成稳定 `invocation_key` 和 `operation_key`，关联工作区、来源、会话、turn 与工具调用 ID。原始提示词、文件内容、密钥不写 operation 记录；只记录规范摘要、公开模型／能力、原键、已知 invocation／job ID 和真实状态。

使用现有私有文件约束：0700／0600、`NOFOLLOW`、有界 JSON、fsync 后原子发表、同 ID 异内容拒绝。mint 和生成必须分别记录派发阶段；只有本机拥有未派发决定才可开始新操作，不能根据“还没有 job ID”推导未收费。

生成 POST 最多一次。超时、失联、中断或未知只通过 `GET /v2/media/operations?invocation_id=&operation_key=` 查询原请求；不自动切模型、重建 operation 或再 POST。mint 尚未获得 token／invocation 与生成已可能派发是不同阶段，不混为一类。重启／新会话恢复仍读取原键。

## Pi 工具和文件

新增固定 host 工具 `create_media`、`get_media_job`、`cancel_media_job`、`download_media_artifact`。工具列表只投影本次已授权并已开放的中央能力；没配置媒体时不向模型宣传可执行媒体工具。工具参数只接受公开模型／能力、限定参数、已授权工作区文件引用和另存输出位置。供应商任务 ID、URL、Key、金额和任意 scope 字段无效。

输入文件复用现有 Pi 工作区保护，不通过文件工具读取 `.env`、账号存储、运行环境或外部目录。打开实际文件后冻结大小／SHA；上传后获得不透明 asset ID。不得把本机绝对路径直接交给中央或将媒体 token 写入 `NEW_API_KEY`。

产物只从原 job 的公开 artifact 元数据下载，当前会话和完整 owner 由中央复验。先独占写临时文件、核对字节数／SHA，再原子另存到允许的 `outputs` 子目录；默认不覆盖原稿。下载失败保留原 job，不重新生成。工具返回安全文件链接及实际执行／交付／费用状态；本机停止不冒充中央取消完成或退款。

skills 中的中央媒体步骤转交上述 host 工具。自定义 CLI／统一网关原配置保持；不会为了运行旧脚本放开 shell 网络、继承产品凭据，或把本地 Whisper／免费配音作为指定订阅模型的自动回退。

## 实施和验收

1. 先为协议客户端写独立 HTTP fixture 红测：凭据不外泄、scope 改变、错误／超时、一次 POST、原键查询、下载 hash、撤销后恢复。实际服务合同使用 M1–M3 现有路由，不另造兼容层。
2. 实现私有 operation 状态和原子发表；故障注入覆盖每个发送前后窗口、重复工具 ID、重启和异账号迟到响应。
3. 将 host 工具接 PiAdapter／worker schema，刷新固定 runtime manifest 和 package 清单。使用实际包内 Pi SDK 的本机模拟协议验证工具往返，不能只 mock 一个返回字符串。
4. 中央真实 PostgreSQL fixture 经 product token→task token→owned input→job→verified artifact→同钱包结算，host 下载文件和 UI 成品链接闭环；取消／断流后只读原操作。
5. 独立审查、原自定义媒体／文件工具回归，再重打 macOS 新版本并核对旧登录、历史和配置。随后按 M5 逐能力真实模型、可打开产物与扣点验收；fixture、包内运行和真实供应商分开报告。

本阶段不新增默认供应商、费率、商户配置、付款成功或用户自助发点。当前真实媒体配置缺口已经询问，等待期间继续独立代码与协议工作。
