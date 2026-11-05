#!/usr/bin/env python3
"""Copy new sealed backups to Cloudflare R2 and alert Feishu when backups go stale or sync fails.

Runs hourly. Uploads are add-only (`--immutable`, never deletes); the envelopes
are already encrypted with the off-host public key. An alert is sent when the
set of problems changes, repeated once a day while unresolved, and a recovery
message is sent when everything is healthy again.

Private config (/etc/easel-backup/backup.env, 0600):
  R2_REMOTE=r2:<bucket>/central          rclone remote path
  FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/...
  FEISHU_SECRET=...                        optional signing secret
  RCLONE_CONFIG=/etc/easel-backup/rclone.conf
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
REQUIRED = ("R2_REMOTE", "FEISHU_WEBHOOK")
DEFAULTS = {"BACKUP_DIR": "/var/backups/easel-encrypted", "STALE_HOURS": "30",
            "STATE_FILE": "/var/lib/easel-backup/state.json", "RCLONE_CONFIG": "/etc/easel-backup/rclone.conf"}
REPEAT_SECONDS = 24 * 3600


class SyncError(Exception):
    pass


def read_config(path):
    values = dict(DEFAULTS)
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

    state = _state(config["STATE_FILE"])
    previous = state.get("problems", [])
    codes = sorted(problems)
    if codes and (codes != previous or current - state.get("alerted_at", 0) >= REPEAT_SECONDS):
        if notify("【Easel 备份告警】\n" + "\n".join(problems[code] for code in codes)):
            state["alerted_at"] = current
    elif not codes and previous:
        notify(f"【Easel 备份恢复正常】最新备份 {local[-1]} 已同步到 R2")
        state.pop("alerted_at", None)
    state["problems"] = codes
    _save_state(config["STATE_FILE"], state)
    return {"problems": [problems[code] for code in codes], "uploaded": uploaded, "local": len(local)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="/etc/easel-backup/backup.env")
    args = parser.parse_args()
    config = read_config(args.config)
    notify = lambda text: feishu.send(config["FEISHU_WEBHOOK"], text, config.get("FEISHU_SECRET") or None)
    result = run(config, rclone=rclone_runner(config["RCLONE_CONFIG"]), notify=notify)
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
