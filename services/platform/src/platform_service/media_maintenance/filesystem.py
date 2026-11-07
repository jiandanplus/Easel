"""Same-inode cooperative flock with actual OFD references.

ControlLock(fd, ...) consumes a trusted already-open control root anchor. For a
host pathname use from_path(), which retains and verifies every ancestor. Neither
interface accepts an archive/HTTP path. Darwin observations are labelled Darwin;
they are never a substitute for the separate Linux validation gate.
"""
import ctypes
import fcntl
import hashlib
import json
import os
import stat
import sys
import threading
from .contracts import MaintenanceError, require

_MINT=object()
_POOL={}
_POOL_LOCK=threading.RLock()
_EXCLUSIVE_FDS=set()
_EXCLUSIVE_LOCK=threading.RLock()

def _before_fork():
    # Serialize fork with open/dup/close, so no lock FD can escape registration.
    _POOL_LOCK.acquire();_EXCLUSIVE_LOCK.acquire()

def _after_fork_parent():
    _EXCLUSIVE_LOCK.release();_POOL_LOCK.release()

def _after_fork():
    global _POOL,_POOL_LOCK,_EXCLUSIVE_FDS,_EXCLUSIVE_LOCK
    # These are inherited OFDs: close the child's copies, never LOCK_UN, which
    # would release the lock still protecting the parent's real operation.
    for fd in {entry[0] for entry in _POOL.values()}|_EXCLUSIVE_FDS:
        try:os.close(fd)
        except OSError:pass
    _POOL={};_POOL_LOCK=threading.RLock()
    _EXCLUSIVE_FDS=set();_EXCLUSIVE_LOCK=threading.RLock()

if hasattr(os,"register_at_fork"):
    os.register_at_fork(before=_before_fork,after_in_parent=_after_fork_parent,after_in_child=_after_fork)


def _duplicate_exclusive(fd):
    with _EXCLUSIVE_LOCK:
        duplicate=os.dup(fd);_EXCLUSIVE_FDS.add(duplicate);return duplicate


def _close_exclusive(fd):
    with _EXCLUSIVE_LOCK:
        require(fd in _EXCLUSIVE_FDS);_EXCLUSIVE_FDS.remove(fd);os.close(fd)


def filesystem_identity(fd):
    try:
        libc=ctypes.CDLL(None,use_errno=True)
        if sys.platform=="darwin":
            class DarwinStatFS(ctypes.Structure):
                _fields_=[("bsize",ctypes.c_uint32),("iosize",ctypes.c_int32),
                  ("blocks",ctypes.c_uint64),("bfree",ctypes.c_uint64),("bavail",ctypes.c_uint64),
                  ("files",ctypes.c_uint64),("ffree",ctypes.c_uint64),("fsid",ctypes.c_int32*2),
                  ("owner",ctypes.c_uint32),("type",ctypes.c_uint32),("flags",ctypes.c_uint32),
                  ("subtype",ctypes.c_uint32),("name",ctypes.c_char*16),
                  ("mount",ctypes.c_char*1024),("source",ctypes.c_char*1024),("reserved",ctypes.c_uint32*8)]
            value=DarwinStatFS();require(libc.fstatfs(fd,ctypes.byref(value))==0)
            name=value.name.decode("ascii");namespace="Darwin:"+str(os.stat("/").st_dev)
            return ("Darwin",name,tuple(value.fsid),namespace)
        require(sys.platform=="linux")
        class LinuxStatFS(ctypes.Structure):
            _fields_=[("type",ctypes.c_long),("bsize",ctypes.c_long),
             ("blocks",ctypes.c_ulong), ("bfree",ctypes.c_ulong),("bavail",ctypes.c_ulong),
             ("files",ctypes.c_ulong),("ffree",ctypes.c_ulong),("fsid",ctypes.c_int32*2),
             ("namelen",ctypes.c_long),("frsize",ctypes.c_long),("flags",ctypes.c_long),("spare",ctypes.c_long*4)]
        value=LinuxStatFS();require(libc.fstatfs(fd,ctypes.byref(value))==0)
        names={0xef53:"ext4",0x58465342:"xfs",0x9123683e:"btrfs",0x01021994:"tmpfs",0x794c7630:"overlay"}
        require(value.type in names)
        return ("Linux",names[value.type],tuple(value.fsid),os.readlink("/proc/self/ns/mnt"))
    except (OSError,ValueError,UnicodeError,AttributeError):raise MaintenanceError() from None


