"""No SSH, credentials or production calls; plan output is allowlisted."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import pytest

SCRIPT=Path(__file__).parents[1]/'scripts/capture_media_restore_plan.py'

@pytest.fixture
def lib():
    spec=importlib.util.spec_from_file_location('capture_media_plan',SCRIPT);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value

def plan(lib):
    hidden='SYNTHETIC_PRIVATE_SENTINEL'
    return {'format':'easel-m1-media-restore-plan-v1','nonce':'a'*32,'target':'ser362540173134',
        'script_sha256':lib.DR_SHA,'core_sha256':'b'*64,'candidate_sha256':'c'*64,'migration_016_sha256':'d'*64,
        'work_dir':'/root/easel-media-restore-drill-20261027-'+'a'*32,'dest':'/var/backups/easel-encrypted-m1-drill',
        'release':'/srv/easel/releases/20261027-images-m1-1-size-v2','image':'postgres@sha256:'+'e'*64,
        'sources':[{'kind':'central','id':'f'*64,'image_id':'0'*64,'database':hidden,'container':hidden,'role':hidden,'owner':hidden}],
        'targets':[{'kind':'central','name':'owned-container','volume':'owned-data','user':hidden,'env':hidden}],
        'resource_limits':{'simultaneous_restore_containers':1,'memory_bytes':536870912,'nano_cpus':750000000,'pids':128,'network':'none','published_ports':0,'dsn':hidden},
        'estimated_disk_upper_bytes':1073741824,'inventory_version':3,'central_schema_sha256':'1'*64,'central_config_sha256':'2'*64,
        'config_hashes':{hidden:'3'*64},'config_metadata_sha256':'4'*64,'expected_migrations':[{'version':hidden,'checksum':'5'*64}],
        'media_uid':997,'media_gid':997,'media_root_inventory_sha256':'6'*64,'media_window_sha256':'7'*64,
        'active_release_processes':{'api':{'pid':123,'command_sha256':'8'*64,'cwd_match':True,'executable_match':True,'media_environment_match':True,'argv':hidden,'environ':hidden}},
        'initial_media_registry_empty':True,'production_writes':False,'stops_production':False,
        'unexpected_private_fields':{'password':hidden},'raw_config':hidden,'database_metadata':hidden,'roles':hidden}

def test_safe_allowlist_never_exports_private_values_even_nested(lib):
    value=plan(lib)
    value['active_release_processes']['SYNTHETIC_PRIVATE_SENTINEL']=value['active_release_processes']['api']
    safe=lib.safe_plan(value)
    raw=json.dumps(safe)
    assert 'SYNTHETIC_PRIVATE_SENTINEL' not in raw
    assert safe['sources']==[{'kind':'central','id':'f'*64,'image_id':'0'*64}]
    assert safe['targets']==[{'kind':'central','name':'owned-container','volume':'owned-data'}]
    assert safe['configuration_count']==1 and safe['migration_count']==1
    assert safe['configuration_hashes_sha256']==lib.digest(lib.canonical(value['config_hashes']))
    assert 'dsn' not in safe['resource_limits'] and 'environ' not in safe['active_release_processes']['api']

def test_full_plan_is_private_exclusive_durable_and_hash_bound(lib,tmp_path):
    value=plan(lib);body={'plan':value,'plan_sha256':lib.digest(lib.canonical(value))};calls=[]
    def run(argv,**kwargs):
        calls.append((argv,kwargs));return SimpleNamespace(returncode=0,stdout=lib.canonical(body),stderr=b'SYNTHETIC_PRIVATE_SENTINEL')
    result=lib.capture_plan(tmp_path,'a'*32,run)
    target=Path(result['plan_path'])
    assert target.read_bytes()==lib.canonical(value)
    assert hashlib.sha256(target.read_bytes()).hexdigest()==result['plan_sha256']
    assert (target.stat().st_uid,stat.S_IMODE(target.stat().st_mode))==(os.geteuid(),0o600)
    assert 'SYNTHETIC_PRIVATE_SENTINEL' not in json.dumps(result)
    assert calls[0][0]==['/usr/bin/python3','-B',str(lib.DR),'--nonce','a'*32]
    assert calls[0][1]['capture_output'] is True and calls[0][1]['timeout']==90
    with pytest.raises(FileExistsError):lib.capture_plan(tmp_path,'a'*32,run)
    assert len(calls)==1

@pytest.mark.parametrize('failure',['returncode','hash','nonce','duplicate','oversized','script'])
def test_failed_capture_never_writes_plan_or_leaks_diagnostics(lib,tmp_path,failure):
    value=plan(lib);body={'plan':value,'plan_sha256':lib.digest(lib.canonical(value))}
    code=0;raw=lib.canonical(body)
    if failure=='returncode':code=1
    elif failure=='hash':body['plan_sha256']='0'*64;raw=lib.canonical(body)
    elif failure=='nonce':body['plan']['nonce']='b'*32;body['plan_sha256']=lib.digest(lib.canonical(body['plan']));raw=lib.canonical(body)
    elif failure=='duplicate':raw=b'{"plan":{},"plan":{}}'
    elif failure=='oversized':raw=b'x'*65537
    elif failure=='script':body['plan']['script_sha256']='0'*64;body['plan_sha256']=lib.digest(lib.canonical(body['plan']));raw=lib.canonical(body)
    with pytest.raises(lib.Rejected) as error:lib.capture_plan(tmp_path,'a'*32,lambda *args,**kwargs:SimpleNamespace(returncode=code,stdout=raw,stderr=b'SYNTHETIC_PRIVATE_SENTINEL'))
    assert str(error.value)=='plan_capture_rejected'
    assert not list(tmp_path.iterdir())

@pytest.mark.parametrize('nonce',['../bad','A'*32,'a'*31,''])
def test_bad_nonce_does_not_launch_child(lib,tmp_path,nonce):
    with pytest.raises(lib.Rejected):lib.capture_plan(tmp_path,nonce,lambda *args,**kwargs:pytest.fail('child must not start'))
