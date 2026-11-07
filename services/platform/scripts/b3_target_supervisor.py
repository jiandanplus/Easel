"""External supervisor protocol primitives. Stdlib only; no import-time IO.

Execution requires an independently reviewed immutable Root spec and a postlaunch
Tool session binding. Import and passive bootstrap perform no native/source IO.
"""
import time
from pathlib import Path
from b3_target_once import (canonical, closed_json, closed, hash_value, require,
                            sha, private_read, write_new)


def validate_session_binding(binding, bootstrap):
    """Validate Root's postlaunch record; never invent a future Tool session."""
    closed(bootstrap, 'format supervisor_pid supervisor_nonce code_sha256 interpreter_sha256')
    require(bootstrap['format'] == 'b3-supervisor-bootstrap-v1')
    require(type(bootstrap['supervisor_pid']) is int and bootstrap['supervisor_pid'] > 0)
    for key in ('supervisor_nonce', 'code_sha256', 'interpreter_sha256'):
        hash_value(bootstrap[key])
    closed(binding, 'format supervisor_pid supervisor_nonce bootstrap_sha256 tool_session')
    require(binding['format'] == 'b3-supervisor-session-binding-v1')
    require(type(binding['supervisor_pid']) is int)
    require(all(binding[key] == bootstrap[key] for key in ('supervisor_pid', 'supervisor_nonce')))
    require(binding['bootstrap_sha256'] == sha(canonical(bootstrap)))
    session = binding['tool_session']
    require(type(session) is str and 0 < len(session) <= 256 and session == session.strip())
    require(all(ord(char) >= 32 and ord(char) != 127 for char in session))
    return session


def bind_original_job(directory, bootstrap, binding, operation_nonce):
    """Freeze one original 900-second clock AFTER external session binding.

    Caller must already hold verified private bootstrap/session records. Writing
    original_job is exclusive and durable; collision/ambiguity never resets it.
    """
    validate_session_binding(binding, bootstrap)
    hash_value(operation_nonce)
    started = float(time.monotonic())
    job = dict(format='b3-root-original-job-v1', operation_nonce=operation_nonce,
               started=started, deadline=started + 900,
               supervisor_pid=bootstrap['supervisor_pid'],
               supervisor_nonce=bootstrap['supervisor_nonce'])
    write_new(Path(directory) / 'original_job.private.json', canonical(job))
    return job

import argparse
import base64
import os
import re
import select
import stat
import subprocess
import signal
import sys
from b3_target_once import (DOCKER_PATH, DOCKER_SHA, SOCKET, SOURCE_CID,
    SOURCE_IMAGE_REF, PARSER_IMAGE, PARSER_TOOL_SHA, TOOLS, SOURCE,
    fd_read, report_write, source_document, dependency_hashes, validate_config,
    proof_documents, parser_plan, validate_descriptor, check_parser_start_budget,
    mkdir_private, stamp, pinned_file)

SELF = Path(__file__).resolve()
ENTRY = SOURCE / 'scripts/verify-b3-target-lifecycle.py'
LAUNCHER = SOURCE / 'scripts/b3_target_launch.py'
COMPONENTS = ('source_capture','authenticated_stage','parser_outputs',
              'central_target','identity_target','journals_and_logs')
ALLOWANCES = (512,512,128,768,768,256)  # MiB, conservative FUTURE allowances
RETAINED = []
STARTUP_ROOTS=(
    '/private/tmp/easel-central-catalog-candidate-20261103-r1/runtime',
    '/private/tmp/easel-b2-t12-install-20261030/venv/lib/python3.12/site-packages',
    '/private/tmp/easel-b2-test-tools-20261030')
VIRTUALENV_HOOK_SHA='6cf30c56faf2a55228914dbbd17f8088ed371ebb08f5e7fa6fd931f913fcaf1d'


def capacity_measurement(job, device, measured_at):
    return dict(format='b3-root-capacity-measurement-v1',
        **{k:job[k] for k in ('operation_nonce','started','deadline')},
        measured_at=measured_at,device=device,method='measured-components-plus-conservative-peak',
        components=[dict(name=name,observation='not-created',measured_bytes=0,
                        peak_allowance_bytes=size*1024**2) for name,size in zip(COMPONENTS,ALLOWANCES)],
        peak_bytes=sum(ALLOWANCES)*1024**2,reserved_bytes=10*1024**3+sum(ALLOWANCES)*1024**2)


def validate_source_inspect(value):
    require(type(value) is dict and value['Id']==SOURCE_CID and value['Image']==PARSER_IMAGE)
    state=value['State']
    require(state['Pid']==62835 and state['StartedAt']=='2026-11-03T09:24:06.729872208Z')
    require(value['Config']['Image']==PARSER_IMAGE)
    require(value['RestartCount']==0 and value['Config']['User'] in ('','0','root'))
    require(all(state[k] is False for k in ('Restarting','Paused','Dead')))
    require(re.fullmatch(r'\d{4}-\d\d-\d\dT[^ ]+Z',state['StartedAt']) is not None)
    require(state['Running'] is True and type(state['Pid']) is int and state['Pid']>0
            and type(state['StartedAt']) is str and bool(state['StartedAt']) and not value['ExecIDs'])
    require(value['NetworkSettings']['Ports']=={'5432/tcp':[{'HostIp':'127.0.0.1','HostPort':'51430'}]})
    volumes=[v['Name'] for v in value['Mounts'] if v['Type']=='volume']
    require(bool(volumes) and len(volumes)==len(set(volumes)))
    require(all(type(v) is str and bool(v) for v in volumes))
    require(all(v['Type']=='volume' for v in value['Mounts']))
    require(len(value['Mounts'])==1 and value['Mounts'][0]['Destination']=='/var/lib/postgresql/data'
            and value['Mounts'][0]['RW'] is True)
    require(volumes==['easel-b2-disk-e419567e242e68b72e0a7b3a26d4385f-data'])
    return sorted(volumes)


