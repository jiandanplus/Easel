# 私有运行健康检查

2026-10-27已按根任务审核的精确安装计划在154安装并启用。首个真实probe为exit0、status=ok、alerts=[]；新timer为active/enabled，原四项业务服务PID／启动时间及原加密备份定义保持。随后已实际观察timer触发、新probe InvocationID及状态文件晚于安装收据的更新，结果仍ok。此证据不表示未来所有周期、真实故障注入或外部通知已验收。安全回执见`outputs/_run/operations-readiness-20261027/evidence/health-install-safe.json`。

检查程序只适用于154.217.245.179（hostname ser362540173134）。默认无参数为只读安全JSON输出；`--record`将安全状态写入`/var/lib/easel-health/status.json`（root0700目录、0600原子替换文件），仅在告警代码／等级变化时写journal。不发邮件、聊天消息、短信或webhook，不监听端口，不读模型密钥、DSN、账户数据、归档明文或私钥。

## 检查范围

| 项目 | 阈值 |
| --- | --- |
| 中央API、Console、供给隧道、Nginx | inactive即error |
| 中央PG、身份PG、Logto容器 | 未运行即error |
| 私有API／Console health | 精确Host、200、ok=true，Console另需assets_ready=true |
| 原加密备份timer／上次结果 | timer非active或上次Result非success即error |
| 最新有效权限密文文件时间 | 超30小时warning，超48小时error；不把文件新鲜度当已验证可恢复 |
| 根盘可用块使用率 | 80%warning，90%error |
| MemAvailable | 512MiB以下warning，256MiB以下error |
| 两个公网业务证书 | 本机443按真实SNI严格CA验证；14天以内warning、3天以内error |

未知指标单独报探测不可用，不按0或健康填充。证书连接只到本机loopback，HTTP health也只到loopback，不执行任何模型／支付请求。

## 文件

- `scripts/check_service_health.py` → `/usr/local/lib/easel-operations/check_service_health.py`（root所有、不可被应用用户写）
- `deploy/easel-health-watch.service` → `/etc/systemd/system/easel-health-watch.service`
- `deploy/easel-health-watch.timer` → `/etc/systemd/system/easel-health-watch.timer`

systemd每5分钟检查，启动后2分钟首次检查，随机抖动最多20秒；没有额外通知通道。unit仅允许写入StateDirectory，开启NoNewPrivileges、PrivateTmp、ProtectHome=read-only、ProtectSystem=strict及core关闭。告警不改变业务服务、模型准入或钱包。

安装必须逐文件核对根任务已审源码SHA，拒绝覆盖不相同的现有自定义unit；先`systemd-analyze verify`，再只读执行检查，最后按批准范围启用本次新timer。原备份timer、API、Console、Nginx、Logto与数据库不重启。

运维只读查看：`systemctl status easel-health-watch.timer`、`systemctl show easel-health-watch.service -p Result -p ExecMainStatus`和`journalctl -u easel-health-watch.service`。实际状态文件包含主机资源、服务状态、备份时间和证书过期时间，不含业务身份或凭据。需要对外通知时另行明确接收方和通道。

测试：`rtk proxy .venv/bin/python -m pytest --noconftest services/platform/tests/test_service_health_watch.py -q --tb=short`，5 passed；服务器安装证据另见上述安全回执，真实业务故障注入尚未执行。
