import importlib.util

def test_reader_api_exists():
    assert importlib.util.find_spec("platform_service.media_backup_b3.network_reader") is not None, "fixed network reader is absent"

import json
import base64
import os
import stat
from pathlib import Path
from types import SimpleNamespace
import pytest
from platform_service.media_backup_b3.contracts import B3Error

FIXTURE = Path(__file__).parent / "fixtures/media_backup_b3/network/linuxkit-6.12.76-same-namespace-v1.public.json"

class FakeKernel:
    def __init__(self):
        self.fixture = json.loads(FIXTURE.read_text())
        self.fds = {}
        self.nextfd = 10
        self.calls = []
        self.count = 0
        self.fail_at = None
        self.content = {}
        for n, v in self.fixture["before"]["sysfs"].items():
            if v["kind"] == "interface":
                for k in ("ifindex", "iflink", "type", "flags", "operstate", "address"):
                    self.content[f"/sys/devices/virtual/net/{n}/{k}"] = (v[k] + "\n").encode()
        for k, name in (("dev", "dev"), ("addresses6", "if_inet6"), ("route4", "route"), ("route6", "ipv6_route"), ("fib_trie", "fib_trie")):
            self.content[f"/proc/self/net/{name}"] = self.fixture["before"]["proc"][k].encode()
    def event(self, name, *args):
        self.calls.append((name, *args))
        self.count += 1
        if self.count == self.fail_at:
            raise OSError("secret")
    def path(self, path, dir_fd):
        return path if dir_fd is None else self.fds[dir_fd][0] + "/" + path
    def open(self, path, flags, *, dir_fd=None):
        self.event("open", path, flags, dir_fd)
        p = self.path(path, dir_fd)
        fd = self.nextfd; self.nextfd += 1
        self.fds[fd] = [p, 0]
        return fd
    def close(self, fd):
        self.event("close", fd)
        del self.fds[fd]
    def read(self, fd, cap):
        self.event("read", fd, cap)
        p, pos = self.fds[fd]
        result = self.content[p][pos:pos + min(cap, 17)]
        self.fds[fd][1] += len(result)
        return result
    def info(self, p, follow=True):
        mode = stat.S_IFDIR | 0o755
        if p == "/proc/self/ns/net": mode = stat.S_IFREG | 0o444
        if p in self.content: mode = stat.S_IFREG | 0o444
        if p == "/sys/class/net/bonding_masters": mode = stat.S_IFREG | 0o644
        if p.startswith("/sys/class/net/") and p != "/sys/class/net/bonding_masters":
            if not follow: mode = stat.S_IFLNK | 0o777
            else: p = "/sys/devices/virtual/net/" + p.rsplit("/", 1)[1]
        return SimpleNamespace(st_mode=mode, st_uid=0, st_gid=0, st_dev=1, st_ino=sum((i+1)*ord(c) for i,c in enumerate(p)))
    def stat(self, p, *, dir_fd=None, follow_symlinks=True):
        self.event("stat", p, dir_fd, follow_symlinks)
        return self.info(self.path(p, dir_fd), follow_symlinks)
    def fstat(self, fd):
        self.event("fstat", fd)
        return self.info(self.fds[fd][0])
    def readlink(self, p, *, dir_fd=None):
        self.event("readlink", p, dir_fd)
        return "../../devices/virtual/net/" + p
    def listdir(self, fd):
        self.event("listdir", fd)
        return list(self.fixture["before"]["sysfs"])
    def uname(self):
        self.event("uname")
        return SimpleNamespace(release=self.fixture["kernel_release"])

@pytest.fixture
def fake(monkeypatch):
    from platform_service.media_backup_b3 import network_reader as m
    k = FakeKernel()
    proxy = SimpleNamespace(**{name:getattr(os, name) for name in ("O_RDONLY", "O_CLOEXEC", "O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")})
    for name in ("open", "close", "read", "stat", "fstat", "readlink", "listdir", "uname"):
        setattr(proxy, name, getattr(k, name))
    monkeypatch.setattr(m, "os", proxy)
    monkeypatch.setattr(m, "_monotonic_ns", lambda: 1)
    from platform_service.media_backup_b3.netlink_observer import NetworkDump
    from platform_service.media_backup_b3.netlink_contract import parse_dump
    dumps = []
    for d in k.fixture["dumps"]:
        args = tuple(d[key] for key in ("request_type", "family", "sequence", "local_portid"))
        packets = tuple(base64.b64decode(p) for p in d["packets"])
        dumps.append(NetworkDump(*args, packets, parse_dump(*args, packets)))
    monkeypatch.setattr(m, "observe_linux_network", lambda deadline: (k.event("dump", deadline), tuple(dumps))[1])
    return m, k

