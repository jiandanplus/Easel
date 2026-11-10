"""Task2A intermediate authenticated private stage; dump inspection is pending.

Only the private adapter imports B2/closure. Authority is independently supplied;
archive fields never select a deadline, path, comparison mode or capacity policy.
Checks are cooperative. Root must supervise blocking IO under the same deadline.
"""
from dataclasses import dataclass, field
import copy
import hashlib
import io
import math
import os
import re
import stat
import sys
import threading
import time

from .contracts import B3Error, ComparisonContext, canonical, require, validate_observation

_CAPS = dict(max_file_bytes=4*1024**3,max_archive_bytes=32*1024**3,
             max_members=100000,max_manifest_bytes=32*1024**2,chunk_bytes=1024**2)
_FIXED = frozenset(('config/central','config/identity','config/media','db/central.dump',
                    'db/identity.dump','db/central.roles','db/identity.roles'))
_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_SEAL = object()


@dataclass(frozen=True)
class StageLimits:
    max_file_bytes: int = _CAPS['max_file_bytes']
    max_archive_bytes: int = _CAPS['max_archive_bytes']
    max_members: int = _CAPS['max_members']
    max_manifest_bytes: int = _CAPS['max_manifest_bytes']
    chunk_bytes: int = _CAPS['chunk_bytes']

    def __post_init__(self):
        for name, maximum in _CAPS.items():
            require(type(getattr(self,name)) is int and 0 < getattr(self,name) <= maximum)


@dataclass(frozen=True, repr=False)
class PreflightAuthority:
    restore_id: str
    stage_name: str
    started_at: float
    deadline: float
    reserved_bytes: int
    minimum_free_bytes: int
    contexts: tuple
    limits: StageLimits = StageLimits()
    cancellation: threading.Event = field(default_factory=threading.Event, repr=False, compare=False)

    def __post_init__(self):
        for name in (self.restore_id,self.stage_name):
            require(type(name) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',name,re.ASCII))
        for value in (self.started_at,self.deadline):
            require(type(value) in (int,float) and math.isfinite(value) and value >= 0)
        require(0 < self.deadline-self.started_at <= 1800)
        for value in (self.reserved_bytes,self.minimum_free_bytes):
            require(type(value) is int and 0 <= value <= 64*1024**3)
        require(self.reserved_bytes > 0 and type(self.limits) is StageLimits)
        self.limits.__post_init__()
        require(type(self.contexts) is tuple and len(self.contexts)==2)
        require(all(type(pair) is tuple and len(pair)==2 and type(pair[0]) is str for pair in self.contexts))
        require(tuple(label for label,ctx in self.contexts)==('central','identity'))
        for label,ctx in self.contexts:
            require(type(ctx) is ComparisonContext and ctx.source_kind==label)
            ctx.__post_init__()
            # Task1 validates semantic pins, but this authority also rejects
            # mutable inner pair aliases for its entire authentication lifetime.
            require(all(type(pair) is tuple and len(pair)==2 and type(pair[0]) is str
                        and type(pair[1]) is tuple for pair in ctx.row_order_pins))
        require(type(self.cancellation) is threading.Event)

    def check(self):
        now=time.monotonic()
        require(not self.cancellation.is_set() and self.started_at <= now < self.deadline)

    def __repr__(self): return '<PreflightAuthority>'


def _identity(info):
    return info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid


def _stamp(info):
    return (*_identity(info),info.st_nlink,info.st_size,info.st_mtime_ns,info.st_ctime_ns)


def _private_dir(info):
    require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode)==0o700
            and (info.st_uid,info.st_gid)==(os.geteuid(),os.getegid()) and info.st_nlink>0)


def _path(fd):
    if sys.platform=='darwin':
        import fcntl
        return os.fsdecode(fcntl.fcntl(fd,50,bytes(1024)).split(b'\0',1)[0])
    require(sys.platform.startswith('linux'))
    value=os.readlink('/proc/self/fd/'+str(fd))
    require(not value.endswith(' (deleted)'))
    return value


def _geometry(fd):
    """Closed supported allocation geometry, independently observed at parent."""
    info=os.fstatvfs(fd)
    values=(info.f_bsize,info.f_frsize)
    for value in values:
        require(type(value) is int and 512<=value<=1024**2 and value&(value-1)==0)
    require(max(values)%min(values)==0)
    return values


def _allocated_bytes(info):
    blocks=getattr(info,'st_blocks',None)
    require(type(blocks) is int and 0<=blocks<2**54)
    return blocks*512