def open_control_root(path, *, owner=(0,0)):
    """Open an absolute root chain with no writable/replaceable ancestor."""
    fd=None
    try:
        require(type(path) is str and path.startswith("/") and "\x00" not in path)
        parts=path.split("/")[1:];require(parts and all(p not in {"",".",".."} for p in parts))
        fd=os.open("/",os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        for part in parts:
            old=os.fstat(fd);require(stat.S_ISDIR(old.st_mode) and old.st_uid==0 and not stat.S_IMODE(old.st_mode)&0o022)
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=child
        current=os.fstat(fd)
        require(_control_root_matches(current,owner))
        result=fd;fd=None;return result
    except (OSError,TypeError,ValueError):raise MaintenanceError() from None
    finally:
        if fd is not None:os.close(fd)


def _root_stamp(info):return info.st_dev,info.st_ino,info.st_uid,info.st_gid,stat.S_IMODE(info.st_mode)
def _file_stamp(info):return info.st_dev,info.st_ino,info.st_uid,info.st_gid,stat.S_IMODE(info.st_mode),info.st_nlink,info.st_size

def _control_root_matches(info,owner):
    # Only root-managed control directories may grant the dedicated app group
    # read/search access. Source/backup roots retain their private 0700 policy.
    return ((info.st_uid,info.st_gid)==owner and
            stat.S_IMODE(info.st_mode) in ({0o700,0o750} if info.st_uid==0 else {0o700}))

class _Opaque:
    def __reduce__(self):raise TypeError("internal capability")
    def __repr__(self):return "<"+type(self).__name__+">"

class _Lease(_Opaque):
    def __init__(self,key,*,_mint=None):require(_mint is _MINT);self._key=key;self._closed=False;self._pid=os.getpid()
    def retain(self):
        with _POOL_LOCK:
            require(os.getpid()==self._pid and not self._closed and self._key in _POOL)
            _POOL[self._key][1]+=1;return _Lease(self._key,_mint=_MINT)
    def close(self):
        with _POOL_LOCK:
            if self._closed:return
            if os.getpid()!=self._pid:self._closed=True;return
            self._closed=True;entry=_POOL[self._key];entry[1]-=1
            if not entry[1]:
                del _POOL[self._key];os.close(entry[0])
    def __enter__(self):require(not self._closed);return self
    def __exit__(self,*args):self.close();return False

class _ExclusiveRef(_Opaque):
    def __init__(self,owner,fd,*,_mint=None):
        require(_mint is _MINT);self._owner=owner;self._fd=fd;self._closed=False;self._started=False;self._pid=os.getpid()
    def start(self):
        require(os.getpid()==self._pid)
        with self._owner._lock:
            require(not self._closed and not self._owner._sealed)
            if not self._started:require(not self._owner._closed);self._started=True
            return self
    def close(self):
        require(os.getpid()==self._pid)
        with self._owner._lock:
            if not self._closed:
                self._closed=True;self._owner._refs.discard(self);_close_exclusive(self._fd)
    def __enter__(self):return self.start()
    def __exit__(self,*args):self.close();return False

class ExclusiveWindow(_Opaque):
    def __init__(self,locks,fd,binding=None,*,_mint=None):
        require(_mint is _MINT);self._locks=locks;self._fd=fd;self._binding=binding;self._closed=False
        self._lock=threading.RLock();self._refs=set();self._pid=os.getpid();self._sealed=False
    @property
    def binding(self):require(os.getpid()==self._pid);return self._binding
    @property
    def active_refs(self):
        require(os.getpid()==self._pid)
        with self._lock:return len(self._refs)
    def verify(self,binding=None):
        require(os.getpid()==self._pid)
        with self._lock:
            require(not self._closed)
            if binding is not None:require(self._binding==binding)
            self._locks.verify()
    def retain(self):
        require(os.getpid()==self._pid)
        with self._lock:
            self.verify();require(not self._sealed)
            ref=_ExclusiveRef(self,_duplicate_exclusive(self._fd),_mint=_MINT);self._refs.add(ref);return ref
    def seal(self,binding):
        require(os.getpid()==self._pid)
        with self._lock:
            self.verify(binding);require(not self._refs);self._sealed=True
    def close_if_idle(self,binding):
        require(os.getpid()==self._pid)
        with self._lock:
            self.verify(binding);require(not self._refs);self.close()
    def close(self):
        require(os.getpid()==self._pid)
        with self._lock:
            if not self._closed:
                self._closed=True
                for ref in tuple(self._refs):
                    if not ref._started:ref.close()
                _close_exclusive(self._fd)
    def __enter__(self):self.verify();return self
    def __exit__(self,*args):self.close();return False

class _BoundRoot:
    def __init__(self,anchor_fd,relative,owner,*,system_chain=False,control=False):
        self.fds=[];self.entries=[];self._pid=os.getpid()
        try:
            require(type(relative) is str and relative and "\x00" not in relative)
            parts=relative.split("/");require(all(p not in {"",".",".."} for p in parts))
            first=os.dup(anchor_fd);self.fds.append(first);root=os.fstat(first)
            require(stat.S_ISDIR(root.st_mode))
            if system_chain:require(root.st_uid==0 and not stat.S_IMODE(root.st_mode)&0o022)
            else:require(_control_root_matches(root,owner) if control else _root_stamp(root)[2:]==(*owner,0o700))
            self.anchor=_root_stamp(root)
            for index,name in enumerate(parts):
                parent=self.fds[-1];child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
                self.fds.append(child);info=os.fstat(child)
                if index==len(parts)-1 or not system_chain:
                    require(_control_root_matches(info,owner) if control else _root_stamp(info)[2:]==(*owner,0o700))
                else:require(info.st_uid==0 and not stat.S_IMODE(info.st_mode)&0o022)
                self.entries.append((parent,name,child,_root_stamp(info)))
        except (OSError,TypeError,ValueError):self.close();raise MaintenanceError() from None
    def verify(self):
        try:
            require(os.getpid()==self._pid and self.fds and _root_stamp(os.fstat(self.fds[0]))==self.anchor)
            for parent,name,child,stamp in self.entries:
                require(_root_stamp(os.fstat(child))==stamp)
                info=os.stat(name,dir_fd=parent,follow_symlinks=False)
                require(stat.S_ISDIR(info.st_mode) and _root_stamp(info)==stamp)
        except (OSError,ValueError,TypeError):raise MaintenanceError() from None
    def close(self):
        while self.fds:os.close(self.fds.pop())

class ControlLock:
    def __init__(self,root_fd,name,*,owner=(0,0),allowed_filesystems=None):
        self._root=None;self._closed=False;self._pid=os.getpid();self._path_chain=None
        try:
            require(type(root_fd) is int and root_fd>=0 and name=="media.lock")
            info=os.fstat(root_fd);require(stat.S_ISDIR(info.st_mode) and _control_root_matches(info,owner))
            self._root=os.dup(root_fd);self._name=name;self._root_stamp=_root_stamp(info)
            fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=self._root)
            try:
                current=os.fstat(fd)
                require(stat.S_ISREG(current.st_mode) and current.st_nlink==1 and current.st_size==0
                        and (current.st_uid,current.st_gid)==owner and stat.S_IMODE(current.st_mode) in {0o600,0o640})
                self._file_stamp=_file_stamp(current);self.identity=filesystem_identity(fd)
                allowed=allowed_filesystems if allowed_filesystems is not None else {"apfs","ext4","xfs","btrfs"}
                require(self.identity[1] in allowed)
                self._key=(current.st_dev,current.st_ino,self.identity)
            finally:os.close(fd)
        except (OSError,TypeError,ValueError):
            if self._root is not None:os.close(self._root);self._root=None
            if self._path_chain is not None:self._path_chain.close();self._path_chain=None
            raise MaintenanceError() from None
    @classmethod
    def from_anchor(cls,anchor_fd,relative,*,owner=(0,0),allowed_filesystems=None):
        chain=_BoundRoot(anchor_fd,relative,owner,control=True)
        try:
            result=cls(chain.fds[-1],"media.lock",owner=owner,allowed_filesystems=allowed_filesystems);result._path_chain=chain;return result
        except BaseException:chain.close();raise
    @classmethod
    def from_path(cls,path,*,owner=(0,0),allowed_filesystems=None):
        require(type(path) is str and path.startswith("/") and path!="/")
        anchor=os.open("/",os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:chain=_BoundRoot(anchor,path[1:],owner,system_chain=True,control=True)
        finally:os.close(anchor)
        try:
            result=cls(chain.fds[-1],"media.lock",owner=owner,allowed_filesystems=allowed_filesystems);result._path_chain=chain;return result
        except BaseException:chain.close();raise
    @property
    def inode(self):return self._file_stamp[:2]
    def verify(self):
        try:
            require(os.getpid()==self._pid and not self._closed and _root_stamp(os.fstat(self._root))==self._root_stamp)
            if self._path_chain is not None:self._path_chain.verify()
            value=os.stat(self._name,dir_fd=self._root,follow_symlinks=False)
            require(stat.S_ISREG(value.st_mode) and _file_stamp(value)==self._file_stamp)
            require(filesystem_identity(self._root)==self.identity)
        except (OSError,ValueError,TypeError):raise MaintenanceError() from None
    def _open(self):
        self.verify();fd=os.open(self._name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=self._root)
        if _file_stamp(os.fstat(fd))!=self._file_stamp:os.close(fd);raise MaintenanceError()
        return fd
    def acquire_shared(self):
        try:
            with _POOL_LOCK:
                self.verify()
                if self._key in _POOL:_POOL[self._key][1]+=1
                else:
                    fd=self._open()
                    try:fcntl.flock(fd,fcntl.LOCK_SH|fcntl.LOCK_NB)
                    except BaseException:os.close(fd);raise
                    _POOL[self._key]=[fd,1]
                return _Lease(self._key,_mint=_MINT)
        except (OSError,ValueError,TypeError):raise MaintenanceError() from None
    def acquire_exclusive(self,binding=None):
        fd=None
        try:
            with _EXCLUSIVE_LOCK:
                fd=self._open();fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                result=ExclusiveWindow(self,fd,binding,_mint=_MINT)
                _EXCLUSIVE_FDS.add(fd);fd=None;return result
        except (OSError,ValueError,TypeError):raise MaintenanceError() from None
        finally:
            if fd is not None:os.close(fd)
    def close(self):
        if not self._closed:
            self._closed=True
            if self._root is not None:os.close(self._root);self._root=None
            if self._path_chain is not None:self._path_chain.close();self._path_chain=None


class SourceRoot(_Opaque):
    """Trusted existing private source FD, with retained path entries when named."""
    def __init__(self,root_fd,*,owner=None,allowed_filesystems=None):
        owner=(os.getuid(),os.getgid()) if owner is None else owner
        self._fd=None;self._path_chain=None;self._pid=os.getpid();self._closed=False
        try:
            require(type(root_fd) is int and root_fd>=0)
            info=os.fstat(root_fd);require(stat.S_ISDIR(info.st_mode) and _root_stamp(info)[2:]==(*owner,0o700))
            self._fd=os.dup(root_fd);self._stamp=_root_stamp(info);self.identity=filesystem_identity(self._fd)
            require(self.identity[1] in (allowed_filesystems if allowed_filesystems is not None else {'apfs','ext4','xfs','btrfs'}))
        except (OSError,ValueError,TypeError):
            if self._fd is not None:os.close(self._fd)
            raise MaintenanceError() from None
    @classmethod
    def from_anchor(cls,anchor_fd,relative,*,owner=None):
        owner=(os.getuid(),os.getgid()) if owner is None else owner
        chain=_BoundRoot(anchor_fd,relative,owner)
        try:result=cls(chain.fds[-1],owner=owner);result._path_chain=chain;return result
        except BaseException:chain.close();raise
    @classmethod
    def from_path(cls,path,*,owner=None):
        owner=(os.getuid(),os.getgid()) if owner is None else owner
        require(type(path) is str and path.startswith('/') and path!='/')
        anchor=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:chain=_BoundRoot(anchor,path[1:],owner,system_chain=True)
        finally:os.close(anchor)
        try:result=cls(chain.fds[-1],owner=owner);result._path_chain=chain;return result
        except BaseException:chain.close();raise
    @property
    def inode(self):return self._stamp[:2]
    def verify(self):
        try:
            require(os.getpid()==self._pid and not self._closed and _root_stamp(os.fstat(self._fd))==self._stamp)
            if self._path_chain is not None:self._path_chain.verify()
            require(filesystem_identity(self._fd)==self.identity)
        except (OSError,ValueError,TypeError):raise MaintenanceError() from None
    def verify_fd(self,fd):
        self.verify();require(_root_stamp(os.fstat(fd))==self._stamp and filesystem_identity(fd)==self.identity)
    def verify_path(self,path):
        self.verify();fd=None
        try:
            fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            require(_root_stamp(os.fstat(fd))==self._stamp and filesystem_identity(fd)==self.identity)
        except (OSError,ValueError,TypeError):raise MaintenanceError() from None
        finally:
            if fd is not None:os.close(fd)
    def close(self):
        require(os.getpid()==self._pid)
        if not self._closed:
            self._closed=True;os.close(self._fd)
            if self._path_chain is not None:self._path_chain.close()


def _identity_digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()).hexdigest()


def domain_attestation(locks,source):
    require(type(locks) is ControlLock and type(source) is SourceRoot)
    locks.verify();source.verify();require(locks.identity[3]==source.identity[3])
    require(_root_stamp(os.fstat(locks._root))[:2]!=source.inode)
    return {'control_dev':locks.inode[0],'control_ino':locks.inode[1],
        'control_fs_sha256':_identity_digest(locks.identity),'control_namespace_sha256':_identity_digest(locks.identity[3]),
        'source_dev':source.inode[0],'source_ino':source.inode[1],
        'source_fs_sha256':_identity_digest(source.identity),'source_namespace_sha256':_identity_digest(source.identity[3])}


def verify_domain_rows(rows,locks,source):
    expected=domain_attestation(locks,source)
    require(type(rows) is list)
    for row in rows:
        require(type(row) is dict and row.get('active') is True and row.get('domain')==expected)
    # An empty actual active-writer inventory is allowed; callers obtain rows
    # from the fixed trusted SQL view, never an HTTP body or supplied boolean.
    return _identity_digest(expected)
