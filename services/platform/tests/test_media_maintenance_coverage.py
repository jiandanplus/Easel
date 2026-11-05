"""Fixed loaded-symbol coverage, not a caller assertion or bypass callback."""
import importlib
import re
import pytest


def api():
    try:return importlib.import_module('platform_service.media_maintenance.coverage')
    except ModuleNotFoundError:pytest.fail('actual fixed media coverage registry is missing')


@pytest.mark.parametrize('profile',['public_001_016','local_001_020'])
def test_capture_coverage_resolves_actual_guarded_slots_and_sql_sources(profile):
    lib=api();proof=lib.require_capture_coverage(profile)
    assert set(proof)=={'format','sha256','method_count'} and proof['format']=='easel-media-coverage-v1'
    assert re.fullmatch('[0-9a-f]{64}',proof['sha256']) and type(proof['method_count'])is int and proof['method_count']>=45
    assert proof==lib.verify_coverage(profile)


@pytest.mark.parametrize('profile',[None,True,'public-001-016','local_001_019','identity'])
def test_coverage_never_infers_unknown_profiles(profile):
    from platform_service.media_maintenance.contracts import MaintenanceError
    with pytest.raises(MaintenanceError):api().require_capture_coverage(profile)


def test_registry_covers_independent_t4_t5_inventory_and_directory_creation():
    from test_media_maintenance_entrypoints import REQUIRED_SEAMS
    lib=api();listed={entry.module+':'+entry.qualname for entry in lib.COVERAGE}
    for owner,names in REQUIRED_SEAMS.items():
        module,class_name=owner.split(':')
        for name in names:assert module+':'+(class_name+'.'if class_name else '')+name in listed
    assert 'platform_service.media.artifacts:ArtifactStore._directory' in listed
    assert 'platform_service.media.storage:StorageLifecycle.gc_delete_owned' in listed
    assert 'platform_service.media_runtime:MediaRuntime.close' in listed


@pytest.mark.parametrize('module,owner,name',[('platform_service.media.core','MediaService','publish'),('platform_service.media.artifacts','ArtifactStore','_put_verified'),('platform_service.media_runtime','MediaRuntime','run_once')])
def test_missing_actual_guard_fails_before_capture(module,owner,name,monkeypatch):
    from platform_service.media_maintenance.contracts import MaintenanceError
    lib=api();target=getattr(importlib.import_module(module),owner)
    monkeypatch.setattr(target,name,lambda *args,**kwargs:None)
    with pytest.raises(MaintenanceError):lib.require_capture_coverage('local_001_020')


def test_substituting_another_real_guarded_method_is_not_the_expected_binding(monkeypatch):
    from platform_service.media.core import MediaService
    from platform_service.media_maintenance.contracts import MaintenanceError
    lib=api();monkeypatch.setattr(MediaService,'publish',MediaService.set_enabled)
    with pytest.raises(MaintenanceError):lib.require_capture_coverage('public_001_016')
