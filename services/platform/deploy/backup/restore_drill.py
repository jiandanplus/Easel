#!/usr/bin/env python3
"""Weekly restore drill on the machine that holds the backup private key.

Takes the newest sealed backup from R2, opens it with the private key, checks
every archive member against the manifest, restores roles and both databases
into a throwaway PostgreSQL 16 container with no network, verifies the credit
ledger, removes the container and reports the result to Feishu.

Private config (~/Easel-build/backup-drill/drill.env, 0600):
  R2_REMOTE=r2:<bucket>/central
  RCLONE_CONFIG=~/Easel-build/backup-drill/rclone.conf   (read-only R2 token)
  PRIVATE_KEY=~/Easel-build/backup-keys/backup-private.pem
  FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/...
  FEISHU_SECRET=...                                        optional
"""
import argparse
import hashlib
import importlib.util
import io
import json
import os
import re
import secrets
import subprocess
import sys
import tarfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import feishu  # noqa: E402
from offsite_sync import NAME, SyncError, age_hours, rclone_runner  # noqa: E402

TARGET = "ser362540173134"
IMAGE = "postgres@sha256:9e2cf6287f25c3151f4c9421ccce2b17f3e60061c111a29165e0d2cdd9ff0a97"
KINDS = ("central", "identity")
MAX_AGE_HOURS = 8 * 24
LEDGER_SQL = """
WITH entries AS (
  SELECT billing_account_id AS account, bucket, sum(delta) AS total
  FROM credit_entries WHERE bucket <> 'issuance' GROUP BY 1, 2),
lots AS (
  SELECT billing_account_id AS account, x.bucket, sum(x.amount) AS total
  FROM credit_lots, LATERAL (VALUES ('available', available), ('held', held),
                                    ('spent', spent), ('expired', expired)) AS x(bucket, amount)
  GROUP BY 1, 2)
SELECT json_build_object(
  'journal_unbalanced', (SELECT count(*) FROM (SELECT journal_id FROM credit_entries
                         GROUP BY journal_id HAVING sum(delta) <> 0) AS j),
  'bucket_mismatch', (SELECT count(*) FROM entries FULL JOIN lots USING (account, bucket)
                      WHERE coalesce(entries.total, 0) <> coalesce(lots.total, 0)),
  'issued_mismatch', (SELECT CASE WHEN coalesce(-(SELECT sum(delta) FROM credit_entries
                        WHERE bucket = 'issuance'), 0) = coalesce((SELECT sum(issued) FROM credit_lots), 0)
                      THEN 0 ELSE 1 END))
"""
ROW_COUNT_SQL = """
SELECT json_build_object('tables', count(*), 'rows', coalesce(sum((xpath('/row/c/text()',
  query_to_xml(format('SELECT count(*) AS c FROM %I.%I', table_schema, table_name), false, true, '')))[1]::text::bigint), 0))
FROM information_schema.tables
WHERE table_type = 'BASE TABLE' AND table_schema NOT IN ('pg_catalog', 'information_schema')
"""


class DrillError(Exception):
    pass


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def newest(names):
    valid = sorted(name for name in names if NAME.match(name))
    return valid[-1] if valid else None


def unseal(transfer, private_pem, envelope, filename):
    # The backup id is bound into the envelope; the filename is an independent copy of it.
    backup_id = filename[len("YYYYMMDDTHHMMSSZ-"):-len(".sealed")]
    return transfer.unseal(private_pem, envelope, backup_id, "central", TARGET)


def read_archive(payload):
    members = {}
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as source:
        for item in source.getmembers():
            if not item.isfile() or item.name in members:
                raise DrillError("备份包结构异常")
            members[item.name] = source.extractfile(item).read()
    try:
        manifest = json.loads(members.pop("manifest.json"))
    except (KeyError, ValueError):
        raise DrillError("备份包缺少清单") from None
    expected = {entry["name"]: entry for entry in manifest["files"]}
    if set(expected) != set(members):
        raise DrillError("备份包文件与清单不一致")
    for name, value in members.items():
        if len(value) != expected[name]["length"] or hashlib.sha256(value).hexdigest() != expected[name]["sha256"]:
            raise DrillError(f"备份包文件校验失败：{name}")
    return members, manifest


def ledger_problems(rows):
    problems = []
    if rows.get("journal_unbalanced"):
        problems.append(f"{rows['journal_unbalanced']} 笔凭证借贷不平")
    if rows.get("bucket_mismatch"):
        problems.append(f"{rows['bucket_mismatch']} 个钱包分账与点数批次不符")
    if rows.get("issued_mismatch"):
        problems.append("发放总额与点数批次不符")
    return problems


def _docker(args, data=None, timeout=600):
    result = subprocess.run(["docker", *args], input=data, capture_output=True, timeout=timeout)
    return result.returncode, result.stdout.decode(errors="replace"), result.stderr.decode(errors="replace")


def _sql(name, database, sql):
    code, out, err = _docker(["exec", "-i", name, "psql", "-U", "postgres", "-d", database, "-tA", "-v",
                              "ON_ERROR_STOP=1"], data=sql.encode())
    if code != 0:
        raise DrillError(f"{database} 查询失败：{err.strip()[-200:]}")
    return json.loads(out.strip())


