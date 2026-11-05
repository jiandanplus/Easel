"""Systemd rendering and smoke summaries never load host credentials."""
import importlib.util
from pathlib import Path
import sys
import pytest


def module():
    source=Path(__file__).parents[1]/'scripts/prepare_m1_image_cutover.py'
    assert source.is_file(), 'bounded candidate/cutover helper required'
    sys.path.insert(0,str(source.parent))
    spec=importlib.util.spec_from_file_location('m1_image_cutover',source)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def unit(kind='api'):
    console=kind=='console';user='easel-console' if console else 'easel-central';port=19875 if console else 19874
    return ('[Unit]\nDescription=Original\n[Service]\nType=simple\nUser='+user+'\nGroup='+user+'\n'
        'WorkingDirectory=/srv/easel/releases/20261026-reconciliation-r2-v1\n'
        'EnvironmentFile=/etc/easel-central/'+('console' if console else 'central')+'.env\n'
        'ExecStart=/srv/easel/releases/20261026-reconciliation-r2-v1/.venv/bin/python -m uvicorn '
        'platform_service.'+('console_app' if console else 'app')+':create_app --factory --host 127.0.0.1 --port '+str(port)+'\n'
        'NoNewPrivileges=true\nProtectSystem=strict\n[Install]\nWantedBy=multi-user.target\n')


def test_candidate_keeps_original_arguments_security_and_private_environment_with_only_loopback_new_port():
    value=module();original=unit();candidate=value.render_candidate(original)
    assert original==unit()
    assert 'NoNewPrivileges=true' in candidate and 'ProtectSystem=strict' in candidate
    assert 'EnvironmentFile=/etc/easel-central/central.env' in candidate
    assert '--host 127.0.0.1 --port 19876' in candidate and '--factory' in candidate
    assert str(value.release.TARGET)+'/.venv/bin/python' in candidate
    assert 'Environment=CENTRAL_MEDIA_CONFIG_FILE=/etc/easel-central/media.json' in candidate


@pytest.mark.parametrize('kind',['api','console'])
def test_cutover_dropin_resets_execstart_and_preserves_each_original_port(kind):
    value=module();dropin=value.render_dropin(unit(kind),kind)
    assert '\nExecStart=\nExecStart=' in dropin
    assert '--port '+('19874' if kind=='api' else '19875') in dropin
    assert 'WorkingDirectory='+str(value.release.TARGET) in dropin
    assert ('CENTRAL_MEDIA_CONFIG_FILE' in dropin)==(kind=='api')


@pytest.mark.parametrize('old,new',[
    ('--host 127.0.0.1','--host 0.0.0.0'),('User=easel-central','User=root'),
    ('WorkingDirectory=/srv/easel/releases/20261026-reconciliation-r2-v1','WorkingDirectory=/unexpected'),
    ('--port 19874','--port 18874'),('EnvironmentFile=/etc/easel-central/central.env','EnvironmentFile=/other/private.env'),
    ('--factory','--factory $UNREVIEWED'),
])
def test_wrong_host_user_release_port_env_or_dynamic_arguments_close_before_unit_write(old,new):
    with pytest.raises(ValueError):module().render_candidate(unit().replace(old,new))


def test_duplicate_or_continued_execstart_requires_explicit_review():
    value=module()
    for extra in ['ExecStart=/another/command\n','ExecStart=/another/command \\\n more\n']:
        with pytest.raises(ValueError):value.render_candidate(unit().replace('[Install]',extra+'[Install]'))


def test_only_expected_smoke_booleans_survive_receipt_projection():
    result=module().smoke_summary({'health':True,'runtime_role':True,'configuration':True,'text_models_unchanged':True,
        'image_models_disabled':True,'financial_snapshot_unchanged':True,'catalog_auth_required':True,
        'private_token':'synthetic-secret','raw_unit':'private-original','raw_environment':'private-env'})
    assert all(result.values()) and len(result)==7
    assert 'synthetic-secret' not in str(result) and 'private-original' not in str(result)


def test_process_identity_checks_actual_cwd_executable_and_command_not_unit_properties_only(tmp_path):
    value=module();target=tmp_path/'new';target.mkdir();binary=target/'python';binary.write_text('synthetic')
    argv=[str(binary),'-m','uvicorn','app:create_app','--port','19876']
    assert value.process_identity_matches(str(target),str(binary),argv,target,argv) is True
    assert value.process_identity_matches(str(tmp_path),str(binary),argv,target,argv) is False
    assert value.process_identity_matches(str(target),str(binary),argv[:-1]+['19874'],target,argv) is False
