#!/usr/bin/env python3
"""Copy new sealed backups to Cloudflare R2, and watch R2 from another machine.

Server (hourly, default): uploads are add-only (`--immutable`, never deletes);
the envelopes are already encrypted with the off-host public key.
Watch (`--watch`, hourly on the Mac that holds the private key): alerts when
the newest backup in R2 is older than STALE_HOURS or R2 cannot be read, which
also covers a server that has stopped backing up or syncing.

Either way an alert is sent when the set of problems changes, repeated once a
day while unresolved, and a recovery message is sent when healthy again.

Private config (server /etc/easel-backup/backup.env, Mac drill.env; 0600):
  R2_REMOTE=r2:<bucket>/central          rclone remote path
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


def watch(config, *, rclone, now=time.time, notify):
    current = now()
    stale = float(config["STALE_HOURS"])
    problems, newest = {}, None
    try:
        names = sorted(name for name in rclone(["lsf", config["R2_REMOTE"].rstrip("/"), "--files-only"]).split()
                       if NAME.match(name))
    except SyncError as error:
        problems["r2"] = f"无法读取 R2：{error}"
    else:
        if not names:
            problems["no_remote"] = "R2 上没有找到备份"
        else:
            newest = names[-1]
            if (age := age_hours(newest, current)) > stale:
                problems["stale_remote"] = (f"R2 上最新备份已有 {int(age)} 小时，超过 {int(stale)} 小时"
                                            "（服务器备份或同步可能已停止）")
    _report(config, problems, current, notify, f"【Easel 备份恢复正常】R2 最新备份 {newest}")
    return {"problems": [problems[code] for code in sorted(problems)], "newest": newest}


def _report(config, problems, current, notify, recovered):
    state = _state(config["STATE_FILE"])
    previous = state.get("problems", [])
    codes = sorted(problems)
    if codes and (codes != previous or current - state.get("alerted_at", 0) >= REPEAT_SECONDS):
        if notify("【Easel 备份告警】\n" + "\n".join(problems[code] for code in codes)):
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
    task = watch if args.watch else run
    result = task(config, rclone=rclone_runner(config["RCLONE_CONFIG"]), notify=notify)
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
