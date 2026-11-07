"""Fixed actual mutation/physical/controller coverage consumed before DRAINING.

This verifies loaded code bindings and packaged SQL bytes. Runtime SQL privileges,
installed trigger inventories, actual OS identities and the behavioral test gate
remain independent checks; no caller-supplied callback/boolean is accepted here.
"""
from dataclasses import dataclass
import hashlib
import importlib
import inspect
import json
from pathlib import Path
from .contracts import MaintenanceError,require
from .schema import profile_tables

PUBLIC='public_001_016'
LOCAL='local_001_020'
PROFILES=frozenset({PUBLIC,LOCAL})
BOTH=(PUBLIC,LOCAL)


@dataclass(frozen=True)
class Binding:
    module:str
    qualname:str
    guard:str
    kinds:tuple=()
    profiles:tuple=BOTH
    origin:tuple|None=None
    names:tuple=()


def _bind(module,owner,names,guard,kinds=(),*,profiles=BOTH,origin=None):
    return tuple(Binding('platform_service.'+module,(owner+'.'if owner else '')+name,guard,tuple(sorted(kinds)),profiles,('platform_service.'+origin[0],origin[1]+'.'+name)if origin else None)for name in names)


COVERAGE=(
 *_bind('media_runtime','MediaRuntime',('mint','upload_asset','submit','cancel','run_once','run_forever'),'runtime'),
 *_bind('media_runtime','MediaRuntime',('start',),'start'),
 *_bind('media_runtime','MediaRuntime',('close',),'close'),
 *_bind('media_runtime','MediaRuntime',('_execute','_heartbeat'),'mutation',('job',)),
 *_bind('media.core','MediaService',('publish','set_enabled'),'mutation',('catalog',)),
 *_bind('media.core','MediaService',('mint',),'mutation',('mint',)),
 *_bind('media.core','MediaService',('admit','dispatch','heartbeat','record_provider_attempt','record_provider_association','record_http_response_reference','record_terminal_evidence','pending','finish','finish_from_evidence','cancel','register_artifact','register_voice','_pending','_release'),'mutation',('job',)),
 *_bind('media.core','MediaService',('record_protocol_facts','_record_protocol_facts'),'mutation',('job',),profiles=(LOCAL,)),
 *_bind('media.core','MediaService',('register_asset',),'mutation',('upload',)),
 *_bind('media.core','MediaService',('recover_financial',),'mutation',('job','maintenance')),
 *_bind('media.core','MediaService',('_reserve_storage_budget','_release_storage_budget'),'mutation',('job',),origin=('media.storage','StorageLifecycle')),
 *_bind('media.core','MediaService',('_stage_storage','stage_blob','_attach_storage'),'mutation',('job','upload'),origin=('media.storage','StorageLifecycle')),
 *_bind('media.core','MediaService',('prepare_gc','claim_gc'),'mutation',('gc','maintenance'),origin=('media.storage','StorageLifecycle')),
 *_bind('media.core','MediaService',('finish_gc',),'mutation',('gc',),origin=('media.storage','StorageLifecycle')),
 *_bind('media.storage','StorageLifecycle',('gc_delete_owned',),'mutation',('gc',)),
 *_bind('media.artifacts','ArtifactStore',('check_capacity','put_image','put_image_async','_put_verified','discard','delete_exact'),'physical'),
 *_bind('media.artifacts','ArtifactStore',('_directory',),'directory'),
 *_bind('media.async_store','MediaAsyncStore',('enqueue','seed','heartbeat','defer','observe','stage_source','attach_source','prepare_settlement','done'),'mutation',('job',),profiles=(LOCAL,)),
 *_bind('media.async_store','MediaAsyncStore',('recover_associations','claim','expire'),'mutation',('job','maintenance'),profiles=(LOCAL,)),
 *_bind('media.async_store','MediaAsyncStore',('owned',),'context',('job',),profiles=(LOCAL,)),
 *_bind('media.worker','MediaWorker',('_write_source','_promote_partial'),'physical',profiles=(LOCAL,)),
 *_bind('media.worker','MediaWorker',('execute','_execute_claim','_heartbeat'),'mutation',('job',),profiles=(LOCAL,)),
 *_bind('media.worker','MediaWorker',('run_once',),'mutation',('job','maintenance'),profiles=(LOCAL,)),
 *_bind('media.audio_execution','',('store_input',),'mutation',('upload',),profiles=(LOCAL,)),
 *_bind('media.audio_execution','',('finish_audio',),'mutation',('job',),profiles=(LOCAL,)),
 *_bind('media.av_artifacts','',('inspect_av',),'physical',profiles=(LOCAL,)),
 *_bind('media.voice_execution','VoiceExecution',('stage','enroll','verify','_bounded_verify','finish','_bounded_finish','_recover_job','_bounded__recover_job'),'mutation',('job',),profiles=(LOCAL,)),
 *_bind('media.voice_execution','VoiceExecution',('recover',),'mutation',('job','maintenance'),profiles=(LOCAL,)),
 *_bind('media.voices','VoiceStages',('begin','observe','record_headers'),'mutation',('job',),profiles=(LOCAL,)),
 *_bind('media.voices','OwnedVoices',('register_ready',),'mutation',('job',),profiles=(LOCAL,)),
 Binding('platform_service.media_maintenance.control','DatabaseControl.verify_window','code',names=('_window','_rpc','phase_deadline_ns')),
 Binding('platform_service.media_maintenance.control','DatabaseControl.phase_deadline_ns','code',names=('_window','verify','min')),
 Binding('platform_service.media_maintenance.control','DatabaseControl.drain_deadline_ns','code',names=('_window','verify','min')),
 Binding('platform_service.media_maintenance.runtime','RuntimeGeneration.start','code',names=('register_worker','attest_worker','verify')),
 Binding('platform_service.media_maintenance.runtime','RuntimeGeneration.acquire','code',names=('_check','admit_operation','validate')),
 Binding('platform_service.media_maintenance.runtime','GuardedDatabase.connection','code',names=('_verify_physical_domain','assert_transaction_permit')),
 Binding('platform_service.media_maintenance.identity','worker_identity','code',names=('WorkerIdentity','host_identity','uuid4')),
)