def test_complete_fake_pass_and_original_deadline(fake):
    m, k = fake
    result = m.observe_worker_network(10_000)
    assert result.proc_before.dev == k.fixture["before"]["proc"]["dev"].encode()
    assert len(result.sysfs_before) == len(result.sysfs_after) == 11
    assert len(result.dumps) == 6
    from platform_service.media_backup_b3.network_profile import validate_network_profile
    validate_network_profile(result)
    assert ("dump", 10_000) in k.calls
    assert not k.fds
    assert all(call[2] <= 65537 for call in k.calls if call[0] == "read")

def test_unknown_entry_is_rejected_before_child_access(fake):
    m, k = fake
    original = k.listdir
    k.listdir = lambda fd: original(fd) + ["../bad"]
    m.os.listdir = k.listdir
    with pytest.raises(B3Error, match="invalid B3 network reader"):
        m.observe_worker_network(10_000)
    assert not any(call[0] == "open" and call[1] == "../bad" for call in k.calls)
    assert not k.fds

@pytest.mark.parametrize("value", [b"01\n", b"1 \n", b"", b"1" * 129])
def test_invalid_scalar_fails_closed(fake, value):
    m, k = fake
    k.content["/sys/devices/virtual/net/lo/ifindex"] = value
    with pytest.raises(B3Error, match="invalid B3 network reader"):
        m.observe_worker_network(10_000)
    assert not k.fds

def test_boolean_deadline_rejected(fake):
    m, k = fake
    with pytest.raises(B3Error): m.observe_worker_network(True)
    assert not k.calls

def test_native_fault_boundaries_close_all_owned(fake, monkeypatch):
    m, baseline = fake
    m.observe_worker_network(10_000)
    positions = [i for i, call in enumerate(baseline.calls, 1) if call[0] != "close"]
    for fail_at in positions:
        k = FakeKernel()
        k.fail_at = fail_at
        for name in ("open", "close", "read", "stat", "fstat", "readlink", "listdir", "uname"):
            setattr(m.os, name, getattr(k, name))
        monkeypatch.setattr(m, "observe_linux_network", lambda deadline: (k.event("dump", deadline), ())[1])
        with pytest.raises(B3Error, match="invalid B3 network reader") as exc:
            m.observe_worker_network(10_000)
        assert exc.value.__cause__ is None
        assert not k.fds

def test_close_failure_forbids_return_and_attempts_remaining_closes(fake):
    m, k = fake
    close = k.close
    failed = []
    def broken(fd):
        if not failed:
            failed.append(fd)
            k.event("close", fd)
            del k.fds[fd]
            raise OSError("secret")
        close(fd)
    m.os.close = broken
    with pytest.raises(B3Error): m.observe_worker_network(10_000)
    assert failed and not k.fds

def test_dump_failure_has_no_retry(fake, monkeypatch):
    m, k = fake
    def bad(deadline):
        k.event("dump", deadline)
        raise OSError("secret")
    monkeypatch.setattr(m, "observe_linux_network", bad)
    with pytest.raises(B3Error): m.observe_worker_network(10_000)
    assert sum(c[0] == "dump" for c in k.calls) == 1
    assert not k.fds

def test_expired_during_operation_still_closes(fake, monkeypatch):
    m, k = fake
    monkeypatch.setattr(m, "_monotonic_ns", lambda: 1 if len(k.calls) < 5 else 10_000)
    with pytest.raises(B3Error): m.observe_worker_network(10_000)
    assert not k.fds

def test_link_text_and_identity_drift_are_rejected(fake):
    m, k = fake
    m.os.readlink = lambda name, *, dir_fd: "../../other/" + name
    with pytest.raises(B3Error): m.observe_worker_network(10_000)
    assert not k.fds

@pytest.mark.parametrize("path", ["/sys", "/sys/class/net/bonding_masters", "/sys/devices/virtual/net/lo"])
def test_unsafe_metadata_rejected(fake, path):
    m, k = fake
    info = k.info
    def unsafe(p, follow=True):
        s = info(p, follow)
        if p == path: s.st_uid = 42
        return s
    k.info = unsafe
    with pytest.raises(B3Error): m.observe_worker_network(10_000)
    assert not k.fds

