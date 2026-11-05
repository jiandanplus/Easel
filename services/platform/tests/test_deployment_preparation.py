"""Offline preparation security contracts; synthetic public inputs only."""
import importlib
import json
import os
from pathlib import Path
import stat
import sys
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
prep = importlib.import_module('prepare_deployment')


def config(**changes):
    return {"version": 1, "public_url": "https://api.example.invalid",
        "console_public_url": "https://console.example.invalid",
        "oidc_issuer": "https://login.example.invalid/oidc",
        "source_oidc_issuer": "http://127.0.0.1:18763/oidc",
        "api_port": 19074, "console_port": 19075,
        "release_dir": "/opt/easel-central/releases/20261023",
        "config_dir": "/etc/easel-central-production",
        "certificate_dir": "/etc/ssl/easel-central-production", **changes}


@pytest.fixture
def public_tree(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    static = tmp_path / 'dist'
    contents = {'__init__.py': b'__version__ = "fixture"\n',
        'app.py': b'PUBLIC_FIXTURE = True\n',
        'migrations/001_fixture.sql': b'CREATE TABLE fixture(id text);\n',
        'migrations/009_fixture.sql': b'-- SYNTHETIC PUBLIC AUDIT\n'}
    assets = {'index.html': b'<script src="/assets/index-fixture.js"></script>',
        'assets/index-fixture.js': b'console.log("SYNTHETIC_PUBLIC");',
        'console-assets.json': b'{"schema_version":1,"credentials_bundled":false}'}
    for root, files in ((source, contents), (static, assets)):
        for relative, value in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
    monkeypatch.setattr(prep, 'SOURCE_ROOT', source)
    monkeypatch.setattr(prep, 'STATIC_ROOT', static)
    return {**{'platform_service/' + k: v for k,v in contents.items()},
            **{'platform_service/console_static/' + k: v for k,v in assets.items()}}


def wheel(tmp_path, files, *, changes=None, missing=(), duplicate=None, symlink=None, metadata=None, dist_name='easel_central_platform-0.1.0.dist-info'):
    path = tmp_path / 'fixture.whl'
    values = {**files, **(changes or {})}
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in values.items():
            if name not in missing:
                archive.writestr(name, value)
        for name in ('METADATA','WHEEL','RECORD'):
            value = (metadata if metadata is not None else b'Metadata-Version: 2.4\nName: easel-central-platform\nVersion: 0.1.0\n') if name == 'METADATA' else b'SYNTHETIC_PUBLIC'
            archive.writestr(dist_name + '/' + name, value)
        if duplicate:
            with pytest.warns(UserWarning, match='Duplicate name'):
                archive.writestr(duplicate, b'DUPLICATE')
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, b'/synthetic/outside')
    return path


def prepare(tmp_path, public_tree, value=None):
    path = tmp_path / 'public.json'
    path.write_text(json.dumps(config() if value is None else value))
    return prep.prepare(path, wheel(tmp_path, public_tree), tmp_path / 'new-output')


