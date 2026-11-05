"""Public synthetic installed origin schema: no process or network IO."""
import importlib.util
import copy
import pytest
from platform_service.media_backup_b3 import roles

def api():
    name='platform_service.media_backup_b3.reconstruction_origin'
    assert importlib.util.find_spec(name) is not None, 'missing fixed installed origin verifier'
    return __import__(name,fromlist=['*'])

def manifest():
    o=api()
    files={p:dict(sha256='a'*64,size=1,mode=0o444) for p in set(o._PROGRAM+o._CLIENT+(o._ARTIFACT,o._EXE,o._LEGACY))}
    return o._manifest(files)

def test_fixed_complete_manifest():
    o=api(); d=manifest()
    assert o._validate(d)==d
    assert d['entrypoint_path'].endswith('/reconstruction_worker.py')
    assert d['program_sha256']==roles._hash(d['program'])
    assert d['client_sha256']==roles._hash(d['client'])
    assert o._LEGACY in d['files'] and o._BUILD not in d['files']

@pytest.mark.parametrize('fault',['extra','bool','mode','missing','aggregate','self','architecture','fileless'])
def test_manifest_refuses_unqualified_data(fault):
    o=api();d=manifest()
    if fault=='extra':d['extra']=0
    elif fault=='bool':d['files'][o._EXE]['size']=True
    elif fault=='mode':d['files'][o._EXE]['mode']=0o666
    elif fault=='missing':del d['files'][o._CLIENT[-1]]
    elif fault=='aggregate':d['program_sha256']='f'*64
    elif fault=='self':d['files'][o._BUILD]=dict(sha256='a'*64,size=1,mode=0o444)
    elif fault=='architecture':d['architecture']='amd64'
    else:d['allowed_fileless_modules']=['arbitrary']
    with pytest.raises(Exception):o._validate(d)


def test_same_filename_replacement_callable_refused(tmp_path):
    from reconstruction_installed_fixture import Installed
    f=Installed(tmp_path/'replaced',lambda **kw:None)
    exec(compile('def _roles_apply(request,budget):return None',api()._ENTRY,'exec'),f.worker.__dict__)
    with pytest.raises(Exception):f.origin._callables()

@pytest.mark.parametrize('raw',[b'{"format":1,"format":2}',b'{"size":1.0}',b'{"size":NaN}',b'['*30+b'0'+b']'*30])
def test_manifest_strict_duplicate_number_and_depth_policy(raw):
    with pytest.raises(Exception):api()._parse(raw)
