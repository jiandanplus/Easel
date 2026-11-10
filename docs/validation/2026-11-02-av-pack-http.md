# AV 主机 HTTP 接收：Task2B1 验证记录

2026-11-02，`codex/ui-redesign`。新增 import-inert 的 `pack_http.py`，只接收调用方提供的 read／guard；本阶段没有打开 socket、创建线程／定时器或操作 Docker。

实现严格的 HTTP/1.1 单响应解析，支持明确 Content-Length 和 chunked 两种方式，拒绝重复字段、混用 framing、编码、升级、trailer、截断及多余字节。响应体、HTTP 头、分块开销和总 wire 分别计数，读取前检查预算，最终返回不可变 body 和数字／framing 信息。它还不负责 JSON、mux 或真实身份／截止时间验收。

三种固定预算：

| 用途 | body 上限 | wire 上限 |
|---|---:|---:|
| 元数据 JSON | 131072 | 163840 |
| stdout mux | 65536 | 98304 |
| 完整清单 mux | 16809985 | 16842752 |

共同 HTTP 开销上限32767、头部16384、字段64、每次 read65536。预算只能收紧。恰好达到 wire 上限时仍作一次最多1字节的非保留 EOF 探测；返回任何字节即失败，不扩大可接受上限。body 长度限制不等于已实测的进程峰值内存。

Root fresh 验证：

```sh
rtk proxy services/platform/.venv/bin/python -I -B -m pytest --noconftest services/platform/tests/test_av_pack_http.py -q
```

最终 **208 passed in 0.46s，exit0**。全部使用小型合成 reader／guard；覆盖分段读取、独立上限及读取位置、故障后安全错误、精确 EOF 前后取消位置。独立审查的非阻断测试精度提示经 RED 重现后修正；原记录、原208项通过结果及失败日志均保留。

源码 SHA256：`ec81d8bd1c60e1b9aed8d917450c24f377c199917ae9257dedf52349cb1557c6`。最终测试 SHA256：`e3d3cad4de9a5cd408b2e7e6fda3a1aa4bf1f89b55ad2d86bf0f845197137499`。

独立复审 `outputs/_run/av-pack-http-20261102/TASK2B1-REVIEW-r2.safe.md`：规格 PASS、质量 PASS，提示已关闭。报告 SHA256 `ab574fca157a9c5389367c19db81bc07e6cffad7f6fe7620d8f1875ad8c33951`。Root 已完整读取源码、测试、报告和修订 diff，并核对原Task2A八项 pin 不变。

下一项 Task2B2 接固定 Unix socket、同一截止时间和取消、有限 API／once 状态；Task2B3 再接私有 pin、recipe、容器配置／终态、guardian、完整控制器和最终写入检查。真实 preflight／资源负控／近16MiB留存／固定真实 pack 验证尚未执行，完整 AV19、FFmpeg 工具链、备份恢复和桌面签名等原交付门继续保留。本项没有前端、账号、钱包、原17868工作台或 Git 变更。