def test_bundle_is_offline_blocked_and_templates_preserve_boundaries(tmp_path, public_tree):
    report = prepare(tmp_path, public_tree)
    output = tmp_path / 'new-output'
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert set(p.name for p in output.iterdir()) == {'deployment-report.json','nginx-central.conf',
        'easel-central-production.service','easel-console-production.service',
        'central.env.template','console.env.template'}
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in output.iterdir())
    assert report['version'] == 1 and report['verification'] == 'offline_only'
    assert report['production_ready'] is False and report['identity_migration_required'] is True
    assert report['reserved_domain_placeholders'] is True
    assert 'reserved_domain_placeholders_must_be_replaced' in report['outstanding_checks']
    assert report['source_files'] and len(report['wheel_sha256']) == 64
    nginx = (output / 'nginx-central.conf').read_text()
    for text in ('server_name api.example.invalid;', 'server_name console.example.invalid;',
        'proxy_set_header Host api.example.invalid;', 'proxy_set_header Host console.example.invalid;',
        'proxy_set_header Origin $http_origin;', 'proxy_buffering off;', 'client_max_body_size 1m;',
        'client_max_body_size 64k;', 'proxy_read_timeout 180s;', '127.0.0.1:19074', '127.0.0.1:19075'):
        assert text in nginx
    assert 'default_server' not in nginx and 'Access-Control-Allow-Origin' not in nginx
    assert 'proxy_set_header Origin ""' not in nginx and '$request' not in nginx
    for name, user, memory in [('easel-central-production.service','easel-central','512M'),
        ('easel-console-production.service','easel-console','256M')]:
        unit = (output / name).read_text()
        assert f'User={user}' in unit and f'MemoryMax={memory}' in unit
        assert 'ProtectSystem=strict' in unit and 'ProtectHome=true' in unit
        assert '--host 127.0.0.1' in unit and ' -I -B -m platform_service ' in unit
        assert 'UnsetEnvironment=' in unit and 'WEB_CONCURRENCY' in unit
        assert 'CENTRAL_MIGRATION_DATABASE_URL' in unit and 'CENTRAL_CONSOLE_MIGRATION_DATABASE_URL' in unit
        assert 'EnvironmentFile=/etc/easel-central-production/' in unit
        assert 'operator.env' not in unit
    api = (output / 'central.env.template').read_text()
    console = (output / 'console.env.template').read_text()
    assert 'REPLACE' in api and 'REPLACE' in console and 'DO NOT START' in api and 'DO NOT START' in console
    assert 'CENTRAL_PAYMENTS_MODE=disabled' in api
    assert 'CENTRAL_ALLOW_LOOPBACK_HTTP=0' in api and 'CENTRAL_CONSOLE_ALLOW_LOOPBACK_HTTP=0' in console
    assert 'CENTRAL_EXECUTION_CONFIG_FILE' not in console and 'CENTRAL_MIGRATION_DATABASE_URL=' not in api
    assert 'CENTRAL_CONSOLE_MIGRATION_DATABASE_URL=' not in console


@pytest.mark.parametrize('field,value',[
    ('public_url','http://api.example.invalid'), ('public_url','https://127.0.0.1'),
    ('public_url','https://api.example.invalid:444'), ('public_url','https://user:CANARY@api.example.invalid'),
    ('public_url','https://api.example.invalid/path'), ('public_url','https://api.example.invalid?CANARY=1'),
    ('public_url','https://api.example.invalid#CANARY'), ('public_url','https://api.example.invalid\\evil'),
    ('public_url','https://api.example.invalid\nCANARY'), ('public_url','https://*.example.invalid'),
    ('public_url','https://例子.invalid'), ('public_url','https://127.0.0.01'),
    ('public_url','https://api.example.123'), ('console_public_url','https://api.example.invalid:443'),
    ('oidc_issuer','https://api.example.invalid/oidc'), ('oidc_issuer','https://login.example.invalid/not-oidc'),
    ('source_oidc_issuer','http://localhost:18763/oidc'), ('source_oidc_issuer','http://10.0.0.1/oidc'),
    ('source_oidc_issuer','https://login.example.invalid/oidc?CANARY=1'),
    ('release_dir','/opt/easel/../CANARY'), ('release_dir','/opt/easel//release'),
    ('release_dir','/opt/easel/$CANARY'), ('release_dir','/opt/easel/%n'),
    ('release_dir','/opt/easel/white space'), ('release_dir','/opt/easel/;CANARY'),
    ('config_dir','relative/CANARY'), ('certificate_dir','/certs/"CANARY'),
    ('version',True), ('api_port',True), ('api_port',1023), ('api_port',65536),
    ('console_port',19074), ('api_port','19074'), ('password','SYNTHETIC_SECRET_CANARY')])
