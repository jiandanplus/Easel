"""Off-host backup: public-key-only sealing, R2 sync, Feishu alerts and the restore drill."""
import base64
import hashlib
import hmac
import io
import json
import sys
import tarfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

DEPLOY = Path(__file__).resolve().parents[1] / "deploy" / "backup"
LEGACY = Path(__file__).resolve().parents[1] / "src/platform_service/media_backup_b2/legacy_sources"
sys.path.insert(0, str(DEPLOY))

import feishu  # noqa: E402
import offsite_sync  # noqa: E402
import restore_drill  # noqa: E402
import sealed_backup  # noqa: E402

TARGET = "ser362540173134"


def rsa_pair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.PEM,
                                           serialization.PublicFormat.SubjectPublicKeyInfo)
    return public, private


def helper():
    return restore_drill.load_module(LEGACY / "sealed_transfer.py", "sealed_transfer_test")


# ---- Feishu ----

def test_feishu_payload_signs_with_timestamp_when_secret_given():
    body = feishu.payload("备份失败", secret="s3cret", now=lambda: 1700000000)
    expected = base64.b64encode(hmac.new(b"1700000000\ns3cret", b"", hashlib.sha256).digest()).decode()
    assert body == {"msg_type": "text", "content": {"text": "备份失败"}, "timestamp": "1700000000", "sign": expected}
    assert "sign" not in feishu.payload("x")


def test_feishu_send_reports_api_level_failure():
    ok = feishu.send("https://open.feishu.cn/hook/x", "t", post=lambda url, data: {"code": 0})
    bad = feishu.send("https://open.feishu.cn/hook/x", "t", post=lambda url, data: {"code": 19021, "msg": "sign"})
    assert ok is True and bad is False
    assert feishu.send("http://insecure/hook", "t", post=lambda url, data: pytest.fail("no http")) is False


def test_feishu_lark_bot_direct_message_needs_an_accepted_reply():
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout='{"ok": true, "identity": "bot"}')
    assert feishu.send_lark("ou_abc", "备份失败", cli="/bin/lark-cli", run=run) is True
    assert calls == [["/bin/lark-cli", "im", "+messages-send", "--as", "bot", "--user-id", "ou_abc", "--text", "备份失败"]]
    refused = lambda args, **kwargs: SimpleNamespace(returncode=0, stdout='{"ok": false}')
    garbled = lambda args, **kwargs: SimpleNamespace(returncode=0, stdout="WARN proxy")
    def missing(args, **kwargs):
        raise FileNotFoundError(args[0])
    assert not any(feishu.send_lark("ou_abc", "t", run=r) for r in (refused, garbled, missing))
    assert feishu.send_lark("someone@example.com", "t", run=lambda *a, **k: pytest.fail("not an open_id")) is False


def test_feishu_notifier_prefers_lark_user_then_webhook():
    assert feishu.notifier({}) is None
    assert feishu.notifier({"FEISHU_USER": "", "FEISHU_WEBHOOK": ""}) is None
    assert callable(feishu.notifier({"FEISHU_USER": "ou_abc"}))
    assert callable(feishu.notifier({"FEISHU_WEBHOOK": "https://open.feishu.cn/hook/x"}))


# ---- public-key-only sealing ----

def fake_backup(payload=b"tar-bytes"):
    calls = SimpleNamespace(saved=None, verified=[])
    def check(condition):
        if not condition:
            raise RuntimeError("backup rejected")
    def save(envelope, backup_id, verify):
        verify(envelope)
        calls.saved = (envelope, backup_id)
        return f"/var/backups/easel-encrypted/20261105T000000Z-{backup_id}.sealed"
    module = SimpleNamespace(
        TARGET=TARGET, ROOT=Path("/root/x"), check=check,
        collect=lambda: ({"databases/central.dump": b"PGDMP"}, {"files": []}),
        archive=lambda entries, manifest: payload,
        verify_payload=lambda value, manifest: calls.verified.append(value),
        crypto=lambda: SimpleNamespace(seal=lambda public, plaintext, metadata: b"ENV" + metadata["transfer_id"].encode()),
        save=save, digest=lambda value: hashlib.sha256(value).hexdigest())
    return module, calls


def test_sealed_backup_needs_no_private_key_and_verifies_plaintext_first():
    module, calls = fake_backup()
    result = sealed_backup.run(module, b"PUBLIC", euid=0, hostname=TARGET)
    assert calls.verified == [b"tar-bytes"]
    assert calls.saved[0] == b"ENV" + calls.saved[1].encode()
    assert result["status"] == "sealed" and result["self_verify"] == "bytes"