def test_before_after_values_retained(fake, monkeypatch):
    m, k = fake
    original = m.observe_linux_network
    def changed(deadline):
        dumps = original(deadline)
        k.content["/sys/devices/virtual/net/lo/ifindex"] = b"9\n"
        k.content["/proc/self/net/dev"] = b"changed\n"
        return dumps
    monkeypatch.setattr(m, "observe_linux_network", changed)
    result = m.observe_worker_network(10_000)
    assert result.proc_before.dev != result.proc_after.dev
    assert next(x.index for x in result.sysfs_before if x.name == "lo") == 1
    assert next(x.index for x in result.sysfs_after if x.name == "lo") == 9

@pytest.mark.parametrize("value", [b"", b"x" * 65537])
def test_proc_bounds(fake, value):
    m, k = fake
    k.content["/proc/self/net/dev"] = value
    with pytest.raises(B3Error): m.observe_worker_network(10_000)
    assert not k.fds

def test_baseexception_propagates_after_cleanup(fake, monkeypatch):
    m, k = fake
    class Stop(BaseException): pass
    def stop(deadline): raise Stop()
    monkeypatch.setattr(m, "observe_linux_network", stop)
    with pytest.raises(Stop): m.observe_worker_network(10_000)
    assert not k.fds

def test_flags_paths_and_order(fake):
    m, k = fake
    m.observe_worker_network(10_000)
    calls = k.calls
    opens = [c for c in calls if c[0] == "open"]
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
    assert opens[0] == ("open", "/sys", flags, None)
    assert any(c[1] == "/proc/self/net" and c[2] == flags and c[3] is None for c in opens)
    assert all(c[2] & os.O_NOFOLLOW and c[2] & os.O_CLOEXEC for c in opens)
    dump = next(i for i,c in enumerate(calls) if c[0] == "dump")
    assert any(c[0] == "read" for c in calls[:dump]) and any(c[0] == "read" for c in calls[dump+1:])
    assert all(c[2] <= 65537 for c in calls if c[0] == "read")

def test_interface_target_identity_drift_between_passes(fake, monkeypatch):
    m, k = fake
    original = m.observe_linux_network
    info = k.info
    changed = [False]
    def drift(p, follow=True):
        s = info(p, follow)
        if changed[0] and (p == "/sys/devices/virtual/net/lo" or (p == "/sys/class/net/lo" and follow)):
            s.st_ino += 100
        return s
    k.info = drift
    def switch(deadline):
        dumps = original(deadline)
        changed[0] = True
        return dumps
    monkeypatch.setattr(m, "observe_linux_network", switch)
    with pytest.raises(B3Error): m.observe_worker_network(10_000)
    assert not k.fds

def test_proc_directory_may_belong_to_current_user(fake):
    m, k = fake
    info = k.info
    def proc_owner(p, follow=True):
        s = info(p, follow)
        if p == "/proc/self/net": s.st_uid = s.st_gid = 10001
        return s
    k.info = proc_owner
    m.observe_worker_network(10_000)
    assert not k.fds