def test_unsafe_public_contract_rejected_with_fixed_errors(tmp_path, field, value):
    path = tmp_path / 'public.json';path.write_text(json.dumps(config(**{field:value})))
    with pytest.raises(prep.PreparationError) as error:
        prep.load_config(path)
    assert error.value.code == 'invalid_public_configuration'
    assert 'CANARY' not in str(error.value)


def test_duplicate_oversize_and_missing_fields_are_rejected(tmp_path):
    path = tmp_path / 'public.json'
    for raw in ('{"version":1,"version":1}', ' ' * 65537, json.dumps({k:v for k,v in config().items() if k!='api_port'})):
        path.write_text(raw)
        with pytest.raises(prep.PreparationError):prep.load_config(path)


def test_origin443_is_normalized_but_exact_issuer_changes_remain_visible(tmp_path, public_tree):
    value = config(public_url='https://API.example.invalid:443',console_public_url='https://console.example.invalid:443',
        source_oidc_issuer='https://login.example.invalid/oidc',oidc_issuer='https://login.example.invalid:443/oidc')
    report = prepare(tmp_path, public_tree, value)
    assert report['public_url']=='https://api.example.invalid'
    assert report['console_public_url']=='https://console.example.invalid'
    assert report['oidc_issuer']=='https://login.example.invalid:443/oidc'
    assert report['identity_migration_required'] is True
    assert 'proxy_set_header Host api.example.invalid;' in (tmp_path/'new-output/nginx-central.conf').read_text()


def test_equal_issuer_and_real_dns_syntax_still_never_prove_readiness(tmp_path, public_tree):
    value=config(public_url='https://api.company.com',console_public_url='https://console.company.com',
        oidc_issuer='https://login.company.com/oidc',source_oidc_issuer='https://login.company.com/oidc')
    report=prepare(tmp_path,public_tree,value)
    assert report['identity_migration_required'] is False and report['reserved_domain_placeholders'] is False
    assert report['production_ready'] is False and 'dns_tls_not_verified' in report['outstanding_checks']
    assert 'backup_restore_not_verified' in report['outstanding_checks']


@pytest.mark.parametrize('change',[
    'source','migration','static','missing','extra_source','duplicate','traversal','absolute','symlink','outside_code'])
def test_wheel_must_exactly_match_all_trusted_source_and_static_bytes(tmp_path,public_tree,change):
    kwargs={}
    if change=='source':kwargs['changes']={'platform_service/app.py':b'TAMPERED'}
    if change=='migration':kwargs['changes']={'platform_service/migrations/001_fixture.sql':b'TAMPERED'}
    if change=='static':kwargs['changes']={'platform_service/console_static/assets/index-fixture.js':b'TAMPERED'}
    if change=='missing':kwargs['missing']=['platform_service/console_static/index.html']
    if change=='extra_source':kwargs['changes']={'platform_service/extra.py':b'EXTRA'}
    if change=='duplicate':kwargs['duplicate']='platform_service/app.py'
    if change=='traversal':kwargs['changes']={'../CANARY.py':b'NO'}
    if change=='absolute':kwargs['changes']={'/CANARY.py':b'NO'}
    if change=='symlink':kwargs['symlink']='platform_service/CANARY.py'
    if change=='outside_code':kwargs['changes']={'CANARY.py':b'NO'}
    with pytest.raises(prep.PreparationError) as error:
        prep.verify_wheel(wheel(tmp_path,public_tree,**kwargs))
    assert error.value.code=='invalid_release_wheel' and 'CANARY' not in str(error.value)


def test_static_inventory_and_wheel_size_bounds_are_enforced(tmp_path,public_tree,monkeypatch):
    valid=wheel(tmp_path,public_tree)
    (prep.STATIC_ROOT/'assets/extra.css').write_text('EXTRA')
    with pytest.raises(prep.PreparationError):prep.verify_wheel(valid)
    (prep.STATIC_ROOT/'assets/extra.css').unlink()
    monkeypatch.setattr(prep,'MAX_WHEEL_BYTES',1)
    with pytest.raises(prep.PreparationError):prep.verify_wheel(valid)


