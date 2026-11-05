# 异地备份、恢复演练与飞书告警

生产 154 每日的 `encrypted_backup` 已经产生 RSA-OAEP + AES-256-GCM 封装的 `.sealed` 备份。本目录补齐四件事：

| 组件 | 运行位置 | 作用 |
| --- | --- | --- |
| `sealed_backup.py` | 生产服务器，替换原每日备份入口 | 复用冻结的 `encrypted_backup.py`，但只需要公钥；明文先校验，写盘后逐字节比对 |
| `offsite_sync.py` + `easel-offsite-sync.timer` | 生产服务器，每小时 | 把新备份只增不删地上传到 Cloudflare R2 |
| `offsite_sync.py --watch` + `com.easel.backup-watch.plist` | 持有私钥的 Mac，每小时 | R2 最新备份超过 30 小时或 R2 无法读取时告警，恢复后再通知一次；服务器停止备份或同步都会体现为 R2 变旧 |
| `restore_drill.py` + `com.easel.backup-drill.plist` | 同一台 Mac，每周一 10:00 | 取 R2 最新备份，用私钥解封，校验全部文件，恢复到无网络的临时 PG16，核对账本，删除容器，结果推送飞书 |
| `feishu.py` | 两端共用 | 飞书告警：`FEISHU_USER`（open_id）经本机 `lark-cli` 应用机器人私聊；或 `FEISHU_WEBHOOK` 自定义群机器人 |

告警都从 Mac 发出，服务器上不放任何飞书凭据。代价是 Mac 关机或长时间休眠期间不会有告警，唤醒后的下一次检查会补报。

解密私钥只在 Mac：`~/Easel-build/backup-keys/backup-private.pem`（0600），另存一份到密码管理器。服务器只放公钥 `backup-public.pem`。

## R2 保留策略

桶 `easel-backups`，前缀 `central/`：对象锁 90 天（服务器令牌即使被盗也删不掉近 90 天的备份），400 天后自动过期。每日一个备份，存量约为单个备份大小 × 400。

## 部署步骤

1. **开通 R2**（控制台，仅一次）：Cloudflare 控制台 → R2 → 开通（可能需要绑定付款方式；免费额度 10 GB）。
2. **建桶**：`CLOUDFLARE_ACCOUNT_ID=<账户 ID> CF=<cf 路径> ./setup_r2.sh`。
3. **建两个 R2 API 令牌**（控制台 → R2 → 管理 API 令牌，都只限 `easel-backups`）：服务器用“对象读写”，Mac 用“对象只读”。复制 Access Key ID 和 Secret Access Key，下面两步会不回显地提示输入。
4. **Mac**：
   ```text
   ./install_mac.sh <你的飞书 open_id>     # 复制文件、建 venv、写 drill.env（不含密钥）
   ./install_mac.sh --keys <账户 ID>       # 输入只读令牌 → ~/Easel-build/backup-drill/rclone.conf（0600）
   ```
5. **服务器**（需要生产操作授权）：`./deploy_server.sh <账户 ID>`，输入读写令牌。脚本经 ssh 安装文件与 rclone 配置、找到调用 `encrypted_backup` 的每日备份单元并用 drop-in 改为 `sealed_backup.py`、立即生成并上传一次备份、启用每小时同步。回滚：删除 `/etc/systemd/system/<单元>.d/sealed.conf` 后 `systemctl daemon-reload`。
6. **启用 Mac 定时任务**：`./install_mac.sh --activate`（先确认 R2 可读，再加载监视与演练两个 launchd 任务）。
7. **首次演练**：`~/Easel-build/backup-drill/venv/bin/python ~/Easel-build/backup-drill/restore_drill.py`，飞书应收到“恢复演练通过”。通过后再把旧私钥 `/root/easel-migration-20261024/transfer-private.pem` 移出服务器。

## 配置文件（0600，不进仓库）

服务器 `/etc/easel-backup/backup.env`：

```text
R2_REMOTE=r2:easel-backups/central
RCLONE_CONFIG=/etc/easel-backup/rclone.conf
```

Mac `~/Easel-build/backup-drill/drill.env`：

```text
R2_REMOTE=r2:easel-backups/central
RCLONE_CONFIG=~/Easel-build/backup-drill/rclone.conf
PRIVATE_KEY=~/Easel-build/backup-keys/backup-private.pem
FEISHU_USER=ou_...
LARK_CLI=/opt/homebrew/bin/lark-cli
```

`lark-cli` 以它自己的应用机器人身份发消息，应用密钥留在 `lark-cli` 的配置里，不进这些文件。
