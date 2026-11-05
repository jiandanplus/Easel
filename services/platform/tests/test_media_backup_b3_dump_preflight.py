"""Pure tempfile composition tests; no actual B2/backend/runtime imports."""
import ast
import copy
import dataclasses
import hashlib
import importlib
import io
import os
from pathlib import Path
import pickle
import re
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
for name,path in (("platform_service",ROOT/'src/platform_service'),
                  ("platform_service.media_backup_b3",ROOT/'src/platform_service/media_backup_b3')):
    if name not in sys.modules:
        module=types.ModuleType(name);module.__path__=[str(path)];sys.modules[name]=module
from platform_service.media_backup_b3 import preflight as p, parser_runner as pr, dump_screen as ds
try:
    from platform_service.media_backup_b3 import dump_preflight as d
except ImportError:
    d=None
from platform_service.media_backup_b3.contracts import canonical, digest, B3Error
from test_media_backup_b3_preflight import context
from test_media_backup_b3_parser_runner import inspect_fixture, CID, IMAGE, TOOL


def legacy_parsers():
    tree=ast.parse((ROOT/'src/platform_service/media_backup_b2/capture.py').read_text())
    names={'parse_dump_copy_inventory','_copy_digest','parse_dump_sequence_values'}
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
    env=dict(hashlib=hashlib,io=io,re=re,canonical_bytes=canonical,require=p.require,
        MAX_SCHEMA=8*1024**2,MAX_DUMP_DATA=16*1024**2,
        _SETVAL=re.compile(rb"^SELECT pg_catalog\.setval\('([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)', (-?[0-9]+), (true|false)\);$",re.M))
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'original-B2-pure-parsers','exec'),env)
    return env['parse_dump_copy_inventory'],env['parse_dump_sequence_values']


class FinalInputTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(d,'closed final-input composition must exist')
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name).resolve();self.base.chmod(0o700)
        self.fd=os.open(self.base,os.O_RDONLY|os.O_DIRECTORY);self.addCleanup(os.close,self.fd)
        self.contexts=tuple((k,context(k)[0]) for k in ('central','identity'))
        now=time.monotonic();cancel=threading.Event()
        self.a=p.PreflightAuthority('toy_nonce','stage1',now-1,now+100,2*1024**2,0,self.contexts,cancellation=cancel)
        snapshot=inspect_fixture();raw=canonical(snapshot)
        self.pa=pr.ParserAuthority(CID,'toy_nonce',IMAGE,'/usr/bin/docker','d'*64,'unix:///private/tmp/toy.sock',
            '/private/tmp/config',TOOL,raw,hashlib.sha256(raw).hexdigest(),self.fd,os.geteuid(),os.getegid(),
            self.a.started_at,self.a.deadline,128*1024**2,0,cancel)
        self.runner=pr.NetworkNoneParserRunner(self.pa)
        self.copy_parser,self.seq_parser=legacy_parsers();self.sequence_inputs=[];self.copy_inputs=[]
        self.data=b'\n'.join((ds._RESTRICT,*ds._DATA_HEADER,b'COPY public.item (id) FROM stdin;',b'\\.',ds._UNRESTRICT,b''))
        self.schema=b'opaque exact schema\n'
        self.toc=b'\n'.join([b';',b'; Archive created at 2026-11-03 00:00:00 UTC',b';     dbname: toy',
            b';     TOC Entries: 1',b';     Compression: gzip',b';     Dump Version: 1.15-0',b';     Format: CUSTOM',
            b';     Integer: 4 bytes',b';     Offset: 8 bytes',b';     Dumped from database version: 16.15',
            b';     Dumped by pg_dump version: 16.15',b';',b';',b'; Selected TOC Entries:',b';',b'1; 0 1 TABLE public item owner',b''])
        self.outputs={(k,m):v for k in ('central','identity') for m,v in (('toc',self.toc),('schema',self.schema),('data',self.data))}
        self.fds={};self.records=[]
        for k in ('central','identity'):
            file=self.base/(k+'.dump');file.write_bytes(k.encode());file.chmod(0o600)
            fd=os.open(file,os.O_RDONLY);self.addCleanup(os.close,fd)
            self.fds['db/'+k+'.dump']=fd
            self.records.append(dict(name='db/'+k+'.dump',size=len(k),sha256=hashlib.sha256(k.encode()).hexdigest()))
        class Owned:
            def __init__(s,outer):s._files=outer.fds;s._records=outer.records;s.closed=False
            def verify(s):
                p.require(not s.closed)
                for r in s._records:
                    p.require(hashlib.sha256(os.pread(s._files[r['name']],100,0)).hexdigest()==r['sha256'])
            def close(s):s.closed=True
        self.owned=Owned(self)
        obs={k:context(k)[1] for k in ('central','identity')}
        self.facts=dict(snapshot_projections={k:self.copy_parser(self.data) for k in obs},
            sequences={k:dict(before=[],dump=[],after=[]) for k in obs},
            dump_schema={k:dict(size=len(self.schema),sha256=hashlib.sha256(self.schema).hexdigest()) for k in obs})
        for k in obs:obs[k]['copy']=copy.deepcopy(self.facts['snapshot_projections'][k])
        self.stage=p.AuthenticatedRestoreStage(p._SEAL,owned=self.owned,authority=self.a,plan={},facts=self.facts,
            observations=obs,receipt=dict(authenticated_stage=True,dump_preflight_complete=False,plan_sha256='e'*64,
                source_facts_sha256=digest(self.facts)))
        self.policy=self.make_policy()
        self.calls=[]
        def execute(tool,args,stdin=None,cap=1024):
            if tool==pr._SHA:return (TOOL+'  '+pr._PG+'\n').encode()
            kind=next(k for k in ('central','identity') if os.pread(stdin,100,0)==k.encode())
            mode=next(m for m,(fixed,_) in pr._MODES.items() if tuple(args)==fixed)
            self.calls.append((kind,mode));value=self.outputs[(kind,mode)]
            if isinstance(value,Exception):raise value
            return value
        for name,value in (('_inspect',lambda s:None),('_check',lambda s:self.a.check()),('_exec',lambda s,*a,**kw:execute(*a,**kw))):
            patcher=patch.object(pr.NetworkNoneParserRunner,name,value);patcher.start();self.addCleanup(patcher.stop)
        def parse_copy(raw):self.copy_inputs.append(raw);return self.copy_parser(raw)
        def parse_seq(raw):self.sequence_inputs.append(raw);return self.seq_parser(raw)
        for name,value in (('_parse_copy_inventory',parse_copy),('_parse_sequence_values',parse_seq)):
            patcher=patch.object(d,name,value);patcher.start();self.addCleanup(patcher.stop)

    def make_policy(self,**kw):
        values=dict(restore_id=self.a.restore_id,started_at=self.a.started_at,deadline=self.a.deadline,
            profiles=tuple((k,c.profile.name,d._profile_sha256(c),c.profile.catalog_sha256) for k,c in self.contexts),
            toc_policies=tuple((k,ds.TocPolicy((('TABLE','public','item','owner'),),1)) for k,c in self.contexts),
            parser_authority_sha256=d._parser_sha256(self.pa))
        values.update(kw);return d.DumpInspectionPolicy(**values)

    def inspect(self):return d.inspect_authenticated_stage(self.stage,self.runner,self.policy)

    def test_six_outputs_owned_receipt_and_reverification(self):
        final=self.inspect();self.assertEqual(len(self.calls),6);self.assertEqual(len(self.copy_inputs),2)
        self.assertTrue(final.safe_receipt['dump_preflight_complete']);final.verify()
        self.assertEqual(self.a.deadline,self.pa.deadline)
        with self.assertRaises(B3Error):self.inspect()
        with self.assertRaises(TypeError):pickle.dumps(final)
        with self.assertRaises(TypeError):d.VerifiedRestoreInput()
        self.assertNotIn('owner',repr(final));final.close()
        with self.assertRaises(B3Error):final.verify()

    def test_sql_looking_copy_row_is_not_sequence_and_legacy_hash_preserved(self):
        row=b"SELECT pg_catalog.setval('public.decoy', 1, false);\n"
        data=self.data.replace(b'\\.\n',row+b'\\.\n')
        for k in ('central','identity'):
            self.outputs[k,'data']=data;self.stage._facts['snapshot_projections'][k]=self.copy_parser(data)
            self.stage._observations[k]['copy']=self.copy_parser(data)
        self.stage._receipt['source_facts_sha256']=digest(self.stage._facts)
        final=self.inspect();final.verify();self.assertEqual(self.sequence_inputs,[b'',b''])
        self.assertEqual(self.copy_inputs,[data,data]);self.assertNotEqual(self.copy_parser(data),self.copy_parser(self.data))

    def test_wrong_policy_and_unknown_runner_reject_before_tools(self):
        for policy in (self.make_policy(restore_id='other'),self.make_policy(parser_authority_sha256='0'*64),
                       self.make_policy(profiles=tuple((k,n,'0'*64,h) for k,n,p,h in self.policy.profiles))):
            with self.assertRaises(B3Error):d.inspect_authenticated_stage(self.stage,self.runner,policy)
        with self.assertRaises(B3Error):d.inspect_authenticated_stage(self.stage,object(),self.policy)
        self.assertEqual(self.calls,[])

    def test_second_kind_failure_never_mints(self):
        self.outputs['identity','schema']=b'different'
        with self.assertRaises(B3Error):self.inspect()
        self.assertEqual(self.calls[:3],[('central','toc'),('central','schema'),('central','data')])
        with self.assertRaises(B3Error):self.inspect()

    def test_closed_cancel_time_and_custody_reject(self):
        self.a.cancellation.set()
        with self.assertRaises(B3Error):self.inspect()
        self.assertEqual(self.calls,[])

    def test_extra_sql_rejects_before_legacy_parser(self):
        self.outputs['central','data']=self.data.replace(ds._UNRESTRICT,b'DROP TABLE public.item;\n'+ds._UNRESTRICT)
        with self.assertRaises(B3Error):self.inspect()
        self.assertEqual(self.copy_inputs,[])

    def test_toc_mismatch_rejects(self):
        self.outputs['central','toc']=self.toc.replace(b' item owner',b' unexpected owner')
        with self.assertRaises(B3Error):self.inspect()
        self.assertEqual(self.calls,[('central','toc')])

    def test_schema_same_size_wrong_hash_rejects(self):
        self.outputs['central','schema']=b'x'*len(self.schema)
        with self.assertRaises(B3Error):self.inspect()
        self.assertEqual(len(self.calls),2)

    def test_copy_columns_rejects(self):
        self.outputs['central','data']=self.data.replace(b'(id)',b'(other)')
        with self.assertRaises(B3Error):self.inspect()
        self.assertEqual(self.copy_inputs,[])

    def test_copy_digest_rejects(self):
        self.outputs['central','data']=self.data.replace(b'\\.\n',b'changed\n\\.\n')
        with self.assertRaises(B3Error):self.inspect()
        self.assertEqual(len(self.copy_inputs),1)
        self.assertEqual(self.sequence_inputs,[])

    def test_missing_empty_table_rejects(self):
        self.outputs['central','data']=self.data.replace(b'COPY public.item (id) FROM stdin;\n\\.\n',b'')
        with self.assertRaises(B3Error):self.inspect()

    def test_unknown_tool_error_retains_runner_and_no_final(self):
        self.outputs['identity','data']=RuntimeError('sensitive SQL')
        with self.assertRaises(B3Error) as error:self.inspect()
        self.assertNotIn('sensitive',str(error.exception));self.assertTrue(self.runner._failed)
        self.assertEqual(len(self.calls),6);self.assertFalse(self.owned.closed)

    def test_forged_proof_not_readable(self):
        proof=pr._InspectedOutput('toc',1,'a'*64,CID,IMAGE,TOOL,d._parser_sha256(self.pa),True)
        with patch.object(pr.NetworkNoneParserRunner,'run',return_value=proof):
            with self.assertRaises(B3Error):self.inspect()
        self.assertEqual(self.calls,[])

    def test_final_stage_file_drift(self):
        final=self.inspect();(self.base/'central.dump').write_bytes(b'drift')
        with self.assertRaises(B3Error):final.verify()

    def test_final_stage_caller_close(self):
        final=self.inspect();self.stage.close()
        with self.assertRaises(B3Error):final.verify()

    def test_final_facts_drift(self):
        final=self.inspect();self.stage._facts['sequences']['central']['before'].append(dict(schema='public',name='fake'))
        with self.assertRaises(B3Error):final.verify()

    def test_final_cancel_and_original_expiry(self):
        final=self.inspect();self.a.cancellation.set()
        with self.assertRaises(B3Error):final.verify()
        self.a.cancellation.clear()
        with patch.object(p.time,'monotonic',return_value=self.a.deadline):
            with self.assertRaises(B3Error):final.verify()
        self.assertEqual(self.a.deadline,self.pa.deadline)

    def test_parser_time_cancellation_and_uid_bindings(self):
        for change in (dict(started_at=self.pa.started_at-1),dict(deadline=self.pa.deadline+1),
                       dict(cancellation=threading.Event()),dict(uid=self.pa.uid+1)):
            candidate=dataclasses.replace(self.pa,**change)
            self.runner._authority=candidate
            self.policy=self.make_policy(parser_authority_sha256=d._parser_sha256(candidate))
            with self.assertRaises(B3Error):self.inspect()
        self.assertEqual(self.calls,[])

    def test_catalog_drift_rejects(self):
        self.stage._observations['central']['schema']['columns'][0][3]='other'
        with self.assertRaises(B3Error):self.inspect()
        self.assertEqual(self.calls,[])

    def test_policy_immutable_and_owner_ceiling(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):self.policy.deadline+=1
        for change in (dict(profiles=list(self.policy.profiles)),
                       dict(toc_policies=(['central',self.policy.toc_policies[0][1]],self.policy.toc_policies[1]))):
            with self.assertRaises(B3Error):self.make_policy(**change)
        policy=ds.TocPolicy((('TABLE','public','item','undeclared'),),1)
        self.policy=self.make_policy(toc_policies=(('central',policy),self.policy.toc_policies[1]))
        with self.assertRaises(B3Error):self.inspect()
        self.assertEqual(self.calls,[])

    def test_actual_sequence_bytes_extraction_bounds_and_copy_cr(self):
        # This independently screens a toy public sequence without inventing
        # complete Stage sequence/catalog authentication in this unit control.
        row=b"SELECT pg_catalog.setval('public.decoy', 9, false);\r\n"
        actual=b"SELECT pg_catalog.setval('public.real', -3, true);\n"
        raw=self.data.replace(b'\\.\n',row+b'\\.\n').replace(ds._UNRESTRICT,actual+ds._UNRESTRICT)
        policy=ds.DataPolicy((('public.item',('id',)),),(('public.real',-3,True),))
        screen=ds.screen_data_sql(raw,policy)
        extracted=d._outside_copy_setvals(raw,screen)
        self.assertEqual(extracted,actual)
        self.assertEqual(self.seq_parser(extracted),[dict(schema='public',name='real',last_value=-3,is_called=True)])
        for value,called in ((-2,True),(-3,False)):
            with self.assertRaises(B3Error):ds.screen_data_sql(raw,ds.DataPolicy(policy.tables,(('public.real',value,called),)))

    def test_composer_closes_owned_stage_after_rejection(self):
        with patch.object(d,'prepare_authenticated_stage',return_value=self.stage) as prepare:
            with self.assertRaises(B3Error):d.preflight_restore('intent',1,b'not a key',self.a,object(),self.policy,self.fd)
        prepare.assert_called_once_with('intent',1,b'not a key',self.a,self.fd)
        self.assertTrue(self.owned.closed)

    def add_sequence(self):
        source=dict(schema='public',name='real',owner='owner',data_type='bigint',start=1,increment=1,
            min=1,max=9223372036854775807,cache=1,cycle=False,owned_schema=None,owned_table=None,
            owned_column=None,dependency=None,last_value=3,is_called=True)
        contexts=[]
        for kind,c in self.contexts:
            self.stage._facts['sequences'][kind]={phase:[copy.deepcopy(source)] for phase in ('before','dump','after')}
            obs=self.stage._observations[kind];obs['sequences']=[copy.deepcopy(source)]
            obs['schema']['sequence_definitions']=[[source[k] for k in ('schema','name','data_type','start','increment',
                'min','max','cache','cycle','owned_schema','owned_table','owned_column','dependency')]]
            schema=copy.deepcopy(obs['schema']);schema['database'][0][0]='<trusted-database>'
            contexts.append((kind,dataclasses.replace(c,profile=dataclasses.replace(c.profile,catalog_sha256=digest(schema)))))
            self.outputs[kind,'data']=self.data.replace(ds._UNRESTRICT,b"SELECT pg_catalog.setval('public.real', 3, true);\n"+ds._UNRESTRICT)
        self.contexts=tuple(contexts);self.a=dataclasses.replace(self.a,contexts=self.contexts);self.stage._authority=self.a
        self.stage._receipt['source_facts_sha256']=digest(self.stage._facts);self.policy=self.make_policy()

    def test_sequence_positive_uses_actual_legacy_bytes(self):
        self.add_sequence();final=self.inspect();final.verify()
        self.assertEqual(self.sequence_inputs,[b"SELECT pg_catalog.setval('public.real', 3, true);\n"]*2)

    def test_sequence_value_rejects(self):
        self.add_sequence();self.outputs['central','data']=self.outputs['central','data'].replace(b', 3, true)',b', 4, true)')
        with self.assertRaises(B3Error):self.inspect()

    def test_sequence_called_rejects(self):
        self.add_sequence();self.outputs['central','data']=self.outputs['central','data'].replace(b', 3, true)',b', 3, false)')
        with self.assertRaises(B3Error):self.inspect()

    def test_sequence_definition_or_source_mutation_rejects(self):
        self.add_sequence();self.stage._facts['sequences']['central']['before'][0]['increment']=2
        with self.assertRaises(B3Error):self.inspect()
        self.assertEqual(self.calls,[])

    def test_final_proof_readback_mutation_rejects(self):
        final=self.inspect();proof=next(iter(self.runner._outputs));self.runner._outputs[proof]=b'forged'
        with self.assertRaises(B3Error):final.verify()

    def test_generated_column_only_excluded_from_copy(self):
        for kind in ('central','identity'):
            row=copy.deepcopy(self.stage._observations[kind]['schema']['columns'][0]);row[5]=2;row[6]='computed';row[16]='s'
            self.stage._observations[kind]['schema']['columns'].append(row)
        # The independent context must be repinned for this distinct catalog.
        contexts=[]
        for kind,c in self.contexts:
            schema=copy.deepcopy(self.stage._observations[kind]['schema']);schema['database'][0][0]='<trusted-database>'
            col=dataclasses.replace(c.profile.tables[0].columns[0],name='computed')
            table=dataclasses.replace(c.profile.tables[0],columns=(*c.profile.tables[0].columns,col))
            profile=dataclasses.replace(c.profile,tables=(table,),catalog_sha256=digest(schema))
            contexts.append((kind,dataclasses.replace(c,profile=profile)))
        self.contexts=tuple(contexts);self.a=dataclasses.replace(self.a,contexts=self.contexts);self.stage._authority=self.a
        self.policy=self.make_policy();final=self.inspect();final.verify()
        self.assertEqual(len(self.stage._observations['central']['schema']['columns']),2)


if __name__=='__main__':unittest.main()