def test_sealed_backup_refuses_wrong_host_or_user():
    module, _ = fake_backup()
    with pytest.raises(RuntimeError):
        sealed_backup.run(module, b"PUBLIC", euid=1000, hostname=TARGET)
    with pytest.raises(RuntimeError):
        sealed_backup.run(module, b"PUBLIC", euid=0, hostname="other")


def test_sealed_backup_loads_only_the_pinned_frozen_script(tmp_path):
    assert sealed_backup.FROZEN.name == "easel_encrypted_backup.py"  # the file name on the server
    module = sealed_backup.load_frozen(LEGACY / "encrypted_backup.py")
    assert module.TARGET == TARGET and callable(module.collect) and callable(module.save)
    tampered = tmp_path / "easel_encrypted_backup.py"
    tampered.write_bytes((LEGACY / "encrypted_backup.py").read_bytes() + b"\n")
    with pytest.raises(sealed_backup.FrozenMismatch):
        sealed_backup.load_frozen(tampered)


def test_real_envelope_from_public_key_opens_only_with_private_key():
    public, private = rsa_pair()
    transfer = helper()
    backup_id = str(uuid.uuid4())
    payload = b"payload"
    metadata = {"transfer_id": backup_id, "kind": "central", "target": TARGET,
                "expected_sha256": hashlib.sha256(payload).hexdigest(), "expected_length": len(payload)}
    envelope = transfer.seal(public, payload, metadata)
    assert restore_drill.unseal(transfer, private, envelope, f"20261105T000000Z-{backup_id}.sealed") == payload
    _, other = rsa_pair()
    with pytest.raises(Exception):
        restore_drill.unseal(transfer, other, envelope, f"20261105T000000Z-{backup_id}.sealed")


# ---- R2 sync and alerting ----

def sealed_name(hours_ago, now):
    import datetime as dt
    stamp = dt.datetime.fromtimestamp(now - hours_ago * 3600, dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4()}.sealed"


class FakeRclone:
    def __init__(self, remote=(), fail=False):
        self.remote = set(remote)
        self.fail = fail
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        if self.fail:
            raise offsite_sync.SyncError("rclone 失败")
        if args[0] == "lsf":
            return "\n".join(sorted(self.remote))
        if args[0] == "copyto":
            self.remote.add(Path(args[2]).name)
            return ""
        raise AssertionError(args)


def config(tmp_path):
    backups = tmp_path / "backups"
    backups.mkdir()
    return {"BACKUP_DIR": str(backups), "R2_REMOTE": "r2:easel-backups/central", "STALE_HOURS": "30",
            "STATE_FILE": str(tmp_path / "state.json"), "FEISHU_WEBHOOK": "https://open.feishu.cn/hook/x"}


def test_sync_uploads_only_missing_backups_and_stays_quiet_when_healthy(tmp_path):
    now = 1_800_000_000
    cfg = config(tmp_path)
    old, new = sealed_name(30, now), sealed_name(2, now)
    for name in (old, new, "notes.txt"):
        (Path(cfg["BACKUP_DIR"]) / name).write_bytes(b"x")
    rclone = FakeRclone(remote={old})
    sent = []
    result = offsite_sync.run(cfg, rclone=rclone, now=lambda: now, notify=lambda text: sent.append(text) or True)
    assert [c for c in rclone.calls if c[0] == "copyto"] == [["copyto", str(Path(cfg["BACKUP_DIR"]) / new), f"r2:easel-backups/central/{new}", "--immutable", "--checksum"]]
    assert result["problems"] == [] and sent == []


def test_sync_alerts_once_on_stale_backup_and_again_on_recovery(tmp_path):
    now = 1_800_000_000
    cfg = config(tmp_path)
    stale = sealed_name(40, now)
    (Path(cfg["BACKUP_DIR"]) / stale).write_bytes(b"x")
    sent = []
    notify = lambda text: sent.append(text) or True
    offsite_sync.run(cfg, rclone=FakeRclone(remote={stale}), now=lambda: now, notify=notify)
    offsite_sync.run(cfg, rclone=FakeRclone(remote={stale}), now=lambda: now + 3600, notify=notify)
    assert len(sent) == 1 and "40" in sent[0] and "告警" in sent[0]
    fresh = sealed_name(1, now + 7200)
    (Path(cfg["BACKUP_DIR"]) / fresh).write_bytes(b"x")
    offsite_sync.run(cfg, rclone=FakeRclone(remote={stale, fresh}), now=lambda: now + 7200, notify=notify)
    assert len(sent) == 2 and "恢复" in sent[1]


def test_sync_repeats_unresolved_alert_daily(tmp_path):
    now = 1_800_000_000
    cfg = config(tmp_path)
    sent = []
    notify = lambda text: sent.append(text) or True
    offsite_sync.run(cfg, rclone=FakeRclone(), now=lambda: now, notify=notify)
    offsite_sync.run(cfg, rclone=FakeRclone(), now=lambda: now + 23 * 3600, notify=notify)
    offsite_sync.run(cfg, rclone=FakeRclone(), now=lambda: now + 25 * 3600, notify=notify)
    assert len(sent) == 2 and "没有找到" in sent[0]