def restore(members, manifest, docker=_docker):
    name = "easel-restore-drill-" + secrets.token_hex(4)
    databases = [snapshot["database"] for snapshot in manifest.get("snapshots", [])]
    if len(databases) != len(KINDS):
        raise DrillError("清单缺少数据库快照信息")
    code, _, err = docker(["run", "-d", "--rm", "--name", name, "--network", "none",
                           "-e", "POSTGRES_PASSWORD=" + secrets.token_hex(16), IMAGE])
    if code != 0:
        raise DrillError(f"无法启动临时数据库：{err.strip()[-200:]}")
    try:
        for _ in range(60):
            if docker(["exec", name, "pg_isready", "-U", "postgres"])[0] == 0:
                break
            time.sleep(1)
        else:
            raise DrillError("临时数据库未就绪")
        report = {}
        for kind, database in zip(KINDS, databases):
            code, _, err = docker(["exec", "-i", name, "psql", "-U", "postgres", "-q"],
                                  data=members[f"roles/{kind}.sql"])
            role_errors = [line for line in err.splitlines() if "ERROR" in line and "already exists" not in line]
            if code != 0 or role_errors:
                raise DrillError(f"{kind} 角色恢复失败：{(role_errors or [err.strip()])[-1][-200:]}")
            if docker(["exec", name, "createdb", "-U", "postgres", database])[0] != 0:
                raise DrillError(f"无法创建 {database}")
            code, _, err = docker(["exec", "-i", name, "pg_restore", "-U", "postgres", "-d", database,
                                   "--exit-on-error"], data=members[f"databases/{kind}.dump"])
            if code != 0:
                raise DrillError(f"{kind} 数据恢复失败：{err.strip()[-200:]}")
            counts = _sql(name, database, ROW_COUNT_SQL)
            if not counts["tables"]:
                raise DrillError(f"{kind} 恢复后没有数据表")
            report[kind] = counts
            if kind == "central":
                has_ledger = _sql(name, database, "SELECT to_json(to_regclass('public.credit_entries') IS NOT NULL)")
                report["ledger"] = ledger_problems(_sql(name, database, LEDGER_SQL)) if has_ledger else None
        return report
    finally:
        docker(["rm", "-f", name])


def message(name, age, report, seconds):
    lines = [f"【Easel 恢复演练通过】备份 {name}（{int(age)} 小时前）"]
    labels = {"central": "中央库", "identity": "身份库"}
    for kind in KINDS:
        lines.append(f"{labels[kind]}：{report[kind]['tables']} 张表，{report[kind]['rows']} 行")
    lines.append("账本核对通过" if report.get("ledger") == [] else "账本核对：未找到账本表")
    lines.append(f"用时 {int(seconds)} 秒")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(Path.home() / "Easel-build/backup-drill/drill.env"))
    parser.add_argument("--transfer", default=str(HERE / "sealed_transfer.py"),
                        help="Copy of the frozen sealed_transfer.py used by the server")
    args = parser.parse_args()
    started = time.time()
    config = {key: os.path.expanduser(value) for key, value in _read_config(args.config).items()}
    notify = lambda text: feishu.send(config["FEISHU_WEBHOOK"], text, config.get("FEISHU_SECRET") or None)
    work = Path(config.get("WORK_DIR", str(Path.home() / "Easel-build/backup-drill/work")))
    work.mkdir(mode=0o700, parents=True, exist_ok=True)
    local = None
    try:
        rclone = rclone_runner(config["RCLONE_CONFIG"])
        remote = config["R2_REMOTE"].rstrip("/")
        name = newest(rclone(["lsf", remote, "--files-only"]).split())
        if name is None:
            raise DrillError("R2 上没有找到备份")
        age = age_hours(name, started)
        if age > MAX_AGE_HOURS:
            raise DrillError(f"R2 上最新备份已有 {int(age)} 小时")
        local = work / name
        rclone(["copyto", f"{remote}/{name}", str(local), "--checksum"])
        transfer = load_module(args.transfer, "easel_sealed_transfer")
        payload = unseal(transfer, Path(config["PRIVATE_KEY"]).read_bytes(), local.read_bytes(), name)
        members, manifest = read_archive(payload)
        report = restore(members, manifest)
        if report.get("ledger"):
            raise DrillError("账本核对失败：" + "；".join(report["ledger"]))
        text = message(name, age, report, time.time() - started)
        ok = True
    except (DrillError, SyncError, OSError, ValueError) as error:
        text, ok = f"【Easel 恢复演练失败】{error}", False
    except Exception as error:  # Opening the envelope fails closed with library-specific errors.
        text, ok = f"【Easel 恢复演练失败】{type(error).__name__}", False
    finally:
        if local is not None:
            local.unlink(missing_ok=True)
    notify(text)
    print(text)
    return 0 if ok else 1


def _read_config(path):
    values = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    missing = [key for key in ("R2_REMOTE", "RCLONE_CONFIG", "PRIVATE_KEY", "FEISHU_WEBHOOK") if not values.get(key)]
    if missing:
        raise DrillError("配置缺少 " + "、".join(missing))
    return values


if __name__ == "__main__":
    raise SystemExit(main())
