"""Closed authenticated dump inspection, never target/SQL/restore authority.

Root supplies independent exact TOC templates and pre-capture profile/catalog
pins. Template coverage in production is Root's responsibility. Only the lazy
private legacy parser adapters import B2; importing this module starts no IO.
Private seals are API discipline, not isolation from hostile Python callers.
"""
from dataclasses import asdict, dataclass
import copy
import hashlib
import math
import os
import re

from .contracts import B3Error, canonical, digest, require
from .preflight import AuthenticatedRestoreStage, prepare_authenticated_stage
from .parser_runner import NetworkNoneParserRunner, ParserAuthority, _InspectedOutput
from .dump_screen import TocPolicy, DataPolicy, parse_toc, screen_data_sql, verify_schema_bytes

_SEAL=object()
_KINDS=('central','identity')
_MODES=('toc','schema','data')
_HASH=re.compile(r'[0-9a-f]{64}\Z',re.ASCII)


def _hash(value):require(type(value) is str and _HASH.fullmatch(value) is not None)


def _profile_sha256(context):
    """Root can compute this pin from its independent immutable profile."""
    return digest(asdict(context.profile))


def _parser_sha256(a):
    """Same closed authority projection as the concrete runner's issued proof."""
    return digest(dict(cid=a.cid,inspect=a.expected_inspect_sha256,docker=a.docker_sha256,
        tool=a.pg_restore_sha256,started=a.started_at,deadline=a.deadline,nonce=a.nonce,
        image=a.image_id,socket=a.socket,docker_path=a.docker_path,docker_config_dir=a.docker_config_dir,
        uid=a.uid,gid=a.gid,work_dir_fd=a.work_dir_fd,reserved=a.reserved_bytes,minimum_free=a.minimum_free_bytes))