def validate_terminal(outcome, manifest_hash, deadline=None, *, expected_root_context=None):
    keys='manifest_sha256 actual_executed success status isolated_restore_verified production provider timer origins independent_contexts_before_capture context_sha256 capture_committed targets source_preservation normal_work_state receipt files stage_retained_after_close caller_fds_alive dump_preflight_complete SQL_restore final_work_state child_or_remote_unknown cleanup_error_classes synthetic_cleanup_complete'
    attest=outcome.get('status')=='target_worker_attestation_verified'
    closed(outcome,keys+(' execution' if attest else ''))
    require(outcome['manifest_sha256']==manifest_hash and outcome['status'] in ('target_lifecycle_verified','target_worker_attestation_verified'))
    for name in ('actual_executed','success','independent_contexts_before_capture','capture_committed',
                 'stage_retained_after_close','caller_fds_alive','dump_preflight_complete','synthetic_cleanup_complete'):
        require(outcome[name] is True)
    for name in ('SQL_restore','isolated_restore_verified','production','provider','timer','child_or_remote_unknown'):
        require(outcome[name] is False)
    require(outcome['cleanup_error_classes']==[])
    preserve=outcome['source_preservation']
    closed(preserve,'scope baseline_sha256 post_sha256 equal')
    require(preserve['scope']=='post-capture-to-post-target-before-own-source-cleanup'
            and preserve['equal'] is True and preserve['baseline_sha256']==preserve['post_sha256'])
    hash_value(preserve['baseline_sha256'])
    for key in ('normal_work_state','final_work_state'):
        from b3_target_once import graph_state
        expected=graph_state({})
        if not attest and not any(k.startswith('execution_') for k in outcome[key]):
            expected={k:v for k,v in expected.items() if not k.startswith('execution_')}
        require(set(outcome['normal_work_state'])==set(outcome['final_work_state']))
        closed(outcome[key],' '.join(expected))
        if attest:
            expected.update(execution_owner_count=1,execution_target_count=2,execution_phase_claims=2,execution_committed_count=2)
        for field,value in outcome[key].items():
            require(type(value) is type(expected[field]))
            if attest and field=='execution_local_terminal_count':require(value>0)
            elif field=='parser_local_terminal_count':require(value>=0)
            else:require(value==expected[field])
    require(type(outcome['targets']) is list and len(outcome['targets'])==2)
    from b3_target_once import safe_receipt, FIXED
    safe_receipt(outcome['receipt'])
    for target in outcome['targets']:
        closed(target,'status identity_sha256 runtime_journal_sha256 policy_sha256 original_deadline readiness_observed tcp_scram_hba_observed durability_observed wrong_bootstrap_password_denied pid1_identity_observed docker_network_none_configured kernel_network_isolation_proven isolated_restore_verified production provider timer'+(' target_cid' if attest else ''))
        if attest:hash_value(target['target_cid'])
        require(target['status']=='target_instance_bound')
        if deadline is not None:require(target['original_deadline']==deadline)
        for k in ('identity_sha256','runtime_journal_sha256','policy_sha256'):hash_value(target[k])
        for k in ('readiness_observed','tcp_scram_hba_observed','durability_observed','wrong_bootstrap_password_denied','pid1_identity_observed','docker_network_none_configured'):require(target[k] is True)
        for k in ('kernel_network_isolation_proven','isolated_restore_verified','production','provider','timer'):require(target[k] is False)
    require(type(outcome['files']) is dict and set(outcome['files'])==FIXED)
    for info in outcome['files'].values():
        closed(info,'size sha256');require(type(info['size']) is int and 0<=info['size']<=64*1024**2);hash_value(info['sha256'])
    require(type(outcome['origins']) is dict and bool(outcome['origins']))
    for origin in outcome['origins'].values():
        closed(origin,'path_sha256 sha256');hash_value(origin['path_sha256']);hash_value(origin['sha256'])
    require(type(outcome['context_sha256']) is list and len(outcome['context_sha256'])==2)
    for value in outcome['context_sha256']:hash_value(value)
    if attest:
        root=expected_root_context
        closed(root,'restore_id source_container_id operation_ids job_device job_inode started_at_ns deadline_ns')
        closed(root['operation_ids'],'central identity')
        hash_value(root['source_container_id'])
        for value in (root['restore_id'],*root['operation_ids'].values()):
            require(type(value) is str and re.fullmatch('[a-zA-Z0-9_][a-zA-Z0-9_-]{0,95}',value))
        for name in ('job_device','job_inode','started_at_ns','deadline_ns'):
            require(type(root[name]) is int and root[name]>=(0 if name in ('job_device','started_at_ns') else 1))
        execution=outcome['execution']
        closed(execution,'format target_bound attestation_complete SQL_restore isolated_restore_verified input_sha256 context_sha256 job_device job_inode started_at_ns deadline_ns summaries')
        require(execution['format']=='b3-target-worker-attestation-v1' and execution['target_bound'] is True
            and execution['attestation_complete'] is True and execution['SQL_restore'] is False and execution['isolated_restore_verified'] is False)
        hash_value(execution['input_sha256'])
        closed(execution['context_sha256'],'central identity')
        require(execution['context_sha256']==dict(zip(('central','identity'),outcome['context_sha256'])))
        for field in ('job_device','job_inode','started_at_ns','deadline_ns'):
            require(type(execution[field]) is int and execution[field]>=(0 if field in ('job_device','started_at_ns') else 1))
        require(0<execution['deadline_ns']-execution['started_at_ns']<=1800*10**9)
        if deadline is not None:
            import math
            require(execution['deadline_ns']==math.floor(deadline*10**9))
        require(all(execution[k]==root[k] for k in ('job_device','job_inode','started_at_ns','deadline_ns')))
        summaries=execution['summaries'];require(type(summaries) is list and len(summaries)==2)
        for i,(kind,summary) in enumerate(zip(('central','identity'),summaries)):
            closed(summary,'binding_data kind format operation binding_sha256 manifest_sha256 request_sha256 private_attestation_sha256 input_sha256 context_sha256 target_identity_sha256 job_device job_inode started_at_ns deadline_ns')
            require(summary['kind']==kind and summary['format']=='easel.b3.worker-attestation-summary.v3' and summary['operation']=='attest_worker')
            for field,value in summary.items():
                if field.endswith('sha256'):hash_value(value)
            for field in ('input_sha256','job_device','job_inode','started_at_ns','deadline_ns'):
                require(type(summary[field]) is type(execution[field]) and summary[field]==execution[field])
            require(summary['context_sha256']==execution['context_sha256'][kind]
                and summary['target_identity_sha256']==outcome['targets'][i]['identity_sha256'])
            binding=summary['binding_data']
            closed(binding,'restore_id operation_id kind operation source_container_id target_container_id target_identity_sha256 verified_input_sha256 context_sha256 started_at_ns deadline_ns job_device job_inode')
            expected=dict(restore_id=root['restore_id'],operation_id=root['operation_ids'][kind],kind=kind,operation='attest_worker',
                source_container_id=root['source_container_id'],target_container_id=outcome['targets'][i]['target_cid'],
                target_identity_sha256=outcome['targets'][i]['identity_sha256'],verified_input_sha256=execution['input_sha256'],
                context_sha256=execution['context_sha256'][kind],
                **{k:root[k] for k in ('job_device','job_inode','started_at_ns','deadline_ns')})
            require(all(type(binding[k]) is type(v) and binding[k]==v for k,v in expected.items()))
            require(binding['source_container_id']!=binding['target_container_id'] and summary['binding_sha256']==sha(canonical(expected)))
        require(outcome['targets'][0]['target_cid']!=outcome['targets'][1]['target_cid'])
        require(summaries[0]['binding_sha256']!=summaries[1]['binding_sha256'] and summaries[0]['request_sha256']!=summaries[1]['request_sha256'])
    return True