def test_module_import_does_not_invoke_native_primitives(monkeypatch):
    import importlib
    import socket
    from platform_service.media_backup_b3 import network_reader as m
    def forbidden(*args, **kwargs): raise AssertionError("native I/O during import")
    for name in ("open", "read", "stat", "fstat", "listdir", "uname"):
        monkeypatch.setattr(os, name, forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    importlib.reload(m)

def test_every_close_failure_forbids_result_but_attempts_rest(fake, monkeypatch):
    m, k = fake
    m.observe_worker_network(10_000)
    count = sum(c[0] == "close" for c in k.calls)
    for fail_index in range(count):
        f = FakeKernel()
        for name in ("open", "read", "stat", "fstat", "readlink", "listdir", "uname"):
            setattr(m.os, name, getattr(f, name))
        index = [0]
        def close(fd):
            current = index[0]; index[0] += 1
            f.close(fd)
            if current == fail_index: raise OSError("secret")
        m.os.close = close
        monkeypatch.setattr(m, "observe_linux_network", lambda deadline: (f.event("dump", deadline), ())[1])
        with pytest.raises(B3Error): m.observe_worker_network(10_000)
        assert index[0] == count and not f.fds

@pytest.mark.parametrize("deadline", [0, -1, True, 60_000_000_002, 2**63, 1.0])
def test_deadline_domain(fake, deadline):
    m, k = fake
    with pytest.raises(B3Error): m.observe_worker_network(deadline)
    assert not k.calls

@pytest.mark.parametrize("field,value", [("flags", b"0X9\n"), ("flags", b"0x09\n"), ("address", b"AA:00\n"), ("operstate", b"UP\n")])
def test_ascii_scalar_grammar(fake, field, value):
    m, k = fake
    k.content["/sys/devices/virtual/net/lo/" + field] = value
    with pytest.raises(B3Error): m.observe_worker_network(10_000)
    assert not k.fds

# M1: direct assertions for native data boundaries and acquisition order.
def test_namespace_identity_drift_is_retained_around_dump(fake, monkeypatch):
    m, k = fake
    original_stat = k.stat
    original_dump = m.observe_linux_network
    after = [False]
    def changed_stat(path, *, dir_fd=None, follow_symlinks=True):
        s = original_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
        if path == "/proc/self/ns/net" and after[0]: s.st_ino += 1
        return s
    def dump(deadline):
        value = original_dump(deadline)
        after[0] = True
        return value
    m.os.stat = changed_stat
    monkeypatch.setattr(m, "observe_linux_network", dump)
    result = m.observe_worker_network(10_000)
    assert result.namespace_before.device == result.namespace_after.device
    assert result.namespace_before.inode + 1 == result.namespace_after.inode
    ns = [i for i,c in enumerate(k.calls) if c[0] == "stat" and c[1] == "/proc/self/ns/net"]
    d = next(i for i,c in enumerate(k.calls) if c[0] == "dump")
    assert len(ns) == 2 and ns[0] < d < ns[1]
    assert not k.fds

@pytest.mark.parametrize("path,new_type", [
    ("/sys/class", stat.S_IFREG),
    ("/sys/class/net/bonding_masters", stat.S_IFLNK),
    ("/sys/devices/virtual/net/lo", stat.S_IFREG),
])
def test_wrong_inode_type_is_rejected(fake, path, new_type):
    m, k = fake
    info = k.info
    def changed(p, follow=True):
        s = info(p, follow)
        if p == path: s.st_mode = new_type | 0o755
        return s
    k.info = changed
    with pytest.raises(B3Error, match="invalid B3 network reader"):
        m.observe_worker_network(10_000)
    assert not k.fds

def test_valid_grammar_scalar_over_cap_is_rejected(fake):
    m, k = fake
    value = b"1" * 128 + b"\n"
    assert len(value) == 129 and value[:-1].isdigit()
    k.content["/sys/devices/virtual/net/lo/ifindex"] = value
    with pytest.raises(B3Error, match="invalid B3 network reader"):
        m.observe_worker_network(10_000)
    assert not k.fds

def test_every_child_anchor_read_cap_and_complete_order(fake):
    m, k = fake
    m.observe_worker_network(10_000)
    descriptors = {}
    next_fd = 10
    opened = []
    read_kinds = set()
    for call in k.calls:
        if call[0] == "open":
            _, name, flags, parent = call
            if parent is None:
                assert name in ("/sys", "/proc/self/net")
                path = name
            else:
                assert parent in descriptors
                assert "/" not in name and name not in (".", "..")
                path = descriptors[parent] + "/" + name
            descriptors[next_fd] = path
            next_fd += 1
            opened.append(path)
        elif call[0] == "read":
            _, fd, cap = call
            path = descriptors[fd]
            if path.startswith("/proc/self/net/"):
                assert 0 < cap <= 65537
                read_kinds.add("proc")
            else:
                assert path.startswith("/sys/devices/virtual/net/")
                assert 0 < cap <= 129
                read_kinds.add("scalar")
    assert read_kinds == {"scalar", "proc"}
    roots = ["/sys", "/sys/class", "/sys/class/net", "/sys/devices", "/sys/devices/virtual", "/sys/devices/virtual/net", "/proc/self/net"]
    names = sorted(n for n,v in k.fixture["before"]["sysfs"].items() if v["kind"] == "interface")
    scalar = ("ifindex", "iflink", "type", "flags", "operstate", "address")
    sysfs = [path for n in names for path in (["/sys/devices/virtual/net/" + n] + ["/sys/devices/virtual/net/" + n + "/" + key for key in scalar])]
    proc = ["/proc/self/net/" + name for name in ("dev", "if_inet6", "route", "ipv6_route", "fib_trie")]
    assert opened == roots + sysfs + proc + sysfs + proc
    dump = next(i for i,c in enumerate(k.calls) if c[0] == "dump")
    open_indexes = [i for i,c in enumerate(k.calls) if c[0] == "open"]
    assert open_indexes[len(roots) + len(sysfs) + len(proc) - 1] < dump < open_indexes[len(roots) + len(sysfs) + len(proc)]