@dataclass(frozen=True,repr=False)
class DumpInspectionPolicy:
    restore_id: str
    started_at: float
    deadline: float
    # (source kind, exact profile label, logical-profile hash, catalog hash)
    profiles: tuple
    toc_policies: tuple
    parser_authority_sha256: str

    def __post_init__(self):
        require(type(self.restore_id) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',self.restore_id,re.ASCII))
        for value in (self.started_at,self.deadline):
            require(type(value) in (int,float) and math.isfinite(value) and value>=0)
        require(0<self.deadline-self.started_at<=1800)
        require(type(self.profiles) is tuple and len(self.profiles)==2)
        for record,kind in zip(self.profiles,_KINDS):
            require(type(record) is tuple and len(record)==4)
            label,name,profile_hash,catalog_hash=record
            require(type(label) is str and label==kind and type(name) is str and
                re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}',name,re.ASCII))
            _hash(profile_hash);_hash(catalog_hash)
        require(type(self.toc_policies) is tuple and len(self.toc_policies)==2)
        for pair,kind in zip(self.toc_policies,_KINDS):
            require(type(pair) is tuple and len(pair)==2 and type(pair[0]) is str and pair[0]==kind)
            require(type(pair[1]) is TocPolicy);pair[1].__post_init__()
        _hash(self.parser_authority_sha256)

    def __repr__(self):return '<DumpInspectionPolicy>'


def _parse_copy_inventory(raw):
    from platform_service.media_backup_b2.capture import parse_dump_copy_inventory
    return parse_dump_copy_inventory(raw)


def _parse_sequence_values(raw):
    from platform_service.media_backup_b2.capture import parse_dump_sequence_values
    return parse_dump_sequence_values(raw)


def _outside_copy_setvals(raw,screen):
    """Retain actual bounded lines, excluding opaque COPY row byte ranges."""
    ranges=sorted((row[3],row[4]) for row in screen._layout)
    lines=[];offset=0;index=0;total=0
    for line in raw.split(b'\n')[:-1]:
        end=offset+len(line)+1
        while index<len(ranges) and offset>=ranges[index][1]:index+=1
        in_copy=index<len(ranges) and ranges[index][0]<=offset<ranges[index][1]
        if not in_copy and line.startswith(b'SELECT pg_catalog.setval('):
            require(len(lines)<1000 and len(line)<=1024)
            lines.append(line+b'\n');total+=len(line)+1;require(total<=8*1024**2)
        offset=end
    return b''.join(lines)


def _binding(stage,runner,policy):
    require(type(stage) is AuthenticatedRestoreStage and type(runner) is NetworkNoneParserRunner
        and type(policy) is DumpInspectionPolicy)
    policy.__post_init__();stage.verify();a=stage._authority;a.__post_init__();a.check()
    # Task2A's authenticated source-facts receipt pins the original canonical
    # document; private mutable observation caches cannot replace its values.
    require(digest(stage._facts)==stage._receipt['source_facts_sha256'])
    b=runner._authority;require(type(b) is ParserAuthority);b.__post_init__()
    require((a.restore_id,a.started_at,a.deadline)==(policy.restore_id,policy.started_at,policy.deadline))
    require((b.nonce,b.started_at,b.deadline)==(a.restore_id,a.started_at,a.deadline)
        and b.cancellation is a.cancellation and (b.uid,b.gid)==(os.geteuid(),os.getegid()))
    require(_parser_sha256(b)==policy.parser_authority_sha256)
    for (kind,ctx),pin,(label,toc) in zip(a.contexts,policy.profiles,policy.toc_policies):
        require(kind==label==ctx.source_kind and pin==(kind,ctx.profile.name,_profile_sha256(ctx),ctx.profile.catalog_sha256))
        owners=set(ctx.role_names)|set(ctx.allowed_grantors)|{'pg_database_owner','-'}
        require(all(record[3] in owners for record in toc.expected_records))
    runner._check()


def _data_policy(stage,kind):
    ctx=dict(stage._authority.contexts)[kind];obs=stage._observations[kind]
    # Stage bound the entire observation already, including historical control
    # scope. This dump analysis selects public tables only, retaining all facts.
    schema=copy.deepcopy(obs['schema']);schema['database'][0][0]='<trusted-database>'
    require(digest(schema)==ctx.profile.catalog_sha256)
    expected={'public.'+table.name for table in ctx.profile.tables}
    copies=stage._facts['snapshot_projections'][kind]
    require(set(copies)==expected and canonical(obs['copy'])==canonical(copies))
    physical={r[1] for r in schema['columns'] if r[0]=='public' and r[2] in {'r','p'}}
    require(physical=={table.name for table in ctx.profile.tables})
    tables=[]
    for table in ctx.profile.tables:
        rows=sorted((r for r in schema['columns'] if r[0]=='public' and r[1]==table.name
            and r[2] in {'r','p'} and r[6] is not None),key=lambda r:r[5])
        require(tuple((r[6],r[7],not r[8]) for r in rows)==tuple((c.name,c.type,c.nullable) for c in table.columns))
        columns=tuple(r[6] for r in rows if r[16]=='')
        require(list(columns)==copies['public.'+table.name]['columns'])
        tables.append(('public.'+table.name,columns))
    source=stage._facts['sequences'][kind]
    require(canonical(source['before'])==canonical(source['dump'])==canonical(source['after']))
    sequences=source['before']
    require(all(row['schema']=='public' for row in sequences))
    observed=[row for row in obs['sequences'] if row['schema']=='public']
    require(canonical(observed)==canonical(sequences))
    return DataPolicy(tuple(tables),tuple((r['schema']+'.'+r['name'],r['last_value'],r['is_called']) for r in sequences))


def _proof_binding(runner,proof,mode,policy):
    require(type(proof) is _InspectedOutput and proof.mode==mode and proof.terminal_observed is True)
    require(type(proof.size) is int and 0<proof.size<=dict(toc=1024**2,schema=8*1024**2,data=16*1024**2)[mode])
    _hash(proof.sha256)
    a=runner._authority
    require((proof.cid,proof.image_id,proof.tool_sha256,proof.authority_sha256)==
        (a.cid,a.image_id,a.pg_restore_sha256,policy.parser_authority_sha256))
    raw=runner.read(proof)
    require(type(raw) is bytes and len(raw)==proof.size and hashlib.sha256(raw).hexdigest()==proof.sha256)
    return raw


class VerifiedRestoreInput:
    """Owns one consumed stage; authenticates dump inspection only."""
    __slots__=('_stage','_runner','_policy','_proofs','_proof_pin','_facts_pin','_receipt','_closed','_stage_authority','_parser_authority')
    def __init__(self,seal=None,*,stage=None,runner=None,policy=None,proofs=None,receipt=None):
        if seal is not _SEAL:raise TypeError('owned verified dump input')
        self._stage=stage;self._runner=runner;self._policy=policy;self._proofs=tuple(proofs)
        self._stage_authority=stage._authority;self._parser_authority=runner._authority
        self._proof_pin=tuple(digest(asdict(proof)) for kind,mode,proof in self._proofs)
        self._facts_pin=digest(dict(facts=stage._facts,observations=stage._observations))
        self._receipt=copy.deepcopy(receipt);self._closed=False
    def __repr__(self):return '<VerifiedRestoreInput: authenticated dump inspection only>'
    def __reduce_ex__(self,protocol):raise TypeError('owned verified dump input')
    def verify(self):
        with self._stage._lock:
            require(not self._closed and self._stage._authority is self._stage_authority
                and self._runner._authority is self._parser_authority)
            _binding(self._stage,self._runner,self._policy)
            require(self._stage._claimed and digest(dict(facts=self._stage._facts,observations=self._stage._observations))==self._facts_pin)
            require(tuple((kind,mode) for kind,mode,proof in self._proofs)==tuple((k,m) for k in _KINDS for m in _MODES))
            require(tuple(digest(asdict(proof)) for kind,mode,proof in self._proofs)==self._proof_pin)
            for kind,mode,proof in self._proofs:_proof_binding(self._runner,proof,mode,self._policy)
            self._stage.verify();self._stage._authority.check()
    @property
    def safe_receipt(self):self.verify();return copy.deepcopy(self._receipt)
    def _borrow_dump(self,kind):self.verify();return self._stage._borrow_dump(kind)
    def _exact_facts(self):self.verify();return copy.deepcopy(self._stage._facts)
    def _exact_observations(self):self.verify();return copy.deepcopy(self._stage._observations)
    def close(self):
        with self._stage._lock:
            if not self._closed:self._closed=True;self._stage.close()
    def __enter__(self):self.verify();return self
    def __exit__(self,*args):self.close()


def inspect_authenticated_stage(stage,parser_runner,policy):
    """Consume a real owned stage once; no targets or SQL can be selected here."""
    try:
        _binding(stage,parser_runner,policy)
        with stage._lock:
            _binding(stage,parser_runner,policy);stage._claim_dump()
            stage_authority=stage._authority;parser_authority=parser_runner._authority
            def check():
                require(stage._authority is stage_authority and parser_runner._authority is parser_authority)
                _binding(stage,parser_runner,policy)
            initial=digest(dict(facts=stage._facts,observations=stage._observations))
            proofs=[];inspections={}
            for kind,toc_policy in policy.toc_policies:
                data_policy=_data_policy(stage,kind);fd=stage._borrow_dump(kind)
                try:
                    summary={}
                    for mode in _MODES:
                        check()
                        proof=parser_runner.run(kind,mode,fd)
                        check()
                        raw=_proof_binding(parser_runner,proof,mode,policy)
                        if mode=='toc':
                            parsed=parse_toc(raw,toc_policy);summary['toc_entries']=parsed.entry_count
                        elif mode=='schema':
                            expected=stage._facts['dump_schema'][kind]
                            verify_schema_bytes(raw,expected['size'],expected['sha256'])
                        else:
                            parsed=screen_data_sql(raw,data_policy)
                            require(canonical(_parse_copy_inventory(raw))==canonical(stage._facts['snapshot_projections'][kind]))
                            actual=_parse_sequence_values(_outside_copy_setvals(raw,parsed))
                            expected=[{key:r[key] for key in ('schema','name','last_value','is_called')}
                                for r in stage._facts['sequences'][kind]['before']]
                            require(canonical(actual)==canonical(expected))
                            summary.update(tables=parsed.table_count,sequences=parsed.sequence_count,rows=parsed.row_count)
                        check()
                        summary[mode]=dict(size=proof.size,sha256=proof.sha256)
                        proofs.append((kind,mode,proof))
                    summary.update(toc_equal=True,schema_equal=True,data_equal=True,copy_equal=True,sequences_equal=True)
                    inspections[kind]=summary
                finally:os.close(fd)
            check()
            require(digest(dict(facts=stage._facts,observations=stage._observations))==initial)
            a=parser_runner._authority
            safe=stage.safe_receipt
            safe.update(dump_preflight_complete=True,dump_inspection=inspections,
                parser=dict(cid=a.cid,image_id=a.image_id,tool_sha256=a.pg_restore_sha256,
                    docker_sha256=a.docker_sha256,inspect_sha256=a.expected_inspect_sha256,authority_sha256=_parser_sha256(a)))
            final=VerifiedRestoreInput(_SEAL,stage=stage,runner=parser_runner,policy=policy,proofs=proofs,receipt=safe)
            final.verify();return final
    except Exception:
        raise B3Error('invalid B3 authenticated dump inspection; evidence retained') from None


def preflight_restore(intent,backup_job_fd,private_pem,authority,parser_runner,policy,stage_parent_fd):
    stage=prepare_authenticated_stage(intent,backup_job_fd,private_pem,authority,stage_parent_fd)
    try:return inspect_authenticated_stage(stage,parser_runner,policy)
    except BaseException:
        stage.close();raise
