# M4 桌面与 Pi 多媒体扩展本机验收

日期：2026-10-27；分支：`codex/ui-redesign`。仅本机合成身份、HTTP fixtures、已有包内 Node/Pi SDK。没有真实供应商请求、生产变更、账号读取、构建、打包或激活。

## 已实现

- 目录只投影 host 支持的中央已启用模型及精确 capability、参数、资源限制、完整多 meter/basis 有理数费率。Pi 不再把首个 image meter 显示成总价。
- image.generate/edit/variation 共用原单张、1024、每轮一次 image 槽；其余能力各每轮一次。稳定 owner/session/turn/tool/capability 生成原 operation；同槽异工具/模型/内容不能重派。旧无 capability 记录只读兼容。
- 工作区素材以 NOFOLLOW 打开实际文件，冻结 SHA/size/格式，检测读中变化，拒绝私有/运行环境/外部/symlink/hardlink 路径。中央 owned 上传返回必须匹配原字节，上传未知保留原记录且不继续提交。
- image、video、music、speech、transcribe、enroll、synthesize 使用通用 create_media；queued/running 返回原 job，后续 GET/cancel/download 不自动生成。ready 中央 voice ID 才能合成；登记与合成分别独立一次预算/记录。
- 原产物只从 owned job 元数据下载。图片保留 20 MiB 上限，通用音视频/JSON最多 64 MiB；校验 MIME、metadata、hash、size、ETag、Content-Length、无压缩、文件头/容器，原字节原子另存 outputs，不覆盖、不转码。图片与 WAV/JSON在本机额外验证内容；MP3/Ogg/MP4的完整解码权威仍是中央，桌面做容器/头和精确字节一致性验证。
- 新提交/上传/取消绑定当前完整 accepted scope；旧会话同 owner 可只读恢复查询与下载。stop/退出/新身份的迟到任务与文件链接被抑制。取消不等于退费。

## 最终测试

最终源码回归：**257 passed，1 个第三方 PendingDeprecationWarning**，12.78 秒。

运行命令：

```sh
rtk proxy env EASEL_PI_TEST_PAYLOAD=/private/tmp/easel-delivery-20261027-v22/pi-runtime python -m pytest tests/test_media_expansion.py tests/test_central_media_host.py tests/test_central_media_auth_rotation.py tests/test_central_media_web.py tests/test_pi_media_host.py tests/test_pi_adapter.py tests/test_pi_runtime.py tests/test_pi_packaging.py tests/test_newapi_image.py tests/test_newapi_video.py tests/test_newapi_music.py tests/test_newapi_voice.py -q
```

本机 socket fixtures 在 sandbox 外执行，仅绑定 loopback 和合成身份。已有 Pi SDK 1.4.0/Node v24.22.1；测试将当前 worker 复制到 pytest 临时目录并仅链接已有 node_modules，不改原发行 payload。实际 SDK 12 个场景包括全部能力的 schema 调用，以及关闭、无效模型和非法音频格式。受控 ffmpeg 只生成测试输入容器，没有对任何付费产物转码。

## 冻结与未完成证据

safe JSON 保存本次文件 SHA256、测试口径与非生产边界；source-manifest 仅更新本次五个 desktop 源码与 worker 条目，未刷新其他代理文件。相关源码原先在此工作区为 untracked，因此冻结 patch 是这些候选文件相对空文件的完整源快照，不能解释为相对 v23 的增量 diff。

尚未宣称：中央 M3 实际 PostgreSQL/ASGI 与新 host 的完整联调、独立安全 review、发行包验收、真实新能力 provider/计费验收。新能力生产继续 closed；未添加默认供应商、价格或可用音色。