@dataclass(frozen=True)
class _AllocationBudget:
    """Conservative Root policy estimate, not a hard quota or universal bound.

    Journal/COW costs and atomic reservation remain Root responsibilities.
    Existing ancestor blocks are not stage allocation; a fixed parent-entry
    growth allowance is included in admission only.
    """
    parent_identity: tuple
    geometry: tuple
    records_sha256: str
    directory_names: tuple
    estimate: int

    @classmethod
    def bind(cls,parent,authority,records):
        authority.check();_private_dir(os.fstat(parent))
        geometry=_geometry(parent);unit=geometry[1]  # f_frsize is the allocation fragment; f_bsize is IO geometry
        require(type(records) is list and len(records)<=authority.limits.max_members)
        names=[];sizes=[];directories={'config','db','media'}
        for row in records:
            name=row['name'];size=row['size']
            require(type(name) is str and name.count('/')==1)
            part,base=name.split('/')
            require(name in _FIXED or (part=='media' and re.fullmatch(r'(?:[0-9a-f]{32}|[0-9a-f]{64})',base)))
            require(type(size) is int and 0<=size<=authority.limits.max_file_bytes)
            names.append(name);sizes.append(size);directories.add(part)
        require(len(names)==len(set(names)) and _FIXED<=set(names))
        require(directories=={'config','db','media'} and sum(sizes)<=authority.limits.max_archive_bytes)
        rounded=lambda n:((n+unit-1)//unit)*unit
        created=(authority.stage_name,)+tuple(authority.stage_name+'/'+part for part in sorted(directories))
        # 64 KiB metadata allowance per file/directory, allocation-unit-rounded
        # file data, one data block per created directory, fixed parent growth.
        estimate=sum(rounded(n)+rounded(65536) for n in sizes)+len(created)*(unit+rounded(65536))+65536
        require(estimate<=authority.reserved_bytes)
        result=cls(_identity(os.fstat(parent)),geometry,hashlib.sha256(canonical(records)).hexdigest(),created,estimate)
        result.check(parent,authority)
        free=os.fstatvfs(parent)
        require((free.f_bsize,free.f_frsize)==geometry)
        require(free.f_bavail*free.f_frsize>=estimate+authority.minimum_free_bytes)
        return result

    def check(self,parent,authority):
        authority.check()
        require(_identity(os.fstat(parent))==self.parent_identity and _geometry(parent)==self.geometry)
        free=os.fstatvfs(parent)
        require((free.f_bsize,free.f_frsize)==self.geometry)
        require(type(free.f_bavail) is int and free.f_bavail>=0)
        require(free.f_bavail*free.f_frsize>=authority.minimum_free_bytes)

    def measure(self,owned):
        self.check(owned._parent,owned._authority)
        require(hashlib.sha256(canonical(owned._records)).hexdigest()==self.records_sha256)
        require(self.directory_names==(owned._authority.stage_name,)+tuple(
            owned._authority.stage_name+'/'+part for part in sorted(owned._dirs)))
        # Only newly created stage dirs + every member; ancestor FDs bind paths.
        fds=(owned._stage,*owned._dirs.values(),*owned._files.values())
        measured={fd:_allocated_bytes(os.fstat(fd)) for fd in fds}
        require(sum(measured.values())<=owned._authority.reserved_bytes)
        for fd,value in measured.items():require(_allocated_bytes(os.fstat(fd))==value)
        self.check(owned._parent,owned._authority)


class _StageFiles:
    """Retains full parent lineage and exact child entries; never deletes stages."""
    def __init__(self,parent_fd,authority,records,allocation=None):
        self._fds=[]; self._links=[]; self._dirs={}; self._files={}; self._closed=False
        self._authority=authority;self._records=copy.deepcopy(records)
        self._allocation=allocation
        try:
            if self._allocation is None:self._allocation=_AllocationBudget.bind(parent_fd,authority,records)
            require(type(self._allocation) is _AllocationBudget)
            self._allocation.check(parent_fd,authority)
            require(hashlib.sha256(canonical(records)).hexdigest()==self._allocation.records_sha256)
            authority.check()
            original=os.fstat(parent_fd);_private_dir(original)
            path=_path(parent_fd)
            require(path.startswith('/') and len(os.fsencode(path))<=16384)
            parts=[s for s in path.split('/') if s]
            require(len(parts)<=128 and all(s not in ('.','..') for s in parts))
            parent=os.open('/',_DIR);self._hold(parent)
            for part in parts:
                child=os.open(part,_DIR,dir_fd=parent);self._hold(child)
                self._links.append((parent,part,child,_identity(os.fstat(child))))
                parent=child
            self._parent=parent
            require(_identity(os.fstat(parent))==_identity(original))
            stage=os.open(authority.stage_name,_DIR,dir_fd=parent);self._hold(stage)
            self._links.append((parent,authority.stage_name,stage,_identity(os.fstat(stage))))
            self._stage=stage;_private_dir(os.fstat(stage))
            for part in ('config','db','media'):
                fd=os.open(part,_DIR,dir_fd=stage);self._hold(fd);self._dirs[part]=fd
                self._links.append((stage,part,fd,_identity(os.fstat(fd))))
            names=[row['name'] for row in records]
            require(len(names)==len(set(names)) and _FIXED<=set(names) and len(names)<=authority.limits.max_members)
            for row in records:
                name=row['name'];require(type(name) is str and name.count('/')==1)
                part,base=name.split('/')
                require(part in self._dirs and base not in ('','.','..') and '\0' not in base)
                require(name in _FIXED or (part=='media' and re.fullmatch(r'(?:[0-9a-f]{32}|[0-9a-f]{64})',base)))
                require(type(row['size']) is int and 0<=row['size']<=authority.limits.max_file_bytes)
                require(type(row['sha256']) is str and re.fullmatch('[0-9a-f]{64}',row['sha256']))
                fd=os.open(base,_READ,dir_fd=self._dirs[part]);self._hold(fd);self._files[name]=fd
            self._stamps={n:_stamp(os.fstat(fd)) for n,fd in self._files.items()}
            self._dir_stamps={fd:_stamp(os.fstat(fd)) for fd in (stage,*self._dirs.values())}
            self.verify()
        except BaseException:
            self.close();raise

    def _hold(self,fd): self._fds.append((fd,_identity(os.fstat(fd))))

    def _guard(self):
        self._authority.check()
        self._allocation.check(self._parent,self._authority)

    def _entries(self,fd,expected):
        found=set()
        with os.scandir(fd) as entries:
            for entry in entries:
                self._guard()
                require(entry.name in expected and entry.name not in found);found.add(entry.name)
        require(found==expected)

    def _metadata(self):
        require(not self._closed);self._guard()
        for fd,identity in self._fds:require(_identity(os.fstat(fd))==identity)
        for parent,name,fd,identity in self._links:
            require(_identity(os.stat(name,dir_fd=parent,follow_symlinks=False))==identity)
        _private_dir(os.fstat(self._parent))
        for fd,stamp in self._dir_stamps.items():
            require(os.fstat(fd).st_dev==self._allocation.parent_identity[0])
            _private_dir(os.fstat(fd));require(_stamp(os.fstat(fd))==stamp)
        self._entries(self._stage,{'config','db','media'})
        for part,fd in self._dirs.items():
            self._entries(fd,{name.split('/')[1] for name in self._files if name.startswith(part+'/')})
        for name,fd in self._files.items():
            part,base=name.split('/');info=os.fstat(fd)
            require(info.st_dev==self._allocation.parent_identity[0])
            require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode)==0o600 and info.st_nlink==1
                and (info.st_uid,info.st_gid)==(os.geteuid(),os.getegid()))
            require(_stamp(info)==self._stamps[name] and
                _stamp(os.stat(base,dir_fd=self._dirs[part],follow_symlinks=False))==self._stamps[name])
        self._guard()

    def _hash(self,row,collect=False):
        fd=self._files[row['name']];require(os.fstat(fd).st_size==row['size'])
        h=hashlib.sha256();offset=0;collected=bytearray()
        while offset<row['size']:
            self._guard()
            raw=os.pread(fd,min(self._authority.limits.chunk_bytes,row['size']-offset),offset)
            self._guard();require(type(raw) is bytes and 0<len(raw)<=min(self._authority.limits.chunk_bytes,row['size']-offset))
            h.update(raw);offset+=len(raw)
            if collect:collected.extend(raw)
        self._guard();require(os.pread(fd,1,offset)==b'');self._guard()
        require(h.hexdigest()==row['sha256'])
        return bytes(collected) if collect else None

    def verify(self):
        self._metadata()
        for row in self._records:self._hash(row)
        self._metadata()
        self._allocation.measure(self)

    def read_small(self,name,maximum):
        self._metadata();row=next(r for r in self._records if r['name']==name)
        require(row['size']<=maximum)
        raw=self._hash(row,True);self._metadata();return raw

    def close(self):
        if self._closed:return
        self._closed=True
        for fd,identity in reversed(self._fds):
            try:
                if _identity(os.fstat(fd))[:2]==identity[:2]:os.close(fd)
            except OSError:pass