def test_output_is_exclusive_and_preserves_preexisting_content(tmp_path,public_tree):
    prepare(tmp_path,public_tree)
    output=tmp_path/'new-output';sentinel=output/'sentinel.txt';sentinel.write_text('PRESERVE')
    with pytest.raises(prep.PreparationError) as error:prepare(tmp_path,public_tree)
    assert error.value.code=='output_already_exists' and sentinel.read_text()=='PRESERVE'
    assert stat.S_IMODE(output.stat().st_mode)==0o700


def test_cli_failure_is_fixed_json_without_config_values(tmp_path,capsys):
    path=tmp_path/'public.json';path.write_text(json.dumps(config(password='SYNTHETIC_SECRET_CANARY')))
    code=prep.main(['--config',str(path),'--wheel',str(tmp_path/'CANARY.whl'),'--output',str(tmp_path/'output')])
    printed=capsys.readouterr()
    assert code==1 and 'CANARY' not in printed.out+printed.err
    assert json.loads(printed.out)=={'error':{'code':'invalid_public_configuration'}}
    assert not (tmp_path/'output').exists()


def test_trusted_tree_symlinks_and_wheel_expansion_limits_fail_closed(tmp_path,public_tree,monkeypatch):
    valid=wheel(tmp_path,public_tree)
    (prep.SOURCE_ROOT/'untrusted.py').symlink_to(tmp_path/'not-read-CANARY')
    with pytest.raises(prep.PreparationError):prep.verify_wheel(valid)
    (prep.SOURCE_ROOT/'untrusted.py').unlink()
    monkeypatch.setattr(prep,'MAX_MEMBERS',1)
    with pytest.raises(prep.PreparationError):prep.verify_wheel(valid)


def test_output_symlink_is_not_followed_or_changed(tmp_path,public_tree):
    existing=tmp_path/'existing';existing.mkdir();(existing/'sentinel').write_text('PRESERVE')
    (tmp_path/'new-output').symlink_to(existing,target_is_directory=True)
    with pytest.raises(prep.PreparationError) as error:prepare(tmp_path,public_tree)
    assert error.value.code=='output_already_exists'
    assert (tmp_path/'new-output').is_symlink()
    assert [p.name for p in existing.iterdir()]==['sentinel']


def test_output_modes_override_restrictive_umask(tmp_path,public_tree):
    value=tmp_path/'public.json';value.write_text(json.dumps(config()))
    archive=wheel(tmp_path,public_tree);previous=os.umask(0o777)
    try:prep.prepare(value,archive,tmp_path/'private-output')
    finally:os.umask(previous)
    output=tmp_path/'private-output'
    assert stat.S_IMODE(output.stat().st_mode)==0o700
    assert all(stat.S_IMODE(path.stat().st_mode)==0o600 for path in output.iterdir())


def test_cli_bad_wheel_never_echoes_bytes_or_traceback(tmp_path,capsys):
    path=tmp_path/'public.json';path.write_text(json.dumps(config()))
    archive=tmp_path/'release.whl';archive.write_bytes(b'SYNTHETIC_SECRET_CANARY_NOT_A_ZIP')
    assert prep.main(['--config',str(path),'--wheel',str(archive),'--output',str(tmp_path/'output')])==1
    printed=capsys.readouterr()
    assert printed.err=='' and 'CANARY' not in printed.out and 'Traceback' not in printed.out
    assert json.loads(printed.out)=={'error':{'code':'invalid_release_wheel'}}
    assert not (tmp_path/'output').exists()


def test_unit_uses_the_documented_dotvenv_release_layout(tmp_path,public_tree):
    prepare(tmp_path,public_tree)
    for name in ('easel-central-production.service','easel-console-production.service'):
        unit=(tmp_path/'new-output'/name).read_text()
        assert 'ExecStart=/opt/easel-central/releases/20261023/.venv/bin/python ' in unit