class ProcessGraph:
    """Strong original handles and bounded pipe observations, no retry/cleanup.

    A pipe reaching its cap is left open and undrained, retaining the original
    child. This prevents unbounded file growth without claiming cancellation.
    """
    def __init__(self,directory,job):
        self.directory=Path(directory);self.job=job;self.attempts=[];self.fds=[]
        self.unknown=False;self.error=None;self.connections=[];self.operations=[]

    def check(self):
        try:
            require(not self.unknown)
            require(self.job['started']<=time.monotonic()<self.job['deadline'])
        except BaseException:
            self.unknown=True
            if self.error is None:self.error=sys.exc_info()
            raise

    def new(self,name,raw=b''):
        path=self.directory/name
        attempt=dict(operation='exclusive-file',path=path,fd=None)
        self.operations.append(attempt)
        try:
            require(path.parent==self.directory and path.name==name and name not in ('.','..'))
            directory=os.open(self.directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            self.fds.append(directory);parent=os.fstat(directory)
            require(stat.S_IMODE(parent.st_mode)==0o700 and
                    (parent.st_uid,parent.st_gid)==(os.getuid(),os.getgid()))
            fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            attempt['fd']=fd;self.fds.append(fd)
            os.fchown(fd,os.getuid(),os.getgid());os.fchmod(fd,0o600)
            if raw:
                offset=0
                while offset<len(raw):
                    count=os.pwrite(fd,raw[offset:],offset);require(count>0);offset+=count
            os.fsync(fd)
            parent=os.open(self.directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            self.fds.append(parent);os.fsync(parent)
            return fd
        except BaseException:
            self.unknown=True;self.error=sys.exc_info();raise

    def _spawn(self,argv,pass_fds):
        self.check()
        attempt=dict(operation='spawn',argv=tuple(argv),process=None,streams=[],terminal=None)
        self.attempts.append(attempt)
        try:
            child=object.__new__(subprocess.Popen);attempt['process']=child
            subprocess.Popen.__init__(child,argv,stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,pass_fds=tuple(pass_fds),close_fds=True,
                env=dict(PATH='/usr/bin:/bin',LANG='C',LC_ALL='C',HOME=str(self.directory)))
            attempt['pid']=child.pid
            for suffix,pipe,limit in (('out',child.stdout,2*1024**2),('err',child.stderr,65536)):
                fd=self.new('native-'+str(len(self.attempts))+'-'+suffix)
                os.set_blocking(pipe.fileno(),False)
                attempt['streams'].append(dict(pipe=pipe,fd=fd,count=0,limit=limit,eof=False))
            return attempt
        except BaseException:
            self.unknown=True;self.error=sys.exc_info();raise

    def observe(self,attempt):
        self.check()
        try:
            for stream in attempt['streams']:
                if stream['eof']:continue
                require(stream['count']<stream['limit'])
                try:data=os.read(stream['pipe'].fileno(),min(65536,stream['limit']-stream['count']))
                except BlockingIOError:continue
                if not data:stream['eof']=True;continue
                count=os.pwrite(stream['fd'],data,stream['count']);require(count==len(data))
                stream['count']+=count
            code=attempt['process'].poll()
            require(code is None or type(code) is int)
            if code is not None and all(v['eof'] for v in attempt['streams']):
                attempt['terminal']=code
                for stream in attempt['streams']:os.fsync(stream['fd'])
                return code
            return None
        except BaseException:
            self.unknown=True;self.error=sys.exc_info();raise

    def wait(self,attempt):
        while True:
            code=self.observe(attempt)
            if code is not None:
                require(code==0)
                require(attempt['streams'][1]['count']==0)
                return os.pread(attempt['streams'][0]['fd'],2*1024**2,0)
            time.sleep(.02)

    def mark_unknown(self):
        self.unknown=True
        if self.error is None:self.error=sys.exc_info()
        if self not in RETAINED:RETAINED.append(self)
        # Best effort status never replaces the original exception or handles.
        original_error=self.error
        try:
            self.new('unknown.safe.json',canonical(dict(status='UNKNOWN',cleanup_performed=False,
                original_attempts=len(self.attempts),original_operations=len(self.operations))))
        except BaseException:pass
        self.error=original_error

    def hold(self):
        self.mark_unknown()
        while True:
            try:
                # Observe only original local handles; never issue a new probe.
                pending=[self]
                while pending:
                    original=pending.pop()
                    pending.extend(op['graph'] for op in original.operations if op['operation']=='job-graph')
                    for attempt in original.attempts:
                        child=attempt.get('process')
                        if child is not None:
                            try:attempt['passive_returncode']=child.poll()
                            except BaseException:pass
                time.sleep(1)
            except BaseException:pass

    def docker(self,*args):
        require(Path(DOCKER_PATH).is_file() and sha(Path(DOCKER_PATH).read_bytes())==DOCKER_SHA)
        return self.wait(self._spawn([DOCKER_PATH,'-H',SOCKET,'--config',str(self.directory/'docker-config'),*args],()))


def pinned_doc(graph,name,value):
    raw=canonical(value);graph.new(name,raw)
    return dict(path=str(graph.directory/name),sha256=sha(raw))


def validate_spec(spec):
    attest=spec.get('format')=='b3-external-supervisor-spec-attest-v1'
    closed(spec,'format bootstrap directory supervisor_nonce operation_nonce source authority runtime interpreter runtime_roots pth_files symlink_targets repository_dependencies'+(' execution_policy' if attest else ''))
    require(spec['format'] in ('b3-external-supervisor-spec-v1','b3-external-supervisor-spec-attest-v1'))
    if attest:
        # Reuse the same closed configuration policy parser without a loader.
        validate_config(dict(format='b3-target-root-config-attest-v1',directory=spec['directory'],started=1.,deadline=901.,
            operation_nonce=spec['operation_nonce'],source=spec['source'],authority=spec['authority'],runtime=spec['runtime'],
            source_proof=dict(path='/private/tmp/source',sha256='0'*64),capacity_proof=dict(path='/private/tmp/capacity',sha256='0'*64),
            original_job=dict(path='/private/tmp/job',sha256='0'*64),execution_policy=spec['execution_policy']),now=2.)
    prefix='/private/tmp/easel-central-catalog-candidate-20261103-r1/runtime'
    require(spec['runtime']['prefix']==prefix and spec['interpreter']['path']==prefix+'/bin/python3')
    require(spec['authority']['path']=='/private/tmp/easel-b2-owned-pg-e419567e242e68b72e0a7b3a26d4385f/authority.private.json')
    require(spec['source']['nonce']=='e419567e242e68b72e0a7b3a26d4385f')
    require(spec['authority']['sha256']=='25ad6293eedc068d8b805353c5d966cf285a308a429b7db50e38868238231ee4')
    for key in ('supervisor_nonce','operation_nonce'):hash_value(spec[key])
    for key in ('bootstrap','directory'):
        path=Path(spec[key]);require(path.is_absolute() and str(path)==os.path.normpath(path) and '..' not in path.parts)
    require(spec['directory']!=spec['bootstrap'])
    closed(spec['source'],'container_id image nonce sha256')
    require(spec['source']['container_id']==SOURCE_CID and spec['source']['image']==SOURCE_IMAGE_REF)
    require(re.fullmatch('[a-f0-9]{32}',spec['source']['nonce']) is not None)
    closed(spec['authority'],'path sha256');hash_value(spec['authority']['sha256'])
    require(spec['authority']['sha256']==spec['source']['sha256'])
    closed(spec['runtime'],'prefix testtools dependencies')
    closed(spec['interpreter'],'path sha256');hash_value(spec['interpreter']['sha256'])
    require(type(spec['runtime_roots']) is list and len(spec['runtime_roots'])>=3)
    require(len(set(spec['runtime_roots']))==len(spec['runtime_roots']))
    require(set(STARTUP_ROOTS)<=set(spec['runtime_roots']))
    require(spec['runtime']['testtools']==STARTUP_ROOTS[2])
    require(type(spec['pth_files']) is list and bool(spec['pth_files']))
    require(type(spec['runtime']['dependencies']) is dict and bool(spec['runtime']['dependencies']))
    require(type(spec['repository_dependencies']) is dict and bool(spec['repository_dependencies']))
    for path,digest in {**spec['runtime']['dependencies'],**spec['repository_dependencies']}.items():
        require(Path(path).is_absolute());hash_value(digest)
    require(spec['runtime']['dependencies'].get(spec['interpreter']['path'])==spec['interpreter']['sha256'])
    require(type(spec['symlink_targets']) is dict)
    for origin,target in spec['symlink_targets'].items():
        require(Path(origin).is_absolute() and Path(target).is_absolute() and origin!=target)
        require(spec['runtime']['dependencies'].get(origin)==spec['runtime']['dependencies'].get(target)
                and origin in spec['runtime']['dependencies'])
    return spec


def validate_bootstrap_interpreter(spec):
    # Python <=3.13 -S deliberately leaves sys.prefix at the base installation.
    require(sys.executable==spec['interpreter']['path'])
    require(sha(Path(sys.executable).read_bytes())==spec['interpreter']['sha256'])


def verify_inventory(spec):
    """Only after bound original_job; no inference/execution of PTH content."""
    dependencies=spec['runtime']['dependencies']
    aliases=spec.get('symlink_targets',{})
    require(set(STARTUP_ROOTS)<=set(spec['runtime_roots']))
    discovered_pth=set()
    def verify_alias(path):
        require(str(path) in aliases and str(path.resolve(strict=True))==aliases[str(path)])
        require(dependencies.get(str(path))==dependencies.get(aliases[str(path)])
                and str(path) in dependencies)
    for origin in aliases:
        require(Path(origin).is_symlink());verify_alias(Path(origin))
    for root in spec['runtime_roots']:
        path=Path(root);require(path.is_absolute() and path.is_dir() and not path.is_symlink())
        for item in path.rglob('*'):
            if item.is_symlink():verify_alias(item)
            if item.is_file() and item.suffix!='.pyc' and '__pycache__' not in item.parts:
                require(str(item) in dependencies)
                if item.suffix=='.pth':discovered_pth.add(str(item))
    listed=spec['pth_files']
    require(len(listed)==len(set(listed)))
    require(set(listed)==discovered_pth=={p for p in dependencies if Path(p).suffix=='.pth'})
    for pth in listed:
        require(pth in dependencies and Path(pth).suffix=='.pth')
        raw=Path(pth).read_bytes()
        require(sha(raw)==dependencies[pth])
        if Path(pth).name=='_virtualenv.pth':
            require(raw==b'import _virtualenv')
            hook=Path(pth).with_name('_virtualenv.py')
            require(dependencies.get(str(hook))==VIRTUALENV_HOOK_SHA)
            continue
        for line in raw.decode('utf-8').splitlines():
            if not line.strip() or line.startswith('#'):continue
            require(line in spec['runtime_roots'])
    for path,digest in {**dependencies,**spec['repository_dependencies']}.items():
        require(sha(Path(path).read_bytes())==digest)
    expected=dependency_hashes(spec['runtime'])
    require(all(expected.get(path)==digest for path,digest in spec['repository_dependencies'].items()))
    repository={path:digest for path,digest in expected.items() if Path(path).is_relative_to(SOURCE)}
    require(repository==spec['repository_dependencies'])


def check_loaded_origins(spec):
    pins={**spec['runtime']['dependencies'],**spec['repository_dependencies']}
    for module in tuple(sys.modules.values()):
        filename=getattr(module,'__file__',None)
        if filename and not filename.startswith('<'):
            path=str(Path(filename).resolve())
            require(path in pins and sha(Path(path).read_bytes())==pins[path])


def source_collect(spec,job,graph):
    verify_inventory(spec)
    data=source_document(dict(authority=spec['authority'],source=spec['source']))
    before=closed_json(graph.docker('inspect','--type','container',SOURCE_CID))
    require(type(before) is list and len(before)==1)
    volumes=validate_source_inspect(before[0])
    paths=[v[1] for v in TOOLS]+['/usr/lib/postgresql/16/bin/pg_restore']
    tool_raw=graph.docker('exec',SOURCE_CID,'/usr/bin/sha256sum',*paths)
    expected=''.join(digest+'  '+path+'\n' for _,path,digest in TOOLS)+(PARSER_TOOL_SHA+'  '+paths[-1]+'\n')
    require(tool_raw==expected.encode())
    import psycopg
    check_loaded_origins(spec)
    graph.check()
    attempt=dict(operation='readonly-connect',connection=None);graph.operations.append(attempt)
    connection=psycopg.connect(host='127.0.0.1',port=51430,dbname=data['database'],user=data['user'],
        password=data['password'],connect_timeout=5,autocommit=True,
        options='-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000')
    attempt['connection']=connection;graph.connections.append(connection)
    graph.check()
    sql=dict(operation='readonly-system-query',cursor=None);graph.operations.append(sql)
    cursor=connection.execute('SELECT system_identifier::text,current_user,current_setting(\'server_version_num\') FROM pg_control_system()')
    sql['cursor']=cursor
    row=cursor.fetchone();require(type(row) in (tuple,list) and len(row)==3)
    system,admin,version=row
    require(type(system) is str and system.isdecimal() and admin==data['user'] and str(version).startswith('16'))
    graph.new('source-sql.private.json',canonical(list(row)))
    graph.operations.append(dict(operation='readonly-connection-close',connection=connection))
    connection.close()
    after=closed_json(graph.docker('inspect','--type','container',SOURCE_CID))
    require(after==before);graph.check()
    raw=canonical(before[0]);graph.new('source-inspect.private.json',raw)
    return dict(format='b3-independent-source-proof-v1',
        **{k:job[k] for k in ('operation_nonce','started','deadline')},observed_at=float(time.monotonic()),
        container_id=SOURCE_CID,image_id=PARSER_IMAGE,system_id_sha256=sha(system.encode()),
        volume_names=volumes,admin_name=admin,inspect_sha256=sha(raw),
        inspect_base64=base64.b64encode(raw).decode(),tools_sha256=sha(tool_raw))


def _read_manifest(spec,config,report):
    directory=Path(spec['directory'])
    raw,_=private_read(directory/'manifest.private.json')
    seal,_=private_read(directory/'seal.private.sha256')
    require(seal==sha(raw).encode())
    m=closed_json(raw)
    closed(report,'prepared manifest_sha256 original_deadline actual_executed')
    require(report==dict(prepared=True,manifest_sha256=sha(raw),original_deadline=config['deadline'],actual_executed=False))
    require(m['config_sha256']==sha(canonical(config)) and m['directory']==str(directory))
    require(all(m[k]==config[k] for k in ('started','deadline','operation_nonce','source')))
    require(m['code']==dependency_hashes(spec['runtime']))
    require(m['parser_plan']==parser_plan(m))
    # Re-render reviewed target plans; never trust exit0 or a sealed plan alone.
    from b3_target_once import target_specs
    sys.path.insert(0,str(SOURCE/'src'))
    from platform_service.media_backup_b3.target_contract import build_target_plan
    expected=[dict(volume=list(build_target_plan(v).volume_argv),container=list(build_target_plan(v).container_argv))
              for v in target_specs(m,('qualification_placeholder',))]
    require(m['target_plans']==expected)
    for target in m['targets'].values():
        raw_env,_=private_read(target['env_file_path'])
        require(sha(raw_env)==target['env_file_sha256'])
    return m,sha(raw)


def _fd(graph,path,original_fd=None):
    raw,custody=private_read(path)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK);graph.fds.append(fd)
    require(stamp(os.fstat(fd))==custody['file'])
    if original_fd is not None:require(stamp(os.fstat(fd))==stamp(os.fstat(original_fd)))
    require(fd_read(fd)==raw)
    return fd


def _runtime_launch(graph,spec,mode,fds):
    verify_inventory(spec)
    require(sha(Path(spec['interpreter']['path']).read_bytes())==spec['interpreter']['sha256'])
    argv=[spec['interpreter']['path'],'-B',str(ENTRY),mode]
    for name,fd in zip(('manifest','authority','report'),fds):argv+=['--'+name+'-fd',str(fd)]
    return graph._spawn(argv,fds)


def workflow(spec,job,session,graph):
    """Only concrete fixed production stages. Private tests patch native edges."""
    graph.check();verify_inventory(spec)
    mkdir_private(graph.directory/'docker-config')
    mkdir_private(graph.directory/'collector')
    spec_fd=graph.new('collector-spec.private.json',canonical(spec))
    job_fd=_fd(graph,graph.directory/'original_job.private.json')
    proof_fd=graph.new('collector-report.private.json')
    collector=graph._spawn([spec['interpreter']['path'],'-B',str(SELF),'--collect-source',
        '--spec-fd',str(spec_fd),'--job-fd',str(job_fd),'--report-fd',str(proof_fd)],(spec_fd,job_fd,proof_fd))
    graph.wait(collector)
    source=closed_json(fd_read(proof_fd));graph.check()
    source_pin=pinned_doc(graph,'source-proof.private.json',source)
    parent=Path(spec['directory']).parent
    device=os.stat(parent,follow_symlinks=False).st_dev
    measurement=capacity_measurement(job,device,float(time.monotonic()))
    free=os.statvfs(parent);require(free.f_bavail*free.f_frsize>=measurement['reserved_bytes'])
    mp=pinned_doc(graph,'measurement.private.json',measurement)
    capacity={k:measurement[k] for k in ('operation_nonce','started','deadline','measured_at','device','peak_bytes','reserved_bytes')}
    capacity.update(format='b3-independent-capacity-proof-v1',measurement_sha256=mp['sha256'],measurement_path=mp['path'])
    config=dict(format='b3-target-root-config-v1',directory=spec['directory'],
        **{k:job[k] for k in ('operation_nonce','started','deadline')},source=spec['source'],authority=spec['authority'],
        runtime=spec['runtime'],source_proof=source_pin,capacity_proof=pinned_doc(graph,'capacity-proof.private.json',capacity),
        original_job=dict(path=str(graph.directory/'original_job.private.json'),sha256=sha(canonical(job))))
    if spec.get('format')=='b3-external-supervisor-spec-attest-v1':
        config.update(format='b3-target-root-config-attest-v1',execution_policy=spec['execution_policy'])
    validate_config(config);proof_documents(config)
    mf=graph.new('prepare-request.private.json',canonical(dict(format='b3-target-prepare-request-v1',config_sha256=sha(canonical(config)))))
    af=graph.new('prepare-envelope.private.json',canonical(dict(config=config)))
    rf=graph.new('prepare-report.private.json')
    mf=_fd(graph,graph.directory/'prepare-request.private.json',mf)
    af=_fd(graph,graph.directory/'prepare-envelope.private.json',af)
    graph.wait(_runtime_launch(graph,spec,'--prepare',(mf,af,rf)))
    manifest,manifest_hash=_read_manifest(spec,config,closed_json(fd_read(rf)))
    check_parser_start_budget(manifest)
    # Everything following this durable record belongs to the ONE launch intent.
    launch=dict(format='b3-root-external-launch-intent-v1',manifest_sha256=manifest_hash,
        **{k:job[k] for k in ('operation_nonce','started','deadline','supervisor_pid','supervisor_nonce')},
        launched_at=float(time.monotonic()))
    job_graph=ProcessGraph(Path(spec['directory']),job);graph.operations.append(dict(operation='job-graph',graph=job_graph))
    try:
        launch_write=job_graph.new('launch.once',canonical(launch))
        launch_fd=_fd(job_graph,job_graph.directory/'launch.once',launch_write)
        # Exactly the prepared parser argv: no rediscovery, replacement or retry.
        cid=graph.wait(graph._spawn(manifest['parser_plan'],())).decode('ascii').strip()
        require(re.fullmatch('[a-f0-9]{64}',cid) and cid!=SOURCE_CID)
        graph.operations.append(dict(operation='parser-identity',cid=cid))
        started=graph.docker('start',cid);require(started.decode('ascii').strip()==cid)
        raw_tool=graph.docker('exec',cid,'/usr/bin/sha256sum','/usr/lib/postgresql/16/bin/pg_restore')
        require(raw_tool==(PARSER_TOOL_SHA+'  /usr/lib/postgresql/16/bin/pg_restore\n').encode())
        inspected=closed_json(graph.docker('inspect','--type','container',cid))
        require(type(inspected) is list and len(inspected)==1)
        raw=canonical(inspected[0]);graph.new('parser-inspect.private.json',raw)
        descriptor=dict(format='easel-b3-root-parser-descriptor-v1',manifest_sha256=manifest_hash,
            **{k:job[k] for k in ('operation_nonce','started','deadline')},restore_id='b3-'+job['operation_nonce'][:32],
            cid=cid,image_id=PARSER_IMAGE,tool_sha256=PARSER_TOOL_SHA,
            inspect_base64=base64.b64encode(raw).decode(),inspect_sha256=sha(raw))
        validate_descriptor(canonical(descriptor),manifest,manifest_hash)
        dp=pinned_doc(graph,'parser-descriptor.private.json',descriptor)
        check_parser_start_budget(manifest)
        mf=_fd(job_graph,Path(spec['directory'])/'manifest.private.json')
        af=job_graph.new('execute-envelope.private.json');rf=job_graph.new('execute-report.private.json')
        original_authority=stamp(os.fstat(af))[:6]
        verify_inventory(spec)
        child=job_graph._spawn([spec['interpreter']['path'],'-B',str(LAUNCHER),'--directory',spec['directory'],
            '--launch-fd',str(launch_fd),'--manifest-fd',str(mf),'--authority-fd',str(af),'--report-fd',str(rf)],
            (launch_fd,mf,af,rf))
        while True:
            require(job_graph.observe(child) is None)
            try:hint=closed_json(private_read(Path(spec['directory'])/'parked.private.json')[0]);break
            except FileNotFoundError:time.sleep(.02)
        closed(hint,'pid ppid executable launch_sha256 independent_observation')
        require(hint['pid']==child['pid'] and hint['ppid']==os.getpid()==job['supervisor_pid'])
        require(hint['executable']==spec['interpreter']['path'] and hint['launch_sha256']==sha(canonical(launch))
                and hint['independent_observation'] is False)
        require(child['process'].pid==child['pid'] and child['process'].poll() is None)
        require(sha(Path(hint['executable']).read_bytes())==spec['interpreter']['sha256'])
        binding={**launch,'format':'b3-root-external-launch-v1','caller_pid':child['pid'],
                 'caller_ppid':os.getpid(),'tool_session':session}
        caller=dict(launch_sha256=sha(canonical(launch)),binding=binding)
        caller_raw=canonical(caller)
        envelope=dict(config=config,parser_descriptor=dp,
            external_launch=dict(path=str(Path(spec['directory'])/'launch.once'),sha256=sha(canonical(launch))),
            external_caller=dict(path=str(Path(spec['directory'])/'caller.once'),sha256=sha(caller_raw)))
        require(stamp(os.fstat(af))[:6]==original_authority)
        report_write(af,envelope)
        require(stamp(os.fstat(af))[:6]==original_authority)
        check_parser_start_budget(manifest)
        job_graph.new('caller.once',caller_raw)  # LAST durable release of ORIGINAL inode.
        job_graph.wait(child)
        result=closed_json(fd_read(rf))
        expected_root_context=None
        if config['format']=='b3-target-root-config-attest-v1':
            import math
            original_directory=os.stat(spec['directory'],follow_symlinks=False)
            require([original_directory.st_dev,original_directory.st_ino]==manifest['directory_custody'][:2])
            expected_root_context=dict(restore_id='b3-'+job['operation_nonce'][:32],source_container_id=spec['source']['container_id'],
                operation_ids={kind:manifest['targets'][kind]['operation_nonce'] for kind in ('central','identity')},
                job_device=original_directory.st_dev,job_inode=original_directory.st_ino,
                started_at_ns=math.ceil(job['started']*10**9),deadline_ns=math.floor(job['deadline']*10**9))
        require(result.get('status')==('target_worker_attestation_verified' if expected_root_context is not None else 'target_lifecycle_verified'))
        validate_terminal(result,manifest_hash,manifest['deadline'],expected_root_context=expected_root_context)
        qualification=dict(target_lifecycle_verified=True,
            target_worker_attestation_verified=result['status']=='target_worker_attestation_verified')
        graph.new('qualification.safe.json',canonical(dict(**qualification,
            manifest_sha256=manifest_hash,result_sha256=sha(canonical(result)),SQL_restore=False,
            isolated_restore_verified=False,production=False,provider=False,timer=False)))
        return result
    except BaseException:
        job_graph.unknown=True;job_graph.error=sys.exc_info();raise


def collect_entry(spec_fd,job_fd,report_fd):
    spec=validate_spec(closed_json(fd_read(spec_fd)));job=closed_json(fd_read(job_fd))
    closed(job,'format operation_nonce started deadline supervisor_pid supervisor_nonce')
    require(job['format']=='b3-root-original-job-v1' and job['supervisor_pid']==os.getppid()
            and job['supervisor_nonce']==spec['supervisor_nonce'] and job['operation_nonce']==spec['operation_nonce'])
    require(type(job['started']) is float and job['deadline']==job['started']+900)
    require(Path(sys.prefix)==Path(spec['runtime']['prefix']))
    original,_=private_read(Path(spec['bootstrap'])/'original_job.private.json')
    require(original==canonical(job));fd_read(report_fd,empty=True)
    graph=ProcessGraph(Path(spec['bootstrap'])/'collector',job)
    def interrupted(number,frame):
        graph.unknown=True
        raise InterruptedError('collector interrupted')
    for number in (signal.SIGTERM,signal.SIGINT):signal.signal(number,interrupted)
    try:
        mkdir_private(graph.directory/'docker-config')
        proof=source_collect(spec,job,graph)
        report_write(report_fd,proof)
    except BaseException:
        graph.error=sys.exc_info();graph.hold()


def run(spec_path):
    raw,_=private_read(spec_path);spec=validate_spec(closed_json(raw))
    validate_bootstrap_interpreter(spec)
    directory=Path(spec['bootstrap']);mkdir_private(directory)
    # This hash is the actual executing interpreter, even with Python3.12 -S
    # reporting base_prefix instead of the approved virtualenv prefix.
    bootstrap=dict(format='b3-supervisor-bootstrap-v1',supervisor_pid=os.getpid(),
        supervisor_nonce=spec['supervisor_nonce'],code_sha256=sha(SELF.read_bytes()),
        interpreter_sha256=sha(Path(sys.executable).read_bytes()))
    write_new(directory/'bootstrap.private.json',canonical(bootstrap))
    write_new(directory/'spec.private.json',raw)
    print(canonical(bootstrap).decode(),flush=True)
    startup=time.monotonic()
    while True:
        require(time.monotonic()-startup<120)
        try:binding=closed_json(private_read(directory/'session-binding.private.json',16384)[0]);break
        except FileNotFoundError:time.sleep(.1)
    session=validate_session_binding(binding,bootstrap)
    started=float(time.monotonic())
    job=dict(format='b3-root-original-job-v1',operation_nonce=spec['operation_nonce'],
        started=started,deadline=started+900,supervisor_pid=os.getpid(),supervisor_nonce=spec['supervisor_nonce'])
    graph=ProcessGraph(directory,job);RETAINED.append(graph)
    # TERM/INT never abandon a live graph. No signal is forwarded automatically.
    def interrupted(number,frame):
        graph.unknown=True
        raise InterruptedError('supervisor interrupted')
    for number in (signal.SIGTERM,signal.SIGINT):signal.signal(number,interrupted)
    try:
        graph.new('original_job.private.json',canonical(job))
        return workflow(spec,job,session,graph)
    except BaseException:
        graph.error=sys.exc_info();graph.hold()


def main():
    parser=argparse.ArgumentParser(description='Fixed B3 external Root supervisor')
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--run',action='store_true');mode.add_argument('--collect-source',action='store_true')
    parser.add_argument('--spec')
    for name in ('spec','job','report'):parser.add_argument('--'+name+'-fd',type=int)
    args=parser.parse_args()
    if args.run:
        require(args.spec is not None and all(getattr(args,n+'_fd') is None for n in ('spec','job','report')))
        run(args.spec)
    else:
        require(args.spec is None and all(type(getattr(args,n+'_fd')) is int for n in ('spec','job','report')))
        require(len({args.spec_fd,args.job_fd,args.report_fd})==3)
        collect_entry(args.spec_fd,args.job_fd,args.report_fd)


if __name__=='__main__':
    try:main()
    except BaseException:
        print('supervisor_bootstrap_rejected',file=sys.stderr)
        raise SystemExit(1)