def _bind_facts(facts,roles,authority):
    bound={}
    for kind,ctx in authority.contexts:
        authority.check()
        require(kind==ctx.source_kind)
        require((facts['profile'] if kind=='central' else facts['schema']['identity']['profile'])==ctx.profile.name)
        inventory=facts['roles'][kind]
        require(canonical(inventory['before'])==canonical(inventory['during'])==canonical(inventory['after']))
        require(roles[kind]==canonical(dict(format='easel-roles-v1',inventory=inventory['before'])))
        seq=facts['sequences'][kind]
        require(canonical(seq['before'])==canonical(seq['dump'])==canonical(seq['after']))
        # B2 authenticates public phases and the complete central historical
        # snapshot separately before this binding. Keep both scopes intact;
        # Task1 still checks the union against every pinned catalog definition.
        public=seq['before']
        require(type(public) is list and all(type(item) is dict and
            item.get('schema')=='public' and type(item.get('name')) is str for item in public))
        historical=[]
        if kind=='central':
            snapshot=facts.get('maintenance_snapshot',{})
            require(type(snapshot) is dict)
            historical=snapshot.get('sequences',[])
            require(type(historical) is list and len(historical)<=1)
            require(all(type(item) is dict and
                (item.get('schema'),item.get('name'))==('easel_backup_control','audit_id_seq')
                for item in historical))
        # Identity has no authenticated control-history scope. Missing catalog
        # coverage must fail; never borrow the central snapshot to fill it.
        sequences=sorted(public+historical,key=lambda item:(item['schema'],item['name']))
        observation=dict(schema=facts['schema']['relations'] if kind=='central' else facts['schema']['identity']['relations'],
            roles=inventory['before'],rows=facts['rows'] if kind=='central' else {},
            copy=facts['snapshot_projections'][kind],sequences=sequences)
        bound[kind]=validate_observation(observation,ctx)
    authority.check();return bound