def test_sync_reports_rclone_failure(tmp_path):
    now = 1_800_000_000
    cfg = config(tmp_path)
    (Path(cfg["BACKUP_DIR"]) / sealed_name(1, now)).write_bytes(b"x")
    sent = []
    result = offsite_sync.run(cfg, rclone=FakeRclone(fail=True), now=lambda: now, notify=lambda t: sent.append(t) or True)
    assert any("R2" in problem for problem in result["problems"]) and len(sent) == 1


def test_config_parser_ignores_comments_and_requires_keys(tmp_path):
    path = tmp_path / "backup.env"
    path.write_text("# 注释\nR2_REMOTE=r2:b/c\nFEISHU_WEBHOOK=https://open.feishu.cn/hook/x\n")
    assert offsite_sync.read_config(path)["R2_REMOTE"] == "r2:b/c"
    path.write_text("R2_REMOTE=r2:b/c\n")  # the server may leave Feishu to the Mac watch
    assert offsite_sync.read_config(path)["STALE_HOURS"] == "30"
    path.write_text("FEISHU_USER=ou_abc\n")
    with pytest.raises(offsite_sync.SyncError):
        offsite_sync.read_config(path)


def test_watch_alerts_when_r2_goes_stale_or_unreadable_and_recovers(tmp_path):
    now = 1_800_000_000
    cfg = config(tmp_path)
    sent = []
    notify = lambda text: sent.append(text) or True
    fresh = sealed_name(3, now)
    assert offsite_sync.watch(cfg, rclone=FakeRclone(remote={fresh, "x.txt"}), now=lambda: now, notify=notify) == {
        "problems": [], "newest": fresh}
    assert sent == []
    later = now + 40 * 3600
    result = offsite_sync.watch(cfg, rclone=FakeRclone(remote={fresh}), now=lambda: later, notify=notify)
    assert "43" in result["problems"][0] and len(sent) == 1 and "告警" in sent[0]
    offsite_sync.watch(cfg, rclone=FakeRclone(fail=True), now=lambda: later + 3600, notify=notify)
    assert len(sent) == 2 and "无法读取 R2" in sent[1]
    newer = sealed_name(1, later + 7200)
    offsite_sync.watch(cfg, rclone=FakeRclone(remote={fresh, newer}), now=lambda: later + 7200, notify=notify)
    assert len(sent) == 3 and "恢复" in sent[2] and newer in sent[2]
    offsite_sync.watch(cfg, rclone=FakeRclone(remote=set()), now=lambda: later + 9000, notify=notify)
    assert "没有找到" in sent[3]


def test_watch_pushes_server_health_errors_but_not_warnings(tmp_path):
    now = 1_800_000_000
    cfg = config(tmp_path)
    sent = []
    notify = lambda text: sent.append(text) or True
    remote = FakeRclone(remote={sealed_name(3, now)})
    record = lambda *alerts: (lambda: {"checked_at": now - 60, "alerts": list(alerts)})
    offsite_sync.watch(cfg, rclone=remote, now=lambda: now, notify=notify,
                       read_health=record({"code": "memory_pressure", "severity": "warning"}))
    assert sent == []
    result = offsite_sync.watch(cfg, rclone=remote, now=lambda: now, notify=notify,
                                read_health=record({"code": "gateway_unhealthy", "severity": "error"}))
    assert result["problems"] == ["服务器巡检：模型网关不可用"] and "运行告警" in sent[0]
    offsite_sync.watch(cfg, rclone=remote, now=lambda: now, notify=notify, read_health=record())
    assert len(sent) == 2 and "恢复" in sent[1]


def test_watch_treats_a_silent_server_as_a_problem(tmp_path):
    now = 1_800_000_000
    remote = FakeRclone(remote={sealed_name(3, now)})

    def unreachable():
        raise offsite_sync.SyncError("ssh 退出码 255")
    cfg = config(tmp_path)
    run = lambda read: offsite_sync.watch(cfg, rclone=remote, now=lambda: now,
                                          notify=lambda text: True, read_health=read)["problems"]
    assert "无法读取服务器巡检结果" in run(unreachable)[0]
    assert "没有更新" in run(lambda: {"checked_at": now - 3600, "alerts": []})[0]


