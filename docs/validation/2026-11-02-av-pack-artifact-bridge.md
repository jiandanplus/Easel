# AV 完整清单交接：Task2A 验证记录

2026-11-02。当前分支 `codex/ui-redesign`。本记录关闭生产端发布、接收端完整清单校验及私有落盘的代码／纯协议验证，尚未关闭真实容器留存和主机端最终验收。

生产端在子进程回收和资源清理完成后，重新读取并核对清单大小、哈希及私有文件属性，将完整规范 JSON 加一个换行发布到 stderr，再发布原有有界 stdout 报告。发布异常返回失败；stdout 部分写入失败后不会追加第二份 JSON。stdout 的原有大小限制不变。

接收端验证 stream2 帧、总大小、换行、报告绑定哈希、规范 JSON、全部条目、路径、目录结构、Git tree、计数及必要条目／gitlink。同一 OID 的原始类型、大小、SHA256 必须一致，普通文件、可执行文件和符号链接可以合法引用相同 blob。外部 gitlink 不被当作已加载原始对象。私有写入采用独占文件、受限权限、同步及落盘后身份／哈希复核。

Root fresh 执行：

```sh
rtk proxy services/platform/.venv/bin/python -I -B -m pytest --noconftest services/platform/tests/test_av_pack_worker.py services/platform/tests/test_av_pack_runtime.py services/platform/tests/test_av_pack_artifact.py -q
```

结果：**287 passed in 3.80s，exit 0**。这是使用小型合成 pack、连接式 FakeMain、假进程协议的测试；五种模式均经过实际假生产端→接收端→私有文件路径。它不证明真实进程调度、资源限制或 Docker 日志留存。

独立复审 `outputs/_run/av-pack-artifact-bridge-20261102/TASK2A-REVIEW-r2.safe.md`：规格 PASS、质量 PASS，先前 A1 已关闭，无新增可操作问题。报告 SHA256 为 `c7e08b404820bbfd15f72a25944b478409fd0b0c5032715358506d6ae3f93d18`。首次复审、4 个新增语义负例的失败日志及所有先前结果保留。

最终源文件 SHA256：

| 文件 | SHA256 |
|---|---|
| pack_worker.py | d522dcc05d696231a676d36f740227f66fe404b9824af57e2cde03011a2e6d07 |
| pack_artifact.py | 2c3b1bd2b86bce1e5442bcb77eb78f57876a62ece8d1cd96c45c2cd0150fca91 |
| pack_runtime.py（保持不变） | c08ffd6b2b92a0b42ccdacaa1ec143d231da0c7fbb93ad8ce13c96358b4c90e2 |

Root 核对全部 8 项源文件／测试 pin，均匹配。执行事实见 `outputs/_run/av-pack-artifact-bridge-20261102/ROOT-TASK2A-R2-PURE.safe.json`。

后续 Task2B 仍须完成有界 HTTP、两次精确日志请求的 once 控制、固定容器身份／配置／终态、绑定私有 pin 的验证器、原始截止时间和写入前后最终检查。随后由 Root 验证实际 preflight／AS／CPU／wall 负控、接近 16MiB 且含密集转义字符的真实留存，再进行已固定公开 pack 的源链验证。真实 pack 尚未解码；AV19、完整 FFmpeg9.0.2 工具链、备份恢复、签名／公证及全部原交付门不因本项代码验证而完成。

本轮没有改动前端、账号、钱包、供应商、原17868工作台或其配置；没有真实容器启动、远程操作、提交、推送或合并。