def _symbol(binding):
    value=importlib.import_module(binding.module)
    for part in binding.qualname.split('.'):value=getattr(value,part)
    require(callable(value))
    origin=binding.origin or (binding.module,binding.qualname)
    require((value.__module__,value.__qualname__)==origin)
    if binding.guard in {'mutation','context'}:
        marker=getattr(value,'__media_guard__',None)
        require(type(marker)is tuple and len(marker)==3 and marker[0]==binding.kinds)
        if binding.guard=='context':require(getattr(value,'__media_context_guard__',None)is True)
    elif binding.guard=='physical':require(getattr(value,'__media_physical_guard__',None)is True)
    elif binding.guard=='directory':require(getattr(value,'__media_directory_guard__',None)is True)
    elif binding.guard=='runtime':require(getattr(value,'__media_runtime_boundary__',None)is True)
    elif binding.guard in {'start','close'}:require(getattr(value,'__media_lifecycle_guard__',None)==binding.guard)
    else:require(binding.guard=='code')
    actual=inspect.unwrap(value)
    if binding.names:require(set(binding.names)<=set(actual.__code__.co_names))
    return actual


def verify_coverage(profile_name=LOCAL):
    require(type(profile_name)is str and profile_name in PROFILES)
    try:
        inventory=[]
        for binding in COVERAGE:
            if profile_name not in binding.profiles:continue
            function=_symbol(binding)
            inventory.append({'symbol':binding.module+':'+binding.qualname,'guard':binding.guard,'kinds':binding.kinds,'source_sha256':hashlib.sha256(inspect.getsource(function).encode()).hexdigest()})
        require(len(inventory)=={PUBLIC:53,LOCAL:90}[profile_name])
        require(len({row['symbol']for row in inventory})==len(inventory))
        root=Path(__file__).parents[1];sql=root/'media_maintenance/sql/001_media_backup_control.sql';raw=sql.read_bytes();text=raw.decode('utf-8')
        for name in ('attest_worker','blob_writer_owned','gc_writer_idle','job_can_block_binding','verify_window','guard_media_mutation','guard_media_lot_commit'):
            require('CREATE FUNCTION easel_backup_control.'+name+'(' in text)
        require('audit_blob_stage_owner' in text and "VALUES('blob_staged'" in text)
        count=16 if profile_name==PUBLIC else 20;migrations=[]
        for index in range(1,count+1):
            matches=list((root/'migrations').glob(str(index).zfill(3)+'_*.sql'));require(len(matches)==1)
            migrations.append((matches[0].name,hashlib.sha256(matches[0].read_bytes()).hexdigest()))
        helpers={name:hashlib.sha256((root/'media_maintenance'/name).read_bytes()).hexdigest()for name in ('contracts.py','control.py','coverage.py','filesystem.py','identity.py','permits.py','runtime.py','schema.py')}
        payload={'format':'easel-media-coverage-v1','profile':profile_name,'methods':inventory,'sql_sha256':hashlib.sha256(raw).hexdigest(),'public_migrations':migrations,'media_tables':sorted(profile_tables(profile_name)),'helpers':helpers}
        digest=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()
        return {'format':payload['format'],'sha256':digest,'method_count':len(inventory)}
    except (OSError,ValueError,TypeError,AttributeError,ImportError,KeyError):raise MaintenanceError()from None


def require_capture_coverage(profile_name):
    return verify_coverage(profile_name)