def test_watch_covers_the_gateway_backup_prefix(tmp_path):
    now = 1_800_000_000
    cfg = {**config(tmp_path), "NEWAPI_R2_REMOTE": "r2:bucket/newapi"}
    fresh, old = sealed_name(3, now), sealed_name(40, now)
    rclone = lambda args: (old if "newapi" in args[1] else fresh)
    result = offsite_sync.watch(cfg, rclone=rclone, now=lambda: now, notify=lambda text: True)
    assert len(result["problems"]) == 1 and "网关备份" in result["problems"][0] and result["newest"] == fresh


# ---- restore drill pieces ----

def archive(files):
    manifest = {"files": [{"name": n, "length": len(v), "sha256": hashlib.sha256(v).hexdigest()} for n, v in sorted(files.items())],
                "snapshots": [{"database": "easel_workbench_preview"}, {"database": "easel_logto_preview"}]}
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as out:
        for name, value in sorted({**files, "manifest.json": json.dumps(manifest).encode()}.items()):
            item = tarfile.TarInfo(name); item.size = len(value)
            out.addfile(item, io.BytesIO(value))
    return stream.getvalue()


def test_drill_reads_archive_and_checks_member_hashes():
    files = {"databases/central.dump": b"PGDMP1", "databases/identity.dump": b"PGDMP2",
             "roles/central.sql": b"CREATE ROLE a;", "roles/identity.sql": b"CREATE ROLE b;"}
    members, manifest = restore_drill.read_archive(archive(files))
    assert members["databases/central.dump"] == b"PGDMP1" and len(manifest["files"]) == 4
    tampered = archive(files).replace(b"PGDMP1", b"PGDMPX")
    with pytest.raises(restore_drill.DrillError):
        restore_drill.read_archive(tampered)


def test_drill_drops_grantor_from_role_grants_only():
    sql = (b"CREATE ROLE preview_owner;\n"
           b"GRANT tenant TO tenant_admin WITH INHERIT TRUE GRANTED BY preview_owner;\n"
           b'GRANT tenant TO "odd role" GRANTED BY "odd owner";\n'
           b"COMMENT ON ROLE x IS 'GRANTED BY y';\n")
    assert restore_drill.role_script(sql) == (
        b"CREATE ROLE preview_owner;\n"
        b"GRANT tenant TO tenant_admin WITH INHERIT TRUE;\n"
        b'GRANT tenant TO "odd role";\n'
        b"COMMENT ON ROLE x IS 'GRANTED BY y';\n")


def test_drill_picks_newest_backup_and_rejects_foreign_names():
    names = ["20261101T193000Z-%s.sealed" % uuid.uuid4(), "20261104T193000Z-%s.sealed" % uuid.uuid4(), "evil.sealed"]
    assert restore_drill.newest(names) == names[1]
    assert restore_drill.newest(["evil.sealed"]) is None


def test_ledger_check_runs_in_the_schema_that_holds_the_ledger():
    sql = restore_drill.ledger_sql('workbench_e6467e17dc33')
    assert sql.startswith('SET search_path TO "workbench_e6467e17dc33";\n')
    assert sql.endswith(restore_drill.LEDGER_SQL)
    assert restore_drill.ledger_sql('a"b').startswith('SET search_path TO "a""b";')


def test_ledger_check_sql_flags_unbalanced_rows():
    rows = {"journal_unbalanced": 0, "bucket_mismatch": 0, "issued_mismatch": 0}
    assert restore_drill.ledger_problems(rows) == []
    assert restore_drill.ledger_problems({**rows, "journal_unbalanced": 2}) == ["2 笔凭证借贷不平"]


def fake_docker(calls, failing=()):
    def docker(args, data=None, timeout=600):
        calls.append(args)
        return (1, "", "boom") if args[0] in failing or (len(args) > 3 and args[3] in failing) else (0, "", "")
    return docker


def test_gateway_restore_reports_counts_and_always_removes_the_container():
    calls = []
    counts = {"channels": 1, "tokens": 1, "users": 1}
    assert restore_drill.restore_newapi(b"PGDMP", fake_docker(calls), lambda *a: counts) == counts
    assert calls[-1][:2] == ["rm", "-f"]
    assert any("pg_restore" in args and "--no-owner" in args for args in calls)


@pytest.mark.parametrize("counts", [{"channels": 0, "tokens": 1, "users": 1}, {"channels": 1, "tokens": 0, "users": 1}])
def test_gateway_restore_fails_when_channels_or_tokens_are_gone(counts):
    with pytest.raises(restore_drill.DrillError, match="缺少数据"):
        restore_drill.restore_newapi(b"PGDMP", fake_docker([]), lambda *a: counts)


def test_gateway_restore_failure_still_removes_the_container():
    calls = []
    with pytest.raises(restore_drill.DrillError, match="网关库恢复失败"):
        restore_drill.restore_newapi(b"x", fake_docker(calls, failing=("pg_restore",)), lambda *a: {})
    assert calls[-1][:2] == ["rm", "-f"]
