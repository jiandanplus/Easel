# 异地备份、恢复演练与飞书告警

生产 154 每日的 `encrypted_backup` 已经产生 RSA-OAEP + AES-256-GCM 封装的 `.sealed` 备份。本目录补齐四件事：

| 组件 | 运行位置 | 作用 |
| --- | --- | --- |
| `sealed_backup.py` | 生产服务器，替换原每日备份入口 | 复用冻结的 `encrypted_backup.py`，但只需要公钥；明文先校验，写盘后逐字节比对 |
| `offsite_sync.py` + `easel-offsite-sync.timer` | 生产服务器，每小时 | 把新备份只增不删地上传到 Cloudflare R2；同步失败或超过 30 小时无新备份时告警，恢复后再通知一次 |
| `restore_drill.py` + `com.easel.backup-drill.plist` | 持有私钥的 Mac，每周一 10:00 | 取 R2 最新备份，用私钥解封，校验全部文件，恢复到无网络的临时 PG16，核对账本，删除容器，结果推送飞书 |
| `feishu.py` | 两端共用 | 飞书自定义机器人消息，支持签名校验 |

解密私钥只在 Mac：`~/Easel-build/backup-keys/backup-private.pem`（0600），另存一份到密码管理器。服务器只放公钥 `backup-public.pem`。

## 需要操作者准备的凭据（不进仓库、不进对话）

1. **R2**：建桶（如 `easel-backups`），开启对象版本或生命周期保留；建两个 API 令牌——服务器用“对象读写”、Mac 用“对象只读”，都只限这个桶。
2. **飞书**：群里添加自定义机器人，复制 Webhook；建议开启“签名校验”并记下密钥。

## 服务器部署（154，root）

```text
apt-get install -y rclone
install -d -m 700 /etc/easel-backup /usr/local/lib/easel-backup /var/lib/easel-backup
install -m 644 backup-public.pem /etc/easel-backup/backup-public.pem
install -m 644 feishu.py offsite_sync.py sealed_backup.py /usr/local/lib/easel-backup/
rclone config --config /etc/easel-backup/rclone.conf   # 交互式新建 S3 / Cloudflare 远端，名为 r2；文件 0600
cat > /etc/easel-backup/backup.env                     # 0600，内容见下
install -m 644 easel-offsite-sync.service easel-offsite-sync.timer /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now easel-offsite-sync.timer
```

`backup.env`：

```text
R2_REMOTE=r2:easel-backups/central
FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/...
FEISHU_SECRET=...
```

最后把每日备份服务的 `ExecStart` 改为 `/usr/bin/python3 -B /usr/local/lib/easel-backup/sealed_backup.py`，手动跑一次确认新备份生成并同步到 R2。新流程经一次恢复演练证明可用后，再把旧私钥 `/root/easel-migration-20261024/transfer-private.pem` 移出服务器。

## Mac 部署

```text
brew install rclone
mkdir -p ~/Easel-build/backup-drill && cd ~/Easel-build/backup-drill
cp <仓库>/services/platform/deploy/backup/{restore_drill.py,offsite_sync.py,feishu.py} .
cp <仓库>/services/platform/src/platform_service/media_backup_b2/legacy_sources/sealed_transfer.py .
uv venv venv --python 3.12 && uv pip install --python venv/bin/python 'cryptography>=42,<47'
rclone config --config ~/Easel-build/backup-drill/rclone.conf   # 只读令牌，远端名 r2
cat > ~/Easel-build/backup-drill/drill.env                       # 0600，内容见下
sed "s#__HOME__#$HOME#g" <仓库>/services/platform/deploy/backup/com.easel.backup-drill.plist > ~/Library/LaunchAgents/com.easel.backup-drill.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.easel.backup-drill.plist
```

`drill.env`：

```text
R2_REMOTE=r2:easel-backups/central
RCLONE_CONFIG=~/Easel-build/backup-drill/rclone.conf
PRIVATE_KEY=~/Easel-build/backup-keys/backup-private.pem
FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/...
FEISHU_SECRET=...
```

手动跑一次：`~/Easel-build/backup-drill/venv/bin/python ~/Easel-build/backup-drill/restore_drill.py`。
