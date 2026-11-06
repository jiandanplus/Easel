#!/usr/bin/env python3
"""Copy new sealed backups to Cloudflare R2, and watch R2 from another machine.

Server (hourly, default): uploads are add-only (`--immutable`, never deletes);
the envelopes are already encrypted with the off-host public key.
Watch (`--watch`, hourly on the Mac that holds the private key): alerts when
the newest backup in R2 is older than STALE_HOURS or R2 cannot be read, which
also covers a server that has stopped backing up or syncing. It also reads the
server's health record, because the server itself has no alert channel.

Either way an alert is sent when the set of problems changes, repeated once a
day while unresolved, and a recovery message is sent when healthy again.

Private config (server /etc/easel-backup/backup.env, Mac drill.env; 0600):
  R2_REMOTE=r2:<bucket>/central          rclone remote path
  NEWAPI_R2_REMOTE=r2:<bucket>/newapi    optional, watch only: gateway backups
  HEALTH_SSH=easel-zhiyin                optional, watch only: ssh host of the health record
  RCLONE_CONFIG=/etc/easel-backup/rclone.conf
  FEISHU_USER=ou_...                       lark-cli app bot direct message, or
  FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/...  (+ FEISHU_SECRET)
The server may leave Feishu unset; the Mac watch then reports for it.
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import time
from pathlib import Path

import feishu

NAME = re.compile(r"^(\d{8}T\d{6}Z)-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.sealed$")
REQUIRED = ("R2_REMOTE",)
DEFAULTS = {"BACKUP_DIR": "/var/backups/easel-encrypted", "STALE_HOURS": "30",
            "STATE_FILE": "/var/lib/easel-backup/state.json", "RCLONE_CONFIG": "/etc/easel-backup/rclone.conf"}
REPEAT_SECONDS = 24 * 3600
HEALTH_STALE_SECONDS = 30 * 60
HEALTH_LABELS = {"gateway_unhealthy": "模型网关不可用", "api_unhealthy": "中央服务不可用",
                 "console_unhealthy": "运营后台不可用", "backup_failed": "服务器备份失败"}


class SyncError(Exception):
    pass


def read_config(path, defaults=DEFAULTS):
    values = dict(defaults)
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    missing = [key for key in REQUIRED if not values.get(key)]
    if missing:
        raise SyncError("配置缺少 " + "、".join(missing))
    return values


def age_hours(name, now):
    stamp = dt.datetime.strptime(NAME.match(name)[1], "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
    return (now - stamp.timestamp()) / 3600


def rclone_runner(config_path):
    def run(args):
        try:
            result = subprocess.run(["rclone", "--config", config_path, *args], capture_output=True,
                                    text=True, timeout=900)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise SyncError(type(error).__name__) from None
        if result.returncode != 0:
            # rclone errors name the remote and object, never the credentials.
            raise SyncError((result.stderr.strip().splitlines() or ["rclone 退出码 %d" % result.returncode])[-1][:200])
        return result.stdout
    return run


def _state(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(".new")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    os.replace(temporary, path)


def run(config, *, rclone, now=time.time, notify):
    current = now()
    directory = Path(config["BACKUP_DIR"])
    remote = config["R2_REMOTE"].rstrip("/")
    stale = float(config["STALE_HOURS"])
    problems = {}
    local = sorted(name for name in os.listdir(directory) if NAME.match(name)) if directory.is_dir() else []
    if not local:
        problems["no_local"] = f"本机没有找到加密备份（{directory}）"
    elif (age := age_hours(local[-1], current)) > stale:
        problems["stale_local"] = f"本机最新备份已有 {int(age)} 小时，超过 {int(stale)} 小时"
    uploaded = []
    try:
        present = set(rclone(["lsf", remote, "--files-only"]).split())
        for name in local:
            if name not in present:
                rclone(["copyto", str(directory / name), f"{remote}/{name}", "--immutable", "--checksum"])
                uploaded.append(name)
    except SyncError as error:
        problems["sync"] = f"同步到 R2 失败：{error}"
    recovered = f"【Easel 备份恢复正常】最新备份 {local[-1]} 已同步到 R2" if local else ""
    _report(config, problems, current, notify, recovered)
    return {"problems": [problems[code] for code in sorted(problems)], "uploaded": uploaded, "local": len(local)}


def health_reader(host):
    def read():
        try:
            result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", host,
                                     "cat /var/lib/easel-health/status.json"],
                                    capture_output=True, text=True, timeout=40)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise SyncError(type(error).__name__) from None
        if result.returncode != 0:
            raise SyncError("ssh 退出码 %d" % result.returncode)
        return json.loads(result.stdout)
    return read


def health_problems(read, current):
    try:
        value = read()
    except (SyncError, ValueError) as error:
        return {"health_unreachable": f"无法读取服务器巡检结果：{error}"}
    if current - value.get("checked_at", 0) > HEALTH_STALE_SECONDS:
        return {"health_stale": "服务器巡检已超过 30 分钟没有更新"}
    return {"health:" + alert["code"]: "服务器巡检：" + HEALTH_LABELS.get(alert["code"], alert["code"])
            for alert in value.get("alerts", []) if alert.get("severity") == "error"}


def remote_problems(rclone, remote, current, stale, *, label="", prefix=""):
    """Return the problems for one R2 prefix and the newest backup found there."""
    try:
        names = sorted(name for name in rclone(["lsf", remote.rstrip("/"), "--files-only"]).split()
                       if NAME.match(name))
    except SyncError as error:
        return {prefix + "r2": f"无法读取 R2：{error}"}, None
    if not names:
        return {prefix + "no_remote": f"R2 上没有找到{label}备份"}, None
    if (age := age_hours(names[-1], current)) > stale:
        return {prefix + "stale_remote": (f"R2 上最新{label}备份已有 {int(age)} 小时，超过 {int(stale)} 小时"
                                          "（服务器备份或同步可能已停止）")}, names[-1]
    return {}, names[-1]


def watch(config, *, rclone, now=time.time, notify, read_health=None):
    current = now()
    stale = float(config["STALE_HOURS"])
    problems, newest = remote_problems(rclone, config["R2_REMOTE"], current, stale)
    if config.get("NEWAPI_R2_REMOTE"):
        problems.update(remote_problems(rclone, config["NEWAPI_R2_REMOTE"], current, stale,
                                        label="网关", prefix="newapi_")[0])
    if read_health is not None:
        problems.update(health_problems(read_health, current))
    _report(config, problems, current, notify, f"【Easel 已恢复正常】服务器巡检和备份正常，R2 最新备份 {newest}",
            label="运行")
    return {"problems": [problems[code] for code in sorted(problems)], "newest": newest}


def _report(config, problems, current, notify, recovered, label="备份"):
    state = _state(config["STATE_FILE"])
    previous = state.get("problems", [])
    codes = sorted(problems)
    if codes and (codes != previous or current - state.get("alerted_at", 0) >= REPEAT_SECONDS):
        if notify(f"【Easel {label}告警】\n" + "\n".join(problems[code] for code in codes)):
            state["alerted_at"] = current
    elif not codes and previous:
        notify(recovered)
        state.pop("alerted_at", None)
    state["problems"] = codes
    _save_state(config["STATE_FILE"], state)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="/etc/easel-backup/backup.env")
    parser.add_argument("--watch", action="store_true", help="Check R2 freshness instead of uploading")
    args = parser.parse_args()
    if args.watch:
        defaults = {**DEFAULTS, "STATE_FILE": str(Path.home() / "Easel-build/backup-drill/watch-state.json")}
        config = {key: os.path.expanduser(value) for key, value in read_config(args.config, defaults).items()}
    else:
        config = read_config(args.config)
    notify = feishu.notifier(config)
    if notify is None:
        if args.watch:
            raise SyncError("配置缺少 FEISHU_USER 或 FEISHU_WEBHOOK")
        notify = lambda text: False  # the Mac watch reports a server that stops syncing
    rclone = rclone_runner(config["RCLONE_CONFIG"])
    if args.watch:
        reader = health_reader(config["HEALTH_SSH"]) if config.get("HEALTH_SSH") else None
        result = watch(config, rclone=rclone, notify=notify, read_health=reader)
    else:
        result = run(config, rclone=rclone, notify=notify)
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
