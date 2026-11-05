"""Bounded fixed native network data acquisition; conveys no authority."""
import os
import re
import stat
import sys
from time import monotonic_ns as _monotonic_ns

from .contracts import B3Error
from .netlink_observer import observe_linux_network
from .network_profile import NetworkObservation, NamespaceIdentity, InterfaceEntry, ControlEntry, ProcViews

_NAMES = frozenset(("erspan0", "gre0", "gretap0", "ip6_vti0", "ip6gre0", "ip6tnl0", "ip_vti0", "lo", "sit0", "tunl0"))
_SCALARS = ("ifindex", "iflink", "type", "flags", "operstate", "address")
_ERROR = "invalid B3 network reader"


def observe_worker_network(deadline_ns: int) -> NetworkObservation:
    owned = []
    cleanup_failed = False
    pending = None
    result = None
    def check():
        if type(deadline_ns) is not int or not 0 < deadline_ns < 2**63:
            raise ValueError()
        left = deadline_ns - _monotonic_ns()
        if not 0 < left <= 60_000_000_000:
            raise ValueError()
    def call(fn, *args, **kwargs):
        check()
        value = fn(*args, **kwargs)
        check()
        return value
    def op(path, flags, parent=None):
        check()
        fd = os.open(path, flags, dir_fd=parent)
        owned.append(fd)
        check()
        return fd
    def identity(s):
        return (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid)
    def directory(fd, root=True):
        s = call(os.fstat, fd)
        if not stat.S_ISDIR(s.st_mode) or (root and (s.st_uid != 0 or s.st_gid != 0 or s.st_mode & 0o022)):
            raise ValueError()
        return identity(s)
    def readfile(parent, name, cap, sysfs=False):
        path_before = call(os.stat, name, dir_fd=parent, follow_symlinks=False)
        fd = op(name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK, parent)
        s = call(os.fstat, fd)
        if identity(path_before) != identity(s): raise ValueError()
        if not stat.S_ISREG(s.st_mode) or (sysfs and (s.st_uid != 0 or s.st_gid != 0 or s.st_mode & 0o022)):
            raise ValueError()
        chunks = []
        count = 0
        while True:
            data = call(os.read, fd, cap + 1 - count)
            if type(data) is not bytes: raise ValueError()
            if not data: break
            chunks.append(data); count += len(data)
            if count > cap: raise ValueError()
        if not count: raise ValueError()
        if identity(call(os.fstat, fd)) != identity(s) or identity(call(os.stat, name, dir_fd=parent, follow_symlinks=False)) != identity(s): raise ValueError()
        return b"".join(chunks)
    try:
        check()
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
        release = call(os.uname).release
        sysroot = op("/sys", flags)
        sysid = directory(sysroot)
        classfd = op("class", flags, sysroot); classid = directory(classfd)
        netfd = op("net", flags, classfd); netid = directory(netfd)
        devices = op("devices", flags, sysroot); devicesid = directory(devices)
        virtual = op("virtual", flags, devices); virtualid = directory(virtual)
        realnet = op("net", flags, virtual); realid = directory(realnet)
        proc = op("/proc/self/net", flags); procid = directory(proc, False)
        dirs = ((sysroot,sysid,True),(classfd,classid,True),(netfd,netid,True),(devices,devicesid,True),(virtual,virtualid,True),(realnet,realid,True),(proc,procid,False))
        edges = ((sysroot, "class", classid), (classfd, "net", netid), (sysroot, "devices", devicesid), (devices, "virtual", virtualid), (virtual, "net", realid))
        def verify_dirs():
            for fd, ident, root in dirs:
                if directory(fd, root) != ident: raise ValueError()
            for parent, name, ident in edges:
                if identity(call(os.stat, name, dir_fd=parent, follow_symlinks=False)) != ident: raise ValueError()
            if identity(call(os.stat, "/sys", follow_symlinks=False)) != sysid or identity(call(os.stat, "/proc/self/net", follow_symlinks=False)) != procid: raise ValueError()
        def namespace():
            s = call(os.stat, "/proc/self/ns/net", follow_symlinks=True)
            return NamespaceIdentity(s.st_dev, s.st_ino)
        seen_identities = {}
        def sysfs():
            names = call(os.listdir, netfd)
            if len(names) != 11 or set(names) != _NAMES | {"bonding_masters"}: raise ValueError()
            entries = []
            for name in sorted(names):
                link = call(os.stat, name, dir_fd=netfd, follow_symlinks=False)
                if name == "bonding_masters":
                    if not stat.S_ISREG(link.st_mode) or (link.st_uid,link.st_gid,stat.S_IMODE(link.st_mode)) != (0,0,0o644): raise ValueError()
                    if name in seen_identities and seen_identities[name] != identity(link): raise ValueError()
                    seen_identities[name] = identity(link)
                    entries.append(ControlEntry(name,link.st_uid,link.st_gid,stat.S_IMODE(link.st_mode)))
                    if identity(call(os.stat, name, dir_fd=netfd, follow_symlinks=False)) != identity(link): raise ValueError()
                    continue
                if not stat.S_ISLNK(link.st_mode) or (link.st_uid,link.st_gid) != (0,0): raise ValueError()
                if call(os.readlink, name, dir_fd=netfd) != "../../devices/virtual/net/" + name: raise ValueError()
                target = call(os.stat, name, dir_fd=netfd, follow_symlinks=True)
                fd = op(name, flags, realnet)
                ident = directory(fd)
                if identity(target) != ident: raise ValueError()
                if name in seen_identities and seen_identities[name] != (identity(link), ident): raise ValueError()
                seen_identities[name] = (identity(link), ident)
                values = [readfile(fd, key, 128, True) for key in _SCALARS]
                for v in values[:3]:
                    if not re.fullmatch(rb"(?:0|[1-9][0-9]*)\n", v): raise ValueError()
                if not re.fullmatch(rb"0x(?:0|[1-9a-f][0-9a-f]*)\n", values[3]): raise ValueError()
                if not re.fullmatch(rb"[a-z]+\n", values[4]): raise ValueError()
                if not re.fullmatch(rb"[0-9a-f]{2}(?::[0-9a-f]{2})*\n", values[5]): raise ValueError()
                entries.append(InterfaceEntry(name, *(int(v[:-1]) for v in values[:3]), int(values[3][:-1],16), values[4][:-1].decode("ascii"), bytes.fromhex(values[5][:-1].decode("ascii").replace(":", ""))))
                if directory(fd) != ident or identity(call(os.stat, name, dir_fd=netfd, follow_symlinks=True)) != ident or identity(call(os.stat, name, dir_fd=netfd, follow_symlinks=False)) != identity(link) or call(os.readlink, name, dir_fd=netfd) != "../../devices/virtual/net/" + name: raise ValueError()
            verify_dirs()
            return tuple(entries)
        def procviews():
            return ProcViews(*(readfile(proc, name, 65536) for name in ("dev", "if_inet6", "route", "ipv6_route", "fib_trie")))
        before_ns = namespace(); before_sys = sysfs(); before_proc = procviews()
        dumps = call(observe_linux_network, deadline_ns)
        after_ns = namespace(); after_sys = sysfs(); after_proc = procviews()
        verify_dirs()
        result = NetworkObservation(release,sys.byteorder,before_ns,after_ns,before_sys,after_sys,before_proc,after_proc,dumps)
    except BaseException as exc:
        pending = exc
    finally:
        for fd in reversed(owned):
            try:
                try: check()
                except BaseException as exc:
                    cleanup_failed = True
                    if not isinstance(exc, Exception) and (pending is None or isinstance(pending, Exception)): pending = exc
                try: os.close(fd)
                except BaseException as exc:
                    cleanup_failed = True
                    if not isinstance(exc, Exception) and (pending is None or isinstance(pending, Exception)): pending = exc
                try: check()
                except BaseException as exc:
                    cleanup_failed = True
                    if not isinstance(exc, Exception) and (pending is None or isinstance(pending, Exception)): pending = exc
            except BaseException as exc:
                cleanup_failed = True
                if not isinstance(exc, Exception) and (pending is None or isinstance(pending, Exception)): pending = exc
    if pending is not None and not isinstance(pending, Exception): raise pending
    if pending is not None or cleanup_failed: raise B3Error(_ERROR) from None
    try: check()
    except Exception: raise B3Error(_ERROR) from None
    return result