def _crossbind(proof,first,second,stage):
    require(proof['status']=='archive_content_verified' and stage['status']=='private_stage_restored_verified')
    require(first==second)
    for left,right in (('archive_bytes','plaintext_length'),('archive_sha256','plaintext_sha256'),
                       ('plan_sha256','plan_sha256'),('registry_sha256','registry_sha256')):
        require(proof[left]==first[right])
    for left,right in (('archive_bytes','plaintext_length'),('archive_sha256','plaintext_sha256'),
                       ('manifest_sha256','plan_sha256'),('registry_sha256','registry_sha256')):
        require(stage[left]==second[right])


class _GuardedRead:
    def __init__(self,reader,check,chunk):self._reader,self._check,self._chunk=reader,check,chunk
    def read(self,size):
        self._check();require(type(size) is int and size>0)
        raw=self._reader.read(min(size,self._chunk));self._check();return raw
    def fileno(self):raise io.UnsupportedOperation()


class _CommittedReadOrigin:
    """Historical producer evidence; never a live intent or execution capability."""
    def __init__(self, supplied, reader, job_fd):
        from platform_service.media_backup_b2.anchor import RootAnchorIntent
        from platform_service.media_backup_b2.reader import OwnedArchiveReader
        from platform_service.media_backup_b2.contracts import CommitRef
        require(type(supplied) is RootAnchorIntent and type(reader) is OwnedArchiveReader)
        require(reader.verified and not reader._closed)
        reader._check()
        require(type(reader._reference) is CommitRef)
        self._supplied=supplied;self._reader=reader;self._intent=reader._intent
        self._original_supplied=supplied;self._original_reader=reader
        self._supplied_policy=supplied._policy;self._supplied_selector=supplied._directory
        self._policy=self._intent._policy;self._issuer=self._policy._issuer
        require(supplied._policy._issuer is self._issuer)
        self._selector_identity=self._intent._directory.identity
        self._directory=reader._directory;self._files=tuple(reader._files)
        require(len(self._files)==4)
        self._reference=reader._reference
        self._commit=self._files[1].read_all(65536)
        require(self._intent._validate_commit(self._commit)==self._reference)
        self._job_identity=_identity(os.fstat(job_fd))
        require(self._directory.identity==self._job_identity)
        self._pin=self._projection()
    def __repr__(self):return '<CommittedReadOrigin redacted>'
    def __copy__(self):raise TypeError('original producer evidence')
    def __deepcopy__(self,memo):raise TypeError('original producer evidence')
    def __reduce_ex__(self,protocol):raise TypeError('original producer evidence')
    def _projection(self):
        return (self._supplied.backup_id,self._supplied.target,self._supplied.commit_sha256,
            self._intent.backup_id,self._intent.target,self._intent.commit_sha256,
            self._intent._directory.identity,self._policy._backup.identity,
            self._directory.identity,tuple(f.stamp for f in self._files),
            self._reference,hashlib.sha256(self._commit).hexdigest(),
            tuple(sorted(self._reader._receipt.items())),tuple(sorted(self._reader._row.items())))
    def verify(self):
        # Readers are deliberately closed by the producer. No historical live
        # method or descriptor re-open is used to manufacture fresh authority.
        require(self._supplied is self._original_supplied and self._reader is self._original_reader)
        require(self._supplied._policy is self._supplied_policy and self._supplied._directory is self._supplied_selector)
        require(self._reader._intent is self._intent and self._intent._policy is self._policy)
        require(self._policy._issuer is self._issuer and self._supplied._policy._issuer is self._issuer)
        require(self._reader._directory is self._directory and tuple(self._reader._files)==self._files)
        require(self._reader._reference is self._reference and self._reader.verified)
        require(self._projection()==self._pin)