def test_nginx_certificate_subdirectories_are_api_and_console(tmp_path,public_tree):
    prepare(tmp_path,public_tree)
    nginx=(tmp_path/'new-output/nginx-central.conf').read_text()
    for role in ('api','console'):
        assert f'ssl_certificate /etc/ssl/easel-central-production/{role}/fullchain.pem;' in nginx
        assert f'ssl_certificate_key /etc/ssl/easel-central-production/{role}/privkey.pem;' in nginx


def test_generated_units_do_not_invent_an_approval_marker(tmp_path,public_tree):
    prepare(tmp_path,public_tree)
    for name in ('easel-central-production.service','easel-console-production.service'):
        unit=(tmp_path/'new-output'/name).read_text()
        assert 'deployment-approved' not in unit and 'approval-required' not in unit
        assert 'ExecStartPre=' not in unit


@pytest.mark.parametrize('metadata,dist_name',[
    (b'Name: wrong-project\nVersion: 0.1.0\n','easel_central_platform-0.1.0.dist-info'),
    (b'Name: easel-central-platform\nVersion: 9.9.9\n','easel_central_platform-0.1.0.dist-info'),
    (b'Name: easel-central-platform\nVersion: 0.1.0\n','easel_central_platform-9.9.9.dist-info'),
    (b'Name: easel-central-platform\nName: wrong-project\nVersion: 0.1.0\n','easel_central_platform-0.1.0.dist-info')])
def test_wheel_metadata_identity_matches_repo_project_and_dist_info(tmp_path,public_tree,metadata,dist_name):
    with pytest.raises(prep.PreparationError) as error:
        prep.verify_wheel(wheel(tmp_path,public_tree,metadata=metadata,dist_name=dist_name))
    assert error.value.code=='invalid_release_wheel'


def test_report_does_not_authenticate_dependency_metadata(tmp_path,public_tree):
    report=prepare(tmp_path,public_tree)
    assert report['metadata_identity_verified'] is True
    assert report['dependency_metadata_authenticated'] is False
    assert 'locked_dependencies_not_verified' in report['outstanding_checks']


def test_console_registry_template_uses_the_existing_runtime_contract(tmp_path,public_tree):
    from platform_service.console_registry import provider_registry
    prepare(tmp_path,public_tree)
    text=(tmp_path/'new-output/console.env.template').read_text()
    env={line.split('=',1)[0]:line.split('=',1)[1] for line in text.splitlines() if line and not line.startswith('#')}
    assert 'CENTRAL_CONSOLE_PROVIDER_REGISTRY' in env
    assert provider_registry(env)==()


@pytest.mark.parametrize('field',['public_url','console_public_url','oidc_issuer'])
@pytest.mark.parametrize('host',[
    '0x7f.0x0.0x0.0x1', '127.0x0.0.0x1', '0x7f.0.0.1', '0x7f.1'])
def test_dns_origins_reject_hex_and_mixed_ipv4_numeric_aliases(tmp_path,field,host):
    value='https://'+host+('/oidc' if field=='oidc_issuer' else '')
    path=tmp_path/'public.json';path.write_text(json.dumps(config(**{field:value})))
    with pytest.raises(prep.PreparationError) as error:prep.load_config(path)
    assert error.value.code=='invalid_public_configuration'


@pytest.mark.parametrize('issuer',['http://127.0.0.1:18763/oidc','http://[::1]:18763/oidc'])
def test_source_issuer_keeps_exact_literal_loopback_contract(tmp_path,issuer):
    path=tmp_path/'public.json';path.write_text(json.dumps(config(source_oidc_issuer=issuer)))
    assert prep.load_config(path).source_oidc_issuer==issuer


def test_source_issuer_rejects_hex_alias_instead_of_resolving_it(tmp_path):
    path=tmp_path/'public.json';path.write_text(json.dumps(config(source_oidc_issuer='http://0x7f.0.0.0x1:18763/oidc')))
    with pytest.raises(prep.PreparationError):prep.load_config(path)
