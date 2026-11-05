# 账号邮箱变更与钱包连续性：隔离数据库验证

本轮新增 `test_identity_email_continuity.py`，以实际中央 ASGI 接口及合成签名身份数据验证身份主键为 issuer＋subject，而非邮箱。相同 issuer／subject 的邮箱从未验证改为已验证、或更换邮箱，均保持原 user、tenant、wallet、membership、grant，以及九张财务表的非空合成基线；相同邮箱不同 subject 获得独立空钱包，错误 issuer 的同 subject 被拒绝。未读取真实账号或密钥，未调用供应商。

最终五文件回归在新建、仅回环监听的专用 PostgreSQL 16 容器运行：**86 passed／0 failed，21.61 秒**，包括 identity、OIDC、API、浏览器错误合同及上述四项新案例；四项先前单跑包含在这 86 项中，不相加。五个测试来源在执行前后逐 SHA 相同。容器只有合成数据库、独立凭据及 tmpfs，无业务挂载；没有宣称 OS 层断网隔离。

较早的小容量 tmpfs 运行在四项通过后，扩大回归时发生磁盘容量不足，保留 1 failed／23 passed／63 errors 的原记录；这不是被改写为应用通过的失败。后续新建 1 GiB tmpfs fixture，才取得上述最终 86 项结果，没有重置或清理原业务容器。

安全结果为 `/private/tmp/easel-email-pg-20261030-r3/email-regression.safe.json`，SHA-256 `f1267aae8a448f513b2f813005249296c8b7c6ec1e5268bbecc85621ab46d2f6`。新测试 SHA `ba3a99954e9fafca8a4a0afb565d6bb24d31b52f37d0e605998cf1f9a1ad7346`。这证明本地身份与资金连续性合同；没有验证真实 SMTP、邮箱送达、密码重置 UI 或第三方身份提供方的实际邮件流程。