class _B2Adapter:
    """Private structural seam for pure sequencing tests, never a public backend."""
    def __init__(self,limits):
        from media_backup.contract import Limits, encode_manifest, member_records, validate_plan
        from media_backup.closure import restore_archive
        from platform_service.media_backup_b2.reader import read_committed
        from platform_service.media_backup_b2.self_verify import verify_archive_content
        from platform_service.media_backup_b2.bundles import decode_bundle,canonical_bytes
        from platform_service.media_backup_b2.source_facts import verify_archived_bundles,validate_source_facts
        self.limits=Limits(**{name:getattr(limits,name) for name in _CAPS})
        self.manifest=encode_manifest;self.records=member_records
        self.validate_plan=validate_plan;self.restore=restore_archive;self.read=read_committed
        self.verify=verify_archive_content;self.decode=decode_bundle;self.canonical=canonical_bytes
        self.bundles=verify_archived_bundles;self.facts=validate_source_facts
        self.origin=lambda intent,job,reader:_CommittedReadOrigin(intent,reader,job)


class AuthenticatedRestoreStage:
    """Owned intermediate only. Raw values stay private; no restore authorization."""
    __slots__=('_owned','_authority','_plan','_facts','_observations','_receipt','_closed','_lock','_claimed','_origin','_original_origin')
    def __init__(self,seal=None,*,owned=None,authority=None,plan=None,facts=None,observations=None,receipt=None,origin=()):
        if seal is not _SEAL:raise TypeError('owned intermediate stage')
        self._owned=owned;self._authority=authority;self._plan=copy.deepcopy(plan);self._facts=copy.deepcopy(facts)
        self._observations=copy.deepcopy(observations);self._receipt=dict(receipt);self._closed=False;self._lock=threading.RLock();self._claimed=False
        self._origin=origin;self._original_origin=origin
    def __repr__(self):return '<AuthenticatedRestoreStage: dump preflight pending>'
    def __reduce_ex__(self,protocol):raise TypeError('owned intermediate stage')
    def _claim_dump(self):
        with self._lock:
            self.verify();require(not self._claimed);self._claimed=True
    def _borrow_dump(self,kind):
        with self._lock:
            self.verify();require(kind in ('central','identity'))
            original=self._owned._files['db/'+kind+'.dump']
            fd=os.open(_path(original),_READ)
            try:
                require(_stamp(os.fstat(fd))==_stamp(os.fstat(original)))
                self.verify();return fd
            except BaseException:
                os.close(fd);raise
    def verify(self):
        with self._lock:
            require(not self._closed)
            try:
                require(self._origin is self._original_origin)
                for origin in self._origin:origin.verify()
                self._owned.verify();self._authority.check()
            except BaseException:self.close();raise
    @property
    def safe_receipt(self):
        self.verify();return dict(self._receipt)
    def close(self):
        with self._lock:
            if self._closed:return
            self._closed=True;self._owned.close()
    def __enter__(self):self.verify();return self
    def __exit__(self,*args):self.close()


