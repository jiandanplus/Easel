"""One-shot companion to the fixed offline installer; separate manifest/receipt."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import sys
import zipfile

# This sibling is part of the independently pinned build tool pair.
_spec=importlib.util.spec_from_file_location('_b3_fixed_base_installer',Path(__file__).with_name('b3_worker_install.py'))
_base=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_base)
_BUILD='/opt/easel/reconstruction-build.json'
_CLAIM='/opt/easel/.reconstruction-install-claim'

def _wheel_program(files,origin):
    b=_base;fd,parent,leaf=b._open(b._ARTIFACT)
    try:
        before=b._stamp(os.fstat(fd))
        with os.fdopen(os.dup(fd),'rb') as stream,zipfile.ZipFile(stream) as wheel:
            entries=wheel.infolist();names=[i.filename for i in entries]
            b._require(len(entries)<=20000 and len(names)==len(set(names)))
            for path in origin._PROGRAM:
                info=wheel.getinfo(path.removeprefix(origin._BASE));meta=files[path]
                b._require(not info.is_dir() and info.file_size==meta['size'] and info.file_size<=512*1024**2 and not info.flag_bits&1)
                count=0;digest=hashlib.sha256()
                with wheel.open(info) as member:
                    while True:
                        data=member.read(65536)
                        if not data:break
                        count+=len(data);b._require(count<=meta['size']);digest.update(data)
                b._require(count==meta['size'] and digest.hexdigest()==meta['sha256'])
        b._require(before==b._stamp(os.fstat(fd))==b._stamp(os.stat(leaf,dir_fd=parent,follow_symlinks=False)))
    finally:os.close(fd);os.close(parent)

def _install():
    b=_base
    # Claim is outside input: the legacy input allowlist is unchanged.
    b._write(_CLAIM,b'easel.b3.reconstruction-install.v1\n',0o444)
    parent,leaf=b._parent(_BUILD,create=True)
    try:
        try:os.stat(leaf,dir_fd=parent,follow_symlinks=False)
        except FileNotFoundError:pass
        else:raise ValueError('reconstruction installer rejected')
    finally:os.close(parent)
    legacy=b._install()
    from platform_service.media_backup_b3 import reconstruction_origin as origin
    files=b._inventory()
    fd,parent,leaf=b._open(b._BUILD)
    try:files[b._BUILD]=b._read(fd,parent,leaf,8*1024**2)
    finally:os.close(fd);os.close(parent)
    _wheel_program(files,origin)
    build=origin._manifest(files)
    raw=json.dumps(build,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')
    b._require(len(raw)<=8*1024**2)
    origin._parse(raw)
    b._write(_BUILD,raw,0o444)
    return dict(manifest_sha256=hashlib.sha256(raw).hexdigest(),legacy_manifest_sha256=legacy['manifest_sha256'],
        artifact_sha256=build['artifact_sha256'],entrypoint_sha256=build['entrypoint_sha256'],
        executable_sha256=build['executable_sha256'],program_sha256=build['program_sha256'],client_sha256=build['client_sha256'],
        files=len(files),bytes=sum(m['size'] for m in files.values()))

def main():
    try:
        _base._require(sys.argv[1:]==[] and sys.platform=='linux' and platform.machine() in ('arm64','aarch64')
            and os.getuid()==os.geteuid()==os.getgid()==os.getegid()==0 and _base._ROOT=='/' and _base._OWNER==(0,0))
        os.umask(0o022)
        raw=json.dumps(_install(),sort_keys=True,separators=(',',':')).encode('ascii')+b'\n'
        _base._require(len(raw)<=1024)
        offset=0
        while offset<len(raw):
            n=os.write(1,raw[offset:]);_base._require(n>0);offset+=n
        return 0
    except BaseException:
        return 1

if __name__=='__main__':sys.exit(main())