def prepare_authenticated_stage(intent,backup_job_fd,private_pem,authority,stage_parent_fd):
    """Authenticate twice, retain exact stage; never invokes a tool or target.

    reserved_bytes is an independently held Root reservation, not one acquired
    here. This function neither refunds it nor settles/removes failure materials.
    """
    owned=None;reader=None;parent=None;allocation=None
    try:
        require(type(authority) is PreflightAuthority);authority.__post_init__();authority.check()
        require(type(stage_parent_fd) is int and stage_parent_fd>=0)
        parent=os.dup(stage_parent_fd);parent_identity=_identity(os.fstat(parent));_private_dir(os.fstat(parent))
        def guard():
            authority.check();require(_identity(os.fstat(parent))==parent_identity)
            capacity=os.fstatvfs(parent)
            require(capacity.f_bavail*capacity.f_frsize>=authority.minimum_free_bytes)
            if allocation is not None:allocation.check(parent,authority)
        guard()
        try:os.stat(authority.stage_name,dir_fd=parent,follow_symlinks=False)
        except FileNotFoundError:pass
        else:raise B3Error('invalid B3 pure contract')
        capacity=os.fstatvfs(parent)
        require(capacity.f_bavail*capacity.f_frsize>=authority.reserved_bytes+authority.minimum_free_bytes)
        adapter=_B2Adapter(authority.limits)
        plan,reader=adapter.read(intent,backup_job_fd,private_pem,check=guard)
        guard();plan=adapter.validate_plan(plan,adapter.limits)
        allocation=_AllocationBudget.bind(parent,authority,adapter.records(plan))
        manifest=adapter.manifest(plan,adapter.limits)
        proof=adapter.verify(reader,plan,check=guard,limits=adapter.limits)
        guard();require(reader.verified);first=reader.receipt
        first_origin=adapter.origin(intent,backup_job_fd,reader)
        reader.close();reader=None
        plan2,reader=adapter.read(intent,backup_job_fd,private_pem,check=guard)
        guard();require(adapter.manifest(plan2,adapter.limits)==manifest)
        stage=adapter.restore(_GuardedRead(reader,guard,authority.limits.chunk_bytes),parent,
                              authority.stage_name,plan,limits=adapter.limits)
        guard();require(reader.verified);second=reader.receipt
        second_origin=adapter.origin(intent,backup_job_fd,reader)
        _crossbind(proof,first,second,stage)
        require(proof['file_count']==stage['file_count']==len(adapter.records(plan)))
        require(proof['blob_count']==stage['blob_count']==sum(row['present'] for row in plan['blobs']))
        require(proof['plan_sha256']==hashlib.sha256(manifest).hexdigest()
                and proof['registry_sha256']==plan['registry_sha256'])
        reader.close();reader=None
        owned=_StageFiles(parent,authority,adapter.records(plan),allocation)
        sizes={row['name']:row['size'] for row in adapter.records(plan)}
        require(sum(sizes['config/'+k] for k in ('central','identity','media'))<=32*1024**2)
        bundles={name:owned.read_small(name,16*1024**2) for name in ('config/central','config/identity','config/media')}
        guard();bundle_proof=adapter.bundles(bundles,plan['files'],plan);guard()
        central=adapter.decode(bundles['config/central'],'central');guard()
        raw=adapter.canonical(central['source_facts']);require(len(raw)<=8*1024**2)
        facts=adapter.facts(raw);guard()
        require(hashlib.sha256(raw).hexdigest()==proof['source_facts_sha256']==bundle_proof['source_facts_sha256']==plan['snapshots']['source_sha256'])
        roles={k:owned.read_small('db/'+k+'.roles',8*1024**2) for k in ('central','identity')}
        observations=_bind_facts(facts,roles,authority)
        owned.verify();guard()
        safe=dict(authenticated_stage=True,dump_preflight_complete=False,plan_sha256=proof['plan_sha256'],
            registry_sha256=proof['registry_sha256'],source_facts_sha256=proof['source_facts_sha256'],
            archive_sha256=proof['archive_sha256'],ciphertext_sha256=first['ciphertext_sha256'])
        result=AuthenticatedRestoreStage(_SEAL,owned=owned,authority=authority,plan=plan,facts=facts,
                                        observations=observations,receipt=safe,origin=(first_origin,second_origin))
        allocation.measure(owned)
        guard()
        owned=None;return result
    except BaseException as error:
        if owned is not None:owned.close()
        if not isinstance(error,Exception):raise
        raise B3Error('invalid B3 authenticated stage') from None
    finally:
        if reader is not None:reader.close()
        if parent is not None:os.close(parent)
